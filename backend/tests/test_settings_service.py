import pytest

from app.services.settings_service import ENCRYPTED_SECRET_PREFIX
from app.services.settings_service import SettingsSecretError
from app.services.settings_service import SettingsService


class FakeSettingsRepository:
    def __init__(self, saved: dict | None = None) -> None:
        self.saved = saved
        self.updated_docs: list[dict] = []

    def get_settings(self) -> dict | None:
        return self.saved

    def upsert_settings(self, doc: dict) -> None:
        self.updated_docs.append(doc)
        self.saved = dict(doc)


def test_service_loads_settings_from_repository() -> None:
    repo = FakeSettingsRepository(
        saved={
            "base_currency": "HKD",
            "timezone": "Asia/Hong_Kong",
            "finnhub_api_key": "saved-key",
            "flex_token": "saved-flex-token",
            "flex_query_id": "12345",
            "pull_frequency_minutes": 30,
            "display_realtime_prices": False,
            "ai_provider": "openai",
            "ai_model": "gpt-5",
            "openai_api_key": "sk-saved",
            "minimax_api_key": "mini-saved",
            "minimax_base_url": "https://api.minimaxi.com/v1",
            "deepseek_api_key": "deepseek-saved",
            "deepseek_base_url": "https://api.deepseek.com",
            "futu_connection_mode": "local_opend",
            "futu_opend_host": "localhost",
            "futu_opend_port": 11111,
            "telegram_bot_token": "telegram-secret",
            "telegram_allowlisted_chat_ids": ["123456789"],
            "telegram_reports_enabled": True,
            "telegram_daily_report_time": "09:30",
        }
    )
    service = SettingsService(repository=repo)
    current = service.get()

    assert current.base_currency == "HKD"
    assert current.timezone == "Asia/Hong_Kong"
    assert current.finnhub_api_key == "saved-key"
    assert current.flex_token == "saved-flex-token"
    assert current.flex_query_id == "12345"
    assert current.pull_frequency_minutes == 30
    assert current.display_realtime_prices is False
    assert current.ai_provider == "openai"
    assert current.ai_model == "gpt-5"
    assert current.openai_api_key == "sk-saved"
    assert current.minimax_api_key == "mini-saved"
    assert current.minimax_base_url == "https://api.minimaxi.com/v1"
    assert current.deepseek_api_key == "deepseek-saved"
    assert current.deepseek_base_url == "https://api.deepseek.com"
    assert current.futu_connection_mode == "local_opend"
    assert current.futu_opend_host == "localhost"
    assert current.futu_opend_port == 11111
    assert current.telegram_bot_token == "telegram-secret"
    assert current.telegram_allowlisted_chat_ids == ["123456789"]
    assert current.telegram_reports_enabled is True
    assert current.telegram_daily_report_time == "09:30"


def test_service_ignores_unknown_repository_fields() -> None:
    repo = FakeSettingsRepository(
        saved={
            "base_currency": "USD",
            "unknown_future_key": "ignored",
        }
    )
    service = SettingsService(repository=repo)

    assert service.get().base_currency == "USD"
    assert service.get().ai_provider == "openai"
    assert service.get().telegram_allowlisted_chat_ids == []


def test_service_update_persists_settings_into_repository(monkeypatch) -> None:
    monkeypatch.setenv("IBKR_DASHBOARD_SETTINGS_KEY", "test-settings-key")
    repo = FakeSettingsRepository()
    service = SettingsService(repository=repo)

    updated = service.update(
        base_currency="CNY",
        timezone="Asia/Shanghai",
        flex_query_id="Q-1",
        pull_frequency_minutes=15,
        display_realtime_prices=False,
        ai_provider="mock",
        ai_model="mock",
        openai_api_key="sk-test",
        minimax_api_key="mini-test",
        minimax_base_url="https://api.minimaxi.com/v1",
        deepseek_api_key="deepseek-test",
        deepseek_base_url="https://api.deepseek.com",
        tavily_api_key="tavily-test",
        futu_connection_mode="local_opend",
        futu_opend_host="127.0.0.1",
        futu_opend_port=11111,
        telegram_bot_token="telegram-token",
        telegram_allowlisted_chat_ids=["123456789"],
        telegram_reports_enabled=True,
        telegram_daily_report_time="10:00",
    )

    assert updated.base_currency == "CNY"
    assert updated.timezone == "Asia/Shanghai"
    assert updated.flex_query_id == "Q-1"
    assert updated.pull_frequency_minutes == 15
    assert updated.display_realtime_prices is False
    assert updated.ai_provider == "mock"
    assert updated.ai_model == "mock"
    assert updated.telegram_allowlisted_chat_ids == ["123456789"]
    assert len(repo.updated_docs) == 1
    assert repo.updated_docs[0]["base_currency"] == "CNY"
    assert repo.updated_docs[0]["display_realtime_prices"] is False
    assert repo.updated_docs[0]["ai_provider"] == "mock"
    assert repo.updated_docs[0]["ai_model"] == "mock"
    assert repo.updated_docs[0]["openai_api_key"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert repo.updated_docs[0]["openai_api_key"] != "sk-test"
    assert repo.updated_docs[0]["minimax_api_key"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert repo.updated_docs[0]["minimax_api_key"] != "mini-test"
    assert repo.updated_docs[0]["minimax_base_url"] == "https://api.minimaxi.com/v1"
    assert repo.updated_docs[0]["deepseek_api_key"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert repo.updated_docs[0]["deepseek_api_key"] != "deepseek-test"
    assert repo.updated_docs[0]["deepseek_base_url"] == "https://api.deepseek.com"
    assert repo.updated_docs[0]["tavily_api_key"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert repo.updated_docs[0]["tavily_api_key"] != "tavily-test"
    assert repo.updated_docs[0]["telegram_bot_token"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert repo.updated_docs[0]["telegram_bot_token"] != "telegram-token"
    assert repo.updated_docs[0]["telegram_allowlisted_chat_ids"] == ["123456789"]

    loaded = SettingsService(repository=repo).get()
    assert loaded.openai_api_key == "sk-test"
    assert loaded.minimax_api_key == "mini-test"
    assert loaded.deepseek_api_key == "deepseek-test"
    assert loaded.tavily_api_key == "tavily-test"
    assert loaded.telegram_bot_token == "telegram-token"


def test_service_loads_existing_plain_secret_fields() -> None:
    repo = FakeSettingsRepository(
        saved={
            "finnhub_api_key": "plain-finnhub",
            "flex_token": "plain-flex",
            "openai_api_key": "plain-openai",
            "telegram_bot_token": "plain-telegram",
        }
    )

    current = SettingsService(repository=repo).get()

    assert current.finnhub_api_key == "plain-finnhub"
    assert current.flex_token == "plain-flex"
    assert current.openai_api_key == "plain-openai"
    assert current.telegram_bot_token == "plain-telegram"


def test_service_raises_when_encrypted_secret_cannot_be_decrypted(monkeypatch) -> None:
    monkeypatch.setenv("IBKR_DASHBOARD_SETTINGS_KEY", "first-test-key")
    repo = FakeSettingsRepository()
    SettingsService(repository=repo).update(openai_api_key="sk-test")
    persisted = dict(repo.saved or {})

    monkeypatch.setenv("IBKR_DASHBOARD_SETTINGS_KEY", "second-test-key")
    with pytest.raises(SettingsSecretError, match="openai_api_key"):
        SettingsService(repository=repo)

    assert repo.saved == persisted


def test_service_raises_on_malformed_encrypted_secret(monkeypatch) -> None:
    monkeypatch.setenv("IBKR_DASHBOARD_SETTINGS_KEY", "test-settings-key")
    repo = FakeSettingsRepository(
        saved={
            "openai_api_key": f"{ENCRYPTED_SECRET_PREFIX}not-valid",
        }
    )

    with pytest.raises(SettingsSecretError, match="openai_api_key"):
        SettingsService(repository=repo)


def test_service_mark_sync_success_persists_timestamp() -> None:
    repo = FakeSettingsRepository()
    service = SettingsService(repository=repo)
    service.mark_sync_success("2026-04-28T12:00:00+00:00")

    current = service.get()
    assert current.last_successful_sync_at == "2026-04-28T12:00:00+00:00"
    assert current.last_successful_sync_date == "2026-04-28"
    assert repo.updated_docs[-1]["last_successful_sync_at"] == "2026-04-28T12:00:00+00:00"
    assert repo.updated_docs[-1]["last_successful_sync_date"] == "2026-04-28"
