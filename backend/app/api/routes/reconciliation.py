from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.response_models import STORAGE_UNAVAILABLE_OPENAPI_RESPONSE
from app.repositories.derived_repository import DerivedRepository
from app.services.auto_reconciliation_service import AutoReconciliationService

router = APIRouter()
_derived_repository: DerivedRepository | object | None = None
_auto_reconciliation_service: AutoReconciliationService | object | None = None
AUTO_RECONCILIATION_FIELDS = (
    "account_id",
    "report_date",
    "status",
    "snapshot_equity",
    "snapshot_cash",
    "positions_total_market_value",
    "expected_equity",
    "diff",
)
AUTO_RECONCILIATION_REQUIRED_FIELDS = ("account_id", "report_date", "status", "diff")


class AutoReconciliationRequest(BaseModel):
    account_id: str
    report_date: str


def set_derived_repository(repository: object | None) -> None:
    global _derived_repository
    _derived_repository = repository


def set_auto_reconciliation_service(service: object | None) -> None:
    global _auto_reconciliation_service
    _auto_reconciliation_service = service


@router.post(
    "/api/reconciliation/auto",
    responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE,
)
def run_auto_reconciliation(payload: AutoReconciliationRequest) -> dict:
    if _auto_reconciliation_service is None:
        raise HTTPException(status_code=503, detail="auto reconciliation unavailable")
    result = _auto_reconciliation_service.reconcile_date(
        account_id=payload.account_id,
        report_date=payload.report_date,
    )
    if result.get("status") == "skipped":
        raise HTTPException(status_code=404, detail="reconciliation snapshot not found")
    return {
        **result,
        "request": {
            "account_id": payload.account_id,
            "report_date": payload.report_date,
        },
    }


@router.get(
    "/api/reconciliation/latest",
    responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE,
)
def get_latest_reconciliation() -> dict:
    if _derived_repository is None:
        raise HTTPException(status_code=503, detail="reconciliation unavailable")
    saved = _derived_repository.get_latest_reconciliation_result()
    if saved is None or not all(field in saved for field in AUTO_RECONCILIATION_REQUIRED_FIELDS):
        raise HTTPException(status_code=404, detail="reconciliation result not found")
    return {
        **{field: saved[field] for field in AUTO_RECONCILIATION_FIELDS if field in saved},
        "meta": {"source": "derived"},
    }
