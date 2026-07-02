"""Тесты Google-синхронизации с замоканным клиентом Google (без сети).

Запуск:  cd backend && python3 tests/test_google_sheets.py
Требует переменные GOOGLE_CLIENT_ID/SECRET (в тесте задаются программно) и
временную БД. Живой раунд-трип к Google здесь не проверяется — только логика.
"""
import io
import json
import os
import tempfile
from unittest import mock

# --- окружение ДО импорта app (settings читает env при импорте) ---
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id.apps.googleusercontent.com"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-secret"
os.environ["GOOGLE_REDIRECT_URI"] = "http://localhost:8000/api/google/callback"
_dbfd, _dbpath = tempfile.mkstemp(suffix=".db")
os.close(_dbfd)
os.environ["DATABASE_URL"] = f"sqlite:///{_dbpath}"
os.environ["ANTHROPIC_API_KEY"] = ""

from app.config import settings
from app.db import Base, engine, SessionLocal, init_db
from app import models
from app.finmodel import google_client, sheets_sync
from app.finmodel.drivers import drivers_from_profile

init_db()
PASS = 0

def ok(msg):
    global PASS; PASS += 1; print("  PASS", msg)

def session():
    return SessionLocal()

# ---------- 1. config gating ----------
assert settings.google_enabled is True, "google_enabled should be True with creds"
ok("settings.google_enabled True when client id/secret present")

# ---------- 2. tables created ----------
tables = set(Base.metadata.tables)
assert "google_credentials" in tables and "sheet_links" in tables
db = session()
db.query(models.GoogleCredential).all(); db.query(models.SheetLink).all()  # no error
ok("new tables google_credentials + sheet_links exist and query")

# ---------- 3. OAuth auth URL ----------
url, state = google_client.build_auth_url()
assert url.startswith("https://accounts.google.com/o/oauth2/auth")
assert "test-client-id" in url and "spreadsheets" in url and "callback" in url
assert "access_type=offline" in url
ok("build_auth_url returns consent URL with client id, scope, offline access")

# ---------- 4. credential save / load / refresh ----------
from google.oauth2.credentials import Credentials
from datetime import datetime, timedelta
fake_creds = Credentials(
    token="atk", refresh_token="rtk", token_uri=google_client.TOKEN_URI,
    client_id=settings.GOOGLE_CLIENT_ID, client_secret=settings.GOOGLE_CLIENT_SECRET,
    scopes=list(settings.GOOGLE_SCOPES), expiry=datetime.utcnow() + timedelta(hours=1),
)
google_client.save_credentials(db, fake_creds, email="user@example.com")
assert google_client.is_connected(db) is True
row = db.query(models.GoogleCredential).first()
assert row.refresh_token == "rtk" and row.email == "user@example.com"
loaded = google_client.load_credentials(db)   # valid token -> no refresh needed
assert loaded.refresh_token == "rtk"
ok("save/load credentials round-trip; is_connected True")

# refresh path: expired token triggers creds.refresh()
row.expiry = datetime.utcnow() - timedelta(hours=1); db.commit()
with mock.patch("google.oauth2.credentials.Credentials.refresh", autospec=True) as m_ref:
    def _do_refresh(self, request):
        self.token = "atk2"; self.expiry = datetime.utcnow() + timedelta(hours=1)
    m_ref.side_effect = _do_refresh
    creds2 = google_client.load_credentials(db)
    assert m_ref.called, "refresh() should be called for expired token"
assert db.query(models.GoogleCredential).first().token == "atk2"
ok("load_credentials refreshes expired access token and persists it")

# ---------- 5. export refactor still works ----------
prof = models.BusinessProfile(industry_code="services", answers_json=json.dumps({
    "monthly_revenue": "1500000", "payroll_monthly": "400000", "rent_monthly": "120000",
    "loan_amount": "2000000", "loan_rate": "18", "discount_rate": "20",
}, ensure_ascii=False))
db.add(prof); db.commit()
drv = drivers_from_profile(prof)
assert drv["base_revenue"] == 1500000 and drv["loan_rate"] == 0.18 and drv["discount_rate"] == 0.20
from app.finmodel.excel_export import export_model_bytes
xlsx = export_model_bytes(title="Тест", drivers=drv)
assert xlsx[:4] == b"PK\x03\x04" and len(xlsx) > 5000
ok("drivers_from_profile + export_model_bytes still produce a valid .xlsx")

# ---------- 6. push_model (mocked Drive) ----------
def fake_drive():
    d = mock.MagicMock()
    d.files().create().execute.return_value = {"id": "SHEET_ABC", "webViewLink": "https://docs.google.com/spreadsheets/d/SHEET_ABC/edit"}
    d.files().update().execute.return_value = {"id": "SHEET_ABC"}
    return d

drive_mock = fake_drive()
with mock.patch.object(google_client, "drive_service", return_value=drive_mock):
    res = sheets_sync.push_model(db, title="Финмодель")
assert res["spreadsheet_id"] == "SHEET_ABC" and "SHEET_ABC" in res["url"]
link = db.query(models.SheetLink).first()
assert link.spreadsheet_id == "SHEET_ABC" and link.last_pushed_at is not None
# verify an .xlsx media was actually uploaded on create
create_kwargs = drive_mock.files().create.call_args
assert create_kwargs.kwargs["body"]["mimeType"] == sheets_sync.SHEET_MIME
ok("push_model uploads xlsx, converts to Google Sheet, stores SheetLink")

# second push -> update path (same fileId, no new create)
drive_mock2 = fake_drive()
with mock.patch.object(google_client, "drive_service", return_value=drive_mock2):
    res2 = sheets_sync.push_model(db, title="Финмодель")
assert res2["spreadsheet_id"] == "SHEET_ABC"
assert drive_mock2.files().update.called and not drive_mock2.files().create.call_args_list[1:]
ok("second push_model updates existing file (stable URL), no new create")

# ---------- 7. pull_edits (mocked Sheets) ----------
# values in PULL_CELL_MAP order: B21,B31,B32,B38,B42,B43,B6
pulled_values = [2000000, 450000, 130000, 3000000, 2500000, 0.19, 0.22]
def fake_sheets():
    s = mock.MagicMock()
    s.spreadsheets().values().batchGet().execute.return_value = {
        "valueRanges": [{"values": [[v]]} for v in pulled_values]
    }
    return s

with mock.patch.object(google_client, "sheets_service", return_value=fake_sheets()):
    pull = sheets_sync.pull_edits(db)
u = pull["updated"]
assert u["monthly_revenue"] == 2000000 and u["payroll_monthly"] == 450000
assert u["loan_rate"] == 19.0 and u["discount_rate"] == 22.0   # fraction*100
prof2 = db.query(models.BusinessProfile).first()
ans = json.loads(prof2.answers_json)
assert ans["monthly_revenue"] == 2000000 and ans["loan_rate"] == 19.0
ok("pull_edits maps driver cells -> profile answers (fractions -> percent)")

# round-trip: pulled drivers feed back into the model
drv2 = drivers_from_profile(prof2)
assert drv2["base_revenue"] == 2000000 and round(drv2["loan_rate"], 4) == 0.19
ok("round-trip: pulled edits flow back into model drivers")

db.close()
print(f"\nALL {PASS} GOOGLE-SYNC UNIT TESTS PASSED (Google client mocked)")
