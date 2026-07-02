"""OAuth2 и клиенты Google API для синхронизации модели с Google Sheets.

Single-tenant MVP: токены хранятся в одной строке `GoogleCredential`. Scope
минимальный — `spreadsheets` (чтение/запись таблиц) и `drive.file` (доступ
только к файлам, созданным приложением). Ключи берутся из окружения; без них
интеграция выключена (`settings.google_enabled == False`).
"""
from __future__ import annotations

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from .. import models
from ..config import settings

AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"


class GoogleNotConfigured(Exception):
    """Нет GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET — интеграция не настроена."""


class GoogleNotConnected(Exception):
    """Нет сохранённых токенов — пользователь ещё не проходил авторизацию."""


def _require_configured() -> None:
    if not settings.google_enabled:
        raise GoogleNotConfigured(
            "Google-интеграция не настроена: задайте GOOGLE_CLIENT_ID и GOOGLE_CLIENT_SECRET."
        )


def _client_config() -> dict:
    return {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": [settings.GOOGLE_REDIRECT_URI],
        }
    }


def _flow(state: str | None = None) -> Flow:
    _require_configured()
    return Flow.from_client_config(
        _client_config(),
        scopes=list(settings.GOOGLE_SCOPES),
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
        state=state,
    )


def build_auth_url() -> tuple[str, str]:
    """(auth_url, state) для перенаправления пользователя на согласие Google."""
    flow = _flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",          # вернуть refresh_token
        include_granted_scopes="true",
        prompt="consent",               # гарантировать выдачу refresh_token
    )
    return auth_url, state


def exchange_code(db, code: str, state: str | None = None) -> "models.GoogleCredential":
    """Обмен кода авторизации на токены и их сохранение."""
    flow = _flow(state=state)
    flow.fetch_token(code=code)
    return save_credentials(db, flow.credentials)


def save_credentials(db, creds: Credentials, email: str | None = None) -> "models.GoogleCredential":
    row = db.query(models.GoogleCredential).first()
    if row is None:
        row = models.GoogleCredential()
        db.add(row)
    row.token = creds.token
    if creds.refresh_token:             # приходит только при первом согласии
        row.refresh_token = creds.refresh_token
    row.token_uri = creds.token_uri or TOKEN_URI
    row.scopes = " ".join(creds.scopes or settings.GOOGLE_SCOPES)
    row.expiry = creds.expiry
    if email:
        row.email = email
    db.commit()
    return row


def _credentials_from_row(row: "models.GoogleCredential") -> Credentials:
    return Credentials(
        token=row.token,
        refresh_token=row.refresh_token,
        token_uri=row.token_uri or TOKEN_URI,
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=(row.scopes or "").split() or list(settings.GOOGLE_SCOPES),
        expiry=row.expiry,
    )


def load_credentials(db) -> Credentials:
    """Актуальные Credentials; при истёкшем access token обновляет по refresh."""
    _require_configured()
    row = db.query(models.GoogleCredential).first()
    if row is None or not (row.refresh_token or row.token):
        raise GoogleNotConnected("Google-аккаунт не подключён.")
    creds = _credentials_from_row(row)
    if not creds.valid:
        if creds.refresh_token:
            creds.refresh(Request())
            save_credentials(db, creds)
        else:
            raise GoogleNotConnected("Токен истёк, refresh_token отсутствует — авторизуйтесь заново.")
    return creds


def is_connected(db) -> bool:
    row = db.query(models.GoogleCredential).first()
    return bool(row and (row.refresh_token or row.token))


def disconnect(db) -> None:
    db.query(models.GoogleCredential).delete()
    db.commit()


def drive_service(db):
    return build("drive", "v3", credentials=load_credentials(db), cache_discovery=False)


def sheets_service(db):
    return build("sheets", "v4", credentials=load_credentials(db), cache_discovery=False)
