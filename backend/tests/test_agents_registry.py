"""Тесты реестра навыков: карта, планировщик, деградация без LLM."""
from app.agents import REGISTRY, available, coverage, plan, resolve


class TestRegistry:
    def test_codes_unique(self):
        codes = [s.code for s in REGISTRY]
        assert len(codes) == len(set(codes))

    def test_every_llm_skill_has_fallback_or_is_listed(self):
        gaps = coverage()["without_fallback"]
        # осознанное исключение: уточнение допущений моделью — не критично,
        # без него работает движок отраслевых правил
        assert set(gaps) <= {"assumptions_ai_refine"}

    def test_roles_are_valid(self):
        assert {s.role for s in REGISTRY} <= {"M", "A", "C"}

    def test_coverage_counts(self):
        data = coverage()
        assert data["total"] == len(REGISTRY)
        assert data["by_role"]["M"] >= 5 and "A" in data["by_role"] and "C" in data["by_role"]


class TestDegradation:
    def test_llm_skills_hidden_without_llm(self):
        codes = {s.code for s in available(llm_enabled=False)}
        assert "extract_assumptions" not in codes
        assert "categorize_rules" in codes

    def test_resolve_falls_back(self):
        skill = resolve("categorize_ai", llm_enabled=False)
        assert skill is not None and skill.code == "categorize_rules"

    def test_resolve_keeps_main_with_llm(self):
        skill = resolve("categorize_ai", llm_enabled=True)
        assert skill.code == "categorize_ai"

    def test_unknown_code(self):
        assert resolve("нет-такого", llm_enabled=True) is None


class TestPlanner:
    def test_plan_for_book_is_ordered_by_stage(self):
        steps = plan("book", llm_enabled=True)
        stages = [s.stage for s in steps]
        assert stages == sorted(stages)
        assert any(s.code == "build_book" for s in steps)

    def test_plan_prefers_deterministic_over_llm(self):
        steps = {s.code for s in plan("assumptions", llm_enabled=True)}
        # дешёвый детерминированный путь выигрывает у дорогого LLM-извлечения
        assert "assumptions_auto" in steps
        assert "extract_assumptions" not in steps

    def test_plan_respects_have(self):
        without = plan("exports.xlsx", llm_enabled=True)
        with_book = plan("exports.xlsx", have={"book"}, llm_enabled=True)
        assert len(with_book) < len(without)

    def test_plan_without_llm_has_no_llm_skills(self):
        steps = plan("book", llm_enabled=False)
        assert all(not s.requires_llm for s in steps)


class TestApi:
    def test_agents_endpoint(self, client):
        payload = client.get("/api/agents").json()
        assert payload["coverage"]["total"] == len(REGISTRY)
        assert isinstance(payload["skills"], list) and payload["skills"]

    def test_agents_plan_endpoint(self, client):
        payload = client.get("/api/agents", params={"goal": "book"}).json()
        assert payload["goal"] == "book"
        assert any(s["code"] == "build_book" for s in payload["plan"])

    def test_health_reports_llm_provider(self, client):
        llm = client.get("/api/health").json()["llm"]
        assert "provider" in llm and "supported" in llm
