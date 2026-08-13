from fastapi.testclient import TestClient

from app.api.routes.settings import set_daily_sync_runner
from app.api.routes.settings import set_pull_frequency_update_handler
from app.api.routes.settings import set_settings_service
from app.main import app
from app.services.settings_service import SettingsService


def setup_function(_: object) -> None:
    set_settings_service(SettingsService())
    set_daily_sync_runner(lambda _token, _query_id: {"status": "scheduled"})
    set_pull_frequency_update_handler(lambda _minutes: None)


def test_get_settings_returns_defaults() -> None:
    client = TestClient(app)
    response = client.get("/api/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["base_currency"] == "USD"
    assert body["timezone"] == "America/New_York"
    assert body["finnhub_api_key"] == ""
    assert body["flex_token"] == ""
    assert body["flex_query_id"] == ""
    assert body["pull_frequency_minutes"] == 60
    assert body["display_realtime_prices"] is False
    assert "ai_provider" not in body
    assert "ai_model" not in body
    assert "openai_api_key" not in body
    assert "minimax_api_key" not in body
    assert "deepseek_api_key" not in body
    assert "tavily_api_key" not in body
    assert body["futu_connection_mode"] == "disabled"
    assert body["futu_opend_host"] == "127.0.0.1"
    assert body["futu_opend_port"] == 11111
    assert body["telegram_bot_token"] == ""
    assert body["telegram_allowlisted_chat_ids"] == []
    assert body["telegram_reports_enabled"] is False
    assert body["telegram_daily_report_time"] == "08:30"
    assert body["last_successful_sync_at"] is None
    assert body["last_successful_sync_date"] is None


def test_ai_model_catalog_is_not_exposed() -> None:
    client = TestClient(app)
    response = client.get("/api/settings/ai-models")

    assert response.status_code == 404


def test_update_settings_persists_values() -> None:
    client = TestClient(app)
    update = {
        "base_currency": "HKD",
        "timezone": "Asia/Hong_Kong",
        "finnhub_api_key": "demo-key",
        "flex_token": "flex-secret",
        "flex_query_id": "Q-1001",
        "pull_frequency_minutes": 30,
        "display_realtime_prices": False,
        "futu_connection_mode": "local_opend",
        "futu_opend_host": "127.0.0.1",
        "futu_opend_port": 11111,
        "telegram_bot_token": "123456:telegram-token",
        "telegram_allowlisted_chat_ids": ["123456789", "123456789", "-100987654321"],
        "telegram_reports_enabled": True,
        "telegram_daily_report_time": "09:15",
    }

    put_response = client.put("/api/settings", json=update)
    assert put_response.status_code == 200

    get_response = client.get("/api/settings")
    assert get_response.status_code == 200
    body = get_response.json()
    assert body["base_currency"] == "HKD"
    assert body["timezone"] == "Asia/Hong_Kong"
    assert body["finnhub_api_key"] == "de****ey"
    assert body["flex_token"] == "fl*******et"
    assert body["flex_query_id"] == "Q-1001"
    assert body["pull_frequency_minutes"] == 30
    assert body["display_realtime_prices"] is False
    assert "ai_provider" not in body
    assert "ai_model" not in body
    assert "openai_api_key" not in body
    assert "minimax_api_key" not in body
    assert "deepseek_api_key" not in body
    assert "tavily_api_key" not in body
    assert body["futu_connection_mode"] == "local_opend"
    assert body["futu_opend_host"] == "127.0.0.1"
    assert body["futu_opend_port"] == 11111
    assert body["telegram_bot_token"].startswith("12")
    assert body["telegram_bot_token"].endswith("en")
    assert "*" in body["telegram_bot_token"]
    assert body["telegram_allowlisted_chat_ids"] == ["123456789", "-100987654321"]
    assert body["telegram_reports_enabled"] is True
    assert body["telegram_daily_report_time"] == "09:15"
    assert body["last_successful_sync_at"] is None
    assert body["last_successful_sync_date"] is None


def test_update_settings_rejects_retired_fields() -> None:
    response = TestClient(app).put("/api/settings", json={"report_cache_enabled": False})

    assert response.status_code == 422


def test_reveal_finnhub_key_requires_confirmation() -> None:
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={
            "base_currency": "USD",
            "timezone": "America/New_York",
            "finnhub_api_key": "real-demo-key",
            "flex_token": "flex-real-token",
        },
    )

    denied = client.post("/api/settings/reveal-finnhub-key", json={"confirm": False})
    assert denied.status_code == 200
    assert denied.json()["finnhub_api_key"] == ""

    allowed = client.post("/api/settings/reveal-finnhub-key", json={"confirm": True})
    assert allowed.status_code == 200
    assert allowed.json()["finnhub_api_key"] == "real-demo-key"

    denied_flex = client.post("/api/settings/reveal-flex-token", json={"confirm": False})
    assert denied_flex.status_code == 200
    assert denied_flex.json()["flex_token"] == ""

    allowed_flex = client.post("/api/settings/reveal-flex-token", json={"confirm": True})
    assert allowed_flex.status_code == 200
    assert allowed_flex.json()["flex_token"] == "flex-real-token"


def test_update_settings_accepts_longbridge_provider() -> None:
    client = TestClient(app)
    response = client.put("/api/settings", json={"futu_connection_mode": "longbridge"})

    assert response.status_code == 200
    assert response.json()["futu_connection_mode"] == "longbridge"


def test_run_daily_sync_requires_flex_credentials() -> None:
    client = TestClient(app)
    response = client.post("/api/settings/daily-sync/run")
    assert response.status_code == 400
    assert "flex_token and flex_query_id" in response.json()["message"]


def test_run_daily_sync_uses_saved_credentials() -> None:
    captured: dict[str, str] = {}

    def fake_runner(token: str, query_id: str) -> dict[str, str]:
        captured["token"] = token
        captured["query_id"] = query_id
        return {"status": "synced"}

    set_daily_sync_runner(fake_runner)
    client = TestClient(app)
    client.put(
        "/api/settings",
        json={
            "base_currency": "USD",
            "timezone": "America/New_York",
            "flex_token": "flex-token-1",
            "flex_query_id": "Q-2002",
        },
    )

    response = client.post("/api/settings/daily-sync/run")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["result"]["status"] == "synced"
    assert body["request"]["flex_query_id"] == "Q-2002"
    assert body["links"]["settings_url"] == "/api/settings"
    assert captured["token"] == "flex-token-1"
    assert captured["query_id"] == "Q-2002"


def test_run_daily_sync_returns_502_when_runner_fails() -> None:
    def flaky_runner(_token: str, _query_id: str) -> dict[str, str]:
        raise RuntimeError("upstream_failed")

    set_daily_sync_runner(flaky_runner)
    client = TestClient(app)
    client.put("/api/settings", json={"flex_token": "token", "flex_query_id": "Q1"})
    response = client.post("/api/settings/daily-sync/run")
    assert response.status_code == 502
    assert "daily_sync_failed" in response.json()["message"]


def test_finnhub_connection_test_endpoint() -> None:
    client = TestClient(app)
    missing_key = client.post("/api/settings/data-sources/finnhub/test", json={})
    assert missing_key.status_code == 200
    assert missing_key.json()["ok"] is False
    assert missing_key.json()["message"] == "missing_api_key"

    client.put("/api/settings", json={"finnhub_api_key": "invalid-key"})
    with_key = client.post("/api/settings/data-sources/finnhub/test", json={"symbol": "AAPL"})
    assert with_key.status_code == 200
    assert "ok" in with_key.json()


def test_futu_connection_test_endpoint_respects_disabled_mode() -> None:
    client = TestClient(app)
    response = client.post("/api/settings/data-sources/futu/test", json={"symbol": "AAPL"})

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["message"] == "futu_connection_mode_disabled"


def test_update_settings_rejects_non_positive_pull_frequency() -> None:
    client = TestClient(app)
    response = client.put("/api/settings", json={"pull_frequency_minutes": 0})
    assert response.status_code == 400
    assert "pull_frequency_minutes must be greater than 0" in response.json()["message"]


def test_update_settings_rejects_invalid_v2_settings() -> None:
    client = TestClient(app)

    invalid_chat_id = client.put(
        "/api/settings",
        json={"telegram_allowlisted_chat_ids": ["not-a-chat"]},
    )
    assert invalid_chat_id.status_code == 400
    assert "invalid telegram_allowlisted_chat_ids" in invalid_chat_id.json()["message"]

    invalid_futu_port = client.put("/api/settings", json={"futu_opend_port": 70000})
    assert invalid_futu_port.status_code == 400
    assert "futu_opend_port" in invalid_futu_port.json()["message"]

    invalid_telegram_time = client.put(
        "/api/settings",
        json={"telegram_daily_report_time": "25:99"},
    )
    assert invalid_telegram_time.status_code == 400
    assert "telegram_daily_report_time" in invalid_telegram_time.json()["message"]


def test_update_settings_notifies_pull_frequency_handler() -> None:
    captured: dict[str, int] = {}

    def handler(minutes: int) -> None:
        captured["minutes"] = minutes

    set_pull_frequency_update_handler(handler)
    client = TestClient(app)
    response = client.put("/api/settings", json={"pull_frequency_minutes": 45})

    assert response.status_code == 200
    assert captured["minutes"] == 45
