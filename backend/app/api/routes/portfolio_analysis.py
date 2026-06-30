import logging
from datetime import datetime, timezone
from threading import Lock, Thread, Timer
from time import monotonic
from typing import Any, Callable
from uuid import uuid4

from fastapi import APIRouter
from fastapi import HTTPException

from app.api.portfolio_analysis_contracts import PortfolioAnalysisResponse
from app.api.portfolio_analysis_contracts import PortfolioAnalysisSectionKey
from app.api.response_models import STORAGE_UNAVAILABLE_OPENAPI_RESPONSE
from app.repositories.raw_repository import RawRepository
from app.services.ai_narrative_service import AINarrativeService
from app.services.industry_mapping_service import IndustryMappingService
from app.services.market_data_provider import build_market_data_provider
from app.services.portfolio_analysis_service import PortfolioAnalysisService
from app.services.quote_service import QuoteService
from app.services.settings_service import SettingsService


router = APIRouter()
logger = logging.getLogger(__name__)
_settings_service: SettingsService = SettingsService()
_raw_repository: RawRepository | object | None = None
_quote_service: QuoteService | None = None
_industry_mapping_service: IndustryMappingService | None = None
_ai_narrative_service = AINarrativeService()
_refresh_jobs: dict[str, dict[str, Any]] = {}
_active_refresh_jobs: dict[tuple[str, str], str] = {}
_refresh_job_lock = Lock()
_TERMINAL_JOB_STATUSES = {"ready", "fallback", "error"}
_MAX_REFRESH_JOBS = 100
_REFRESH_JOB_DEADLINE_SECONDS = 225.0


def set_settings_service(service: SettingsService) -> None:
    global _settings_service
    _settings_service = service


def set_raw_repository(repository: RawRepository | object | None) -> None:
    global _raw_repository
    _raw_repository = repository


def set_quote_service(service: QuoteService | None) -> None:
    global _quote_service
    _quote_service = service


def set_industry_mapping_service(service: IndustryMappingService | None) -> None:
    global _industry_mapping_service
    _industry_mapping_service = service


@router.get(
    "/api/portfolio-analysis",
    response_model=PortfolioAnalysisResponse,
    responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE,
)
def get_portfolio_analysis(
    section: PortfolioAnalysisSectionKey | None = None,
    symbol: str | None = None,
    refresh_ai: bool = False,
) -> PortfolioAnalysisResponse:
    service = _build_service()
    return service.get_analysis(section=section, symbol=symbol, refresh_ai=refresh_ai)


@router.post("/api/portfolio-analysis/narrative/refresh", status_code=202)
def refresh_portfolio_analysis_narrative(
    section: PortfolioAnalysisSectionKey,
    symbol: str | None = None,
) -> dict[str, object]:
    normalized_symbol = symbol.upper() if symbol else None
    resolved_symbol = (
        None
        if section == PortfolioAnalysisSectionKey.PORTFOLIO
        else _build_service().mark_narrative_refresh_started(section=section, symbol=normalized_symbol)
    )
    key = (section.value, resolved_symbol or "")
    with _refresh_job_lock:
        active_job_id = _active_refresh_jobs.get(key)
        active_job = _refresh_jobs.get(active_job_id) if active_job_id else None
        if active_job is not None and not active_job.get("_worker_finished", False):
            return _refresh_job_snapshot(active_job)
        _prune_refresh_jobs_locked()
        if len(_refresh_jobs) >= _MAX_REFRESH_JOBS:
            raise HTTPException(status_code=429, detail="portfolio_analysis_refresh_job_capacity_reached")
        job_id = uuid4().hex
        now = _now_iso()
        _refresh_jobs[job_id] = {
            "job_id": job_id,
            "status": "accepted",
            "stage": "accepted",
            "section": section.value,
            "symbol": resolved_symbol,
            "message": "分析任务已受理",
            "started_at": now,
            "updated_at": now,
            "completed_positions": 0,
            "total_positions": 0,
            "stage_durations_ms": {},
            "reason": None,
            "failed_stage": None,
            "_stage_started_at": monotonic(),
            "_worker_finished": False,
        }
        deadline_timer = Timer(_REFRESH_JOB_DEADLINE_SECONDS, _expire_refresh_job, args=(job_id,))
        deadline_timer.daemon = True
        _refresh_jobs[job_id]["_deadline_timer"] = deadline_timer
        _active_refresh_jobs[key] = job_id
        response = _refresh_job_snapshot(_refresh_jobs[job_id])
    deadline_timer.start()
    Thread(target=_refresh_narrative_task, args=(job_id, section, resolved_symbol), daemon=True).start()
    return response


@router.get("/api/portfolio-analysis/narrative/refresh/{job_id}")
def get_portfolio_analysis_refresh_job(job_id: str) -> dict[str, object]:
    with _refresh_job_lock:
        job = _refresh_jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="portfolio_analysis_refresh_job_not_found")
        return _refresh_job_snapshot(job)


def _build_service(
    progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
) -> PortfolioAnalysisService:
    settings = _settings_service.get()
    provider = build_market_data_provider(settings, _quote_service)
    return PortfolioAnalysisService(
        raw_repository=_raw_repository,
        settings_service=_settings_service,
        market_data_provider=provider,
        industry_mapping_service=_industry_mapping_service,
        ai_narrative_service=_ai_narrative_service,
        progress_callback=progress_callback,
    )


def _refresh_narrative_task(job_id: str, section: PortfolioAnalysisSectionKey, symbol: str | None) -> None:
    def progress(stage: str, details: dict[str, Any] | None = None) -> None:
        _update_refresh_job(job_id, stage=stage, status="running", **(details or {}))

    try:
        progress("preparing_inputs", {"message": "正在读取并整理最新持仓"})
        result = _build_service(progress).get_analysis(section=section, symbol=symbol, refresh_ai=True)
        if section == PortfolioAnalysisSectionKey.PORTFOLIO:
            portfolio = result.sections.portfolio
            meta = portfolio.analysis_meta or {}
            provider = str(meta.get("ai_overlay_provider") or "")
            overlay_status = str(meta.get("ai_overlay_status") or "")
            fallback = provider in {"local_rules", "mock"} or str(meta.get("ai_overlay_reason") or "").startswith("fallback_after_")
            status = "fallback" if fallback or overlay_status != "ready" else "ready"
            _update_refresh_job(
                job_id,
                stage=status,
                status=status,
                message="联网研究不可用，已保留本地规则结果" if status == "fallback" else "持仓风险研究已完成",
                completed_positions=int(meta.get("researched_position_count") or (len(portfolio.risk_rows) if status == "ready" else 0)),
                total_positions=len(portfolio.risk_rows),
                reason=meta.get("ai_overlay_reason"),
            )
        else:
            _update_refresh_job(job_id, stage="ready", status="ready", message="AI 分析已完成")
    except Exception as exc:  # pragma: no cover - background diagnostics only
        _update_refresh_job(
            job_id,
            stage="error",
            status="error",
            message="分析任务失败",
            reason=str(exc),
        )
        logger.warning("portfolio_analysis_narrative_refresh_failed: %s", exc)
    finally:
        _finish_refresh_job(job_id)


def _update_refresh_job(
    job_id: str,
    *,
    stage: str,
    status: str,
    message: str | None = None,
    completed_positions: int | None = None,
    total_positions: int | None = None,
    reason: object | None = None,
) -> None:
    with _refresh_job_lock:
        job = _refresh_jobs.get(job_id)
        if job is None:
            return
        if job.get("status") in _TERMINAL_JOB_STATUSES:
            return
        now = monotonic()
        previous_stage = str(job.get("stage") or "accepted")
        if previous_stage != stage:
            durations = job["stage_durations_ms"]
            durations[previous_stage] = int((now - float(job["_stage_started_at"])) * 1000)
            job["_stage_started_at"] = now
        job.update({"stage": stage, "status": status, "updated_at": _now_iso()})
        if message is not None:
            job["message"] = message
        if completed_positions is not None:
            job["completed_positions"] = completed_positions
        if total_positions is not None:
            job["total_positions"] = total_positions
        if reason is not None:
            job["reason"] = str(reason)
        if status in _TERMINAL_JOB_STATUSES:
            job["stage_durations_ms"][stage] = int((now - float(job["_stage_started_at"])) * 1000)
            job["failed_stage"] = previous_stage if status in {"fallback", "error"} else None


def _expire_refresh_job(job_id: str) -> None:
    _update_refresh_job(
        job_id,
        stage="error",
        status="error",
        message="分析任务超过服务端总时限",
        reason="refresh_job_deadline_exceeded",
    )


def _finish_refresh_job(job_id: str) -> None:
    timer: Timer | None = None
    with _refresh_job_lock:
        job = _refresh_jobs.get(job_id)
        if job is None:
            return
        job["_worker_finished"] = True
        key = (str(job["section"]), str(job.get("symbol") or ""))
        if _active_refresh_jobs.get(key) == job_id:
            _active_refresh_jobs.pop(key, None)
        stored_timer = job.get("_deadline_timer")
        if isinstance(stored_timer, Timer):
            timer = stored_timer
    if timer is not None:
        timer.cancel()


def _refresh_job_snapshot(job: dict[str, Any]) -> dict[str, object]:
    snapshot = {key: value for key, value in job.items() if not key.startswith("_")}
    snapshot["stage_durations_ms"] = dict(job.get("stage_durations_ms") or {})
    return snapshot


def _prune_refresh_jobs_locked() -> None:
    if len(_refresh_jobs) < _MAX_REFRESH_JOBS:
        return
    terminal = [
        job
        for job in _refresh_jobs.values()
        if job.get("status") in _TERMINAL_JOB_STATUSES and job.get("_worker_finished", False)
    ]
    terminal.sort(key=lambda job: str(job.get("updated_at") or ""))
    for job in terminal[: max(1, len(_refresh_jobs) - _MAX_REFRESH_JOBS + 1)]:
        _refresh_jobs.pop(str(job["job_id"]), None)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
