from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.api.routes import overview as overview_route
from app.api.routes import positions as positions_route
from app.api.routes import telegram as telegram_route
from app.api.routes import trades as trades_route
from app.main import app, raw_repository
from app.services.settings_service import SettingsService


def _compact(day: datetime) -> str:
    return day.strftime("%Y%m%d")


def _option_xml(*, option_count: int = 6) -> str:
    today = datetime.now(ZoneInfo("Asia/Shanghai"))
    report_date = _compact(today)
    offsets = [-1, 1, 7, 30, 45]
    options = []
    trades = []
    for index in range(option_count):
        offset = offsets[index] if index < len(offsets) else 2 + index
        expiry = _compact(today + timedelta(days=offset))
        right = "P" if index % 2 else "C"
        quantity = -1 if index % 3 == 0 else 1
        raw_symbol = f"AAPL{expiry[2:]}{right}{200 + index}"
        options.append(
            f'<OpenPosition accountId="UOPT" currency="USD" assetCategory="OPT" '
            f'symbol="{raw_symbol}" underlyingSymbol="AAPL" reportDate="{report_date}" '
            f'position="{quantity}" markPrice="{10 + index}" positionValue="{quantity * (1000 + index * 10)}" '
            f'costBasisMoney="{quantity * 900}" costBasisPrice="9" fifoPnlUnrealized="{quantity * 100}" '
            f'expiry="{expiry}" strike="{200 + index}" putCall="{right}" multiplier="100" levelOfDetail="SUMMARY" />'
        )
        trades.append(
            f'<Trade accountId="UOPT" tradeID="opt-{index}" assetCategory="OPT" symbol="{raw_symbol}" '
            f'underlyingSymbol="AAPL" buySell="BUY" quantity="1" tradePrice="{10 + index}" '
            f'tradeDate="{report_date}" currency="USD" expiry="{expiry}" strike="{200 + index}" '
            f'putCall="{right}" multiplier="100" openCloseIndicator="O" transactionType="Trade" notes="opening option" />'
        )
    options.append(
        f'<OpenPosition accountId="UOPT" currency="USD" assetCategory="FOP" symbol="INCOMPLETE-1" '
        f'underlyingSymbol="ES" reportDate="{report_date}" position="1" markPrice="5" positionValue="250" '
        'costBasisMoney="200" fifoPnlUnrealized="50" conid="900001" levelOfDetail="SUMMARY" />'
    )
    return f"""<FlexQueryResponse><FlexStatements><FlexStatement accountId="UOPT">
      <EquitySummaryInBase><EquitySummaryByReportDateInBase accountId="UOPT" currency="USD" reportDate="{report_date}" cash="-200" stock="1000" total="1000" /></EquitySummaryInBase>
      <OpenPositions>
        <OpenPosition accountId="UOPT" currency="USD" assetCategory="STK" symbol="MSFT" reportDate="{report_date}" position="5" markPrice="200" positionValue="1000" costBasisMoney="900" fifoPnlUnrealized="100" levelOfDetail="SUMMARY" />
        {''.join(options)}
      </OpenPositions>
      <Trades>
        <Trade accountId="UOPT" tradeID="stock-1" assetCategory="STK" symbol="MSFT" buySell="SELL" quantity="1" tradePrice="210" tradeDate="{report_date}" currency="USD" />
        {''.join(trades)}
      </Trades>
    </FlexStatement></FlexStatements></FlexQueryResponse>"""


def _configure_routes() -> SettingsService:
    settings = SettingsService()
    settings.update(timezone="Asia/Shanghai", display_realtime_prices=False, base_currency="USD")
    positions_route.set_raw_repository(raw_repository)
    positions_route.set_settings_service(settings)
    positions_route.set_quote_service(None)
    trades_route.set_raw_repository(raw_repository)
    trades_route.set_settings_service(settings)
    overview_route.set_raw_repository(raw_repository)
    overview_route.set_settings_service(settings)
    overview_route.set_quote_service(None)
    telegram_route.set_raw_repository(raw_repository)
    telegram_route.set_settings_service(settings)
    return settings


def _import_xml(client: TestClient, xml: str) -> None:
    created = client.post(
        "/api/import/tasks/content/create",
        json={"files": [{"filename": "redacted-options.xml", "content": xml}]},
    )
    assert created.status_code == 202
    run = client.post(created.json()["run_url"])
    assert run.status_code == 200
    assert run.json()["status"] == "completed"


def test_option_positions_are_distinct_filtered_and_summarized_before_pagination() -> None:
    _configure_routes()
    with TestClient(app) as client:
        xml = _option_xml()
        _import_xml(client, xml)
        _import_xml(client, xml)
        _import_xml(client, xml.replace('strike="200"', 'strike="260"'))

        stock = client.get("/api/positions", params={"asset_type": "stock", "page_size": 1}).json()
        class FailingQuoteService:
            def get_latest_quote(self, symbol: str) -> dict:
                raise AssertionError(f"option quote lookup is forbidden: {symbol}")

            def get_snapshot_quote(self, symbol: str) -> dict:
                raise AssertionError(f"option quote lookup is forbidden: {symbol}")

        positions_route.set_quote_service(FailingQuoteService())
        try:
            options = client.get("/api/positions", params={"asset_type": "option", "page_size": 3}).json()
            all_options = client.get("/api/positions", params={"asset_type": "option", "page_size": 100}).json()
            within_7 = client.get(
                "/api/positions",
                params={"asset_type": "option", "expiry_status": "within_7", "page_size": 100},
            ).json()
        finally:
            positions_route.set_quote_service(None)
        industry = client.get("/api/positions/industry-allocation").json()

    assert stock["total"] == 1
    assert stock["items"][0]["symbol"] == "MSFT"
    assert options["total"] == 7
    assert len(options["items"]) == 3
    assert all("|200|" not in item["contract_key"] for item in all_options["items"])
    assert len({item["contract_key"] for item in options["items"]}) == 3
    assert all(item["asset_category"] in {"OPT", "FOP"} for item in options["items"])
    assert options["summary"] == {
        "option_net_market_value": 2340.0,
        "option_unrealized_pnl": 250.0,
        "expiring_30_contracts": 4,
        "expiring_30_short_contracts": 1,
    }
    assert options["snapshot_date"]
    assert options["is_stale"] is False
    assert {item["days_to_expiry"] for item in within_7["items"]} == {1, 7}
    assert industry["items"][0]["market_value"] == 1000.0
    assert industry["total_market_value"] == 1000.0


def test_overview_and_telegram_share_bounded_expiration_alerts() -> None:
    settings = _configure_routes()
    settings.update(
        telegram_allowlisted_chat_ids=["123"],
        telegram_reports_enabled=True,
        ai_provider="mock",
    )
    telegram_route.set_settings_service(settings)
    with TestClient(app) as client:
        _import_xml(client, _option_xml(option_count=12))
        overview = client.get("/api/overview").json()
        report = client.post("/api/telegram/reports/dry-run").json()

    alerts = overview["option_expiration_alerts"]
    assert alerts["total"] == 11
    assert len(alerts["items"]) == 5
    assert alerts["remaining_count"] == 6
    assert alerts["items"][0]["expiry_status"] == "expired"
    assert alerts["items"][0]["snapshot_date"]
    assert "期权到期提醒" in report["message"]
    assert report["message"].count("\n- ") == 10
    assert "另有 1 个，请到看板查看" in report["message"]


def test_option_trades_can_be_filtered_and_searched_by_underlying_or_contract() -> None:
    _configure_routes()
    xml = _option_xml(option_count=2)
    with TestClient(app) as client:
        _import_xml(client, xml)
        options = client.get("/api/trades", params={"asset_type": "option", "page_size": 1}).json()
        by_underlying = client.get("/api/trades", params={"asset_type": "option", "symbol": "AAPL"}).json()
        raw_symbol = by_underlying["items"][0]["symbol"]
        by_contract = client.get("/api/trades", params={"symbol": raw_symbol}).json()

    assert options["total"] == 2
    assert options["summary"]["trade_count"] == 2
    assert by_underlying["total"] == 2
    assert by_contract["total"] == 1
    trade = by_contract["items"][0]
    assert trade["asset_category"] == "OPT"
    assert trade["underlying_symbol"] == "AAPL"
    assert trade["contract_title"].startswith("AAPL · ")
    assert trade["open_close_indicator"] == "O"
    assert trade["transaction_type"] == "Trade"
    assert trade["notes"] == "opening option"


def test_overview_removes_net_exposure_and_uses_signed_all_position_margin_formula() -> None:
    _configure_routes()
    with TestClient(app) as client:
        _import_xml(client, _option_xml(option_count=1))
        body = client.get("/api/overview").json()

    metrics = {item["key"]: item for item in body["risk_dashboard"]["metrics"]}
    assert "net_exposure" not in metrics
    assert metrics["margin_usage"]["label"] == "Margin 使用率（估算）"
    assert metrics["margin_usage"]["value"] == 20.0
    assert "不是 IBKR 官方维持保证金占用率" in metrics["margin_usage"]["action"]


def test_beta_contract_uses_stock_only_renormalizes_and_reports_coverage() -> None:
    _configure_routes()
    today = datetime.now(ZoneInfo("Asia/Shanghai"))
    report_date = _compact(today)
    raw_repository.upsert_account_snapshot(
        {
            "id": f"UBETA_{report_date}",
            "account_id": "UBETA",
            "report_date": report_date,
            "total_equity": "110000",
            "cash": "0",
            "stock_market_value": "100000",
            "base_currency": "USD",
        }
    )
    for symbol, category, value in [("AAPL", "STK", 60000), ("MSFT", "STK", 40000), ("AAPL-OPT", "OPT", 10000)]:
        raw_repository.es.update(
            index="ibkr_position_snapshots_v1",
            id=f"UBETA_{report_date}_{category}_{symbol}",
            doc={
                "account_id": "UBETA",
                "report_date": report_date,
                "symbol": symbol,
                "asset_category": category,
                "market_value_snapshot": value,
                "level_of_detail": "SUMMARY",
            },
            doc_as_upsert=True,
        )

    def prices(returns: list[float]) -> list[float]:
        result = [100.0]
        for value in returns:
            result.append(result[-1] * (1 + value))
        return result

    market_returns = [0.01, 0.02, -0.01, 0.03]
    series = {
        "QQQ": prices(market_returns),
        "^IXIC": prices(market_returns),
        "^GSPC": prices(market_returns),
        "AAPL": prices([value * 2 for value in market_returns]),
        "MSFT": [],
    }
    dates = [(today - timedelta(days=4 - index)).date().isoformat() for index in range(5)]

    def fake_fetcher(symbol: str, _start: str, _end: str) -> list[dict]:
        return [{"date": day, "value": value, "source": "test"} for day, value in zip(dates, series.get(symbol, []))]

    overview_route.set_benchmark_history_fetcher(fake_fetcher)
    with TestClient(app) as client:
        body = client.get("/api/overview/risk-warning").json()
        schema = client.get("/openapi.json").json()

    qqq = next(item for item in body["benchmarks"] if item["key"] == "qqq")
    assert body["window"] == 60
    assert qqq["portfolio_beta"] == 2.0
    assert qqq["coverage_pct"] == 60.0
    assert {item["symbol"] for item in body["positions"]} == {"AAPL", "MSFT"}
    assert not {"scenarios", "custom_drawdown", "var_comparison"} & body.keys()
    parameters = schema["paths"]["/api/overview/risk-warning"]["get"]["parameters"]
    assert {item["name"] for item in parameters} == {"benchmark", "window"}
