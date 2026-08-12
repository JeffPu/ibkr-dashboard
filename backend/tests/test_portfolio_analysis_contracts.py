from fastapi.testclient import TestClient

from app.api.portfolio_analysis_contracts import AnalysisStatus
from app.api.portfolio_analysis_contracts import build_empty_portfolio_analysis_response
from app.api.routes import portfolio_analysis as portfolio_analysis_route
from app.main import app
from app.services.portfolio_analysis_service import PortfolioAnalysisService
from app.services.settings_service import SettingsService


METRIC_KEYS = {"value", "unit", "source", "as_of", "confidence", "status"}


def test_empty_portfolio_analysis_contract_standardizes_metrics() -> None:
    response = build_empty_portfolio_analysis_response(
        display_currency="USD",
        valuation_mode="snapshot",
    )
    body = response.model_dump(mode="json")

    metrics = [
        body["sections"]["market"]["regime"],
        *body["sections"]["market"]["indicators"].values(),
        *body["sections"]["portfolio"]["concentration"].values(),
        *body["sections"]["portfolio"]["greeks"].values(),
        *body["sections"]["portfolio"]["expiration_risk"].values(),
        *body["sections"]["portfolio"]["advisor_facts"],
        *body["sections"]["stock"]["profile"].values(),
        *body["sections"]["stock"]["indicators"].values(),
    ]

    assert metrics
    for metric in metrics:
        assert METRIC_KEYS.issubset(metric)
        assert metric["value"] is None
        assert metric["as_of"] is None
        assert metric["confidence"] == 0.0
        assert metric["status"] == "missing_data"
        assert metric["source"]
        assert metric["reason"]


def test_market_analysis_empty_route_returns_market_only() -> None:
    service = SettingsService()
    portfolio_analysis_route.set_settings_service(service)
    portfolio_analysis_route.set_raw_repository(None)

    with TestClient(app) as client:
        response = client.get("/api/portfolio-analysis")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "missing_data"
    assert body["generated_at"] is None
    assert body["display_currency"] == "USD"
    assert body["valuation_mode"] == "snapshot"
    assert set(body) == {"status", "generated_at", "display_currency", "valuation_mode", "market", "links"}
    assert body["market"]["regime"]["status"] == "missing_data"
    assert "narrative" not in body["market"]


def test_market_analysis_route_does_not_expose_removed_sections_or_ai() -> None:
    service = SettingsService()
    service.update(
        base_currency="HKD",
        display_realtime_prices=True,
        telegram_bot_token="telegram-token",
        telegram_allowlisted_chat_ids=["123456789", "-100987654321"],
        telegram_reports_enabled=True,
        telegram_daily_report_time="09:15",
    )
    portfolio_analysis_route.set_settings_service(service)
    portfolio_analysis_route.set_raw_repository(None)

    with TestClient(app) as client:
        response = client.get("/api/portfolio-analysis")

    assert response.status_code == 200
    body = response.json()
    assert body["display_currency"] == "HKD"
    assert body["valuation_mode"] == "realtime"
    assert "sections" not in body
    assert "integrations" not in body
    assert "narrative" not in body["market"]


def test_portfolio_analysis_openapi_declares_response_and_storage_error() -> None:
    with TestClient(app) as client:
        spec = client.get("/openapi.json").json()

    endpoint = spec["paths"]["/api/portfolio-analysis"]["get"]
    assert endpoint["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/MarketAnalysisResponse"
    )
    assert endpoint["responses"]["503"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/StorageUnavailableResponse"
    )
    assert "parameters" not in endpoint
    assert "/api/portfolio-analysis/narrative/refresh" not in spec["paths"]
    assert "/api/portfolio-analysis/narrative/refresh/{job_id}" not in spec["paths"]


def test_portfolio_section_returns_risk_rows_and_only_weight_change_chart() -> None:
    service = PortfolioAnalysisService(
        raw_repository=_FakeRawRepository(),
        settings_service=SettingsService(),
    )

    response = service.get_analysis(section=portfolio_analysis_route.PortfolioAnalysisSectionKey.PORTFOLIO)
    portfolio = response.sections.portfolio

    assert portfolio.status == AnalysisStatus.READY
    assert [row.symbol for row in portfolio.risk_rows] == ["NVDA", "INTC", "RKLB"]
    assert portfolio.risk_rows[0].weight_pct == 50.0
    assert portfolio.risk_rows[0].current_price == 225.32
    assert portfolio.risk_rows[0].unrealized_pnl == 1200.0
    assert portfolio.risk_rows[0].position_key == "STK:NVDA"
    assert len(portfolio.risk_rows[0].risk_points) == 3
    assert len(portfolio.risk_rows[0].tracking_points) == 3
    assert portfolio.risk_rows[0].research_status == "missing"
    assert "ai_relevance" not in portfolio.risk_rows[0].model_dump()
    assert "position_role" not in portfolio.risk_rows[0].model_dump()
    assert "portfolio_positions" in portfolio.risk_rows[0].source
    assert portfolio.risk_rows[1].recommendation
    assert portfolio.rebalance_advice.status == AnalysisStatus.READY
    assert len(portfolio.rebalance_advice.cards) == 4
    assert portfolio.analysis_meta["risk_row_count"] == 3
    assert len(portfolio.charts) == 1
    assert portfolio.charts[0].title == "持仓权重 vs 当日涨跌"


class _FakeES:
    def __init__(self) -> None:
        self.rows = [
            {
                "account_id": "DU123",
                "report_date": "2026-05-18",
                "level_of_detail": "SUMMARY",
                "symbol": "NVDA",
                "market_value_snapshot": 5000,
                "mark_price_snapshot": 225.32,
                "unrealized_pnl_snapshot": 1200,
                "daily_change_pct": 0.012,
                "quantity": 22,
                "industry": "Semiconductor",
            },
            {
                "account_id": "DU123",
                "report_date": "2026-05-18",
                "level_of_detail": "SUMMARY",
                "symbol": "INTC",
                "market_value_snapshot": 3000,
                "mark_price_snapshot": 108.77,
                "unrealized_pnl_snapshot": -250,
                "daily_change_pct": -0.061,
                "quantity": 28,
                "industry": "Semiconductor",
            },
            {
                "account_id": "DU123",
                "report_date": "2026-05-18",
                "level_of_detail": "SUMMARY",
                "symbol": "RKLB",
                "market_value_snapshot": 2000,
                "mark_price_snapshot": 124.77,
                "unrealized_pnl_snapshot": 300,
                "daily_change_pct": 0.005,
                "quantity": 16,
                "industry": "Aerospace",
            },
        ]

    def search(self, *, index: str, size: int, term_filters: dict | None = None, **_: object) -> list[dict]:
        if index != "ibkr_position_snapshots_v1":
            return []
        rows = self.rows
        if term_filters:
            rows = [
                row for row in rows
                if all(str(row.get(key)) == str(value) for key, value in term_filters.items())
            ]
        return [dict(row) for row in rows[:size]]


class _FakeRawRepository:
    def __init__(self) -> None:
        self.es = _FakeES()

    def get_latest_account_snapshot(self) -> dict:
        return {"account_id": "DU123", "report_date": "2026-05-18"}
