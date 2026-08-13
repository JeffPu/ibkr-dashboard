from pydantic import BaseModel
from fastapi import APIRouter

from app.api.response_models import STORAGE_UNAVAILABLE_OPENAPI_RESPONSE
from app.services.market_data_provider import build_market_data_provider
from app.services.portfolio_analysis_service import PortfolioAnalysisService
from app.services.settings_service import SettingsService
from app.services.telegram_service import TelegramCommandService


router = APIRouter()
_settings_service: SettingsService = SettingsService()
_raw_repository: object | None = None


class TelegramDryRunRequest(BaseModel):
    chat_id: str
    text: str


def set_settings_service(service: SettingsService) -> None:
    global _settings_service
    _settings_service = service


def set_raw_repository(repository: object | None) -> None:
    global _raw_repository
    _raw_repository = repository


@router.post("/api/telegram/commands/dry-run", responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE)
def dry_run_telegram_command(payload: TelegramDryRunRequest) -> dict[str, object]:
    service = _telegram_command_service()
    return service.handle_command(chat_id=payload.chat_id, text=payload.text)


@router.post("/api/telegram/reports/dry-run", responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE)
def dry_run_telegram_report() -> dict[str, object]:
    settings = _settings_service.get()
    service = _telegram_command_service()
    message, status = service.build_daily_report_message()
    return {
        "ok": bool(settings.telegram_reports_enabled and settings.telegram_allowlisted_chat_ids),
        "status": status,
        "message": message,
        "would_send_to": len(settings.telegram_allowlisted_chat_ids),
        "schedule": settings.telegram_daily_report_time if settings.telegram_reports_enabled else None,
    }


def _telegram_command_service() -> TelegramCommandService:
    settings = _settings_service.get()
    provider = build_market_data_provider(settings)
    analysis_service = PortfolioAnalysisService(
        raw_repository=_raw_repository,
        settings_service=_settings_service,
        market_data_provider=provider,
    )
    return TelegramCommandService(
        settings_service=_settings_service,
        analysis_service=analysis_service,
        raw_repository=_raw_repository,
    )
