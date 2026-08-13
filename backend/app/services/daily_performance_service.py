from typing import Any

from app.repositories.derived_repository import DerivedRepository
from app.repositories.raw_repository import RawRepository
from app.services.analytics_service import simple_return


class DailyPerformanceService:
    def __init__(
        self,
        *,
        raw_repository: RawRepository,
        derived_repository: DerivedRepository,
    ) -> None:
        self._raw = raw_repository
        self._derived = derived_repository

    def compute_for_date(
        self,
        *,
        account_id: str,
        report_date: str,
    ) -> dict[str, Any] | None:
        current = self._get_snapshot(account_id, report_date)
        if current is None:
            return None

        previous = self._find_previous_snapshot(account_id, report_date)
        if previous is None:
            return None

        v_begin = float(previous.get("total_equity", 0) or 0)
        v_end = float(current.get("total_equity", 0) or 0)
        net_cash_inflow = float(current.get("net_cash_inflow_daily", 0) or 0)
        daily_return = simple_return(v_begin, v_end, net_cash_inflow)

        doc_id = f"{account_id}_{report_date}_daily"
        result = {
            "account_id": account_id,
            "date": report_date,
            "range": "daily",
            "simple_return": daily_return,
            "v_begin": v_begin,
            "v_end": v_end,
            "net_cash_inflow": net_cash_inflow,
        }
        self._derived.upsert_portfolio_return(doc_id=doc_id, doc=result)
        return result

    def _get_snapshot(self, account_id: str, report_date: str) -> dict | None:
        doc_id = f"{account_id}_{report_date}"
        try:
            return self._raw.get_account_snapshot(doc_id)
        except Exception:
            return None

    def _find_previous_snapshot(self, account_id: str, report_date: str) -> dict | None:
        snapshots = self._raw.es.search(
            index="ibkr_account_snapshots_v1",
            size=1,
            sort_field="report_date",
            descending=True,
            term_filters={"account_id": account_id},
        )
        filtered = [
            s for s in snapshots
            if s.get("report_date", "") < report_date
        ]
        if not filtered:
            all_snapshots = self._raw.es.search(
                index="ibkr_account_snapshots_v1",
                size=100,
                sort_field="report_date",
                descending=True,
            )
            filtered = [
                s for s in all_snapshots
                if s.get("account_id") == account_id
                and s.get("report_date", "") < report_date
            ]
        if not filtered:
            return None
        filtered.sort(key=lambda s: s.get("report_date", ""), reverse=True)
        return filtered[0]
