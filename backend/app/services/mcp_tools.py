from typing import Any

from app.api.portfolio_analysis_contracts import PortfolioAnalysisSectionKey
from app.services.market_data_provider import build_market_data_provider
from app.services.portfolio_analysis_service import PortfolioAnalysisService
from app.services.settings_service import SettingsService
from app.utils.numbers import to_float as _to_float


READ_ONLY_TOOLS = [
    "get_account_overview",
    "list_positions",
    "get_position_detail",
    "get_portfolio_risk",
    "get_market_regime",
    "get_stock_analysis",
    "get_performance_summary",
    "list_cash_flows",
    "get_wheel_snapshot",
]


class ReadOnlyMCPTools:
    def __init__(
        self,
        *,
        raw_repository: object | None,
        derived_repository: object | None,
        settings_service: SettingsService,
        quote_service: object | None = None,
    ) -> None:
        self._raw = raw_repository
        self._derived = derived_repository
        self._settings = settings_service
        market_data_provider = build_market_data_provider(settings_service.get(), quote_service)
        self._analysis = PortfolioAnalysisService(
            raw_repository=raw_repository,
            settings_service=settings_service,
            market_data_provider=market_data_provider,
        )

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "description": f"Read-only IBKR dashboard tool: {name}",
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "symbol": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                },
            }
            for name in READ_ONLY_TOOLS
        ]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        if name not in READ_ONLY_TOOLS:
            return {"status": "error", "message": "unknown_read_only_tool"}
        if name == "get_account_overview":
            return self._account_overview()
        if name == "list_positions":
            return self._list_positions(limit=int(args.get("limit") or 20))
        if name == "get_position_detail":
            symbol = str(args.get("symbol") or "").upper()
            rows = self._current_position_rows(symbol=symbol, limit=20)
            return {"status": "ready" if rows else "missing_data", "symbol": symbol, "items": rows}
        if name == "get_portfolio_risk":
            analysis = self._analysis.get_analysis(section=PortfolioAnalysisSectionKey.PORTFOLIO, allow_ai=False)
            return analysis.sections.portfolio.model_dump(mode="json", exclude={"narrative"})
        if name == "get_market_regime":
            analysis = self._analysis.get_analysis(section=PortfolioAnalysisSectionKey.MARKET, allow_ai=False)
            return analysis.sections.market.model_dump(mode="json", exclude={"narrative"})
        if name == "get_stock_analysis":
            analysis = self._analysis.get_analysis(
                section=PortfolioAnalysisSectionKey.STOCK,
                symbol=str(args.get("symbol") or "") or None,
                allow_ai=False,
            )
            return analysis.sections.stock.model_dump(mode="json", exclude={"narrative"})
        if name == "get_performance_summary":
            return self._list_index("portfolio_returns_v1", limit=int(args.get("limit") or 20))
        if name == "list_cash_flows":
            return self._list_index("ibkr_stmt_funds_lines_v1", limit=int(args.get("limit") or 20))
        if name == "get_wheel_snapshot":
            rows = [
                row
                for row in self._current_position_rows(limit=100)
                if str(row.get("asset_category", "")).upper() == "OPT" or row.get("put_call")
            ]
            return {"status": "ready" if rows else "missing_data", "items": rows[:20]}
        return {"status": "error", "message": "unhandled_tool"}

    def _account_overview(self) -> dict[str, Any]:
        latest = self._raw.get_latest_account_snapshot() if self._raw is not None else None
        if not latest:
            return {"status": "missing_data", "snapshot": None}
        return {
            "status": "ready",
            "snapshot": {
                "report_date": latest.get("report_date"),
                "base_currency": latest.get("base_currency"),
                "total_equity": latest.get("total_equity"),
                "cash": latest.get("cash"),
                "stock_market_value": latest.get("stock_market_value"),
            },
        }

    def _list_positions(self, *, limit: int) -> dict[str, Any]:
        latest = self._raw.get_latest_account_snapshot() if self._raw is not None else None
        rows = self._current_position_rows(limit=limit)
        return {
            "status": "ready" if rows else "missing_data",
            "account_id": latest.get("account_id") if latest else None,
            "account_report_date": latest.get("report_date") if latest else None,
            "positions_report_date": rows[0].get("report_date") if rows else None,
            "items": rows,
        }

    def _list_index(self, index: str, *, limit: int) -> dict[str, Any]:
        rows = self._search(index, limit=limit)
        return {"status": "ready" if rows else "missing_data", "items": rows}

    def _search(self, index: str, *, symbol: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if self._raw is None:
            return []
        filters = {"symbol": symbol} if symbol else None
        return self._raw.es.search(index=index, size=max(1, min(limit, 100)), term_filters=filters)

    def _current_position_rows(self, *, symbol: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if self._raw is None:
            return []
        latest = self._raw.get_latest_account_snapshot()
        account_id = str((latest or {}).get("account_id", "") or "")
        report_date = str((latest or {}).get("report_date", "") or "")
        filters = {"account_id": account_id, "report_date": report_date} if account_id and report_date else None
        rows = self._raw.es.search(index="ibkr_position_snapshots_v1", size=10000, term_filters=filters)
        if not rows:
            fallback_filters = {"account_id": account_id} if account_id else None
            candidates = self._raw.es.search(
                index="ibkr_position_snapshots_v1",
                size=10000,
                sort_field="report_date",
                descending=True,
                term_filters=fallback_filters,
            )
            rows = self._nearest_position_snapshot(candidates, report_date=report_date)
        if symbol:
            rows = [row for row in rows if str(row.get("symbol", "") or "").upper() == symbol]
        summary_rows = [row for row in rows if str(row.get("level_of_detail", "") or "").upper() == "SUMMARY"]
        rows = summary_rows or rows
        rows.sort(key=lambda row: abs(_to_float(row.get("market_value_snapshot", row.get("position_value", 0)))), reverse=True)
        return rows[: max(1, min(limit, 100))]

    def _nearest_position_snapshot(self, rows: list[dict[str, Any]], *, report_date: str) -> list[dict[str, Any]]:
        if not rows:
            return []
        dates = {
            str(row.get("report_date", "") or "")
            for row in rows
            if row.get("report_date") and (not report_date or str(row.get("report_date", "") or "") <= report_date)
        }
        if not dates:
            dates = {str(row.get("report_date", "") or "") for row in rows if row.get("report_date")}
        if not dates:
            return rows
        latest_date = max(dates)
        return [row for row in rows if str(row.get("report_date", "") or "") == latest_date]
