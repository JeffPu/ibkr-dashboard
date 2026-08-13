from collections.abc import AsyncIterator, Callable
from dataclasses import asdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
import json

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.response_models import STORAGE_UNAVAILABLE_OPENAPI_RESPONSE
from app.api.response_models import StorageAuthMode
from app.api.response_models import StorageFailureType
from app.api.routes.cash_flows import router as cash_flows_router
from app.api.routes.cash_flows import set_raw_repository as set_cash_flows_raw_repository
from app.api.routes.cash_flows import set_settings_service as set_cash_flows_settings_service
from app.api.routes.import_tasks import router as import_tasks_router
from app.api.routes.import_tasks import set_ingestion_service
from app.api.routes.overview import router as overview_router
from app.api.routes.overview import set_benchmark_history_fetcher as set_overview_benchmark_history_fetcher
from app.api.routes.overview import set_industry_mapping_service as set_overview_industry_mapping_service
from app.api.routes.overview import set_quote_service as set_overview_quote_service
from app.api.routes.overview import set_raw_repository
from app.api.routes.overview import set_derived_repository as set_overview_derived_repository
from app.api.routes.overview import set_settings_service as set_overview_settings_service
from app.api.routes.performance import router as performance_router
from app.api.routes.performance import set_derived_repository as set_performance_derived_repository
from app.api.routes.performance import set_raw_repository as set_performance_raw_repository
from app.api.routes.performance import set_settings_service as set_performance_settings_service
from app.api.routes.portfolio_analysis import router as portfolio_analysis_router
from app.api.routes.portfolio_analysis import set_industry_mapping_service as set_portfolio_analysis_industry_mapping_service
from app.api.routes.portfolio_analysis import set_quote_service as set_portfolio_analysis_quote_service
from app.api.routes.portfolio_analysis import set_raw_repository as set_portfolio_analysis_raw_repository
from app.api.routes.portfolio_analysis import set_settings_service as set_portfolio_analysis_settings_service
from app.api.routes.industry_mapping import router as industry_mapping_router
from app.api.routes.industry_mapping import set_mapping_service
from app.api.routes.positions import router as positions_router
from app.api.routes.positions import set_industry_mapping_service as set_positions_industry_mapping_service
from app.api.routes.positions import set_raw_repository as set_positions_raw_repository
from app.api.routes.positions import set_settings_service as set_positions_settings_service
from app.api.routes.positions import set_quote_service as set_positions_quote_service
from app.api.routes.quotes import router as quotes_router
from app.api.routes.quotes import set_quote_service
from app.api.routes.reconciliation import router as reconciliation_router
from app.api.routes.reconciliation import (
    set_auto_reconciliation_service,
    set_derived_repository as set_reconciliation_derived_repository,
)
from app.api.routes.settings import router as settings_router
from app.api.routes.settings import set_daily_sync_runner
from app.api.routes.settings import set_pull_frequency_update_handler
from app.api.routes.settings import set_quote_service as set_settings_quote_service
from app.api.routes.settings import set_raw_repository as set_settings_raw_repository
from app.api.routes.settings import set_settings_service
from app.api.routes.settings import set_telegram_report_update_handler
from app.api.routes.telegram import router as telegram_router
from app.api.routes.telegram import set_raw_repository as set_telegram_raw_repository
from app.api.routes.telegram import set_settings_service as set_telegram_settings_service
from app.api.routes.trades import router as trades_router
from app.api.routes.trades import set_raw_repository as set_trades_raw_repository
from app.api.routes.trades import set_settings_service as set_trades_settings_service
from app.core.config import load_settings
from app.core.config import validate_settings
from app.core.errors import register_error_handlers
from app.core.logging import get_logger
from app.core.trace import register_trace_middleware
from app.core.trace import generate_trace_id
from app.jobs.manual_backfill_job import validate_backfill_range
from app.jobs.daily_sync_job import build_auto_backfill_plan
from app.jobs.daily_sync_job import run_daily_sync_with_retry
from app.jobs.sync_scheduler import DailySyncScheduler, DailyTimeScheduler
from app.repositories.derived_repository import DerivedRepository
from app.repositories.es_client import ElasticsearchLike
from app.repositories.http_es_client import HttpElasticsearchClient
from app.repositories.industry_mapping_repository import IndustryMappingRepository
from app.repositories.in_memory_es import InMemoryElasticsearchClient
from app.repositories.raw_repository import RawRepository
from app.repositories.settings_repository import SettingsRepository
from app.services.auto_reconciliation_service import AutoReconciliationService
from app.services.industry_mapping_service import IndustryMappingService
from app.services.daily_performance_service import DailyPerformanceService
from app.services.flex_client import FlexStatementClient
from app.services.quote_service import QuoteService, fetch_benchmark_history, fetch_finnhub_quote, fetch_longbridge_quote, fetch_yahoo_quote
from app.services.ingestion_service import IngestionService
from app.services.market_data_provider import build_market_data_provider
from app.services.manual_backfill_service import ManualBackfillService
from app.services.portfolio_analysis_service import PortfolioAnalysisService
from app.services.settings_service import SettingsService
from app.services.telegram_service import (
    TelegramCommandService,
    TelegramDeliveryService,
    TelegramUpdatePollingService,
)
from app.services.xml_parser import parse_xml_string


logger = get_logger(__name__)
runtime_settings = load_settings()


def ensure_runtime_settings_valid() -> None:
    try:
        validate_settings(runtime_settings)
    except ValueError as exc:
        logger.error(
            json.dumps(
                {
                    "event": "config_error",
                    "backend": runtime_settings.es_backend,
                    "error": str(exc),
                }
            ),
            extra={"trace_id": "startup"},
        )
        raise


ensure_runtime_settings_valid()


def detect_es_auth_mode() -> StorageAuthMode:
    if runtime_settings.es_api_key:
        return StorageAuthMode.API_KEY
    if runtime_settings.es_username and runtime_settings.es_password:
        return StorageAuthMode.BASIC
    return StorageAuthMode.NONE


def build_es_client() -> ElasticsearchLike:
    if runtime_settings.es_backend == "http":
        return HttpElasticsearchClient(
            base_url=runtime_settings.es_base_url,
            timeout_seconds=runtime_settings.es_timeout_seconds,
            api_key=runtime_settings.es_api_key or None,
            username=runtime_settings.es_username or None,
            password=runtime_settings.es_password or None,
            headers=runtime_settings.es_extra_headers or None,
        )
    return InMemoryElasticsearchClient()


shared_es = build_es_client()
required_indexes = [
    "ibkr_account_snapshots_v1",
    "ibkr_position_snapshots_v1",
    "ibkr_trade_records_v1",
    "ibkr_cash_transactions_v1",
    "ibkr_stmt_funds_lines_v1",
    "ibkr_fx_rates_v1",
    "portfolio_returns_v1",
    "reconciliation_results_v1",
    "app_settings_v1",
    "symbol_industry_overrides_v1",
    "portfolio_ai_analysis_cache_v1",
]


def initialize_storage(es_client: ElasticsearchLike, index_status: dict[str, bool]) -> None:
    es_client.ping()
    for index in required_indexes:
        es_client.ensure_index(index)
        index_status[index] = True


raw_repository = RawRepository(es_client=shared_es)
derived_repository = DerivedRepository(es_client=shared_es)
settings_repository = SettingsRepository(es_client=shared_es)
industry_mapping_repository = IndustryMappingRepository(es_client=shared_es)
set_ingestion_service(IngestionService(raw_repository=raw_repository))
set_raw_repository(raw_repository)
set_settings_raw_repository(raw_repository)
set_positions_raw_repository(raw_repository)
set_portfolio_analysis_raw_repository(raw_repository)
set_telegram_raw_repository(raw_repository)
set_trades_raw_repository(raw_repository)
set_cash_flows_raw_repository(raw_repository)
set_performance_derived_repository(derived_repository)
set_performance_raw_repository(raw_repository)
set_reconciliation_derived_repository(derived_repository)
settings_service = SettingsService(repository=settings_repository)
set_settings_service(settings_service)
set_positions_settings_service(settings_service)
set_trades_settings_service(settings_service)
set_cash_flows_settings_service(settings_service)
set_performance_settings_service(settings_service)
set_overview_settings_service(settings_service)
set_portfolio_analysis_settings_service(settings_service)
set_telegram_settings_service(settings_service)
set_overview_derived_repository(derived_repository)
daily_performance_service = DailyPerformanceService(
    raw_repository=raw_repository,
    derived_repository=derived_repository,
)
auto_reconciliation_service = AutoReconciliationService(
    raw_repository=raw_repository,
    derived_repository=derived_repository,
)
set_auto_reconciliation_service(auto_reconciliation_service)


def _build_quote_service() -> QuoteService:
    def primary(symbol: str) -> float | None:
        return fetch_longbridge_quote(symbol)

    def secondary(symbol: str) -> float | None:
        api_key = settings_service.get().finnhub_api_key
        return fetch_finnhub_quote(symbol, api_key=api_key) or fetch_yahoo_quote(symbol)

    def snapshot(symbol: str) -> float:
        positions = raw_repository.list_positions(symbol=symbol, page=1, page_size=1)
        if positions:
            return float(positions[0].get("mark_price_snapshot", 0) or 0)
        return 0.0

    return QuoteService(
        primary_fetcher=primary,
        secondary_fetcher=secondary,
        snapshot_fetcher=snapshot,
    )


_quote_service_instance = _build_quote_service()
set_quote_service(_quote_service_instance)
set_settings_quote_service(_quote_service_instance)
set_positions_quote_service(_quote_service_instance)
set_overview_quote_service(_quote_service_instance)
set_portfolio_analysis_quote_service(_quote_service_instance)
set_overview_benchmark_history_fetcher(
    lambda symbol, start_date, end_date: fetch_benchmark_history(
        symbol,
        start_date=start_date,
        end_date=end_date,
        finnhub_api_key=settings_service.get().finnhub_api_key,
    )
)


def run_daily_sync_with_credentials(token: str, query_id: str) -> dict[str, str]:
    flex_client = FlexStatementClient()

    def fetch() -> dict[str, str]:
        statement_xml = flex_client.fetch_statement_xml(token=token, query_id=query_id)
        parsed = parse_xml_string(statement_xml)
        raw_repository.upsert_parsed_data(parsed)
        post_sync_errors: list[str] = []
        for snapshot in parsed.account_snapshots:
            account_id = snapshot.account_id
            report_date = snapshot.report_date
            try:
                daily_performance_service.compute_for_date(
                    account_id=account_id,
                    report_date=report_date,
                )
            except Exception as exc:
                post_sync_errors.append(
                    f"performance:{account_id}:{report_date}:{exc}"
                )
            try:
                auto_reconciliation_service.reconcile_date(
                    account_id=account_id,
                    report_date=report_date,
                )
            except Exception as exc:
                post_sync_errors.append(
                    f"reconciliation:{account_id}:{report_date}:{exc}"
                )
        if post_sync_errors:
            raise RuntimeError(
                "daily sync post-processing failed: "
                + "; ".join(post_sync_errors)
            )
        settings_service.mark_sync_success(datetime.now(timezone.utc).isoformat())
        return {
            "status": "synced",
            "statement_size": str(len(statement_xml)),
            "account_snapshots": str(len(parsed.account_snapshots)),
            "positions": str(len(parsed.positions)),
            "trades": str(len(parsed.trades)),
            "cash_transactions": str(len(parsed.cash_transactions)),
        }

    return run_daily_sync_with_retry(fetch)


def run_configured_daily_sync() -> dict[str, str]:
    settings = settings_service.get()
    if not settings.flex_token or not settings.flex_query_id:
        raise ValueError("flex settings are missing")
    return run_daily_sync_with_credentials(settings.flex_token, settings.flex_query_id)


def execute_scheduled_daily_sync() -> None:
    try:
        result = run_configured_daily_sync()
        logger.info(
            json.dumps({"event": "daily_sync_succeeded", "result": result}),
            extra={"trace_id": "scheduler"},
        )
    except Exception as exc:  # pragma: no cover - defensive runtime logging
        logger.error(
            json.dumps({"event": "daily_sync_failed", "error": str(exc)}),
            extra={"trace_id": "scheduler"},
        )


def _build_telegram_command_service() -> TelegramCommandService:
    settings = settings_service.get()
    market_data_provider = build_market_data_provider(settings, _quote_service_instance)
    analysis_service = PortfolioAnalysisService(
        raw_repository=raw_repository,
        settings_service=settings_service,
        market_data_provider=market_data_provider,
        industry_mapping_service=_industry_mapping_service_instance,
    )
    return TelegramCommandService(
        settings_service=settings_service,
        analysis_service=analysis_service,
        raw_repository=raw_repository,
    )


def run_configured_telegram_report() -> dict[str, object]:
    settings = settings_service.get()
    service = _build_telegram_command_service()
    delivery = TelegramDeliveryService(bot_token=settings.telegram_bot_token)
    return service.deliver_daily_report(delivery)


def _build_telegram_delivery_service(bot_token: str) -> TelegramDeliveryService:
    return TelegramDeliveryService(bot_token=bot_token)


def execute_scheduled_telegram_report() -> None:
    try:
        result = run_configured_telegram_report()
        logger.info(
            json.dumps({"event": "telegram_daily_report_done", "result": result}),
            extra={"trace_id": "scheduler"},
        )
    except Exception as exc:  # pragma: no cover - defensive runtime logging
        logger.error(
            json.dumps({"event": "telegram_daily_report_failed", "error": str(exc)}),
            extra={"trace_id": "scheduler"},
        )


def update_configured_telegram_report_schedule() -> None:
    settings = settings_service.get()
    if (
        not settings.telegram_reports_enabled
        or not settings.telegram_bot_token
        or not settings.telegram_allowlisted_chat_ids
    ):
        telegram_report_scheduler.clear()
        return
    telegram_report_scheduler.schedule(time_of_day=settings.telegram_daily_report_time)


def run_startup_auto_backfill(
    *,
    today: date | None = None,
    runner: Callable[[], dict[str, str]] | None = None,
) -> list[str]:
    settings = settings_service.get()
    last_sync_date = settings.last_successful_sync_date
    if not last_sync_date:
        return []
    try:
        parsed_last_sync_date = date.fromisoformat(last_sync_date)
    except ValueError:
        return []
    plan = build_auto_backfill_plan(
        last_successful_sync_date=parsed_last_sync_date,
        today=today or date.today(),
        max_missed_days=7,
    )
    executed_dates: list[str] = []
    run_fn = runner or run_configured_daily_sync
    for sync_day in plan:
        run_fn()
        executed_dates.append(sync_day.isoformat())
    return executed_dates


daily_sync_scheduler = DailySyncScheduler(run_job=execute_scheduled_daily_sync)
telegram_report_scheduler = DailyTimeScheduler(
    run_job=execute_scheduled_telegram_report,
    job_id="telegram_daily_report",
)
telegram_update_poller = TelegramUpdatePollingService(
    settings_service=settings_service,
    command_service_factory=_build_telegram_command_service,
    delivery_service_factory=_build_telegram_delivery_service,
)
manual_backfill_service = ManualBackfillService()
set_daily_sync_runner(run_daily_sync_with_credentials)
set_pull_frequency_update_handler(
    lambda minutes: daily_sync_scheduler.update_interval(interval_minutes=minutes)
)
set_telegram_report_update_handler(update_configured_telegram_report_schedule)

@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    application.state.storage_ready = True
    application.state.storage_error = None
    application.state.storage_failure_type = None
    application.state.storage_last_checked_at = None
    application.state.storage_backend = runtime_settings.es_backend
    application.state.storage_auth_mode = detect_es_auth_mode()
    application.state.storage_index_status = {index: False for index in required_indexes}
    try:
        initialize_storage(shared_es, application.state.storage_index_status)
        application.state.storage_last_checked_at = datetime.now(timezone.utc).isoformat()
        logger.info(
            json.dumps(
                {
                    "event": "storage_init_succeeded",
                    "backend": application.state.storage_backend,
                    "auth_mode": application.state.storage_auth_mode,
                    "indexes_total": len(required_indexes),
                    "indexes_initialized": len(required_indexes),
                }
            ),
            extra={"trace_id": "startup"},
        )
    except Exception as exc:
        application.state.storage_ready = False
        application.state.storage_error = str(exc)
        application.state.storage_failure_type = StorageFailureType.STORAGE_CONNECT_ERROR
        application.state.storage_last_checked_at = datetime.now(timezone.utc).isoformat()
        initialized = sum(
            1 for ok in application.state.storage_index_status.values() if ok
        )
        logger.error(
            json.dumps(
                {
                    "event": "storage_connect_error",
                    "backend": application.state.storage_backend,
                    "auth_mode": application.state.storage_auth_mode,
                    "error": str(exc),
                    "indexes_total": len(required_indexes),
                    "indexes_initialized": initialized,
                }
            ),
            extra={"trace_id": "startup"},
        )
    pull_frequency = settings_service.get().pull_frequency_minutes
    try:
        daily_sync_scheduler.start(interval_minutes=pull_frequency)
        logger.info(
            json.dumps(
                {"event": "daily_sync_scheduler_started", "interval_minutes": pull_frequency}
            ),
            extra={"trace_id": "startup"},
        )
    except Exception as exc:  # pragma: no cover - defensive runtime logging
        logger.error(
            json.dumps({"event": "daily_sync_scheduler_failed", "error": str(exc)}),
            extra={"trace_id": "startup"},
        )
    try:
        update_configured_telegram_report_schedule()
        logger.info(
            json.dumps({"event": "telegram_report_scheduler_checked"}),
            extra={"trace_id": "startup"},
        )
    except Exception as exc:  # pragma: no cover - defensive runtime logging
        logger.error(
            json.dumps({"event": "telegram_report_scheduler_failed", "error": str(exc)}),
            extra={"trace_id": "startup"},
        )
    try:
        telegram_update_poller.start()
        logger.info(
            json.dumps({"event": "telegram_update_poller_started"}),
            extra={"trace_id": "startup"},
        )
    except Exception as exc:  # pragma: no cover - defensive runtime logging
        logger.error(
            json.dumps({"event": "telegram_update_poller_failed", "error": str(exc)}),
            extra={"trace_id": "startup"},
        )
    try:
        backfilled_days = run_startup_auto_backfill()
        if backfilled_days:
            logger.info(
                json.dumps(
                    {"event": "daily_sync_auto_backfill_done", "days": backfilled_days}
                ),
                extra={"trace_id": "startup"},
            )
    except Exception as exc:  # pragma: no cover - defensive runtime logging
        logger.error(
            json.dumps({"event": "daily_sync_auto_backfill_failed", "error": str(exc)}),
            extra={"trace_id": "startup"},
        )
    yield
    telegram_update_poller.stop()
    telegram_report_scheduler.shutdown()
    daily_sync_scheduler.shutdown()


app = FastAPI(lifespan=lifespan)
register_trace_middleware(app)
register_error_handlers(app)
app.state.storage_ready = True
app.state.storage_error = None
app.state.storage_failure_type = None
app.state.storage_last_checked_at = None
app.state.storage_backend = runtime_settings.es_backend
app.state.storage_auth_mode = detect_es_auth_mode()
app.state.storage_index_status = {index: False for index in required_indexes}


def build_storage_unavailable_content(
    trace_id: str,
) -> dict[str, str | StorageFailureType | None]:
    error_detail = getattr(app.state, "storage_error", None)
    failure_type = getattr(
        app.state,
        "storage_failure_type",
        StorageFailureType.STORAGE_CONNECT_ERROR,
    )
    message = "storage backend is unavailable"
    if error_detail:
        message = f"{message}: {error_detail}"
    return {
        "code": "STORAGE_UNAVAILABLE",
        "message": message,
        "failureType": failure_type,
        "lastCheckedAt": getattr(app.state, "storage_last_checked_at", None),
        "traceId": trace_id,
    }


@app.middleware("http")
async def enforce_storage_ready(request, call_next):
    if request.url.path.startswith("/api/") and not getattr(app.state, "storage_ready", True):
        return JSONResponse(
            status_code=503,
            content=build_storage_unavailable_content(
                getattr(request.state, "trace_id", generate_trace_id())
            ),
        )
    return await call_next(request)


app.include_router(import_tasks_router)
app.include_router(overview_router)
app.include_router(positions_router)
app.include_router(portfolio_analysis_router)
app.include_router(performance_router)
app.include_router(trades_router)
app.include_router(cash_flows_router)
app.include_router(settings_router)
app.include_router(telegram_router)
app.include_router(reconciliation_router)
app.include_router(quotes_router)
app.include_router(industry_mapping_router)
_industry_mapping_service_instance = IndustryMappingService(repository=industry_mapping_repository)
set_mapping_service(_industry_mapping_service_instance)
set_positions_industry_mapping_service(_industry_mapping_service_instance)
set_overview_industry_mapping_service(_industry_mapping_service_instance)
set_portfolio_analysis_industry_mapping_service(_industry_mapping_service_instance)


class ManualBackfillRequest(BaseModel):
    start_date: date
    end_date: date


class StorageIndexStatus(BaseModel):
    total: int
    initialized: int
    pending: list[str]


class StorageHealthResponse(BaseModel):
    status: str
    backend: str
    auth_mode: StorageAuthMode
    ready: bool
    failure_type: StorageFailureType | None
    last_checked_at: str | None
    indexes: StorageIndexStatus


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(
    "/health/storage",
    response_model=StorageHealthResponse,
    responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE,
)
def storage_health(request: Request):
    if not getattr(app.state, "storage_ready", True):
        return JSONResponse(
            status_code=503,
            content=build_storage_unavailable_content(
                getattr(request.state, "trace_id", generate_trace_id())
            ),
        )
    index_status = getattr(app.state, "storage_index_status", {})
    initialized = [name for name, ok in index_status.items() if ok]
    pending = [name for name, ok in index_status.items() if not ok]
    return StorageHealthResponse(
        status="ok",
        backend=getattr(app.state, "storage_backend", runtime_settings.es_backend),
        auth_mode=getattr(app.state, "storage_auth_mode", detect_es_auth_mode()),
        ready=True,
        failure_type=getattr(app.state, "storage_failure_type", None),
        last_checked_at=getattr(app.state, "storage_last_checked_at", None),
        indexes=StorageIndexStatus(
            total=len(index_status),
            initialized=len(initialized),
            pending=pending,
        ),
    )


@app.post(
    "/api/settings/manual-backfill",
    status_code=202,
    responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE,
)
def manual_backfill(payload: ManualBackfillRequest) -> dict[str, str]:
    try:
        validate_backfill_range(payload.start_date, payload.end_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    task_id = manual_backfill_service.enqueue(
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    return {
        "status": "accepted",
        "task_id": task_id,
        "task_url": f"/api/settings/manual-backfill/tasks/{task_id}",
        "run_url": f"/api/settings/manual-backfill/tasks/{task_id}/run",
    }


@app.post(
    "/api/settings/manual-backfill/tasks",
    status_code=202,
    responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE,
)
def create_manual_backfill_task(payload: ManualBackfillRequest) -> dict[str, str]:
    try:
        validate_backfill_range(payload.start_date, payload.end_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    task_id = manual_backfill_service.enqueue(
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    return {
        "task_id": task_id,
        "task_url": f"/api/settings/manual-backfill/tasks/{task_id}",
        "run_url": f"/api/settings/manual-backfill/tasks/{task_id}/run",
    }


@app.get(
    "/api/settings/manual-backfill/tasks",
    responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE,
)
def list_manual_backfill_tasks(
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    normalized_page = max(page, 1)
    normalized_page_size = max(min(page_size, 100), 1)
    tasks = manual_backfill_service.list_tasks(
        page=normalized_page,
        page_size=normalized_page_size,
    )
    return {
        "items": [asdict(task) for task in tasks],
        "page": normalized_page,
        "page_size": normalized_page_size,
        "total": len(manual_backfill_service.tasks),
    }


@app.get(
    "/api/settings/manual-backfill/tasks/{task_id}",
    responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE,
)
def get_manual_backfill_task(task_id: str) -> dict:
    task = manual_backfill_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="manual backfill task not found")
    payload = asdict(task)
    payload["task_url"] = f"/api/settings/manual-backfill/tasks/{task_id}"
    payload["run_url"] = f"/api/settings/manual-backfill/tasks/{task_id}/run"
    return payload


@app.post(
    "/api/settings/manual-backfill/tasks/{task_id}/run",
    responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE,
)
def run_manual_backfill_task(task_id: str) -> dict:
    task = manual_backfill_service.run_task(task_id, run_configured_daily_sync)
    if task is None:
        raise HTTPException(status_code=404, detail="manual backfill task not found")
    payload = asdict(task)
    payload["task_url"] = f"/api/settings/manual-backfill/tasks/{task_id}"
    payload["run_url"] = f"/api/settings/manual-backfill/tasks/{task_id}/run"
    return payload
