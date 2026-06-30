from collections.abc import Callable
from datetime import date
from datetime import timedelta
import re

from fastapi import APIRouter
from fastapi import HTTPException
from pydantic import BaseModel

from app.api.response_models import STORAGE_UNAVAILABLE_OPENAPI_RESPONSE
from app.services.flex_client import FlexStatementClient
from app.repositories.raw_repository import RawRepository
from app.services.market_data_provider import LongbridgeReadOnlyProvider
from app.services.market_data_provider import build_futu_opend_provider
from app.services.quote_service import QuoteService
from app.services.quote_service import fetch_finnhub_quote
from app.services.quote_service import refresh_longbridge_history_cache
from app.services.settings_service import SettingsService

router = APIRouter()
settings_service = SettingsService()
raw_repository: RawRepository | object | None = None
quote_service: QuoteService | None = None
daily_sync_runner: Callable[[str, str], dict[str, str]] | None = None
pull_frequency_update_handler: Callable[[int], None] | None = None
telegram_report_update_handler: Callable[[], None] | None = None
SUPPORTED_AI_PROVIDERS = {"openai", "minimax", "deepseek", "mock"}
MAX_AI_MODEL_LENGTH = 100
SUPPORTED_FUTU_CONNECTION_MODES = {"disabled", "local_opend", "longbridge"}
DEFAULT_HISTORY_REFRESH_SYMBOLS = ["SPY", "QQQ", "DIA", "IWM", "^GSPC", "^IXIC", "^NDX", "^VIX"]
AI_MODEL_OPTIONS = {
    "openai": [
        {"value": "gpt-5-mini", "label": "GPT-5 mini · 更快"},
        {"value": "gpt-5", "label": "GPT-5 · 质量优先"},
    ],
    "minimax": [
        {"value": "MiniMax-M2.5-highspeed", "label": "MiniMax M2.5 highspeed · 更快"},
        {"value": "MiniMax-M2.7-highspeed", "label": "MiniMax M2.7 highspeed · 质量优先"},
    ],
    "deepseek": [
        {"value": "deepseek-v4-flash", "label": "DeepSeek V4 Flash · 更快"},
        {"value": "deepseek-v4-pro", "label": "DeepSeek V4 Pro · 质量优先"},
    ],
    "mock": [
        {"value": "mock", "label": "Mock · 本地模拟"},
    ],
}
TIME_OF_DAY_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
TELEGRAM_CHAT_ID_PATTERN = re.compile(r"^-?\d{5,20}$")


class SettingsUpdateRequest(BaseModel):
    base_currency: str | None = None
    timezone: str | None = None
    finnhub_api_key: str | None = None
    flex_token: str | None = None
    flex_query_id: str | None = None
    pull_frequency_minutes: int | None = None
    display_realtime_prices: bool | None = None
    ai_provider: str | None = None
    ai_model: str | None = None
    openai_api_key: str | None = None
    minimax_api_key: str | None = None
    minimax_base_url: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str | None = None
    tavily_api_key: str | None = None
    futu_connection_mode: str | None = None
    futu_opend_host: str | None = None
    futu_opend_port: int | None = None
    telegram_bot_token: str | None = None
    telegram_allowlisted_chat_ids: list[str | int] | None = None
    telegram_reports_enabled: bool | None = None
    telegram_daily_report_time: str | None = None
    mcp_server_enabled: bool | None = None
    report_cache_enabled: bool | None = None
    report_cache_ttl_minutes: int | None = None


class RevealApiKeyRequest(BaseModel):
    confirm: bool


class FinnhubConnectionTestRequest(BaseModel):
    api_key: str | None = None
    symbol: str | None = None


class FutuConnectionTestRequest(BaseModel):
    symbol: str | None = None


class LongbridgeConnectionTestRequest(BaseModel):
    symbol: str | None = None
    days: int | None = None


class HistoryRefreshRequest(BaseModel):
    symbols: list[str] | None = None
    days: int | None = None


def set_settings_service(service: SettingsService) -> None:
    global settings_service
    settings_service = service


def set_raw_repository(repository: object | None) -> None:
    global raw_repository
    raw_repository = repository


def set_quote_service(service: QuoteService | None) -> None:
    global quote_service
    quote_service = service


def set_daily_sync_runner(runner: Callable[[str, str], dict[str, str]]) -> None:
    global daily_sync_runner
    daily_sync_runner = runner


def set_pull_frequency_update_handler(handler: Callable[[int], None]) -> None:
    global pull_frequency_update_handler
    pull_frequency_update_handler = handler


def set_telegram_report_update_handler(handler: Callable[[], None]) -> None:
    global telegram_report_update_handler
    telegram_report_update_handler = handler


def _default_daily_sync_runner(token: str, query_id: str) -> dict[str, str]:
    flex_client = FlexStatementClient()
    statement_xml = flex_client.fetch_statement_xml(token=token, query_id=query_id)
    return {"status": "synced", "statement_size": str(len(statement_xml))}


@router.get("/api/settings", responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE)
def get_settings() -> dict[str, object]:
    return _settings_response()


@router.get("/api/settings/ai-models", responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE)
def get_ai_models() -> dict[str, object]:
    return {
        "providers": [
            {
                "provider": provider,
                "default_model": options[0]["value"],
                "models": options,
            }
            for provider, options in AI_MODEL_OPTIONS.items()
        ]
    }


@router.put("/api/settings", responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE)
def update_settings(payload: SettingsUpdateRequest) -> dict[str, object]:
    if payload.pull_frequency_minutes is not None and payload.pull_frequency_minutes <= 0:
        raise HTTPException(
            status_code=400,
            detail="pull_frequency_minutes must be greater than 0",
        )
    if payload.ai_provider is not None and payload.ai_provider not in SUPPORTED_AI_PROVIDERS:
        raise HTTPException(status_code=400, detail="unsupported ai_provider")
    if payload.ai_model is not None and len(payload.ai_model.strip()) > MAX_AI_MODEL_LENGTH:
        raise HTTPException(status_code=400, detail="ai_model is too long")
    if payload.minimax_base_url is not None and not payload.minimax_base_url.startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="minimax_base_url must be an http(s) URL")
    if payload.deepseek_base_url is not None and not payload.deepseek_base_url.startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="deepseek_base_url must be an http(s) URL")
    if (
        payload.futu_connection_mode is not None
        and payload.futu_connection_mode not in SUPPORTED_FUTU_CONNECTION_MODES
    ):
        raise HTTPException(status_code=400, detail="unsupported futu_connection_mode")
    if payload.futu_opend_host is not None and not payload.futu_opend_host.strip():
        raise HTTPException(status_code=400, detail="futu_opend_host must not be empty")
    if payload.futu_opend_port is not None and not 1 <= payload.futu_opend_port <= 65535:
        raise HTTPException(status_code=400, detail="futu_opend_port must be between 1 and 65535")
    if (
        payload.telegram_daily_report_time is not None
        and not TIME_OF_DAY_PATTERN.fullmatch(payload.telegram_daily_report_time)
    ):
        raise HTTPException(status_code=400, detail="telegram_daily_report_time must use HH:MM")
    if payload.report_cache_ttl_minutes is not None and payload.report_cache_ttl_minutes <= 0:
        raise HTTPException(status_code=400, detail="report_cache_ttl_minutes must be greater than 0")
    telegram_allowlisted_chat_ids = None
    if payload.telegram_allowlisted_chat_ids is not None:
        telegram_allowlisted_chat_ids = _normalize_telegram_chat_ids(payload.telegram_allowlisted_chat_ids)
    settings = settings_service.update(
        base_currency=payload.base_currency,
        timezone=payload.timezone,
        finnhub_api_key=payload.finnhub_api_key,
        flex_token=payload.flex_token,
        flex_query_id=payload.flex_query_id,
        pull_frequency_minutes=payload.pull_frequency_minutes,
        display_realtime_prices=payload.display_realtime_prices,
        ai_provider=payload.ai_provider,
        ai_model=payload.ai_model.strip() if payload.ai_model is not None else None,
        openai_api_key=payload.openai_api_key,
        minimax_api_key=payload.minimax_api_key,
        minimax_base_url=payload.minimax_base_url.rstrip("/") if payload.minimax_base_url else payload.minimax_base_url,
        deepseek_api_key=payload.deepseek_api_key,
        deepseek_base_url=payload.deepseek_base_url.rstrip("/") if payload.deepseek_base_url else payload.deepseek_base_url,
        tavily_api_key=payload.tavily_api_key,
        futu_connection_mode=payload.futu_connection_mode,
        futu_opend_host=payload.futu_opend_host,
        futu_opend_port=payload.futu_opend_port,
        telegram_bot_token=payload.telegram_bot_token,
        telegram_allowlisted_chat_ids=telegram_allowlisted_chat_ids,
        telegram_reports_enabled=payload.telegram_reports_enabled,
        telegram_daily_report_time=payload.telegram_daily_report_time,
        mcp_server_enabled=payload.mcp_server_enabled,
        report_cache_enabled=payload.report_cache_enabled,
        report_cache_ttl_minutes=payload.report_cache_ttl_minutes,
    )
    if (
        payload.pull_frequency_minutes is not None
        and pull_frequency_update_handler is not None
    ):
        pull_frequency_update_handler(settings.pull_frequency_minutes)
    if (
        (
            payload.telegram_bot_token is not None
            or payload.telegram_allowlisted_chat_ids is not None
            or payload.telegram_reports_enabled is not None
            or payload.telegram_daily_report_time is not None
        )
        and telegram_report_update_handler is not None
    ):
        telegram_report_update_handler()
    return _settings_response()


@router.post("/api/settings/reveal-finnhub-key", responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE)
def reveal_finnhub_api_key(payload: RevealApiKeyRequest) -> dict[str, str]:
    if payload.confirm is not True:
        return {"finnhub_api_key": ""}
    settings = settings_service.get()
    return {"finnhub_api_key": settings.finnhub_api_key}


@router.post("/api/settings/reveal-flex-token", responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE)
def reveal_flex_token(payload: RevealApiKeyRequest) -> dict[str, str]:
    if payload.confirm is not True:
        return {"flex_token": ""}
    settings = settings_service.get()
    return {"flex_token": settings.flex_token}


@router.post("/api/settings/daily-sync/run", responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE)
def run_daily_sync() -> dict[str, object]:
    settings = settings_service.get()
    if not settings.flex_token or not settings.flex_query_id:
        raise HTTPException(
            status_code=400,
            detail="flex_token and flex_query_id must be configured before daily sync",
        )
    runner = daily_sync_runner or _default_daily_sync_runner
    try:
        result = runner(settings.flex_token, settings.flex_query_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"daily_sync_failed: {exc}") from exc
    return {
        "status": "accepted",
        "result": result,
        "request": {
            "flex_query_id": settings.flex_query_id,
        },
        "links": {
            "settings_url": "/api/settings",
        },
    }


@router.post(
    "/api/settings/data-sources/finnhub/test",
    responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE,
)
def test_finnhub_connection(payload: FinnhubConnectionTestRequest) -> dict[str, object]:
    symbol = (payload.symbol or "AAPL").upper()
    api_key = payload.api_key if payload.api_key is not None else settings_service.get().finnhub_api_key
    if not api_key:
        return {"ok": False, "symbol": symbol, "message": "missing_api_key"}
    price = fetch_finnhub_quote(symbol=symbol, api_key=api_key)
    if price is None:
        return {
            "ok": False,
            "symbol": symbol,
            "message": "finnhub_unreachable_or_invalid_key",
        }
    return {"ok": True, "symbol": symbol, "price": price, "message": "connected"}


@router.post(
    "/api/settings/data-sources/futu/test",
    responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE,
)
def test_futu_connection(payload: FutuConnectionTestRequest) -> dict[str, object]:
    settings = settings_service.get()
    if settings.futu_connection_mode != "local_opend":
        return {"ok": False, "message": "futu_connection_mode_disabled"}
    provider = build_futu_opend_provider(settings)
    quote = provider.get_quote(payload.symbol or "AAPL")
    return {
        "ok": quote.get("status") == "ready",
        "symbol": quote.get("symbol"),
        "price": quote.get("price"),
        "source": quote.get("source"),
        "message": quote.get("reason") or "connected",
    }


@router.post(
    "/api/settings/data-sources/longbridge/test",
    responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE,
)
def test_longbridge_connection(payload: LongbridgeConnectionTestRequest) -> dict[str, object]:
    provider = LongbridgeReadOnlyProvider()
    symbol = payload.symbol or "AAPL"
    quote = provider.get_quote(symbol)
    days = payload.days if payload.days is not None else 5
    history = provider.get_kline_history(symbol, days=max(1, min(days, 30)))
    return {
        "ok": quote.get("status") == "ready" and bool(history),
        "symbol": quote.get("symbol") or symbol.upper(),
        "price": quote.get("price"),
        "source": quote.get("source"),
        "history_points": len(history),
        "latest_history_date": history[-1].date if history else None,
        "message": quote.get("reason") or ("connected" if history else "longbridge_kline_unavailable"),
    }


@router.post(
    "/api/settings/data-sources/ibkr/test",
    responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE,
)
def test_ibkr_connection() -> dict[str, object]:
    settings = settings_service.get()
    if not settings.flex_token or not settings.flex_query_id:
        return {"ok": False, "message": "missing_flex_credentials"}
    try:
        flex_client = FlexStatementClient()
        reference_code = flex_client.request_reference_code(
            token=settings.flex_token,
            query_id=settings.flex_query_id,
        )
        return {"ok": True, "reference_code": reference_code, "message": "connected"}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


@router.post(
    "/api/settings/data-sources/quotes/refresh",
    responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE,
)
def refresh_quotes_now() -> dict[str, object]:
    if raw_repository is None or quote_service is None:
        raise HTTPException(status_code=503, detail="quote refresh service unavailable")
    latest = raw_repository.get_latest_account_snapshot()
    if latest is None:
        return {"status": "no_data", "symbols": [], "result": {"total": 0, "refreshed": 0, "failed": 0}}
    account_id = str(latest.get("account_id", "") or "")
    report_date = str(latest.get("report_date", "") or "")
    positions = raw_repository.es.search(
        index="ibkr_position_snapshots_v1",
        size=10000,
        term_filters={"account_id": account_id, "report_date": report_date} if account_id and report_date else None,
    )
    symbols = sorted(
        {
            str(row.get("symbol", "") or "")
            for row in positions
            if str(row.get("symbol", "") or "")
        }
    )
    result = quote_service.refresh_quotes(symbols)
    return {"status": "ok", "symbols": symbols, "result": result}


@router.post(
    "/api/settings/data-sources/history/refresh",
    responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE,
)
def refresh_market_history_now(payload: HistoryRefreshRequest | None = None) -> dict[str, object]:
    request = payload or HistoryRefreshRequest()
    end_date = date.today()
    days = request.days if request.days is not None else 365
    start_date = end_date - timedelta(days=max(30, min(days, 1825)))
    symbols = request.symbols or _latest_position_symbols()
    symbols = sorted({*DEFAULT_HISTORY_REFRESH_SYMBOLS, *symbols})
    if not symbols:
        return {"status": "no_data", "symbols": [], "points": 0}
    return refresh_longbridge_history_cache(
        symbols,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )


def _mask_secret(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 4:
        return "*" * len(secret)
    visible = 2
    middle = len(secret) - (visible * 2)
    return f"{secret[:visible]}{'*' * middle}{secret[-visible:]}"


def _normalize_telegram_chat_ids(values: list[str | int]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        chat_id = str(value).strip()
        if not TELEGRAM_CHAT_ID_PATTERN.fullmatch(chat_id):
            raise HTTPException(status_code=400, detail="invalid telegram_allowlisted_chat_ids")
        if chat_id not in seen:
            seen.add(chat_id)
            normalized.append(chat_id)
    return normalized


def _latest_position_symbols() -> list[str]:
    if raw_repository is None:
        return []
    latest = raw_repository.get_latest_account_snapshot()
    if latest is None:
        return []
    account_id = str(latest.get("account_id", "") or "")
    report_date = str(latest.get("report_date", "") or "")
    positions = raw_repository.es.search(
        index="ibkr_position_snapshots_v1",
        size=10000,
        term_filters={"account_id": account_id, "report_date": report_date} if account_id and report_date else None,
    )
    return sorted(
        {
            str(row.get("symbol", "") or "").upper()
            for row in positions
            if str(row.get("symbol", "") or "")
        }
    )


def _settings_response() -> dict[str, object]:
    settings = settings_service.get()
    return {
        "base_currency": settings.base_currency,
        "timezone": settings.timezone,
        "finnhub_api_key": _mask_secret(settings.finnhub_api_key),
        "flex_token": _mask_secret(settings.flex_token),
        "flex_query_id": settings.flex_query_id,
        "pull_frequency_minutes": settings.pull_frequency_minutes,
        "display_realtime_prices": settings.display_realtime_prices,
        "ai_provider": settings.ai_provider,
        "ai_model": _resolved_ai_model(settings.ai_provider, settings.ai_model),
        "openai_api_key": _mask_secret(settings.openai_api_key),
        "minimax_api_key": _mask_secret(settings.minimax_api_key),
        "minimax_base_url": settings.minimax_base_url,
        "deepseek_api_key": _mask_secret(settings.deepseek_api_key),
        "deepseek_base_url": settings.deepseek_base_url,
        "tavily_api_key": _mask_secret(settings.tavily_api_key),
        "futu_connection_mode": settings.futu_connection_mode,
        "futu_opend_host": settings.futu_opend_host,
        "futu_opend_port": settings.futu_opend_port,
        "telegram_bot_token": _mask_secret(settings.telegram_bot_token),
        "telegram_allowlisted_chat_ids": list(settings.telegram_allowlisted_chat_ids),
        "telegram_reports_enabled": settings.telegram_reports_enabled,
        "telegram_daily_report_time": settings.telegram_daily_report_time,
        "mcp_server_enabled": settings.mcp_server_enabled,
        "report_cache_enabled": settings.report_cache_enabled,
        "report_cache_ttl_minutes": settings.report_cache_ttl_minutes,
        "last_successful_sync_at": settings.last_successful_sync_at,
        "last_successful_sync_date": settings.last_successful_sync_date,
    }


def _resolved_ai_model(provider: str, model: str) -> str:
    if model:
        return model
    options = AI_MODEL_OPTIONS.get(provider) or AI_MODEL_OPTIONS["openai"]
    return str(options[0]["value"])
