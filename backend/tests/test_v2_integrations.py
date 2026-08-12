import json
import subprocess
import sys
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.portfolio_analysis_contracts import AINarrativePayload
from app.api.portfolio_analysis_contracts import AnalysisStatus
from app.api.portfolio_analysis_contracts import PortfolioAnalysisSectionKey
from app.services.ai_narrative_service import AINarrativeService
from app.services.ai_narrative_service import DeepSeekChatCompletionsProvider
from app.services.ai_narrative_service import MiniMaxChatCompletionsProvider
from app.services.ai_narrative_service import OpenAIResponsesProvider
from app.services.ai_narrative_service import PORTFOLIO_TOTAL_BUDGET_SECONDS
from app.services.ai_narrative_service import build_ai_provider
from app.api.routes import portfolio_analysis as portfolio_route
from app.api.routes import telegram as telegram_route
from app.main import app
from app.repositories.in_memory_es import InMemoryElasticsearchClient
from app.repositories.raw_repository import RawRepository
from app.services.industry_mapping_service import IndustryMappingService
from app.services.market_data_provider import FutuOpenDReadOnlyProvider
from app.services.market_data_provider import MarketDataPoint
from app.services.market_data_provider import _normalize_futu_symbol
from app.services.market_data_provider import calculate_rsi
from app.services.mcp_tools import READ_ONLY_TOOLS, ReadOnlyMCPTools
from app.services.portfolio_analysis_service import PortfolioAnalysisService
from app.services.settings_service import SettingsService
from app.services.telegram_service import (
    TelegramCommandService,
    TelegramDeliveryService,
    TelegramUpdatePollingService,
)
from app.services.xml_parser import parse_xml_string


def _repo_with_positions() -> RawRepository:
    es = InMemoryElasticsearchClient()
    repo = RawRepository(es_client=es)
    es.update(
        index="ibkr_account_snapshots_v1",
        id="U1_20260501",
        doc={
            "account_id": "U1",
            "report_date": "20260501",
            "base_currency": "USD",
            "total_equity": "100000",
            "cash": "10000",
            "stock_market_value": "90000",
        },
        doc_as_upsert=True,
    )
    es.update(
        index="ibkr_position_snapshots_v1",
        id="U1_20260501_STK_NVDA_SUMMARY",
        doc={
            "account_id": "U1",
            "report_date": "20260501",
            "asset_category": "STK",
            "symbol": "NVDA",
            "level_of_detail": "SUMMARY",
            "quantity": "10",
            "mark_price_snapshot": "900",
            "market_value_snapshot": "9000",
            "industry": "Semiconductors",
        },
        doc_as_upsert=True,
    )
    es.update(
        index="ibkr_position_snapshots_v1",
        id="U1_20260501_OPT_AAPL_SUMMARY",
        doc={
            "account_id": "U1",
            "report_date": "20260501",
            "asset_category": "OPT",
            "symbol": "AAPL",
            "level_of_detail": "SUMMARY",
            "quantity": "1",
            "mark_price_snapshot": "12",
            "market_value_snapshot": "1200",
            "expiry": "20260619",
            "put_call": "CALL",
            "delta": "0.42",
            "gamma": "0.02",
            "theta": "-0.03",
            "vega": "0.11",
        },
        doc_as_upsert=True,
    )
    return repo


def _valid_portfolio_overlay(metrics: dict, *, label: str = "模型判断") -> dict:
    rows = []
    urls = []
    for index, position in enumerate(metrics.get("positions", []), start=1):
        source_id = f"S{index}"
        url = f"https://example.com/research/{index}"
        urls.append(url)
        rows.append(
            {
                "position_key": position["position_key"],
                "symbol": position["symbol"],
                "logic_status": f"{label}：最新证据需要持续验证",
                "recommendation": "只读建议：继续跟踪风险证据，不给出交易数量",
                "risk_points": [
                    {"severity": "high", "title": "经营风险", "detail": "最新经营证据可能影响持仓逻辑。", "evidence_ids": [source_id]},
                    {"severity": "medium", "title": "估值风险", "detail": "估值变化可能放大价格波动。", "evidence_ids": [source_id]},
                    {"severity": "low", "title": "流动性风险", "detail": "市场流动性变化需要持续观察。", "evidence_ids": [source_id]},
                ],
                "tracking_points": [
                    {"item": "财报", "why": "验证经营趋势", "trigger": "指引下调", "horizon": "quarterly", "evidence_ids": [source_id]},
                    {"item": "公司公告", "why": "识别新增事实", "trigger": "重大事项公告", "horizon": "30d", "evidence_ids": [source_id]},
                    {"item": "风险暴露", "why": "监控组合影响", "trigger": "风险显著上升", "horizon": "7d", "evidence_ids": [source_id]},
                ],
                "sources": [
                    {
                        "id": source_id,
                        "title": f"研究来源 {index}",
                        "url": url,
                        "published_at": "2026-06-30",
                        "source_type": "financial_media",
                    }
                ],
                "research_status": "ready",
                "confidence": 0.82,
            }
        )
    return {
        "risk_rows": rows,
        "rebalance_advice": {
            "cards": [
                {"rank": "01", "icon": "alert", "title": "组合首要风险", "body": "优先核实最大风险暴露。"},
                {"rank": "02", "icon": "search", "title": "优先复核持仓", "body": "先复核证据不足的持仓。"},
                {"rank": "03", "icon": "compass", "title": "组合结构与集中度", "body": "关注集中度和相关性。"},
                {"rank": "04", "icon": "calendar", "title": "未来30天跟踪清单", "body": "跟踪财报、公告和风险变化。"},
            ],
            "action_today": "今天先核实证据，不生成交易动作。",
            "thinking_prompt": "当前最大风险是否仍有足够证据支撑？",
            "confidence": 0.81,
        },
        "confidence": 0.81,
        "_researched_urls_by_position": {
            row["position_key"]: [url]
            for row, url in zip(metrics.get("positions", []), urls, strict=True)
        },
    }


def _portfolio_overlay_from_tool_messages(messages: list[dict], positions: list[dict]) -> dict:
    by_key = {position["position_key"]: position for position in positions}
    rows = []
    for message in messages:
        if message.get("role") != "tool":
            continue
        result = json.loads(message["content"])
        if "position_key" not in result:
            continue
        position = by_key[result["position_key"]]
        source = result["sources"][0]
        source_id = source["id"]
        rows.append(
            {
                "position_key": position["position_key"],
                "symbol": position["symbol"],
                "logic_status": "最新公开证据已纳入风险判断",
                "recommendation": "继续跟踪证据变化，不生成交易数量",
                "risk_points": [
                    {"severity": "high", "title": "经营风险", "detail": "经营证据可能削弱持仓逻辑。", "evidence_ids": [source_id]},
                    {"severity": "medium", "title": "估值风险", "detail": "估值变化可能放大波动。", "evidence_ids": [source_id]},
                    {"severity": "low", "title": "流动性风险", "detail": "流动性变化需要跟踪。", "evidence_ids": [source_id]},
                ],
                "tracking_points": [
                    {"item": "财报", "why": "验证经营趋势", "trigger": "指引下调", "horizon": "quarterly", "evidence_ids": [source_id]},
                    {"item": "公告", "why": "识别新增事实", "trigger": "重大事项", "horizon": "30d", "evidence_ids": [source_id]},
                    {"item": "风险暴露", "why": "监控组合影响", "trigger": "风险上升", "horizon": "7d", "evidence_ids": [source_id]},
                ],
                "sources": [
                    {
                        "id": source_id,
                        "title": source["title"],
                        "url": source["url"],
                        "published_at": source.get("published_at"),
                        "source_type": source.get("source_type", "other"),
                    }
                ],
                "research_status": "ready",
                "confidence": 0.8,
            }
        )
    overlay = _valid_portfolio_overlay({"positions": []})
    overlay["risk_rows"] = rows
    return overlay


def test_xml_parser_persists_optional_option_fields() -> None:
    xml = """<root>
      <OpenPosition accountId="U1" reportDate="20260501" assetCategory="OPT" symbol="AAPL" levelOfDetail="SUMMARY" position="1" markPrice="12" positionValue="1200" expiry="20260619" strike="200" putCall="CALL" multiplier="100" underlyingSymbol="AAPL" delta="0.42" gamma="0.02" theta="-0.03" vega="0.11" />
      <Trade tradeID="T1" accountId="U1" symbol="AAPL" buySell="BUY" quantity="1" tradePrice="12" expiry="20260619" strike="200" putCall="CALL" multiplier="100" underlyingSymbol="AAPL" />
    </root>"""
    parsed = parse_xml_string(xml)

    assert parsed.positions[0].expiry == "20260619"
    assert parsed.positions[0].strike == "200"
    assert parsed.positions[0].put_call == "CALL"
    assert parsed.positions[0].underlying == "AAPL"
    assert parsed.positions[0].delta == "0.42"
    assert parsed.trades[0].expiry == "20260619"
    assert parsed.trades[0].multiplier == "100"


def test_futu_provider_exposes_only_read_only_methods() -> None:
    provider = FutuOpenDReadOnlyProvider()
    public = {name for name in dir(provider) if not name.startswith("_")}
    forbidden = {"place_order", "modify_order", "cancel_order", "submit_order", "buy", "sell", "unlock_trade"}

    assert forbidden.isdisjoint(public)
    assert {"get_quote", "get_kline_history", "get_option_indicators", "get_sentiment"}.issubset(public)


def test_futu_symbol_normalization_defaults_plain_tickers_to_us() -> None:
    assert _normalize_futu_symbol("AAPL") == "US.AAPL"
    assert _normalize_futu_symbol("HK.00700") == "HK.00700"
    assert _normalize_futu_symbol("00700") == "HK.00700"
    assert _normalize_futu_symbol("600000") == "SH.600000"


def test_futu_provider_reads_snapshot_and_kline_with_read_only_context(monkeypatch) -> None:
    class FakeQuoteContext:
        def __init__(self, *, host: str, port: int) -> None:
            assert host == "127.0.0.1"
            assert port == 11111

        def get_market_snapshot(self, codes: list[str]):
            assert codes == ["US.AAPL"]
            return 0, [{"last_price": "195.5", "update_time": "2026-05-13 09:30:00"}]

        def request_history_kline(self, code: str, **kwargs):
            assert code == "US.AAPL"
            assert kwargs["ktype"] == "K_DAY"
            assert kwargs["autype"] == "QFQ"
            return 0, [
                {"time_key": "2026-05-10 00:00:00", "open": "10", "high": "12", "low": "9", "close": "11", "volume": "1000"},
                {"time_key": "2026-05-11 00:00:00", "open": "11", "high": "13", "low": "10", "close": "12", "volume": "1200"},
            ], None

        def close(self) -> None:
            return None

    class FakeFutu:
        RET_OK = 0
        OpenQuoteContext = FakeQuoteContext

        class KLType:
            K_DAY = "K_DAY"

        class AuType:
            QFQ = "QFQ"

    monkeypatch.setattr("app.services.market_data_provider.importlib.import_module", lambda name: FakeFutu)

    provider = FutuOpenDReadOnlyProvider()
    quote = provider.get_quote("AAPL")
    history = provider.get_kline_history("AAPL", days=2)

    assert quote["status"] == "ready"
    assert quote["symbol"] == "US.AAPL"
    assert quote["price"] == 195.5
    assert [point.close for point in history] == [11.0, 12.0]


def test_rsi_handles_flat_rising_and_falling_series() -> None:
    assert calculate_rsi([10.0] * 20) == 50.0
    assert calculate_rsi([float(value) for value in range(1, 25)]) == 100.0
    falling = calculate_rsi([float(value) for value in range(25, 1, -1)])
    assert falling is not None
    assert falling < 1


def test_minimax_provider_is_configurable_and_supports_legacy_openai_key_slot() -> None:
    provider = build_ai_provider(
        provider_name="minimax",
        openai_api_key="legacy-minimax-key",
        minimax_api_key="",
        ai_model="MiniMax-M2.7-highspeed",
    )

    assert isinstance(provider, MiniMaxChatCompletionsProvider)
    assert provider.api_key == "legacy-minimax-key"
    assert provider.model == "MiniMax-M2.7-highspeed"
    assert provider.base_url == "https://api.minimaxi.com/v1"


def test_ai_provider_uses_fast_default_models() -> None:
    minimax = build_ai_provider(provider_name="minimax", openai_api_key="", minimax_api_key="mini-key")
    openai = build_ai_provider(provider_name="openai", openai_api_key="sk-test")
    deepseek = build_ai_provider(provider_name="deepseek", openai_api_key="", deepseek_api_key="deepseek-key")

    assert isinstance(minimax, MiniMaxChatCompletionsProvider)
    assert minimax.model == "MiniMax-M2.5-highspeed"
    assert openai.model == "gpt-5-mini"
    assert isinstance(deepseek, DeepSeekChatCompletionsProvider)
    assert deepseek.model == "deepseek-v4-flash"
    assert deepseek.base_url == "https://api.deepseek.com"
    assert deepseek.timeout_seconds == 60.0
    assert PORTFOLIO_TOTAL_BUDGET_SECONDS < 240


def test_deepseek_portfolio_overlay_uses_controlled_tavily_tool_calls(monkeypatch) -> None:
    positions = [
        {
            "position_key": "STK:NVDA",
            "symbol": "NVDA",
            "asset_category": "STK",
            "quantity": 10,
            "average_cost": 800,
            "market_value": 9000,
            "weight_pct": 88.24,
            "unrealized_pnl": 1000,
        },
        {
            "position_key": "OPT:AAPL:20260619:200:CALL",
            "symbol": "AAPL",
            "asset_category": "OPT",
            "expiry": "20260619",
            "strike": 200,
            "put_call": "CALL",
            "quantity": 1,
            "market_value": 1200,
            "weight_pct": 11.76,
        },
    ]
    metrics = {"portfolio": {"position_count": 2}, "positions": positions, "policy": {"read_only": True}}
    requests: list[dict] = []
    progress: list[tuple[str, dict]] = []

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    def fake_post(url: str, **kwargs):
        requests.append({"url": url, **kwargs})
        if url == "https://api.tavily.com/search":
            index = 2 if "AAPL" in kwargs["json"]["query"] else 1
            return FakeResponse(
                {
                    "results": [
                        {
                            "title": f"研究来源 {index}",
                            "url": f"https://example.com/research/{index}",
                            "published_date": "2026-06-30",
                            "content": "最新公开研究摘要",
                            "score": 0.9,
                        }
                    ]
                }
            )
        messages = kwargs["json"]["messages"]
        if not any(message.get("role") == "tool" for message in messages):
            tool_calls = [
                {
                    "id": f"call-{index}",
                    "type": "function",
                    "function": {
                        "name": "search_financial_research",
                        "arguments": json.dumps(
                            {"position_key": position["position_key"], "focus": "catalysts_and_risks"}
                        ),
                    },
                }
                for index, position in enumerate(positions, start=1)
            ]
            return FakeResponse({"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": tool_calls}}]})
        return FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(_valid_portfolio_overlay(metrics), ensure_ascii=False),
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("app.services.ai_narrative_service.httpx.post", fake_post)
    provider = DeepSeekChatCompletionsProvider(
        api_key="deepseek-key",
        tavily_api_key="tavily-key",
        progress_callback=lambda stage, details: progress.append((stage, details)),
    )

    overlay = AINarrativeService().generate_portfolio_overlay(
        provider=provider,
        metrics=metrics,
        cache_key="controlled-search",
        force=True,
    )

    assert overlay["status"] == "ready"
    assert {row["position_key"] for row in overlay["risk_rows"]} == {row["position_key"] for row in positions}
    deepseek_requests = [request for request in requests if request["url"].endswith("/chat/completions")]
    tavily_requests = [request for request in requests if request["url"] == "https://api.tavily.com/search"]
    assert len(deepseek_requests) == 2
    assert deepseek_requests[0]["json"]["tool_choice"] == "auto"
    assert "不可信证据材料" in deepseek_requests[0]["json"]["messages"][0]["content"]
    assert "顶层 JSON 必须恰好只有 risk_rows、rebalance_advice、confidence" in deepseek_requests[0]["json"]["messages"][1]["content"]
    assert any(message.get("role") == "tool" for message in deepseek_requests[1]["json"]["messages"])
    assert len(tavily_requests) == 2
    for request in tavily_requests:
        assert request["headers"]["Authorization"] == "Bearer tavily-key"
        assert set(request["json"]) == {
            "query",
            "topic",
            "search_depth",
            "max_results",
            "include_answer",
            "include_raw_content",
        }
        serialized = json.dumps(request["json"])
        assert "tavily-key" not in serialized
        assert all(field not in serialized for field in ("quantity", "average_cost", "market_value", "weight_pct", "unrealized_pnl"))
    assert [stage for stage, _ in progress] == ["researching_web", "analyzing_risks"]


def test_deepseek_portfolio_overlay_repairs_schema_once_without_researching_again(monkeypatch) -> None:
    position = {
        "position_key": "STK:NVDA",
        "symbol": "NVDA",
        "asset_category": "STK",
        "weight_pct": 100,
    }
    metrics = {"portfolio": {"position_count": 1}, "positions": [position]}
    requests: list[dict] = []
    tavily_calls = 0

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    def fake_post(url: str, **kwargs):
        nonlocal tavily_calls
        requests.append({"url": url, **kwargs})
        if url == "https://api.tavily.com/search":
            tavily_calls += 1
            return FakeResponse(
                {
                    "results": [
                        {
                            "title": "研究来源 1",
                            "url": "https://example.com/research/1",
                            "published_date": "2026-06-30",
                            "content": "最新公开研究摘要",
                            "score": 0.9,
                        }
                    ]
                }
            )

        messages = kwargs["json"]["messages"]
        if "JSON schema 修复器" in messages[0]["content"]:
            return FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(_valid_portfolio_overlay(metrics), ensure_ascii=False)
                            }
                        }
                    ]
                }
            )
        if not any(message.get("role") == "tool" for message in messages):
            return FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "search_financial_research",
                                            "arguments": json.dumps(
                                                {
                                                    "position_key": "STK:NVDA",
                                                    "focus": "catalysts_and_risks",
                                                }
                                            ),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            )

        invalid_overlay = _portfolio_overlay_from_tool_messages(messages, [position])
        invalid_overlay["risk_rows"][0]["risk_points"][0]["evidence_ids"] = []
        return FakeResponse(
            {"choices": [{"message": {"content": json.dumps(invalid_overlay, ensure_ascii=False)}}]}
        )

    monkeypatch.setattr("app.services.ai_narrative_service.httpx.post", fake_post)
    overlay = AINarrativeService().generate_portfolio_overlay(
        provider=DeepSeekChatCompletionsProvider(api_key="deepseek-key", tavily_api_key="tavily-key"),
        metrics=metrics,
        cache_key="repair-schema-once",
        force=True,
    )

    assert overlay["status"] == "ready"
    assert tavily_calls == 1
    repair_requests = [
        request
        for request in requests
        if request["url"].endswith("/chat/completions")
        and "JSON schema 修复器" in request["json"]["messages"][0]["content"]
    ]
    assert len(repair_requests) == 1
    assert "tools" not in repair_requests[0]["json"]
    assert repair_requests[0]["json"]["thinking"] == {"type": "disabled"}


def test_deepseek_portfolio_overlay_batches_more_than_twenty_positions(monkeypatch) -> None:
    positions = [
        {
            "position_key": f"STK:S{index:02d}",
            "symbol": f"S{index:02d}",
            "asset_category": "STK",
            "weight_pct": round(100 / 21, 2),
            "market_value": 1000 + index,
        }
        for index in range(21)
    ]
    metrics = {"portfolio": {"position_count": 21}, "positions": positions, "portfolio_alerts": []}
    requests: list[dict] = []

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    def fake_post(url: str, **kwargs):
        requests.append({"url": url, **kwargs})
        if url == "https://api.tavily.com/search":
            symbol = kwargs["json"]["query"].split()[0]
            return FakeResponse(
                {
                    "results": [
                        {
                            "title": f"{symbol} 官方研究",
                            "url": f"https://example.com/{symbol.lower()}",
                            "published_date": "2026-06-30",
                            "content": "公开金融事实",
                            "score": 0.9,
                        }
                    ]
                }
            )
        messages = kwargs["json"]["messages"]
        if "只读组合风险汇总器" in messages[0]["content"]:
            return FakeResponse(
                {"choices": [{"message": {"content": json.dumps({"rebalance_advice": _valid_portfolio_overlay({"positions": []})["rebalance_advice"]}, ensure_ascii=False)}}]}
            )
        tool_messages = [message for message in messages if message.get("role") == "tool"]
        if not tool_messages:
            prompt = messages[1]["content"]
            batch = [position for position in positions if f'"position_key": "{position["position_key"]}"' in prompt]
            calls = [
                {
                    "id": f"call-{position['position_key']}",
                    "type": "function",
                    "function": {
                        "name": "search_financial_research",
                        "arguments": json.dumps({"position_key": position["position_key"], "focus": "catalysts_and_risks"}),
                    },
                }
                for position in batch
            ]
            return FakeResponse({"choices": [{"message": {"content": "", "tool_calls": calls}}]})
        return FakeResponse(
            {"choices": [{"message": {"content": json.dumps(_portfolio_overlay_from_tool_messages(messages, positions), ensure_ascii=False)}}]}
        )

    monkeypatch.setattr("app.services.ai_narrative_service.httpx.post", fake_post)
    overlay = AINarrativeService().generate_portfolio_overlay(
        provider=DeepSeekChatCompletionsProvider(api_key="deepseek-key", tavily_api_key="tavily-key"),
        metrics=metrics,
        cache_key="batch-21",
        force=True,
    )

    assert overlay["status"] == "ready"
    assert len(overlay["risk_rows"]) == 21
    assert {row["position_key"] for row in overlay["risk_rows"]} == {position["position_key"] for position in positions}
    assert overlay["research_stats"]["batch_count"] == 3
    assert overlay["research_stats"]["search_calls"] == 21
    deepseek_requests = [request for request in requests if request["url"].endswith("/chat/completions")]
    assert len(deepseek_requests) == 7
    batch_inputs = []
    seen_message_lists: set[int] = set()
    for request in deepseek_requests:
        messages = request["json"]["messages"]
        if "持仓风险研究员" not in messages[0]["content"] or id(messages) in seen_message_lists:
            continue
        seen_message_lists.add(id(messages))
        batch_inputs.append(json.loads(messages[1]["content"].split("输入 JSON：\n", 1)[1]))
    assert len(batch_inputs) == 3
    assert [(batch["portfolio"]["position_count"], len(batch["positions"])) for batch in batch_inputs] == [
        (8, 8),
        (8, 8),
        (5, 5),
    ]


def test_deepseek_portfolio_advice_repairs_invalid_card_schema_once(monkeypatch) -> None:
    requests: list[dict] = []

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    def fake_post(url: str, **kwargs):
        requests.append(kwargs["json"])
        if len(requests) == 1:
            content = json.dumps({"cards": [], "action_today": "复核", "thinking_prompt": "风险？", "confidence": 0.7})
        else:
            content = json.dumps(_valid_portfolio_overlay({"positions": []})["rebalance_advice"], ensure_ascii=False)
        return FakeResponse({"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr("app.services.ai_narrative_service.httpx.post", fake_post)
    advice = DeepSeekChatCompletionsProvider(
        api_key="deepseek-key",
        tavily_api_key="tavily-key",
    )._synthesize_portfolio_advice(
        metrics={"portfolio": {}, "portfolio_alerts": []},
        verified_rows=[],
        deadline=10**12,
    )

    assert advice is not None
    assert [card["rank"] for card in advice["cards"]] == ["01", "02", "03", "04"]
    assert len(requests) == 2
    assert "tools" not in requests[1]
    assert requests[1]["thinking"] == {"type": "disabled"}


def test_deepseek_portfolio_advice_falls_back_to_verified_rows_after_failed_repair(monkeypatch) -> None:
    calls = 0

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps({"cards": []})}}]}

    def fake_post(url: str, **kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse()

    position = {"position_key": "STK:NVDA", "symbol": "NVDA", "asset_category": "STK", "weight_pct": 100}
    verified_row = _valid_portfolio_overlay({"positions": [position]})["risk_rows"][0]
    monkeypatch.setattr("app.services.ai_narrative_service.httpx.post", fake_post)
    advice = DeepSeekChatCompletionsProvider(
        api_key="deepseek-key",
        tavily_api_key="tavily-key",
    )._synthesize_portfolio_advice(
        metrics={"portfolio": {}, "positions": [position], "portfolio_alerts": []},
        verified_rows=[verified_row],
        deadline=10**12,
    )

    assert advice is not None
    assert calls == 2
    assert advice["cards"][0]["title"] == "组合首要风险"
    assert "NVDA" in advice["cards"][0]["body"]
    assert "NVDA 100.00%" in advice["cards"][2]["body"]


def test_deepseek_portfolio_overlay_deduplicates_sources_per_position(monkeypatch) -> None:
    position = {"position_key": "STK:NVDA", "symbol": "NVDA", "asset_category": "STK", "weight_pct": 100}
    metrics = {"portfolio": {"position_count": 1}, "positions": [position]}
    tavily_call = 0

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    def fake_post(url: str, **kwargs):
        nonlocal tavily_call
        if url == "https://api.tavily.com/search":
            tavily_call += 1
            suffixes = ["a", "a", "b"]
            return FakeResponse(
                {
                    "results": [
                        {
                            "title": f"来源 {suffix}",
                            "url": f"https://example.com/{suffix}",
                            "content": "公开金融事实",
                            "score": 0.9,
                        }
                        for suffix in suffixes
                    ]
                }
            )
        messages = kwargs["json"]["messages"]
        tool_messages = [message for message in messages if message.get("role") == "tool"]
        if not tool_messages:
            calls = [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "search_financial_research",
                        "arguments": json.dumps(
                            {"position_key": "STK:NVDA", "focus": "catalysts_and_risks"}
                        ),
                    },
                }
            ]
            return FakeResponse({"choices": [{"message": {"content": "", "tool_calls": calls}}]})

        source_groups = [json.loads(message["content"])["sources"] for message in tool_messages]
        assert [len(group) for group in source_groups] == [2]
        sources = [source for group in source_groups for source in group]
        overlay = _valid_portfolio_overlay(metrics)
        overlay["risk_rows"][0]["sources"] = [
            {key: source[key] for key in ("id", "title", "url", "published_at", "source_type")}
            for source in sources
        ]
        return FakeResponse({"choices": [{"message": {"content": json.dumps(overlay, ensure_ascii=False)}}]})

    monkeypatch.setattr("app.services.ai_narrative_service.httpx.post", fake_post)
    overlay = AINarrativeService().generate_portfolio_overlay(
        provider=DeepSeekChatCompletionsProvider(api_key="deepseek-key", tavily_api_key="tavily-key"),
        metrics=metrics,
        cache_key="deduplicated-sources",
        force=True,
    )

    assert overlay["status"] == "ready"
    sources = overlay["risk_rows"][0]["sources"]
    assert tavily_call == 1
    assert len(sources) == 2
    assert len({source["url"] for source in sources}) == 2


def test_deepseek_tavily_search_retries_one_transient_timeout(monkeypatch) -> None:
    attempts = 0

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "results": [
                    {
                        "title": "NVDA 官方来源",
                        "url": "https://example.com/nvda",
                        "content": "公开金融事实",
                        "score": 0.9,
                    }
                ]
            }

    def fake_post(url: str, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("timed out", request=httpx.Request("POST", url))
        return FakeResponse()

    monkeypatch.setattr("app.services.ai_narrative_service.httpx.post", fake_post)
    result = DeepSeekChatCompletionsProvider(
        api_key="deepseek-key",
        tavily_api_key="tavily-key",
    )._search_financial_research(
        position={"position_key": "STK:NVDA", "symbol": "NVDA", "asset_category": "STK"},
        focus="catalysts_and_risks",
    )

    assert attempts == 2
    assert result["status"] == "ready"
    assert result["sources"][0]["url"] == "https://example.com/nvda"


@pytest.mark.parametrize(
    ("failure", "expected_reason"),
    [
        (401, "search_unauthorized"),
        (429, "search_rate_limited"),
        ("timeout", "search_timed_out"),
    ],
)
def test_deepseek_portfolio_overlay_preserves_tavily_failure_reason(monkeypatch, failure, expected_reason) -> None:
    position = {"position_key": "STK:NVDA", "symbol": "NVDA", "asset_category": "STK", "weight_pct": 100}
    metrics = {"portfolio": {"position_count": 1}, "positions": [position]}

    class FakeResponse:
        def __init__(self, payload: dict | None = None) -> None:
            self.payload = payload or {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    def fake_post(url: str, **kwargs):
        if url == "https://api.tavily.com/search":
            request = httpx.Request("POST", url)
            if failure == "timeout":
                raise httpx.ReadTimeout("timed out", request=request)
            response = httpx.Response(int(failure), request=request)
            raise httpx.HTTPStatusError("search failed", request=request, response=response)
        return FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "search_financial_research",
                                        "arguments": json.dumps({"position_key": "STK:NVDA", "focus": "catalysts_and_risks"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("app.services.ai_narrative_service.httpx.post", fake_post)
    overlay = AINarrativeService().generate_portfolio_overlay(
        provider=DeepSeekChatCompletionsProvider(api_key="deepseek-key", tavily_api_key="tavily-key"),
        metrics=metrics,
        cache_key=f"failure-{expected_reason}",
        force=True,
    )

    assert overlay["status"] == "error"
    assert overlay["reason"] == expected_reason


def test_deepseek_portfolio_overlay_reports_partial_search_failure(monkeypatch) -> None:
    positions = [
        {"position_key": "STK:GOOD", "symbol": "GOOD", "asset_category": "STK", "weight_pct": 50},
        {"position_key": "STK:FAIL", "symbol": "FAIL", "asset_category": "STK", "weight_pct": 50},
    ]
    metrics = {"portfolio": {"position_count": 2}, "positions": positions}

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    def fake_post(url: str, **kwargs):
        if url == "https://api.tavily.com/search":
            if kwargs["json"]["query"].startswith("FAIL"):
                raise httpx.ReadTimeout("timed out", request=httpx.Request("POST", url))
            return FakeResponse({"results": [{"title": "GOOD 来源", "url": "https://example.com/good", "content": "公开事实", "score": 1.0}]})
        calls = [
            {
                "id": f"call-{position['symbol']}",
                "type": "function",
                "function": {
                    "name": "search_financial_research",
                    "arguments": json.dumps({"position_key": position["position_key"], "focus": "catalysts_and_risks"}),
                },
            }
            for position in positions
        ]
        return FakeResponse({"choices": [{"message": {"content": "", "tool_calls": calls}}]})

    monkeypatch.setattr("app.services.ai_narrative_service.httpx.post", fake_post)
    overlay = AINarrativeService().generate_portfolio_overlay(
        provider=DeepSeekChatCompletionsProvider(api_key="deepseek-key", tavily_api_key="tavily-key"),
        metrics=metrics,
        cache_key="partial-search",
        force=True,
    )

    assert overlay["status"] == "error"
    assert overlay["reason"] == "search_partial:search_timed_out"


def test_deepseek_portfolio_overlay_rejects_duplicate_calls_and_answers_every_tool_call(monkeypatch) -> None:
    positions = [
        {"position_key": f"STK:C{index:02d}", "symbol": f"C{index:02d}", "asset_category": "STK", "weight_pct": 5}
        for index in range(8)
    ]
    metrics = {"portfolio": {"position_count": 8}, "positions": positions}
    tavily_calls = 0
    final_messages: list[dict] = []

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    def fake_post(url: str, **kwargs):
        nonlocal tavily_calls, final_messages
        if url == "https://api.tavily.com/search":
            tavily_calls += 1
            symbol = kwargs["json"]["query"].split()[0]
            return FakeResponse({"results": [{"title": symbol, "url": f"https://example.com/{symbol}", "content": "公开事实", "score": 1.0}]})
        messages = kwargs["json"]["messages"]
        if not any(message.get("role") == "tool" for message in messages):
            requested_positions = positions + positions[:5]
            calls = [
                {
                    "id": f"call-{index}",
                    "type": "function",
                    "function": {
                        "name": "search_financial_research",
                        "arguments": json.dumps({"position_key": position["position_key"], "focus": "catalysts_and_risks"}),
                    },
                }
                for index, position in enumerate(requested_positions)
            ]
            return FakeResponse({"choices": [{"message": {"content": "", "tool_calls": calls}}]})
        final_messages = messages
        return FakeResponse({"choices": [{"message": {"content": json.dumps(_portfolio_overlay_from_tool_messages(messages, positions), ensure_ascii=False)}}]})

    monkeypatch.setattr("app.services.ai_narrative_service.httpx.post", fake_post)
    overlay = AINarrativeService().generate_portfolio_overlay(
        provider=DeepSeekChatCompletionsProvider(api_key="deepseek-key", tavily_api_key="tavily-key"),
        metrics=metrics,
        cache_key="call-limit",
        force=True,
    )

    assert overlay["status"] == "ready"
    assert tavily_calls == 8
    tool_messages = [message for message in final_messages if message.get("role") == "tool"]
    assert len(tool_messages) == 13
    assert sum("position_already_researched" in message["content"] for message in tool_messages) == 5


def test_deepseek_portfolio_overlay_restores_missing_sources_from_tavily_values(monkeypatch) -> None:
    position = {"position_key": "STK:NVDA", "symbol": "NVDA", "asset_category": "STK", "weight_pct": 100}
    metrics = {"portfolio": {"position_count": 1}, "positions": [position]}

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    def fake_post(url: str, **kwargs):
        if url == "https://api.tavily.com/search":
            return FakeResponse({"results": [{"title": "Tavily 原始标题", "url": "https://sec.gov/filing", "published_date": "2026-06-30", "content": "公开事实", "score": 1.0}]})
        messages = kwargs["json"]["messages"]
        tool_messages = [message for message in messages if message.get("role") == "tool"]
        if not tool_messages:
            return FakeResponse({"choices": [{"message": {"content": "", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "search_financial_research", "arguments": json.dumps({"position_key": "STK:NVDA", "focus": "financials_and_guidance"})}}]}}]})
        result = json.loads(tool_messages[0]["content"])
        overlay = _portfolio_overlay_from_tool_messages(messages, [position])
        overlay["risk_rows"][0]["sources"] = []
        overlay["risk_rows"][0]["research_status"] = "missing"
        overlay["risk_rows"][0]["logic_status"] = ""
        overlay["risk_rows"][0]["recommendation"] = ""
        overlay["risk_rows"][0]["risk_points"] = overlay["risk_rows"][0]["risk_points"][:1]
        overlay["risk_rows"][0]["tracking_points"] *= 2
        assert result["sources"][0]["title"] == "Tavily 原始标题"
        return FakeResponse({"choices": [{"message": {"content": json.dumps(overlay, ensure_ascii=False)}}]})

    monkeypatch.setattr("app.services.ai_narrative_service.httpx.post", fake_post)
    monkeypatch.setattr(
        "app.services.ai_narrative_service._attach_canonical_sources_to_rows",
        lambda overlay, canonical_sources_by_position: None,
    )
    overlay = AINarrativeService().generate_portfolio_overlay(
        provider=DeepSeekChatCompletionsProvider(api_key="deepseek-key", tavily_api_key="tavily-key"),
        metrics=metrics,
        cache_key="canonical-source",
        force=True,
    )

    assert overlay["status"] == "ready"
    source = overlay["risk_rows"][0]["sources"][0]
    assert source == {
        "id": "S1",
        "title": "Tavily 原始标题",
        "url": "https://sec.gov/filing",
        "published_at": "2026-06-30",
        "source_type": "filing",
    }
    assert overlay["risk_rows"][0]["logic_status"].startswith("重点风险：")
    assert overlay["risk_rows"][0]["recommendation"].startswith("重点跟踪：")
    assert len(overlay["risk_rows"][0]["risk_points"]) == 1
    assert len(overlay["risk_rows"][0]["tracking_points"]) == 5


def test_minimax_provider_fails_closed_without_key() -> None:
    provider = build_ai_provider(provider_name="minimax", openai_api_key="", minimax_api_key="")
    narrative = provider.generate(section="market", metrics={})

    assert narrative.provider == "minimax"
    assert narrative.status == "unavailable"
    assert narrative.reason == "minimax_api_key_not_configured"


def test_minimax_provider_uses_reasoning_split_for_m2_chat_payload(monkeypatch) -> None:
    seen_payload: dict | None = None

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "摘要",
                                    "bullets": ["要点"],
                                    "risks": ["风险"],
                                    "confidence": 0.7,
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    def fake_post(_url: str, **kwargs):
        nonlocal seen_payload
        seen_payload = kwargs["json"]
        return Response()

    monkeypatch.setattr("app.services.ai_narrative_service.httpx.post", fake_post)

    provider = MiniMaxChatCompletionsProvider(api_key="mini-key")
    narrative = provider.generate(section="market", metrics={"x": 1})

    assert narrative.status == AnalysisStatus.READY
    assert seen_payload is not None
    assert "response_format" not in seen_payload
    assert seen_payload["reasoning_split"] is True
    assert seen_payload["max_tokens"] == 1600


def test_minimax_provider_retries_with_response_format_when_plain_content_empty(monkeypatch) -> None:
    seen_payloads: list[dict] = []

    class EmptyResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": ""}}]}

    class JsonResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "兼容重试摘要",
                                    "bullets": ["要点"],
                                    "risks": ["风险"],
                                    "confidence": 0.72,
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    def fake_post(_url: str, **kwargs):
        seen_payloads.append(kwargs["json"])
        return EmptyResponse() if len(seen_payloads) == 1 else JsonResponse()

    monkeypatch.setattr("app.services.ai_narrative_service.httpx.post", fake_post)

    provider = MiniMaxChatCompletionsProvider(api_key="mini-key")
    narrative = provider.generate(section="market", metrics={"x": 1})

    assert narrative.status == AnalysisStatus.READY
    assert narrative.summary == "兼容重试摘要"
    assert len(seen_payloads) == 2
    assert "response_format" not in seen_payloads[0]
    assert seen_payloads[1]["response_format"] == {"type": "json_object"}


def test_minimax_provider_reads_reasoning_content_when_message_content_empty(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": json.dumps(
                                {
                                    "summary": "reasoning json",
                                    "bullets": [],
                                    "risks": [],
                                    "confidence": 0.61,
                                }
                            ),
                        }
                    }
                ]
            }

    monkeypatch.setattr("app.services.ai_narrative_service.httpx.post", lambda *_args, **_kwargs: Response())

    provider = MiniMaxChatCompletionsProvider(api_key="mini-key")
    narrative = provider.generate(section="market", metrics={"x": 1})

    assert narrative.status == AnalysisStatus.READY
    assert narrative.summary == "reasoning json"


def test_minimax_provider_strips_think_tags_before_json_parse(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "<think>这里可能包含推理文字和 {非 JSON 片段}</think>"
                                + json.dumps(
                                    {
                                        "summary": "剥离思考标签后的摘要",
                                        "bullets": [],
                                        "risks": [],
                                        "confidence": 0.66,
                                    },
                                    ensure_ascii=False,
                                )
                            )
                        }
                    }
                ]
            }

    monkeypatch.setattr("app.services.ai_narrative_service.httpx.post", lambda *_args, **_kwargs: Response())

    provider = MiniMaxChatCompletionsProvider(api_key="mini-key")
    narrative = provider.generate(section="market", metrics={"x": 1})

    assert narrative.status == AnalysisStatus.READY
    assert narrative.summary == "剥离思考标签后的摘要"


def test_minimax_provider_reports_non_json_http_body(monkeypatch) -> None:
    class Response:
        text = ""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            raise json.JSONDecodeError("Expecting value", "", 0)

    monkeypatch.setattr("app.services.ai_narrative_service.httpx.post", lambda *_args, **_kwargs: Response())

    provider = MiniMaxChatCompletionsProvider(api_key="mini-key")
    narrative = provider.generate(section="market", metrics={"x": 1})

    assert narrative.status == AnalysisStatus.ERROR
    assert narrative.reason is not None
    assert "AI response is not JSON: empty HTTP body" in narrative.reason


def test_minimax_provider_normalizes_scalar_risks_and_percent_confidence(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "摘要",
                                    "bullets": "单条要点",
                                    "risks": "单条风险",
                                    "confidence": 95,
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    monkeypatch.setattr("app.services.ai_narrative_service.httpx.post", lambda *_args, **_kwargs: Response())

    provider = MiniMaxChatCompletionsProvider(api_key="mini-key")
    narrative = provider.generate(section="market", metrics={"x": 1})

    assert narrative.status == AnalysisStatus.READY
    assert narrative.bullets == ["单条要点"]
    assert narrative.risks == ["单条风险"]
    assert narrative.confidence == 0.95


def test_market_analysis_uses_positions_for_market_context(monkeypatch) -> None:
    repo = _repo_with_positions()
    service = SettingsService()

    class FakeMarketDataProvider:
        name = "fake_market"

        def get_kline_history(self, _symbol: str, *, days: int = 90) -> list[MarketDataPoint]:
            return []

        def get_sentiment(self, symbol: str) -> dict[str, object]:
            return {"status": "missing_data", "symbol": symbol, "source": self.name}

    portfolio_route.set_raw_repository(repo)
    portfolio_route.set_settings_service(service)
    monkeypatch.setattr(
        portfolio_route,
        "_build_service",
        lambda: PortfolioAnalysisService(
            raw_repository=repo,
            settings_service=service,
            market_data_provider=FakeMarketDataProvider(),
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/portfolio-analysis")

    assert response.status_code == 200
    body = response.json()
    market = body["market"]
    assert market["status"] == "ready"
    assert market["indicators"]["portfolio_weighted_change"]["status"] == "ready"
    assert market["indicators"]["breadth"]["status"] == "ready"
    assert market["watch_symbols"] == ["NVDA", "AAPL"]
    assert market["portfolio_impact"]
    assert market["opportunities"]
    assert "narrative" not in market
    assert "sections" not in body


def test_market_and_stock_rsi_use_different_symbols_when_available() -> None:
    class FakeMarketDataProvider:
        name = "fake"

        def get_kline_history(self, symbol: str, *, days: int = 60) -> list[MarketDataPoint]:
            if symbol == "QQQ":
                closes = [float(value) for value in range(1, 31)]
            elif symbol == "NVDA":
                closes = [float(value) for value in range(31, 1, -1)]
            else:
                closes = [20.0] * 30
            return [
                MarketDataPoint(date=f"2026-05-{index + 1:02d}", close=close)
                for index, close in enumerate(closes)
            ]

        def get_quote(self, symbol: str) -> dict[str, object]:
            return {"status": "ready", "symbol": symbol, "price": 100.0}

    analysis = PortfolioAnalysisService(
        raw_repository=_repo_with_positions(),
        settings_service=SettingsService(),
        market_data_provider=FakeMarketDataProvider(),
    )

    market = analysis.get_analysis(section=PortfolioAnalysisSectionKey.MARKET).sections.market
    stock = analysis.get_analysis(section=PortfolioAnalysisSectionKey.STOCK, symbol="NVDA").sections.stock

    assert market.indicators["rsi"].source == "fake:QQQ"
    assert stock.available_symbols[0].symbol == "NVDA"
    assert stock.memo.symbol == "NVDA"
    assert stock.memo.status == AnalysisStatus.READY
    assert stock.indicators == {}
    assert stock.charts == []
    assert market.market_pulse
    assert market.playbook
    assert market.strategy


def test_market_analysis_derives_weighted_change_and_crowding_metrics() -> None:
    class FakeMarketDataProvider:
        name = "fake"

        def get_kline_history(self, symbol: str, *, days: int = 90) -> list[MarketDataPoint]:
            closes = [100.0 + index for index in range(30)]
            volumes = [1000.0] * 29 + [1500.0]
            if symbol == "^VIX":
                closes = [15.0 + index * 0.1 for index in range(30)]
            return [
                MarketDataPoint(date=f"2026-05-{index + 1:02d}", close=close, volume=volumes[index])
                for index, close in enumerate(closes)
            ]

        def get_quote(self, symbol: str) -> dict[str, object]:
            return {"status": "ready", "symbol": symbol, "price": 100.0}

        def get_sentiment(self, symbol: str) -> dict[str, object]:
            return {"status": "missing_data", "symbol": symbol, "source": "fake"}

    repo = _repo_with_positions()
    repo.es.update(
        index="ibkr_account_snapshots_v1",
        id="U1_20260430",
        doc={
            "account_id": "U1",
            "report_date": "20260430",
            "base_currency": "USD",
            "total_equity": "99000",
        },
        doc_as_upsert=True,
    )
    repo.es.update(
        index="ibkr_position_snapshots_v1",
        id="U1_20260430_STK_NVDA_SUMMARY",
        doc={
            "account_id": "U1",
            "report_date": "20260430",
            "asset_category": "STK",
            "symbol": "NVDA",
            "level_of_detail": "SUMMARY",
            "quantity": "10",
            "mark_price_snapshot": "800",
            "market_value_snapshot": "8000",
            "industry": "Semiconductors",
        },
        doc_as_upsert=True,
    )
    analysis = PortfolioAnalysisService(
        raw_repository=repo,
        settings_service=SettingsService(),
        market_data_provider=FakeMarketDataProvider(),
    )

    market = analysis.get_analysis(section=PortfolioAnalysisSectionKey.MARKET).sections.market

    assert market.indicators["portfolio_weighted_change"].value != 0
    assert market.indicators["iv_percentile"].status == AnalysisStatus.READY
    assert market.indicators["put_call_ratio"].status == AnalysisStatus.READY
    assert market.indicators["put_call_ratio"].confidence >= 0.58
    assert market.indicators["volume_anomaly"].value == 1.5
    assert market.indicators["volume_anomaly"].confidence == 0.82


def test_portfolio_analysis_uses_default_industry_mapping_for_top_positions() -> None:
    repo = RawRepository(es_client=InMemoryElasticsearchClient())
    repo.es.update(
        index="ibkr_account_snapshots_v1",
        id="U1_20260501",
        doc={
            "account_id": "U1",
            "report_date": "20260501",
            "base_currency": "USD",
            "total_equity": "100000",
        },
        doc_as_upsert=True,
    )
    for symbol, value in (("RKLB", "30000"), ("INTC", "20000"), ("MU", "15000")):
        repo.es.update(
            index="ibkr_position_snapshots_v1",
            id=f"U1_20260501_STK_{symbol}_SUMMARY",
            doc={
                "account_id": "U1",
                "report_date": "20260501",
                "asset_category": "STK",
                "symbol": symbol,
                "level_of_detail": "SUMMARY",
                "quantity": "100",
                "mark_price_snapshot": "100",
                "market_value_snapshot": value,
            },
            doc_as_upsert=True,
        )

    analysis = PortfolioAnalysisService(
        raw_repository=repo,
        settings_service=SettingsService(),
        industry_mapping_service=IndustryMappingService(),
    )

    positions = analysis._current_positions()
    industries = {str(row["symbol"]): row.get("industry") for row in positions}

    assert industries["RKLB"] == "工业 / 航空航天与国防"
    assert industries["INTC"] == "信息技术 / 半导体"
    assert industries["MU"] == "信息技术 / 存储半导体"


def test_portfolio_analysis_converts_mixed_positions_to_krw() -> None:
    repo = RawRepository(es_client=InMemoryElasticsearchClient())
    repo.upsert_account_snapshot(
        {
            "id": "U1_20260629",
            "account_id": "U1",
            "report_date": "20260629",
            "base_currency": "USD",
            "total_equity": "200",
        }
    )
    for symbol, currency, value, rate in (
        ("AAPL", "USD", "100", ""),
        ("005930", "KRW", "100000", "0.001"),
    ):
        repo.es.update(
            index="ibkr_position_snapshots_v1",
            id=f"U1_20260629_STK_{symbol}_SUMMARY",
            doc={
                "account_id": "U1",
                "report_date": "20260629",
                "asset_category": "STK",
                "symbol": symbol,
                "currency": currency,
                "fx_rate_to_base": rate,
                "level_of_detail": "SUMMARY",
                "quantity": "1",
                "mark_price_snapshot": value,
                "market_value_snapshot": value,
            },
            doc_as_upsert=True,
        )
    repo.es.update(
        index="ibkr_fx_rates_v1",
        id="20260629_KRW_USD",
        doc={
            "rate_date": "20260629",
            "from_currency": "KRW",
            "to_currency": "USD",
            "rate": "0.001",
        },
        doc_as_upsert=True,
    )
    settings = SettingsService()
    settings.update(base_currency="KRW")

    rows = PortfolioAnalysisService(
        raw_repository=repo,
        settings_service=settings,
    )._current_positions()
    by_symbol = {row["symbol"]: row for row in rows}

    assert by_symbol["AAPL"]["market_value_snapshot"] == 100000.0
    assert by_symbol["005930"]["market_value_snapshot"] == 100000.0
    assert by_symbol["AAPL"]["display_currency"] == "KRW"
    assert by_symbol["005930"]["source_values"]["market_value_snapshot"] == "100000"


def test_portfolio_analysis_only_uses_structured_ai_overlay_after_manual_refresh(monkeypatch) -> None:
    AINarrativeService._shared_structured_cache.clear()
    AINarrativeService._shared_structured_state.clear()
    AINarrativeService._shared_structured_cache.clear()

    def fail_generate(self, *, section: str, metrics: dict):
        raise AssertionError("narrative AI should only run on explicit refresh")

    overlay_calls = 0

    def fake_overlay(self, *, metrics: dict):
        nonlocal overlay_calls
        overlay_calls += 1
        return _valid_portfolio_overlay(metrics)

    monkeypatch.setattr(DeepSeekChatCompletionsProvider, "generate", fail_generate)
    monkeypatch.setattr(DeepSeekChatCompletionsProvider, "generate_portfolio_overlay", fake_overlay)
    repo = _repo_with_positions()
    service = SettingsService()
    service.update(ai_provider="deepseek", deepseek_api_key="deepseek-key", tavily_api_key="tavily-key")
    analysis = PortfolioAnalysisService(raw_repository=repo, settings_service=service)

    initial = analysis.get_analysis(section=PortfolioAnalysisSectionKey.PORTFOLIO)

    assert overlay_calls == 0
    assert initial.sections.portfolio.status == AnalysisStatus.READY
    assert initial.sections.portfolio.risk_rows[0].source.startswith("portfolio_positions")
    assert initial.sections.portfolio.analysis_meta["ai_overlay_provider"] == "deepseek"
    assert initial.sections.portfolio.analysis_meta["ai_overlay_status"] == "unavailable"
    assert initial.sections.portfolio.analysis_meta["ai_overlay_reason"] == "structured_ai_overlay_waiting_for_manual_refresh"

    refreshed = analysis.get_analysis(section=PortfolioAnalysisSectionKey.PORTFOLIO, refresh_ai=True)
    cached = analysis.get_analysis(section=PortfolioAnalysisSectionKey.PORTFOLIO)

    assert overlay_calls == 1
    assert refreshed.sections.portfolio.risk_rows[0].source.startswith("deepseek_tavily_web_research")
    assert refreshed.sections.portfolio.risk_rows[0].logic_status.startswith("模型判断")
    assert refreshed.sections.portfolio.analysis_meta["ai_overlay_status"] == "ready"
    assert refreshed.sections.portfolio.narrative.status == AnalysisStatus.READY
    assert cached.sections.portfolio.risk_rows[0].source.startswith("deepseek_tavily_web_research")


def test_portfolio_refresh_sends_every_position_without_longbridge_external_context(monkeypatch) -> None:
    AINarrativeService._shared_structured_cache.clear()
    AINarrativeService._shared_structured_state.clear()
    repo = _repo_with_positions()
    for index in range(11):
        symbol = f"TST{index}"
        repo.es.update(
            index="ibkr_position_snapshots_v1",
            id=f"U1_20260501_STK_{symbol}_SUMMARY",
            doc={
                "account_id": "U1",
                "report_date": "20260501",
                "asset_category": "STK",
                "symbol": symbol,
                "level_of_detail": "SUMMARY",
                "quantity": str(index + 1),
                "mark_price_snapshot": str(20 + index),
                "market_value_snapshot": str(500 + index),
                "unrealized_pnl_snapshot": str(index - 5),
            },
            doc_as_upsert=True,
        )
    captured: dict = {}

    def fake_overlay(self, *, metrics: dict):
        captured.update(metrics)
        return _valid_portfolio_overlay(metrics)

    class FailIfCalledMarketProvider:
        name = "longbridge"
        sentiment_calls = 0

        def get_sentiment(self, symbol: str):
            self.sentiment_calls += 1
            raise AssertionError(f"portfolio refresh must not call Longbridge sentiment for {symbol}")

    monkeypatch.setattr(DeepSeekChatCompletionsProvider, "generate_portfolio_overlay", fake_overlay)
    settings = SettingsService()
    settings.update(ai_provider="deepseek", deepseek_api_key="deepseek-key", tavily_api_key="tavily-key")
    market_provider = FailIfCalledMarketProvider()
    result = PortfolioAnalysisService(
        raw_repository=repo,
        settings_service=settings,
        market_data_provider=market_provider,
    ).get_analysis(section=PortfolioAnalysisSectionKey.PORTFOLIO, refresh_ai=True)

    assert captured["portfolio"]["position_count"] == 13
    assert len(captured["positions"]) == 13
    assert "selected_rows" not in captured
    assert "omitted_symbols" not in captured
    assert len({row["position_key"] for row in captured["positions"]}) == 13
    assert market_provider.sentiment_calls == 0
    nvda = next(row for row in result.sections.portfolio.risk_rows if row.symbol == "NVDA")
    assert nvda.current_price == 900.0
    assert nvda.weight_pct > 0
    assert nvda.source == "deepseek_tavily_web_research"


def test_invalid_partial_portfolio_overlay_is_rejected_atomically(monkeypatch) -> None:
    AINarrativeService._shared_structured_cache.clear()
    AINarrativeService._shared_structured_state.clear()

    def partial_overlay(self, *, metrics: dict):
        overlay = _valid_portfolio_overlay(metrics)
        overlay["risk_rows"] = overlay["risk_rows"][:-1]
        return overlay

    monkeypatch.setattr(DeepSeekChatCompletionsProvider, "generate_portfolio_overlay", partial_overlay)
    settings = SettingsService()
    settings.update(ai_provider="deepseek", deepseek_api_key="deepseek-key", tavily_api_key="tavily-key")
    result = PortfolioAnalysisService(
        raw_repository=_repo_with_positions(),
        settings_service=settings,
    ).get_analysis(section=PortfolioAnalysisSectionKey.PORTFOLIO, refresh_ai=True)

    portfolio = result.sections.portfolio
    assert portfolio.analysis_meta["ai_overlay_provider"] == "local_rules"
    assert "invalid_portfolio_overlay: position_key_set_mismatch" in portfolio.analysis_meta["ai_overlay_reason"]
    assert all(row.source == "local_rules_structured_ai" for row in portfolio.risk_rows)
    assert all(row.research_status == "missing" for row in portfolio.risk_rows)


@pytest.mark.parametrize(
    ("case", "expected_reason"),
    [
        ("duplicate_key", "position_key_set_mismatch"),
        ("invalid_cards", "rebalance_cards_count_invalid"),
        ("unsearched_url", "source_not_returned_by_search"),
        ("duplicate_source_url", "source_urls_duplicate"),
        ("too_many_sources", "source_count_invalid"),
    ],
)
def test_invalid_portfolio_overlay_variants_fail_closed_atomically(monkeypatch, case, expected_reason) -> None:
    AINarrativeService._shared_structured_cache.clear()
    AINarrativeService._shared_structured_state.clear()

    def invalid_overlay(self, *, metrics: dict):
        overlay = _valid_portfolio_overlay(metrics)
        if case == "duplicate_key":
            overlay["risk_rows"][1]["position_key"] = overlay["risk_rows"][0]["position_key"]
        elif case == "invalid_cards":
            overlay["rebalance_advice"]["cards"].pop()
        elif case == "unsearched_url":
            overlay["risk_rows"][0]["sources"][0]["url"] = "https://unsearched.example/fabricated"
        elif case == "duplicate_source_url":
            duplicate = dict(overlay["risk_rows"][0]["sources"][0])
            duplicate["id"] = "S-duplicate"
            overlay["risk_rows"][0]["sources"].append(duplicate)
        else:
            template = overlay["risk_rows"][0]["sources"][0]
            overlay["risk_rows"][0]["sources"].extend(
                [
                    {
                        **template,
                        "id": f"S-extra-{index}",
                        "url": f"https://example.com/extra/{index}",
                    }
                    for index in range(5)
                ]
            )
        return overlay

    monkeypatch.setattr(DeepSeekChatCompletionsProvider, "generate_portfolio_overlay", invalid_overlay)
    settings = SettingsService()
    settings.update(ai_provider="deepseek", deepseek_api_key="deepseek-key", tavily_api_key="tavily-key")
    portfolio = PortfolioAnalysisService(
        raw_repository=_repo_with_positions(),
        settings_service=settings,
    ).get_analysis(section=PortfolioAnalysisSectionKey.PORTFOLIO, refresh_ai=True).sections.portfolio

    assert portfolio.analysis_meta["ai_overlay_provider"] == "local_rules"
    assert expected_reason in portfolio.analysis_meta["ai_overlay_reason"]
    assert all(row.source == "local_rules_structured_ai" for row in portfolio.risk_rows)
    assert all(row.research_status == "missing" for row in portfolio.risk_rows)


def test_portfolio_overlay_persistence_failure_is_reported_and_not_cached(monkeypatch) -> None:
    AINarrativeService._shared_structured_cache.clear()
    AINarrativeService._shared_structured_state.clear()

    monkeypatch.setattr(
        DeepSeekChatCompletionsProvider,
        "generate_portfolio_overlay",
        lambda self, *, metrics: _valid_portfolio_overlay(metrics),
    )
    monkeypatch.setattr(
        PortfolioAnalysisService,
        "_persist_portfolio_ai_overlay",
        lambda self, *, provider, cache_key, overlay: False,
    )
    settings = SettingsService()
    settings.update(ai_provider="deepseek", deepseek_api_key="deepseek-key", tavily_api_key="tavily-key")
    analysis = PortfolioAnalysisService(raw_repository=_repo_with_positions(), settings_service=settings)

    refreshed = analysis.get_analysis(
        section=PortfolioAnalysisSectionKey.PORTFOLIO,
        refresh_ai=True,
    ).sections.portfolio
    cached = analysis.get_analysis(section=PortfolioAnalysisSectionKey.PORTFOLIO).sections.portfolio

    assert refreshed.analysis_meta["ai_overlay_provider"] == "local_rules"
    assert refreshed.analysis_meta["ai_overlay_reason"] == "fallback_after_portfolio_overlay_persistence_failed"
    assert cached.analysis_meta["ai_overlay_provider"] == "local_rules"
    assert cached.analysis_meta["ai_overlay_reason"] == "fallback_after_portfolio_overlay_persistence_failed"


def test_portfolio_analysis_reads_persisted_structured_ai_overlay_without_generating(monkeypatch) -> None:
    AINarrativeService._shared_structured_cache.clear()
    AINarrativeService._shared_structured_state.clear()
    overlay_calls = 0

    def fake_overlay(self, *, metrics: dict):
        nonlocal overlay_calls
        overlay_calls += 1
        return _valid_portfolio_overlay(metrics, label="模型判断：来自持久化缓存")

    monkeypatch.setattr(DeepSeekChatCompletionsProvider, "generate_portfolio_overlay", fake_overlay)
    repo = _repo_with_positions()
    settings = SettingsService()
    settings.update(ai_provider="deepseek", deepseek_api_key="deepseek-key", tavily_api_key="tavily-key")
    writer = PortfolioAnalysisService(raw_repository=repo, settings_service=settings)

    refreshed = writer.get_analysis(section=PortfolioAnalysisSectionKey.PORTFOLIO, refresh_ai=True)
    AINarrativeService._shared_structured_cache.clear()
    AINarrativeService._shared_structured_state.clear()
    reader = PortfolioAnalysisService(raw_repository=repo, settings_service=settings)
    cached = reader.get_analysis(section=PortfolioAnalysisSectionKey.PORTFOLIO)

    assert overlay_calls == 1
    assert refreshed.sections.portfolio.analysis_meta["ai_overlay_status"] == "ready"
    assert cached.sections.portfolio.analysis_meta["ai_overlay_status"] == "ready"
    assert cached.sections.portfolio.risk_rows[0].source.startswith("deepseek_tavily_web_research")
    assert cached.sections.portfolio.risk_rows[0].logic_status.startswith("模型判断")


def test_structured_ai_overlay_pending_expires_instead_of_looping() -> None:
    AINarrativeService._shared_structured_cache.clear()
    AINarrativeService._shared_structured_state.clear()
    provider = build_ai_provider(provider_name="minimax", openai_api_key="", minimax_api_key="mini-key")
    service = AINarrativeService()
    cache_key = "stale-pending"
    service.mark_portfolio_overlay_started(provider=provider, cache_key=cache_key)
    key = (datetime.now(timezone.utc).date().isoformat(), provider.name, f"portfolio_overlay:{cache_key}")
    AINarrativeService._shared_structured_state[key]["as_of"] = (
        datetime.now(timezone.utc) - timedelta(seconds=120)
    ).isoformat()

    overlay = service.cached_portfolio_overlay_or_pending(provider=provider, cache_key=cache_key)

    assert overlay["status"] == "error"
    assert overlay["reason"] == "structured_ai_overlay_timed_out"


def test_portfolio_analysis_manual_ai_refresh_does_not_block_on_narrative(monkeypatch) -> None:
    AINarrativeService._shared_daily_cache.clear()
    AINarrativeService._shared_refresh_state.clear()
    AINarrativeService._shared_structured_cache.clear()
    calls = 0

    def fake_generate(self, *, section: str, metrics: dict):
        nonlocal calls
        calls += 1
        return AINarrativePayload(
            provider=self.name,
            model=self.model,
            status=AnalysisStatus.READY,
            summary=f"{section} cached summary",
            bullets=[],
            risks=[],
            source_metrics=sorted(metrics.keys()),
            confidence=0.8,
        )

    monkeypatch.setattr(DeepSeekChatCompletionsProvider, "generate", fake_generate)
    monkeypatch.setattr(
        DeepSeekChatCompletionsProvider,
        "generate_portfolio_overlay",
        lambda self, *, metrics: _valid_portfolio_overlay(metrics),
    )
    repo = _repo_with_positions()
    settings = SettingsService()
    settings.update(ai_provider="deepseek", deepseek_api_key="deepseek-key", tavily_api_key="tavily-key")
    analysis = PortfolioAnalysisService(
        raw_repository=repo,
        settings_service=settings,
        ai_narrative_service=AINarrativeService(),
    )

    refreshed = analysis.get_analysis(section=PortfolioAnalysisSectionKey.PORTFOLIO, refresh_ai=True)
    cached = analysis.get_analysis(section=PortfolioAnalysisSectionKey.PORTFOLIO)

    assert calls == 0
    assert refreshed.sections.portfolio.narrative.status == AnalysisStatus.READY
    assert refreshed.sections.portfolio.analysis_meta["ai_overlay_status"] == "ready"
    assert cached.sections.portfolio.narrative.status == AnalysisStatus.READY


def test_openai_provider_generate_calls_responses_api(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "output_text": json.dumps(
                    {
                        "summary": "组合摘要",
                        "bullets": ["重点一"],
                        "risks": ["风险一"],
                        "confidence": 0.73,
                    },
                    ensure_ascii=False,
                )
            }

    def fake_post(url: str, **kwargs):
        calls.append({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr("app.services.ai_narrative_service.httpx.post", fake_post)
    provider = OpenAIResponsesProvider(api_key="sk-test", model="gpt-test")

    narrative = provider.generate(section="portfolio", metrics={"risk": "high"})

    assert calls
    assert calls[0]["url"] == "https://api.openai.com/v1/responses"
    assert calls[0]["json"]["model"] == "gpt-test"
    assert narrative.status == AnalysisStatus.READY
    assert narrative.summary == "组合摘要"


def test_non_deepseek_portfolio_overlay_falls_back_to_local_rules() -> None:
    AINarrativeService._shared_daily_cache.clear()
    AINarrativeService._shared_refresh_state.clear()
    AINarrativeService._shared_structured_cache.clear()
    AINarrativeService._shared_structured_state.clear()

    repo = _repo_with_positions()
    settings = SettingsService()
    settings.update(ai_provider="openai", openai_api_key="sk-test")
    analysis = PortfolioAnalysisService(raw_repository=repo, settings_service=settings)

    result = analysis.get_analysis(section=PortfolioAnalysisSectionKey.PORTFOLIO, refresh_ai=True)

    assert result.sections.portfolio.status == AnalysisStatus.READY
    assert result.sections.portfolio.analysis_meta["ai_overlay_status"] == "ready"
    assert result.sections.portfolio.analysis_meta["ai_overlay_provider"] == "local_rules"
    assert result.sections.portfolio.analysis_meta["ai_overlay_reason"] == "fallback_after_portfolio_web_research_requires_deepseek_and_tavily"
    assert result.sections.portfolio.risk_rows[0].source.startswith("local_rules_structured_ai")


def test_ai_narrative_compacts_large_metrics_before_provider_call() -> None:
    class CapturingProvider:
        name = "mock"
        model = "mock"

        def __init__(self) -> None:
            self.metrics: dict | None = None

        def generate(self, *, section: str, metrics: dict):
            self.metrics = metrics
            return AINarrativePayload(
                provider=self.name,
                model=self.model,
                status=AnalysisStatus.READY,
                summary="ok",
                bullets=[],
                risks=[],
                source_metrics=sorted(metrics.keys()),
                confidence=0.7,
            )

    provider = CapturingProvider()
    service = AINarrativeService()
    service.generate(
        provider=provider,
        section="market",
        metrics={
            "market_pulse": [
                {
                    "title": "卡片",
                    "value": 1,
                    "sparkline": [{"date": "2026-05-01", "value": index} for index in range(100)],
                    "playbook": [{"label": "x"} for _ in range(100)],
                    "reading": "很长" * 300,
                }
            ],
            "top_positions": [{"symbol": str(index), "weight": index} for index in range(20)],
        },
        force=True,
    )

    assert provider.metrics is not None
    pulse = provider.metrics["market_pulse"][0]
    assert "sparkline" not in pulse
    assert "playbook" not in pulse
    assert len(pulse["reading"]) < 270
    assert len(provider.metrics["top_positions"]) == 6


def test_minimax_portfolio_overlay_is_not_used_for_web_research(monkeypatch) -> None:
    AINarrativeService._shared_daily_cache.clear()
    AINarrativeService._shared_refresh_state.clear()
    AINarrativeService._shared_structured_cache.clear()
    AINarrativeService._shared_structured_state.clear()
    calls = 0

    def fake_generate(self, *, section: str, metrics: dict):
        nonlocal calls
        calls += 1
        return AINarrativePayload(
            provider=self.name,
            model=self.model,
            status=AnalysisStatus.READY,
            summary="should not block portfolio refresh",
            bullets=[],
            risks=[],
            source_metrics=sorted(metrics.keys()),
            confidence=0.8,
        )

    monkeypatch.setattr(MiniMaxChatCompletionsProvider, "generate", fake_generate)
    repo = _repo_with_positions()
    settings = SettingsService()
    settings.update(ai_provider="minimax", minimax_api_key="mini-key")
    analysis = PortfolioAnalysisService(
        raw_repository=repo,
        settings_service=settings,
        ai_narrative_service=AINarrativeService(),
    )

    result = analysis.get_analysis(section=PortfolioAnalysisSectionKey.PORTFOLIO, refresh_ai=True)

    assert calls == 0
    assert result.sections.portfolio.analysis_meta["ai_overlay_provider"] == "local_rules"
    assert result.sections.portfolio.analysis_meta["ai_overlay_status"] == "ready"
    assert result.sections.portfolio.analysis_meta["ai_overlay_reason"] == "fallback_after_portfolio_web_research_requires_deepseek_and_tavily"
    assert result.sections.portfolio.narrative.status == AnalysisStatus.READY


def test_telegram_dry_run_rejects_unknown_chat_and_allows_read_only_command() -> None:
    repo = _repo_with_positions()
    service = SettingsService()
    service.update(telegram_allowlisted_chat_ids=["123456789"], ai_provider="mock")
    telegram_route.set_raw_repository(repo)
    telegram_route.set_settings_service(service)

    with TestClient(app) as client:
        rejected = client.post("/api/telegram/commands/dry-run", json={"chat_id": "99999", "text": "/risk"})
        allowed = client.post("/api/telegram/commands/dry-run", json={"chat_id": "123456789", "text": "/risk"})

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "forbidden"
    assert allowed.status_code == 200
    assert allowed.json()["ok"] is True
    assert "组合风险：" in allowed.json()["message"]


def test_telegram_report_dry_run_uses_cached_analysis_shape() -> None:
    repo = _repo_with_positions()
    service = SettingsService()
    service.update(
        telegram_allowlisted_chat_ids=["123456789", "-100987654321"],
        telegram_reports_enabled=True,
        telegram_daily_report_time="08:15",
        ai_provider="mock",
    )
    telegram_route.set_raw_repository(repo)
    telegram_route.set_settings_service(service)

    with TestClient(app) as client:
        response = client.post("/api/telegram/reports/dry-run")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["would_send_to"] == 2
    assert body["schedule"] == "08:15"
    assert "市场分析日报" in body["message"]


def test_telegram_delivery_never_sends_without_bot_token() -> None:
    delivery = TelegramDeliveryService(bot_token="")

    result = delivery.send_message(chat_id="123456789", text="hello")

    assert result == {"ok": False, "status": "missing_bot_token"}


def test_telegram_scheduled_report_uses_allowlist_and_delivery_client() -> None:
    repo = _repo_with_positions()
    settings = SettingsService()
    settings.update(
        telegram_allowlisted_chat_ids=["123456789", "987654321"],
        telegram_reports_enabled=True,
        ai_provider="mock",
    )
    analysis = PortfolioAnalysisService(
        raw_repository=repo,
        settings_service=settings,
    )
    command_service = TelegramCommandService(
        settings_service=settings,
        analysis_service=analysis,
        raw_repository=repo,
    )

    class FakeDelivery:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def send_message(self, *, chat_id: str, text: str) -> dict[str, object]:
            self.calls.append((chat_id, text))
            return {"ok": True, "status": "sent"}

    delivery = FakeDelivery()
    result = command_service.deliver_daily_report(delivery)  # type: ignore[arg-type]

    assert result["status"] == "sent"
    assert result["sent"] == 2
    assert [chat_id for chat_id, _message in delivery.calls] == ["123456789", "987654321"]
    assert all("市场分析日报" in message for _chat_id, message in delivery.calls)


def test_telegram_command_rejects_plain_language_question_without_ai() -> None:
    repo = _repo_with_positions()
    settings = SettingsService()
    settings.update(
        telegram_allowlisted_chat_ids=["123456789"],
    )
    analysis = PortfolioAnalysisService(
        raw_repository=repo,
        settings_service=settings,
    )
    command_service = TelegramCommandService(
        settings_service=settings,
        analysis_service=analysis,
        raw_repository=repo,
    )

    result = command_service.handle_command(chat_id="123456789", text="我的组合现在最大风险是什么？")

    assert result["ok"] is False
    assert result["status"] == "unsupported_command"
    assert "可用命令" in result["message"]


def test_telegram_update_poller_consumes_updates_and_sends_reply() -> None:
    repo = _repo_with_positions()
    settings = SettingsService()
    settings.update(
        telegram_bot_token="123456:telegram-token",
        telegram_allowlisted_chat_ids=["123456789"],
        ai_provider="mock",
    )
    analysis = PortfolioAnalysisService(
        raw_repository=repo,
        settings_service=settings,
    )
    command_service = TelegramCommandService(
        settings_service=settings,
        analysis_service=analysis,
        raw_repository=repo,
    )

    class FakeTelegramClient:
        def __init__(self) -> None:
            self.get_calls: list[dict[str, object]] = []
            self.post_calls: list[dict[str, object]] = []

        def get(self, url: str, *, params: dict[str, object], timeout: float) -> httpx.Response:
            self.get_calls.append(dict(params))
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 101,
                            "message": {
                                "chat": {"id": 123456789},
                                "text": "/summary",
                            },
                        }
                    ],
                },
            )

        def post(self, url: str, *, json: dict[str, object], timeout: float) -> httpx.Response:
            self.post_calls.append(dict(json))
            return httpx.Response(200, request=httpx.Request("POST", url), json={"ok": True})

    client = FakeTelegramClient()
    poller = TelegramUpdatePollingService(
        settings_service=settings,
        command_service_factory=lambda: command_service,
        delivery_service_factory=lambda bot_token: TelegramDeliveryService(bot_token=bot_token, client=client),
        client=client,
        poll_interval_seconds=0.1,
    )

    result = poller.poll_once()

    assert result == {"ok": True, "status": "polled", "processed": 1, "sent": 1}
    assert client.get_calls[0]["limit"] == 20
    assert client.post_calls[0]["chat_id"] == "123456789"
    assert "账户概览" in str(client.post_calls[0]["text"])


def test_mcp_tools_are_read_only_and_callable() -> None:
    tools = ReadOnlyMCPTools(
        raw_repository=_repo_with_positions(),
        derived_repository=None,
        settings_service=SettingsService(),
    )
    forbidden_terms = ("order", "buy", "sell", "trade_password", "unlock_trade")

    assert "list_positions" in READ_ONLY_TOOLS
    assert all(not any(term in name for term in forbidden_terms) for name in READ_ONLY_TOOLS)
    result = tools.call_tool("get_portfolio_risk", {})
    assert result["status"] == "ready"


def test_mcp_list_positions_uses_latest_account_snapshot_date() -> None:
    repo = _repo_with_positions()
    repo.es.update(
        index="ibkr_account_snapshots_v1",
        id="U1_20260512",
        doc={
            "account_id": "U1",
            "report_date": "20260512",
            "base_currency": "USD",
            "total_equity": "120000",
            "cash": "5000",
            "stock_market_value": "115000",
        },
        doc_as_upsert=True,
    )
    repo.es.update(
        index="ibkr_position_snapshots_v1",
        id="U1_20260512_STK_RKLB_SUMMARY",
        doc={
            "account_id": "U1",
            "report_date": "20260512",
            "asset_category": "STK",
            "symbol": "RKLB",
            "level_of_detail": "SUMMARY",
            "quantity": "200",
            "mark_price_snapshot": "117.56",
            "market_value_snapshot": "23512",
        },
        doc_as_upsert=True,
    )
    tools = ReadOnlyMCPTools(
        raw_repository=repo,
        derived_repository=None,
        settings_service=SettingsService(),
    )

    result = tools.call_tool("list_positions", {"limit": 10})

    assert result["status"] == "ready"
    assert result["account_report_date"] == "20260512"
    assert result["positions_report_date"] == "20260512"
    assert {row["symbol"] for row in result["items"]} == {"RKLB"}


def test_mcp_cli_smoke_lists_tools() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "app.mcp_server", "--list-tools"],
        cwd=".",
        check=True,
        capture_output=True,
        text=True,
    )
    body = json.loads(result.stdout)
    names = {tool["name"] for tool in body["tools"]}
    assert set(READ_ONLY_TOOLS).issubset(names)
