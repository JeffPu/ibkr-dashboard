from __future__ import annotations

import json
from datetime import date
from datetime import datetime
from datetime import timezone
from datetime import timedelta
from functools import lru_cache
from statistics import mean
from threading import Thread
from typing import Any, Callable

from app.api.currency_conversion import convert_position_money_values
from app.api.currency_conversion import normalize_currency_code
from app.api.currency_conversion import resolve_position_display_fx
from app.api.portfolio_analysis_contracts import AINarrativePayload
from app.api.portfolio_analysis_contracts import AnalysisStatus
from app.api.portfolio_analysis_contracts import EChartsPayload
from app.api.portfolio_analysis_contracts import EChartsSeries
from app.api.portfolio_analysis_contracts import PortfolioRebalanceAdvice
from app.api.portfolio_analysis_contracts import PortfolioAnalysisResponse
from app.api.portfolio_analysis_contracts import PortfolioAnalysisSectionKey
from app.api.portfolio_analysis_contracts import PortfolioRiskRow
from app.api.portfolio_analysis_contracts import StockResearchMemo
from app.api.portfolio_analysis_contracts import StockSelectionOption
from app.api.portfolio_analysis_contracts import StandardMetric
from app.api.portfolio_analysis_contracts import build_empty_portfolio_analysis_response
from app.api.portfolio_analysis_contracts import empty_chart
from app.api.portfolio_analysis_contracts import missing_metric
from app.repositories.raw_repository import RawRepository
from app.services.ai_narrative_service import AINarrativeService
from app.services.ai_narrative_service import MockAIProvider
from app.services.ai_narrative_service import build_ai_provider
from app.services.industry_mapping_service import IndustryMappingService
from app.services.market_data_provider import MarketDataPoint
from app.services.market_data_provider import MarketDataProvider
from app.services.market_data_provider import build_futu_opend_provider
from app.services.market_data_provider import calculate_rsi
from app.services.market_data_provider import fetch_cnn_fear_greed
from app.services.quote_service import fetch_longbridge_valuation_rank
from app.services.settings_service import SettingsService
from app.utils.numbers import optional_float as _optional_float
from app.utils.numbers import to_float as _to_float


AI_SYMBOL_HINTS = {
    "AI",
    "NVDA",
    "MSFT",
    "GOOGL",
    "GOOG",
    "META",
    "AMD",
    "TSM",
    "AVGO",
    "ASML",
    "SMCI",
    "MU",
    "INTC",
    "TSLA",
}
SEMICONDUCTOR_HINTS = {"NVDA", "AMD", "TSM", "AVGO", "ASML", "SMCI", "MU", "INTC", "QCOM", "AMAT", "LRCX"}
CYCLICAL_HINTS = {"TSLA", "RKLB", "BA", "CAT", "XOM", "CVX", "JPM", "BAC"}
MARKET_BENCHMARKS = {"growth": "QQQ", "broad": "SPY"}
MARKET_VOLUME_SYMBOLS = ("SPY", "QQQ", "DIA", "IWM")
PORTFOLIO_AI_CACHE_INDEX = "portfolio_ai_analysis_cache_v1"


class PortfolioAnalysisService:
    def __init__(
        self,
        *,
        raw_repository: RawRepository | object | None = None,
        settings_service: SettingsService,
        market_data_provider: MarketDataProvider | None = None,
        industry_mapping_service: IndustryMappingService | None = None,
        ai_narrative_service: AINarrativeService | None = None,
        progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._raw_repository = raw_repository
        self._settings_service = settings_service
        self._market_data_provider = market_data_provider
        self._industry_mapping_service = industry_mapping_service
        self._ai_narrative_service = ai_narrative_service or AINarrativeService()
        self._progress_callback = progress_callback

    def get_analysis(
        self,
        *,
        section: PortfolioAnalysisSectionKey | None = None,
        symbol: str | None = None,
        refresh_ai: bool = False,
        allow_ai: bool = True,
    ) -> PortfolioAnalysisResponse:
        settings = self._settings_service.get()
        valuation_mode = "realtime" if settings.display_realtime_prices else "snapshot"
        normalized_symbol = symbol.upper() if symbol else None
        response = build_empty_portfolio_analysis_response(
            display_currency=settings.base_currency,
            valuation_mode=valuation_mode,
            section=section,
            symbol=normalized_symbol,
            ai_provider=settings.ai_provider,
            telegram_enabled=bool(settings.telegram_bot_token),
            telegram_allowlisted_chat_ids_count=len(settings.telegram_allowlisted_chat_ids),
            telegram_schedule=settings.telegram_daily_report_time if settings.telegram_reports_enabled else None,
        )

        positions = self._current_positions()
        selected_symbol = normalized_symbol or _largest_position_symbol(positions)
        if section in {None, PortfolioAnalysisSectionKey.MARKET}:
            response.sections.market = self._build_market_section(positions)
        if section in {None, PortfolioAnalysisSectionKey.PORTFOLIO}:
            response.sections.portfolio = self._build_portfolio_section(positions, refresh_ai=refresh_ai, allow_ai=allow_ai)
        if section in {None, PortfolioAnalysisSectionKey.STOCK}:
            response.sections.stock = self._build_stock_section(positions, selected_symbol, refresh_ai=refresh_ai, allow_ai=allow_ai)
        response.integrations.ai = _active_narrative(response, section)
        response.status = _combine_status(
            [
                response.sections.market.status,
                response.sections.portfolio.status,
                response.sections.stock.status,
            ]
        )
        return response

    def mark_narrative_refresh_started(
        self,
        *,
        section: PortfolioAnalysisSectionKey,
        symbol: str | None = None,
    ) -> str | None:
        resolved_symbol = self._resolve_stock_symbol(symbol) if section == PortfolioAnalysisSectionKey.STOCK else symbol
        provider = self._ai_provider()
        self._ai_narrative_service.mark_refresh_started(
            provider=provider,
            section=section.value,
            cache_key=_narrative_cache_key(section, resolved_symbol),
        )
        return resolved_symbol

    def _build_market_section(self, positions: list[dict]):
        response = build_empty_portfolio_analysis_response(
            display_currency=self._settings_service.get().base_currency,
            valuation_mode="realtime" if self._settings_service.get().display_realtime_prices else "snapshot",
            symbol=_largest_position_symbol(positions),
            ai_provider=self._settings_service.get().ai_provider,
        ).sections.market
        if not positions:
            return response

        benchmark = _market_benchmark_for_positions(positions)
        history_by_symbol = self._market_histories(["SPY", "QQQ", "DIA", "IWM", "^VIX", "^NDX"], days=90)
        history = history_by_symbol.get(benchmark) or []
        closes = [point.close for point in history if point.close is not None]
        rsi = calculate_rsi([float(value) for value in closes])
        weighted_change = _weighted_daily_change_pct(positions)
        breadth = _positive_breadth(positions)
        ai_weight = _ai_theme_weight(positions, _positions_total(positions))
        local_fear_greed = _fear_greed_proxy(rsi, weighted_change, breadth, ai_weight)
        ndx_rsi = calculate_rsi([float(point.close) for point in history_by_symbol.get("^NDX", []) if point.close is not None])
        vix_value, vix_change = _latest_value_and_change(history_by_symbol.get("^VIX", []))
        iv_percentile = _iv_percentile_from_vix(history_by_symbol.get("^VIX", []))
        volume_anomaly = _market_volume_anomaly(history_by_symbol)
        volume_confidence = _market_volume_anomaly_confidence(history_by_symbol)
        volume_source = _market_volume_source(history_by_symbol)
        response.watch_symbols = _top_symbols(positions, limit=5)
        sentiment_bundle = self._market_sentiments(response.watch_symbols[:3])
        primary_sentiment = _primary_sentiment(sentiment_bundle)
        fear_greed = _number_or_none(primary_sentiment.get("value"), local_fear_greed)
        put_call_proxy = _put_call_crowding_proxy(fear_greed=fear_greed, vix=vix_value, breadth=breadth)
        crowding_confidence = _market_crowding_confidence(primary_sentiment=primary_sentiment, vix=vix_value, breadth=breadth)
        sentiment_source = str(primary_sentiment.get("source") or "portfolio_theme_and_breadth_proxy")
        sentiment_confidence = _sentiment_confidence(primary_sentiment, fallback=0.55)
        sentiment_reason = str(primary_sentiment.get("reason") or "external_sentiment_first_with_local_proxy_fallback")
        response.indicators["portfolio_weighted_change"] = _metric(
            value=round(weighted_change * 100, 2),
            unit="percent",
            source="portfolio_positions",
            status=AnalysisStatus.READY,
            confidence=0.85,
            reason="current_positions_weighted_daily_change",
        )
        response.indicators["breadth"] = _metric(
            value=round(breadth * 100, 2),
            unit="percent",
            source="portfolio_positions",
            status=AnalysisStatus.READY,
            confidence=0.8,
            reason="positive_position_count_share",
        )
        response.indicators["news_heat"] = _metric(
            value=fear_greed,
            unit="index",
            source=sentiment_source,
            status=AnalysisStatus.READY,
            confidence=sentiment_confidence,
            reason=sentiment_reason,
        )
        response.indicators["fear_greed"] = _metric(
            value=fear_greed,
            unit="index",
            source=sentiment_source,
            status=AnalysisStatus.READY,
            confidence=sentiment_confidence,
            reason=sentiment_reason,
        )
        response.indicators["iv_percentile"] = _metric(
            value=iv_percentile,
            unit="percent",
            source=f"{self._market_data_provider.name}:^VIX" if self._market_data_provider else "^VIX",
            status=AnalysisStatus.READY if iv_percentile is not None else AnalysisStatus.MISSING_DATA,
            confidence=0.72 if iv_percentile is not None else 0.0,
            reason="vix_90_day_percentile_proxy",
        )
        response.indicators["put_call_ratio"] = _metric(
            value=put_call_proxy,
            unit="ratio",
            source="market_crowding_proxy",
            status=AnalysisStatus.READY,
            confidence=crowding_confidence,
            reason="multi_signal_proxy_from_sentiment_vix_and_portfolio_breadth_not_actual_occ_put_call",
        )
        response.indicators["volume_anomaly"] = _metric(
            value=volume_anomaly,
            unit="ratio",
            source=f"{self._market_data_provider.name}:{volume_source}" if self._market_data_provider else volume_source,
            status=AnalysisStatus.READY if volume_anomaly is not None else AnalysisStatus.MISSING_DATA,
            confidence=volume_confidence,
            reason="latest_major_us_etf_volume_vs_20_day_average",
        )
        _attach_sentiment_indicators(response.indicators, sentiment_bundle, local_fear_greed)
        if ndx_rsi is not None:
            response.indicators["ndx_rsi"] = _metric(
                value=ndx_rsi,
                unit="index",
                source=f"{self._market_data_provider.name}:^NDX" if self._market_data_provider else "^NDX",
                status=AnalysisStatus.READY,
                confidence=0.72,
                reason="computed_from_NDX_daily_closes",
            )
        if vix_value is not None:
            response.indicators["vix"] = _metric(
                value=vix_value,
                unit="index",
                source=f"{self._market_data_provider.name}:^VIX" if self._market_data_provider else "^VIX",
                status=AnalysisStatus.READY,
                confidence=0.75,
                reason="latest_vix_close_from_market_data_provider",
            )
            response.indicators["vix_change"] = _metric(
                value=vix_change,
                unit="point",
                source=f"{self._market_data_provider.name}:^VIX" if self._market_data_provider else "^VIX",
                status=AnalysisStatus.READY,
                confidence=0.75,
                reason="latest_vix_daily_point_change",
            )
        if rsi is not None:
            regime_value, reasons, risks = _classify_regime(rsi, weighted_change, breadth, ai_weight)
            response.regime = _metric(
                value=regime_value,
                source=f"{self._market_data_provider.name}:{benchmark}" if self._market_data_provider else benchmark,
                status=AnalysisStatus.READY,
                confidence=0.7,
                reason=f"classified_from_{benchmark}_rsi_and_portfolio_breadth",
            )
            response.indicators["rsi"] = _metric(
                value=rsi,
                unit="index",
                source=f"{self._market_data_provider.name}:{benchmark}" if self._market_data_provider else benchmark,
                status=AnalysisStatus.READY,
                confidence=0.75,
                reason=f"computed_from_{benchmark}_daily_closes",
            )
            response.reasons = reasons
            response.risks = risks
        else:
            response.risks = ["市场基准 K 线不可用，当前只使用持仓日内变化估算组合受市场状态影响。"]
        response.status = AnalysisStatus.READY
        response.portfolio_impact = _market_portfolio_impact(positions, benchmark, rsi, weighted_change, breadth)
        response.opportunities = _market_opportunities(positions, weighted_change, breadth)
        response.market_pulse = _market_pulse_rows(
            benchmark=benchmark,
            history_by_symbol=history_by_symbol,
            rsi=rsi,
            ndx_rsi=ndx_rsi,
            fear_greed=fear_greed,
            local_fear_greed=local_fear_greed,
            regime=str(response.regime.value or "中性观察"),
            sentiment_bundle=sentiment_bundle,
            iv_percentile=iv_percentile,
            put_call_proxy=put_call_proxy,
            crowding_confidence=crowding_confidence,
            volume_anomaly=volume_anomaly,
            volume_confidence=volume_confidence,
            volume_source=volume_source,
        )
        response.playbook = _market_playbook_rows(fear_greed=fear_greed, rsi=rsi, vix=vix_value)
        response.strategy = _market_strategy_rows(
            regime=str(response.regime.value or "中性观察"),
            rsi=rsi,
            fear_greed=fear_greed,
            vix=vix_value,
            volume_anomaly=volume_anomaly,
            put_call_proxy=put_call_proxy,
        )
        response.charts = []
        return response

    def _market_histories(self, symbols: list[str], *, days: int) -> dict[str, list[MarketDataPoint]]:
        if self._market_data_provider is None:
            return {}
        histories: dict[str, list[MarketDataPoint]] = {}
        for symbol in symbols:
            try:
                histories[symbol] = self._market_data_provider.get_kline_history(symbol, days=days)
            except Exception:
                histories[symbol] = []
        return histories

    def _market_sentiments(self, watch_symbols: list[str]) -> dict[str, Any]:
        provider_name = str(getattr(self._market_data_provider, "name", "") or "")
        bundle: dict[str, Any] = {
            "cnn_fear_greed": fetch_cnn_fear_greed() if provider_name in {"longbridge", "futu_opend", "quote_fallback"} else {},
            "longbridge_market_temp": {},
            "longbridge_topics": [],
            "futu_market_heat": {},
            "futu_watchlist_heat": {},
        }
        if self._market_data_provider is not None:
            try:
                bundle["longbridge_market_temp"] = self._market_data_provider.get_sentiment("US_MARKET")
            except Exception as exc:
                bundle["longbridge_market_temp"] = {"status": "missing_data", "source": "longbridge_market_temp", "reason": str(exc)}
            for symbol in watch_symbols:
                try:
                    sentiment = self._market_data_provider.get_sentiment(symbol)
                except Exception as exc:
                    sentiment = {"status": "missing_data", "symbol": symbol, "source": "longbridge_topic", "reason": str(exc)}
                bundle["longbridge_topics"].append(sentiment)
        futu_bundle = self._futu_sentiments(watch_symbols)
        bundle.update(futu_bundle)
        return bundle

    def _futu_sentiments(self, watch_symbols: list[str]) -> dict[str, Any]:
        settings = self._settings_service.get()
        if settings.futu_connection_mode == "disabled":
            return {}
        provider = build_futu_opend_provider(settings)
        try:
            spy = provider.get_kline_history("SPY", days=30)
            qqq = provider.get_kline_history("QQQ", days=30)
        except Exception as exc:
            return {
                "futu_market_heat": {"status": "missing_data", "source": "futu_market_heat", "reason": str(exc)},
                "futu_watchlist_heat": {"status": "missing_data", "source": "futu_watchlist_heat", "reason": str(exc)},
            }
        market_heat = _futu_market_heat(spy, qqq)
        watch_histories: list[list[MarketDataPoint]] = []
        for symbol in watch_symbols[:5]:
            try:
                history = provider.get_kline_history(symbol, days=30)
            except Exception:
                history = []
            if history:
                watch_histories.append(history)
        return {
            "futu_market_heat": market_heat,
            "futu_watchlist_heat": _futu_watchlist_heat(watch_histories),
        }

    def _build_portfolio_section(self, positions: list[dict], *, refresh_ai: bool = False, allow_ai: bool = True):
        response = build_empty_portfolio_analysis_response(
            display_currency=self._settings_service.get().base_currency,
            valuation_mode="realtime" if self._settings_service.get().display_realtime_prices else "snapshot",
            ai_provider=self._settings_service.get().ai_provider,
        ).sections.portfolio
        if not positions:
            return response

        total = sum(abs(_position_value(position)) for position in positions)
        sector_rows = self._sector_rows(positions, total)
        top_position = max(positions, key=lambda row: abs(_position_value(row)))
        top_weight = 0.0 if total == 0 else abs(_position_value(top_position)) / total
        ai_weight = _ai_theme_weight(positions, total)
        factor_rows = _factor_rows(positions, total)
        theme_cluster_weight = _theme_cluster_weight(positions, total)
        top3_weight = _top_n_weight(positions, total, 3)
        tail_loss = _daily_loss_at_risk(positions)
        downside_breadth = _downside_breadth(positions)
        response.status = AnalysisStatus.READY
        response.concentration = {
            "sector": _metric(
                value=round(max((row["weight"] for row in sector_rows), default=0.0) * 100, 2),
                unit="percent",
                source="portfolio_positions",
                status=AnalysisStatus.READY,
                confidence=0.9,
                reason="largest_sector_weight",
            ),
            "single_name": _metric(
                value=round(top_weight * 100, 2),
                unit="percent",
                source="portfolio_positions",
                status=AnalysisStatus.READY,
                confidence=0.9,
                reason=f"largest_position_{str(top_position.get('symbol', '')).upper()}",
            ),
            "ai_theme": _metric(
                value=round(ai_weight * 100, 2),
                unit="percent",
                source="portfolio_positions",
                status=AnalysisStatus.READY,
                confidence=0.5,
                reason="symbol_and_industry_keyword_tagging",
            ),
        }
        response.factor_exposure = {
            row["key"]: _metric(
                value=round(row["weight"] * 100, 2),
                unit="percent",
                source="portfolio_positions",
                status=AnalysisStatus.READY,
                confidence=row["confidence"],
                reason=row["reason"],
            )
            for row in factor_rows
        }
        response.correlation = {
            "top3_weight": _metric(
                value=round(top3_weight * 100, 2),
                unit="percent",
                source="portfolio_positions",
                status=AnalysisStatus.READY,
                confidence=0.85,
                reason="largest_three_positions_weight",
            ),
            "theme_cluster": _metric(
                value=round(theme_cluster_weight * 100, 2),
                unit="percent",
                source="portfolio_positions",
                status=AnalysisStatus.READY,
                confidence=0.65,
                reason="rule_based_ai_semiconductor_ev_space_cluster",
            ),
        }
        response.tail_risk = {
            "daily_loss_at_risk": _metric(
                value=round(tail_loss, 2),
                unit=self._settings_service.get().base_currency,
                source="portfolio_positions",
                status=AnalysisStatus.READY,
                confidence=0.75,
                reason="sum_of_negative_current_snapshot_daily_changes",
            ),
            "downside_breadth": _metric(
                value=round(downside_breadth * 100, 2),
                unit="percent",
                source="portfolio_positions",
                status=AnalysisStatus.READY,
                confidence=0.8,
                reason="share_of_positions_with_negative_daily_change",
            ),
        }
        response.macro_sensitivity = _macro_sensitivity_metrics(positions, ai_weight)
        response.greeks = {}
        response.expiration_risk = {}
        response.hedge_suggestions = _hedge_suggestions(response, sector_rows)
        response.alerts = _risk_alerts(response, positions, sector_rows)
        response.advisor_facts = [
            _metric(
                value=len(positions),
                unit="只持仓",
                source="portfolio_positions",
                status=AnalysisStatus.READY,
                confidence=1.0,
                reason="current_position_count",
            ),
            _metric(
                value=round(total, 2),
                unit=self._settings_service.get().base_currency,
                source="portfolio_positions",
                status=AnalysisStatus.READY,
                confidence=0.85,
                reason="absolute_market_value_sum",
            ),
        ]
        response.risk_rows = _portfolio_risk_rows(
            positions,
            total,
            display_currency=self._settings_service.get().base_currency,
        )
        response.rebalance_advice = _portfolio_rebalance_advice(
            risk_rows=response.risk_rows,
            alerts=response.alerts,
            top3_weight=top3_weight,
            downside_breadth=downside_breadth,
        )
        if allow_ai:
            ai_overlay = self._portfolio_ai_overlay(
                positions=positions,
                risk_rows=response.risk_rows,
                alerts=response.alerts,
                total=total,
                top3_weight=top3_weight,
                downside_breadth=downside_breadth,
                refresh_ai=refresh_ai,
            )
            response.risk_rows, response.rebalance_advice = _apply_portfolio_ai_overlay(
                risk_rows=response.risk_rows,
                advice=response.rebalance_advice,
                overlay=ai_overlay,
            )
            response.analysis_meta = _portfolio_analysis_meta(
                rows=response.risk_rows,
                advice=response.rebalance_advice,
                ai_overlay=ai_overlay,
            )
            response.narrative = _portfolio_overlay_narrative(ai_overlay)
        else:
            response.analysis_meta = _portfolio_analysis_meta(
                rows=response.risk_rows,
                advice=response.rebalance_advice,
            )
        response.charts = self._portfolio_risk_charts(positions, total)
        return response

    def _portfolio_ai_overlay(
        self,
        *,
        positions: list[dict],
        risk_rows: list[PortfolioRiskRow],
        alerts: list[dict[str, Any]],
        total: float,
        top3_weight: float,
        downside_breadth: float,
        refresh_ai: bool,
    ) -> dict[str, Any]:
        provider = self._ai_provider()
        self._emit_progress("researching_web", {"message": "正在检索持仓风险证据", "total_positions": len(risk_rows)})
        cache_key = _portfolio_overlay_cache_key(risk_rows)
        metrics = _portfolio_overlay_metrics(
            positions=positions,
            risk_rows=risk_rows,
            alerts=alerts,
            total=total,
            top3_weight=top3_weight,
            downside_breadth=downside_breadth,
        )
        if provider.name != "mock" and not refresh_ai:
            overlay = self._ai_narrative_service.cached_portfolio_overlay_or_unavailable(
                provider=provider,
                cache_key=cache_key,
            )
            if overlay.get("reason") == "structured_ai_overlay_waiting_for_manual_refresh":
                persisted = self._load_persisted_portfolio_ai_overlay(provider=provider, cache_key=cache_key)
                if persisted is not None:
                    self._ai_narrative_service.cache_portfolio_overlay(
                        provider=provider,
                        cache_key=cache_key,
                        overlay=persisted,
                    )
                    overlay = persisted
            return _portfolio_overlay_with_local_fallback(provider=provider, metrics=metrics, overlay=overlay)
        overlay = self._ai_narrative_service.generate_portfolio_overlay(
            provider=provider,
            metrics=metrics,
            cache_key=cache_key,
            force=refresh_ai,
        )
        if overlay.get("status") == AnalysisStatus.READY.value:
            self._emit_progress("persisting", {"message": "正在校验并保存分析结果"})
            persisted = self._persist_portfolio_ai_overlay(provider=provider, cache_key=cache_key, overlay=overlay)
            if not persisted:
                overlay = self._ai_narrative_service.mark_portfolio_overlay_failed(
                    provider=provider,
                    cache_key=cache_key,
                    reason="portfolio_overlay_persistence_failed",
                )
        return _portfolio_overlay_with_local_fallback(provider=provider, metrics=metrics, overlay=overlay)

    def _load_persisted_portfolio_ai_overlay(self, *, provider: Any, cache_key: str) -> dict[str, Any] | None:
        if self._raw_repository is None or not hasattr(self._raw_repository, "es"):
            return None
        try:
            source = self._raw_repository.es.get(
                index=PORTFOLIO_AI_CACHE_INDEX,
                id=_portfolio_overlay_cache_doc_id(provider=provider, cache_key=cache_key),
            )["_source"]
        except Exception:
            return None
        overlay = None
        if isinstance(source, dict):
            overlay_json = source.get("overlay_json")
            if isinstance(overlay_json, str):
                try:
                    parsed = json.loads(overlay_json)
                    overlay = parsed if isinstance(parsed, dict) else None
                except (TypeError, ValueError):
                    overlay = None
            if overlay is None:
                overlay = source.get("overlay")
        if not isinstance(overlay, dict) or overlay.get("status") != AnalysisStatus.READY.value:
            return None
        return dict(overlay)

    def _persist_portfolio_ai_overlay(self, *, provider: Any, cache_key: str, overlay: dict[str, Any]) -> bool:
        if self._raw_repository is None or not hasattr(self._raw_repository, "es"):
            return True
        if (
            not isinstance(overlay, dict)
            or overlay.get("status") != AnalysisStatus.READY.value
            or overlay.get("provider") != getattr(provider, "name", None)
        ):
            return False
        try:
            self._raw_repository.es.update(
                index=PORTFOLIO_AI_CACHE_INDEX,
                id=_portfolio_overlay_cache_doc_id(provider=provider, cache_key=cache_key),
                doc={
                    "provider": getattr(provider, "name", ""),
                    "cache_key": cache_key,
                    "cache_date": date.today().isoformat(),
                    "updated_at": _now_iso(),
                    # Store the schema-evolving model payload as JSON so legacy
                    # Elasticsearch dynamic mappings cannot reject newer nested fields.
                    "overlay_json": json.dumps(overlay, ensure_ascii=False),
                },
                doc_as_upsert=True,
            )
        except Exception:
            return False
        return True

    def _portfolio_risk_charts(self, positions: list[dict], total: float) -> list[EChartsPayload]:
        return [_weight_change_scatter_chart(positions, total)]

    def _build_stock_section(self, positions: list[dict], symbol: str | None, *, refresh_ai: bool = False, allow_ai: bool = True):
        response = build_empty_portfolio_analysis_response(
            display_currency=self._settings_service.get().base_currency,
            valuation_mode="realtime" if self._settings_service.get().display_realtime_prices else "snapshot",
            symbol=symbol,
            ai_provider=self._settings_service.get().ai_provider,
        ).sections.stock
        total = sum(abs(_position_value(item)) for item in positions)
        response.available_symbols = _stock_selection_options(positions, total)
        if not positions:
            response.status = AnalysisStatus.MISSING_DATA
            response.memo = _stock_memo_unavailable(symbol=symbol, reason="no_current_holdings")
            return response
        if not symbol:
            response.status = AnalysisStatus.MISSING_DATA
            response.memo = _stock_memo_unavailable(symbol=None, reason="selected_symbol_required")
            return response
        position = next((item for item in positions if str(item.get("symbol", "")).upper() == symbol), None)
        if not position:
            response.status = AnalysisStatus.UNAVAILABLE
            response.memo = _stock_memo_unavailable(symbol=symbol, reason="selected_symbol_not_in_current_holdings")
            return response

        metrics = _stock_memo_metrics(
            selected_symbol=symbol,
            selected_position=position,
            positions=positions,
            total=total,
            display_currency=self._settings_service.get().base_currency,
            industry=self._position_industry(position),
        )
        if not allow_ai:
            response.memo = _local_stock_memo(metrics, reason="model_analysis_disabled")
            response.status = response.memo.status
            return response
        memo_payload = self._stock_ai_memo(metrics=metrics, symbol=symbol, refresh_ai=refresh_ai)
        response.memo = _coerce_stock_memo(memo_payload, metrics=metrics)
        response.status = response.memo.status
        response.narrative = _stock_memo_narrative(response.memo, provider=str(memo_payload.get("provider") or self._settings_service.get().ai_provider))
        return response

    def _stock_ai_memo(self, *, metrics: dict[str, Any], symbol: str, refresh_ai: bool) -> dict[str, Any]:
        provider = self._ai_provider()
        cache_key = _stock_memo_cache_key(metrics)
        if provider.name != "mock" and not refresh_ai:
            api_key = str(getattr(provider, "api_key", "") or "")
            if not api_key:
                return self._ai_narrative_service.generate_stock_memo(
                    provider=provider,
                    metrics=metrics,
                    cache_key=cache_key,
                )
            memo = self._ai_narrative_service.cached_stock_memo_or_pending(
                provider=provider,
                cache_key=cache_key,
            )
            if memo.get("reason") == "stock_memo_waiting_for_background_refresh":
                self._ai_narrative_service.mark_stock_memo_started(provider=provider, cache_key=cache_key)
                Thread(
                    target=self._refresh_stock_memo,
                    args=(provider, metrics, cache_key),
                    daemon=True,
                ).start()
                memo = self._ai_narrative_service.cached_stock_memo_or_pending(
                    provider=provider,
                    cache_key=cache_key,
                )
            memo.setdefault("symbol", symbol)
            return memo
        return self._ai_narrative_service.generate_stock_memo(
            provider=provider,
            metrics=metrics,
            cache_key=cache_key,
            force=refresh_ai,
        )

    def _refresh_stock_memo(self, provider: Any, metrics: dict[str, Any], cache_key: str) -> None:
        try:
            self._ai_narrative_service.generate_stock_memo(
                provider=provider,
                metrics=metrics,
                cache_key=cache_key,
                force=True,
            )
        except Exception:
            return

    def _current_positions(self) -> list[dict]:
        if self._raw_repository is None:
            return []
        latest = self._raw_repository.get_latest_account_snapshot()
        account_id = str((latest or {}).get("account_id", "") or "")
        report_date = str((latest or {}).get("report_date", "") or "")
        account_base_currency = normalize_currency_code((latest or {}).get("base_currency"))
        display_currency = normalize_currency_code(self._settings_service.get().base_currency)
        filters = {"account_id": account_id, "report_date": report_date} if account_id and report_date else None
        rows = self._raw_repository.es.search(
            index="ibkr_position_snapshots_v1",
            size=10000,
            term_filters=filters,
        )
        all_rows = self._raw_repository.es.search(index="ibkr_position_snapshots_v1", size=10000)
        if not rows:
            rows = all_rows
        if not rows:
            return []
        latest_date = max(str(row.get("report_date", "") or "") for row in rows)
        latest_rows = [row for row in rows if str(row.get("report_date", "") or "") == latest_date]
        summary_rows = [row for row in latest_rows if str(row.get("level_of_detail", "") or "").upper() == "SUMMARY"]
        current_rows = summary_rows or latest_rows
        enriched = self._with_industry_enrichment(
            _with_position_daily_changes(current_rows, all_rows or rows, latest_date)
        )
        return [
            convert_position_money_values(
                position,
                resolve_position_display_fx(
                    raw_repository=self._raw_repository,
                    position=position,
                    account_base_currency=account_base_currency,
                    display_currency=display_currency,
                ),
            )
            for position in enriched
        ]

    def _with_industry_enrichment(self, positions: list[dict]) -> list[dict]:
        if self._industry_mapping_service is None:
            return positions
        enriched: list[dict] = []
        for position in positions:
            item = dict(position)
            symbol = str(item.get("symbol", "") or "").upper()
            if symbol and not str(item.get("industry") or "").strip():
                industry = self._industry_mapping_service.get(symbol)
                if industry:
                    item["industry"] = industry
                    item["industry_source"] = "industry_mapping"
            enriched.append(item)
        return enriched

    def _sector_rows(self, positions: list[dict], total: float) -> list[dict]:
        grouped: dict[str, float] = {}
        for position in positions:
            industry = self._position_industry(position)
            grouped[industry] = grouped.get(industry, 0.0) + abs(_position_value(position))
        rows = [
            {"industry": industry, "market_value": value, "weight": 0.0 if total == 0 else value / total}
            for industry, value in grouped.items()
        ]
        rows.sort(key=lambda row: row["market_value"], reverse=True)
        return rows

    def _position_industry(self, position: dict) -> str:
        symbol = str(position.get("symbol", "") or "").upper()
        industry = str(position.get("industry") or "")
        if not industry and self._industry_mapping_service is not None:
            industry = self._industry_mapping_service.get(symbol) or ""
        return industry or "未知行业"

    def _greek_metrics(self, positions: list[dict]) -> dict[str, StandardMetric]:
        result: dict[str, StandardMetric] = {}
        for greek in ("delta", "gamma", "theta", "vega"):
            values = [_to_float(position.get(greek)) for position in positions if str(position.get(greek, "") or "") != ""]
            if values:
                result[greek] = _metric(
                    value=round(sum(values), 4),
                    source="portfolio_positions",
                    status=AnalysisStatus.READY,
                    confidence=0.75,
                    reason="aggregated_from_position_greeks",
                )
            else:
                result[greek] = missing_metric(source="portfolio_positions", reason="missing_greeks")
        return result

    def _expiration_metrics(self, positions: list[dict]) -> dict[str, StandardMetric]:
        expiries = [_parse_yyyymmdd(str(position.get("expiry", "") or "")) for position in positions]
        expiries = [expiry for expiry in expiries if expiry is not None]
        today = date.today()
        count_7 = sum(1 for expiry in expiries if 0 <= (expiry - today).days <= 7)
        count_30 = sum(1 for expiry in expiries if 0 <= (expiry - today).days <= 30)
        status = AnalysisStatus.READY if expiries else AnalysisStatus.MISSING_DATA
        reason = "computed_from_option_expiry" if expiries else "option_expiry_data_not_available"
        return {
            "next_7_days": _metric(value=count_7 if expiries else None, unit="contracts", source="portfolio_positions", status=status, confidence=0.8 if expiries else 0.0, reason=reason),
            "next_30_days": _metric(value=count_30 if expiries else None, unit="contracts", source="portfolio_positions", status=status, confidence=0.8 if expiries else 0.0, reason=reason),
        }

    def _narrative(
        self,
        section: str,
        metrics: dict[str, Any],
        cache_key: str = "default",
        *,
        refresh_ai: bool = False,
    ) -> AINarrativePayload:
        provider = self._ai_provider()
        if not refresh_ai and provider.name != "mock":
            return self._ai_narrative_service.cached_or_pending(
                provider=provider,
                section=section,
                cache_key=cache_key,
            )
        return self._ai_narrative_service.generate(
            provider=provider,
            section=section,
            metrics=metrics,
            cache_key=cache_key,
            force=refresh_ai,
        )

    def _ai_provider(self):
        settings = self._settings_service.get()
        return build_ai_provider(
            provider_name=settings.ai_provider,
            openai_api_key=settings.openai_api_key,
            ai_model=settings.ai_model,
            minimax_api_key=settings.minimax_api_key,
            minimax_base_url=settings.minimax_base_url,
            deepseek_api_key=settings.deepseek_api_key,
            deepseek_base_url=settings.deepseek_base_url,
            tavily_api_key=settings.tavily_api_key,
            progress_callback=self._progress_callback,
        )

    def _emit_progress(self, stage: str, details: dict[str, Any] | None = None) -> None:
        if self._progress_callback is not None:
            self._progress_callback(stage, details or {})

    def _resolve_stock_symbol(self, symbol: str | None) -> str | None:
        normalized_symbol = symbol.upper() if symbol else None
        if normalized_symbol:
            return normalized_symbol
        positions = self._current_positions()
        if not positions:
            return None
        return str(positions[0].get("symbol", "") or "").upper() or None


def _metric(
    *,
    value: float | int | str | bool | None,
    source: str,
    status: AnalysisStatus,
    unit: str | None = None,
    as_of: str | None = None,
    confidence: float | None = None,
    reason: str | None = None,
) -> StandardMetric:
    return StandardMetric(
        value=value,
        unit=unit,
        source=source,
        as_of=as_of,
        confidence=confidence,
        status=status,
        reason=reason,
    )


def _position_value(position: dict) -> float:
    return _to_float(position.get("market_value_snapshot", position.get("position_value", 0)))


def _position_key(position: dict) -> str:
    asset = str(position.get("asset_category") or "STK").upper()
    symbol = str(position.get("symbol") or "").upper()
    if asset in {"OPT", "FOP"} or position.get("expiry") or position.get("put_call"):
        expiry = str(position.get("expiry") or "-")
        strike = str(position.get("strike") or "-")
        put_call = str(position.get("put_call") or "-").upper()
        return f"{asset}:{symbol}:{expiry}:{strike}:{put_call}"
    return f"{asset}:{symbol}"


def _positions_total(positions: list[dict]) -> float:
    return sum(abs(_position_value(position)) for position in positions)


def _stock_selection_options(positions: list[dict], total: float) -> list[StockSelectionOption]:
    options: list[StockSelectionOption] = []
    for position in sorted(positions, key=lambda item: abs(_position_value(item)), reverse=True):
        symbol = str(position.get("symbol", "") or "").upper()
        if not symbol:
            continue
        market_value = _optional_float(position.get("market_value_snapshot", position.get("position_value")))
        weight_pct = round(_position_weight(position, total) * 100, 2)
        options.append(
            StockSelectionOption(
                symbol=symbol,
                label=f"{symbol} · {weight_pct:.2f}%",
                weight_pct=weight_pct,
                market_value=None if market_value is None else round(market_value, 2),
                quantity=_optional_float(position.get("quantity")),
            )
        )
    return options


def _stock_memo_metrics(
    *,
    selected_symbol: str,
    selected_position: dict,
    positions: list[dict],
    total: float,
    display_currency: str,
    industry: str,
) -> dict[str, Any]:
    return {
        "selected_symbol": selected_symbol,
        "selected_position": _stock_holding_row(selected_position, total=total, display_currency=display_currency, industry=industry),
        "current_holdings": [
            _stock_holding_row(position, total=total, display_currency=display_currency, industry=str(position.get("industry") or ""))
            for position in sorted(positions, key=lambda item: abs(_position_value(item)), reverse=True)
        ],
        "prompt_version": "stock_research_memo_v1",
        "analysis_boundary": "read_only_no_trade_execution",
        "missing_external_data_policy": "write_missing_or_unable_to_judge_do_not_invent",
    }


def _stock_holding_row(position: dict, *, total: float, display_currency: str, industry: str) -> dict[str, Any]:
    return {
        "symbol": str(position.get("symbol", "") or "").upper(),
        "report_date": str(position.get("report_date") or ""),
        "currency": display_currency,
        "industry": industry or str(position.get("industry") or ""),
        "weight_pct": round(_position_weight(position, total) * 100, 2),
        "market_value": _optional_float(position.get("market_value_snapshot", position.get("position_value"))),
        "current_price": _optional_float(position.get("mark_price_snapshot")),
        "avg_cost": _optional_float(position.get("cost_basis_price") or position.get("avg_cost")),
        "position_qty": _optional_float(position.get("quantity")),
        "unrealized_pnl": _optional_float(position.get("unrealized_pnl_snapshot")),
        "daily_change_pct": round(_to_float(position.get("daily_change_pct")) * 100, 2),
    }


def _stock_memo_cache_key(metrics: dict[str, Any]) -> str:
    selected = metrics.get("selected_position") if isinstance(metrics.get("selected_position"), dict) else {}
    return ":".join(
        str(selected.get(key, ""))
        for key in ("symbol", "report_date", "weight_pct", "market_value", "current_price", "unrealized_pnl")
    )


def _coerce_stock_memo(payload: dict[str, Any], *, metrics: dict[str, Any]) -> StockResearchMemo:
    status = _analysis_status(str(payload.get("status") or "ready"))
    if status == AnalysisStatus.PENDING:
        return StockResearchMemo(
            status=AnalysisStatus.PENDING,
            symbol=str(payload.get("symbol") or metrics.get("selected_symbol") or "").upper() or None,
            source=_stock_memo_source(payload),
            as_of=str(payload.get("as_of") or _now_iso()),
            confidence=0.0,
            reason=str(payload.get("reason") or "stock_memo_pending"),
        )
    if status != AnalysisStatus.READY:
        reason = str(payload.get("reason") or "stock_memo_unavailable")
        if isinstance(metrics.get("selected_position"), dict):
            return _local_stock_memo(metrics, reason=reason)
        return _stock_memo_unavailable(symbol=str(metrics.get("selected_symbol") or "") or None, reason=reason)

    return StockResearchMemo(
        status=AnalysisStatus.READY,
        symbol=str(payload.get("symbol") or metrics.get("selected_symbol") or "").upper() or None,
        one_line_view=_clean_memo_text(payload.get("one_line_view")),
        position_role=_choice(payload.get("position_role"), {"核心仓", "卫星仓", "观察仓", "待复核仓"}, "待复核仓"),
        logic_status=_choice(payload.get("logic_status"), {"增强", "维持", "削弱", "无法判断"}, "无法判断"),
        ai_relevance=_choice(payload.get("ai_relevance"), {"极高", "高", "中", "低", "无", "无法判断"}, "无法判断"),
        holding_thesis=_memo_list(payload.get("holding_thesis")),
        facts=_memo_list(payload.get("facts")),
        inferences=_memo_list(payload.get("inferences")),
        portfolio_impact=_memo_list(payload.get("portfolio_impact")),
        key_risks=_memo_list(payload.get("key_risks")),
        tracking_questions=_memo_list(payload.get("tracking_questions")),
        invalidation_signals=_memo_list(payload.get("invalidation_signals")),
        read_only_suggestion=_clean_memo_text(payload.get("read_only_suggestion")),
        source=_stock_memo_source(payload),
        as_of=str(payload.get("as_of") or _now_iso()),
        confidence=_clamp_confidence(payload.get("confidence")),
        reason=_clean_memo_text(payload.get("reason")),
    )


def _local_stock_memo(metrics: dict[str, Any], *, reason: str) -> StockResearchMemo:
    selected = metrics.get("selected_position") if isinstance(metrics.get("selected_position"), dict) else {}
    symbol = str(metrics.get("selected_symbol") or selected.get("symbol") or "").upper()
    weight_pct = _to_float(selected.get("weight_pct"))
    daily_change_pct = _to_float(selected.get("daily_change_pct"))
    unrealized = _optional_float(selected.get("unrealized_pnl"))
    industry = str(selected.get("industry") or "未标注行业")
    ai_relevance = _stock_ai_relevance_label(symbol=symbol, industry=industry)
    role = _stock_role(weight_pct=weight_pct, ai_relevance=ai_relevance)
    pnl_text = "缺失" if unrealized is None else f"{unrealized:.2f}"
    return StockResearchMemo(
        status=AnalysisStatus.READY,
        symbol=symbol or None,
        one_line_view=f"{symbol} 当前按{role}复核，核心逻辑仍需外部证据验证。" if symbol else None,
        position_role=role,
        logic_status="削弱" if daily_change_pct <= -5 else "无法判断",
        ai_relevance=ai_relevance,
        holding_thesis=[
            f"组合权重约 {weight_pct:.2f}%，需要按其对组合波动的实际贡献评估。",
            f"行业标记为 {industry}，主题相关性只能作为初步分类。",
            "当前缺少新闻、财报、订单和估值输入，不能确认基本面是否增强。",
        ],
        facts=[
            f"组合权重约 {weight_pct:.2f}%。",
            f"当日涨跌约 {daily_change_pct:.2f}%。",
            f"未实现盈亏约 {pnl_text}。",
        ],
        inferences=[
            "仓位越高，个股判断错误对组合的影响越大。",
            "单日涨跌只能提示复核优先级，不能替代财报和行业证据。",
        ],
        portfolio_impact=[
            "该标的通过权重直接影响组合回撤和反弹弹性。",
            "如果同主题持仓较多，需要合并评估主题集中度。",
        ],
        key_risks=[
            "外部基本面数据缺失，逻辑强弱无法充分验证。",
            "估值和业绩兑现阶段未知。",
            "组合集中度可能放大单一标的波动。",
        ],
        tracking_questions=[
            "最近财报和管理层指引是否支持继续持有的核心逻辑？",
            "同行订单、价格和资本开支是否验证同一趋势？",
            "当前估值是否已经提前反映主要利好？",
        ],
        invalidation_signals=[
            "收入、毛利率或订单证据连续转弱。",
            "行业景气度下降且公司没有相对优势证据。",
            "仓位风险高于可验证的基本面强度。",
        ],
        read_only_suggestion="只读建议：先补齐财报、新闻、估值和同行证据，再复核该标的在组合中的角色。",
        source="local_rules:stock_memo",
        as_of=_now_iso(),
        confidence=0.45,
        reason=f"fallback_after_{reason}",
    )


def _stock_memo_unavailable(*, symbol: str | None, reason: str) -> StockResearchMemo:
    return StockResearchMemo(
        status=AnalysisStatus.UNAVAILABLE,
        symbol=symbol,
        source="portfolio_positions",
        as_of=_now_iso(),
        confidence=0.0,
        reason=reason,
    )


def _stock_memo_narrative(memo: StockResearchMemo, *, provider: str) -> AINarrativePayload:
    return AINarrativePayload(
        provider=provider,
        model=None,
        status=memo.status,
        summary=memo.one_line_view,
        bullets=[*memo.holding_thesis[:3], *memo.portfolio_impact[:2]],
        risks=memo.key_risks,
        source_metrics=["selected_position", "current_holdings"],
        as_of=memo.as_of,
        confidence=memo.confidence,
        reason=memo.reason,
    )


def _stock_memo_source(payload: dict[str, Any]) -> str:
    provider = str(payload.get("provider") or "ai_provider")
    model = str(payload.get("model") or "").strip()
    return f"{provider}:{model}:stock_memo" if model else f"{provider}:stock_memo"


def _analysis_status(value: str) -> AnalysisStatus:
    try:
        return AnalysisStatus(value)
    except ValueError:
        return AnalysisStatus.READY


def _choice(value: object, allowed: set[str], default: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else default


def _memo_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_cleaned for item in value if (_cleaned := _clean_memo_text(item))][:4]


def _clean_memo_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _clamp_confidence(value: object) -> float:
    parsed = _to_float(value)
    return max(0.0, min(1.0, parsed))


def _stock_ai_relevance_label(*, symbol: str, industry: str) -> str:
    relevance, score = _ai_relevance({"symbol": symbol, "industry": industry}, {})
    if score >= 0.85:
        return "极高"
    if score >= 0.6:
        return "高"
    if score >= 0.3:
        return "中"
    return "无" if relevance == "无" else "低"


def _stock_role(*, weight_pct: float, ai_relevance: str) -> str:
    if weight_pct >= 18 and ai_relevance in {"极高", "高"}:
        return "核心仓"
    if weight_pct >= 8:
        return "卫星仓"
    if weight_pct >= 2:
        return "观察仓"
    return "待复核仓"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _largest_position_symbol(positions: list[dict]) -> str | None:
    if not positions:
        return None
    largest = max(positions, key=lambda row: abs(_position_value(row)))
    return str(largest.get("symbol", "") or "").upper() or None


def _top_symbols(positions: list[dict], *, limit: int) -> list[str]:
    rows = sorted(positions, key=lambda row: abs(_position_value(row)), reverse=True)
    return [str(row.get("symbol", "") or "").upper() for row in rows[:limit] if row.get("symbol")]


def _top_position_rows(positions: list[dict], total: float, *, limit: int) -> list[dict[str, Any]]:
    rows = sorted(positions, key=lambda row: abs(_position_value(row)), reverse=True)
    return [
        {
            "symbol": str(row.get("symbol", "") or "").upper(),
            "industry": str(row.get("industry") or "未知行业"),
            "weight": round((abs(_position_value(row)) / total) * 100, 2) if total else 0.0,
            "daily_change_pct": round(_to_float(row.get("daily_change_pct")) * 100, 2),
            "market_value": round(abs(_position_value(row)), 2),
        }
        for row in rows[:limit]
    ]


def _portfolio_risk_rows(
    positions: list[dict],
    total: float,
    *,
    display_currency: str,
) -> list[PortfolioRiskRow]:
    rows = sorted(positions, key=lambda row: abs(_position_value(row)), reverse=True)
    return [
        _portfolio_risk_row(
            position,
            total,
            display_currency=display_currency,
        )
        for position in rows
        if str(position.get("symbol", "") or "").strip()
    ]


def _portfolio_risk_row(
    position: dict,
    total: float,
    *,
    display_currency: str,
) -> PortfolioRiskRow:
    symbol = str(position.get("symbol", "") or "").upper()
    weight_pct = round(_position_weight(position, total) * 100, 2)
    daily_change_pct = _to_float(position.get("daily_change_pct")) * 100
    unrealized = _optional_float(position.get("unrealized_pnl_snapshot"))
    current_price = _optional_float(position.get("mark_price_snapshot"))
    logic_status = _logic_status(position, weight_pct=weight_pct)
    recommendation = _risk_recommendation(
        position,
        weight_pct=weight_pct,
        logic_status=logic_status,
    )
    return PortfolioRiskRow(
        position_key=_position_key(position),
        symbol=symbol,
        current_price=None if current_price is None else round(current_price, 2),
        weight_pct=weight_pct,
        unrealized_pnl=None if unrealized is None else round(unrealized, 2),
        logic_status=logic_status,
        recommendation=recommendation,
        risk_points=_local_risk_points(
            symbol=symbol,
            weight_pct=weight_pct,
            daily_change_pct=daily_change_pct,
            unrealized=unrealized,
            display_currency=display_currency,
        ),
        tracking_points=_local_tracking_points(symbol=symbol, weight_pct=weight_pct),
        sources=[],
        research_status="missing",
        status=AnalysisStatus.READY,
        confidence=0.5,
        source="portfolio_positions+portfolio_risk_rules",
        as_of=str(position.get("report_date") or date.today().isoformat()),
        reason="local_rules_without_live_research",
    )


def _ai_relevance(position: dict, external_context: dict[str, Any]) -> tuple[str, float]:
    symbol = str(position.get("symbol", "") or "").upper()
    industry = str(position.get("industry", "") or "").upper()
    if symbol in {"NVDA", "MU", "AMD", "AVGO", "ASML", "TSM", "SMCI"}:
        return ("极高（AI算力/存储核心）", 0.95)
    if symbol in {"LITE", "COHR"} or "光" in industry or "OPTICAL" in industry:
        return ("极高（光模块/网络）", 0.88)
    if symbol in AI_SYMBOL_HINTS or "AI" in industry or "SEMICONDUCTOR" in industry or "半导体" in industry:
        return ("中（AI主题受益）", 0.68)
    sentiment = (external_context.get("sentiments") or {}).get(symbol, {})
    if isinstance(sentiment, dict) and sentiment.get("status") == "ready" and _number_or_none(sentiment.get("value"), 0.0) >= 60:
        return ("低（外部热度相关）", 0.38)
    return ("无", 0.05)


def _logic_status(position: dict, *, weight_pct: float) -> str:
    daily_change_pct = _to_float(position.get("daily_change_pct")) * 100
    unrealized = _to_float(position.get("unrealized_pnl_snapshot"))
    if daily_change_pct <= -5:
        return "今日明显承压，需区分市场拖累和基本面变化"
    if weight_pct >= 15:
        return "单票权重较高，持仓逻辑需要更强证据支撑"
    if unrealized < 0:
        return "持续亏损，逻辑需重新审视"
    return "持仓逻辑未触发异常，继续跟踪关键证据"


def _risk_recommendation(
    position: dict,
    *,
    weight_pct: float,
    logic_status: str,
) -> str:
    daily_change_pct = _to_float(position.get("daily_change_pct")) * 100
    if weight_pct >= 18:
        return "先复核集中度和最新基本面证据，不扩大风险暴露"
    if daily_change_pct <= -5:
        return "维持观察，今日下跌后优先核实是否只是市场拖累"
    if "重新确认" in logic_status or "持续亏损" in logic_status:
        return "暂不加仓，确认逻辑仍成立后再评估"
    return "维持观察，持续跟踪财务、竞争和估值证据"


def _local_risk_points(
    *,
    symbol: str,
    weight_pct: float,
    daily_change_pct: float,
    unrealized: float | None,
    display_currency: str,
) -> list[dict[str, Any]]:
    concentration = "high" if weight_pct >= 18 else "medium" if weight_pct >= 10 else "low"
    pnl_detail = "未实现盈亏数据缺失" if unrealized is None else f"未实现盈亏约 {display_currency} {round(unrealized, 2)}"
    return [
        {
            "severity": concentration,
            "title": "单票集中度",
            "detail": f"{symbol} 组合权重约 {weight_pct}%，权重越高越需要持续验证持仓逻辑。",
            "evidence_ids": [],
        },
        {
            "severity": "medium" if daily_change_pct <= -5 or (unrealized or 0) < 0 else "low",
            "title": "价格与盈亏压力",
            "detail": f"当日涨跌约 {round(daily_change_pct, 2)}%；{pnl_detail}。价格变化不能替代基本面判断。",
            "evidence_ids": [],
        },
        {
            "severity": "medium",
            "title": "实时证据缺口",
            "detail": "本地规则尚未完成联网研究，财报、公告、估值和竞争变化需要模型检索后确认。",
            "evidence_ids": [],
        },
    ]


def _local_tracking_points(*, symbol: str, weight_pct: float) -> list[dict[str, Any]]:
    return [
        {
            "item": f"{symbol} 最新财报与公司指引",
            "why": "收入、利润率和指引决定持仓逻辑是否仍被经营证据支持。",
            "trigger": "指引下调、利润率恶化或关键业务增长明显放缓。",
            "horizon": "quarterly",
            "evidence_ids": [],
        },
        {
            "item": "行业竞争与政策变化",
            "why": "竞争格局和政策可能改变盈利能力与估值中枢。",
            "trigger": "主要竞争对手提速、价格压力扩大或监管约束升级。",
            "horizon": "30d",
            "evidence_ids": [],
        },
        {
            "item": "组合集中度",
            "why": f"当前权重约 {weight_pct}%，需要与证据强度匹配。",
            "trigger": "权重继续上升但没有新增基本面证据。",
            "horizon": "7d",
            "evidence_ids": [],
        },
    ]


def _portfolio_rebalance_advice(
    *,
    risk_rows: list[PortfolioRiskRow],
    alerts: list[dict[str, Any]],
    top3_weight: float,
    downside_breadth: float,
) -> PortfolioRebalanceAdvice:
    crowded = [row for row in risk_rows if row.weight_pct >= 12][:4]
    weak = [
        row for row in risk_rows
        if "重新确认" in row.logic_status or (row.unrealized_pnl is not None and row.unrealized_pnl < 0)
    ][:4]
    top = risk_rows[0] if risk_rows else None
    first_risk = f"{top.symbol} 权重约 {top.weight_pct}%，优先核实集中度与逻辑证据。" if top else "暂无持仓风险。"
    review = "、".join(row.symbol for row in weak[:3]) or "暂无单独需要优先复核的持仓"
    structure = f"前三大持仓合计约 {round(top3_weight * 100, 2)}%，先控制集中度和相关性风险。"
    tracking = "未来30天逐项跟踪财报、公司指引、竞争格局、政策和估值变化。"
    action_today = _today_action(risk_rows, alerts, downside_breadth)
    thinking_prompt = _thinking_prompt(weak, crowded)
    cards = [
        {"rank": "01", "icon": "alert", "title": "组合首要风险", "body": first_risk},
        {"rank": "02", "icon": "search", "title": "优先复核持仓", "body": review},
        {"rank": "03", "icon": "compass", "title": "组合结构与集中度", "body": structure},
        {"rank": "04", "icon": "calendar", "title": "未来30天跟踪清单", "body": tracking},
    ]
    return PortfolioRebalanceAdvice(
        cards=cards,
        action_today=action_today,
        thinking_prompt=thinking_prompt,
        status=AnalysisStatus.READY if risk_rows else AnalysisStatus.MISSING_DATA,
        source="portfolio_positions+portfolio_risk_rules",
        as_of=date.today().isoformat(),
        confidence=0.5 if risk_rows else 0.0,
        reason="local_portfolio_risk_rules_without_live_research",
    )


def _today_action(risk_rows: list[PortfolioRiskRow], alerts: list[dict[str, Any]], downside_breadth: float) -> str:
    pressure = [row for row in risk_rows if "今日明显承压" in row.logic_status]
    high_alerts = [alert for alert in alerts if alert.get("severity") == "high"]
    if pressure:
        symbols = "、".join(row.symbol for row in pressure[:3])
        return f"{symbols} 今日承压，先核实是否有基本面新证据；没有证据前不把下跌直接当作加仓信号。"
    if high_alerts:
        return "组合已触发高优先级集中风险，今日最重要的是控制同主题继续叠加。"
    if downside_breadth >= 0.5:
        return "下跌广度扩散，优先判断是市场拖累还是持仓逻辑同时变弱。"
    return "今日没有必须立刻处理的风险，维持跟踪并等待新证据。"


def _thinking_prompt(weak: list[PortfolioRiskRow], crowded: list[PortfolioRiskRow]) -> str:
    if weak:
        symbols = "、".join(row.symbol for row in weak[:3])
        return f"{symbols} 的买入逻辑需要重新确认：它们是否仍有独立基本面证据，而不是只跟随主题上涨。"
    if crowded:
        symbols = "、".join(row.symbol for row in crowded[:3])
        return f"{symbols} 是当前组合波动的主要来源，需要确认这些仓位是否仍值得占用风险预算。"
    return "当前组合没有单一必须重审的问题，继续按证据而非情绪调整。"


def _portfolio_analysis_meta(
    *,
    rows: list[PortfolioRiskRow],
    advice: PortfolioRebalanceAdvice,
    ai_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    overlay = ai_overlay if isinstance(ai_overlay, dict) else {}
    researched = [row for row in rows if row.research_status == "ready" and row.sources]
    missing = [row.position_key for row in rows if row.research_status != "ready" or not row.sources]
    result = {
        "status": advice.status.value,
        "source": advice.source,
        "confidence": advice.confidence,
        "as_of": advice.as_of,
        "risk_row_count": len(rows),
        "external_provider": "tavily" if researched else None,
        "external_ready": bool(rows) and len(researched) == len(rows),
        "researched_position_count": len(researched),
        "research_source_count": sum(len(row.sources) for row in researched),
        "missing_reasons": [f"{key}:research_missing" for key in missing[:8]],
    }
    if overlay:
        result.update(
            {
                "ai_overlay_status": overlay.get("status") or AnalysisStatus.UNAVAILABLE.value,
                "ai_overlay_provider": overlay.get("provider"),
                "ai_overlay_model": overlay.get("model"),
                "ai_overlay_reason": overlay.get("reason"),
            }
        )
    return result


def _portfolio_overlay_metrics(
    *,
    positions: list[dict],
    risk_rows: list[PortfolioRiskRow],
    alerts: list[dict[str, Any]],
    total: float,
    top3_weight: float,
    downside_breadth: float,
) -> dict[str, Any]:
    positions_by_key = {_position_key(row): row for row in positions}
    return {
        "portfolio": {
            "total_market_value": round(total, 2),
            "position_count": len(risk_rows),
            "top3_weight_pct": round(top3_weight * 100, 2),
            "downside_breadth_pct": round(downside_breadth * 100, 2),
            "display_currency": next((str(row.get("display_currency")) for row in positions if row.get("display_currency")), "USD"),
            "as_of": max((str(row.get("report_date") or "") for row in positions), default=""),
        },
        "positions": [
            _risk_row_for_overlay(row, positions_by_key.get(row.position_key, {}))
            for row in risk_rows
        ],
        "portfolio_alerts": [
            {
                "severity": alert.get("severity"),
                "title": alert.get("title"),
                "detail": alert.get("detail"),
            }
            for alert in alerts[:8]
            if isinstance(alert, dict)
        ],
        "policy": {
            "read_only": True,
            "no_order_quantity": True,
            "require_live_sources": True,
            "do_not_invent_missing_facts": True,
        },
    }


def _risk_row_for_overlay(row: PortfolioRiskRow, position: dict) -> dict[str, Any]:
    return {
        "position_key": row.position_key,
        "symbol": row.symbol,
        "asset_category": str(position.get("asset_category") or "STK").upper(),
        "expiry": position.get("expiry") or None,
        "strike": _optional_float(position.get("strike")) if position else None,
        "put_call": str(position.get("put_call") or "").upper() or None,
        "current_price": row.current_price,
        "weight_pct": row.weight_pct,
        "unrealized_pnl": row.unrealized_pnl,
        "average_cost": _optional_float(
            position.get("average_cost_price")
            or position.get("cost_basis_price")
            or position.get("cost_price_moving_weighted")
            or position.get("cost_price_adjusted")
        ) if position else None,
        "quantity": _optional_float(position.get("quantity")) if position else None,
        "daily_change_pct": round(_position_daily_change_pct(position) * 100, 2) if position else None,
        "industry": str(position.get("industry") or "") if position else "",
        "market_value": round(abs(_position_value(position)), 2) if position else None,
    }


def _portfolio_overlay_cache_key(risk_rows: list[PortfolioRiskRow]) -> str:
    chunks = [
        f"{row.position_key}:{round(row.weight_pct, 2)}:{round(row.unrealized_pnl or 0, 2)}"
        for row in risk_rows
    ]
    return f"v4:deepseek_tavily_full_portfolio:{'|'.join(chunks) or 'empty'}"


def _portfolio_overlay_cache_doc_id(*, provider: Any, cache_key: str) -> str:
    return f"{date.today().isoformat()}:{getattr(provider, 'name', 'ai_provider')}:{cache_key}"


def _portfolio_overlay_with_local_fallback(
    *,
    provider: Any,
    metrics: dict[str, Any],
    overlay: dict[str, Any],
) -> dict[str, Any]:
    status = overlay.get("status")
    if status not in {AnalysisStatus.ERROR.value, AnalysisStatus.UNAVAILABLE.value}:
        return overlay
    reason = str(overlay.get("reason") or f"{getattr(provider, 'name', 'ai_provider')}_structured_overlay_unavailable")
    if reason == "structured_ai_overlay_waiting_for_manual_refresh":
        return overlay
    fallback = MockAIProvider().generate_portfolio_overlay(metrics=metrics)
    fallback["status"] = AnalysisStatus.READY.value
    fallback["provider"] = "local_rules"
    fallback["model"] = None
    fallback["as_of"] = _now_iso()
    fallback["reason"] = f"fallback_after_{reason}"
    return fallback


def _portfolio_overlay_narrative(overlay: dict[str, Any]) -> AINarrativePayload:
    provider = str(overlay.get("provider") or "local_rules")
    model = overlay.get("model")
    status = overlay.get("status")
    reason = overlay.get("reason")
    if status == AnalysisStatus.READY.value:
        return AINarrativePayload(
            provider=provider,
            model=str(model) if model else None,
            status=AnalysisStatus.READY,
            summary="结构化持仓分析已生成。",
            bullets=[],
            risks=[],
            source_metrics=["portfolio_overlay"],
            as_of=str(overlay.get("as_of")) if overlay.get("as_of") else _now_iso(),
            confidence=_bounded_confidence(overlay.get("confidence"), fallback=0.0),
        )
    return AINarrativePayload(
        provider=provider,
        model=str(model) if model else None,
        status=AnalysisStatus.UNAVAILABLE,
        bullets=[],
        risks=[],
        source_metrics=[],
        as_of=str(overlay.get("as_of")) if overlay.get("as_of") else _now_iso(),
        confidence=0.0,
        reason=str(reason or "structured_ai_overlay_waiting_for_manual_refresh"),
    )


def _apply_portfolio_ai_overlay(
    *,
    risk_rows: list[PortfolioRiskRow],
    advice: PortfolioRebalanceAdvice,
    overlay: dict[str, Any] | None,
) -> tuple[list[PortfolioRiskRow], PortfolioRebalanceAdvice]:
    if not isinstance(overlay, dict) or overlay.get("status") != AnalysisStatus.READY.value:
        return risk_rows, advice
    provider = str(overlay.get("provider") or "structured_ai")
    source_prefix = "deepseek_tavily_web_research" if provider == "deepseek" else f"{provider}_structured_ai"
    raw_rows = overlay.get("risk_rows")
    if not isinstance(raw_rows, list):
        return risk_rows, advice
    keys = [str(row.get("position_key") or "") for row in raw_rows if isinstance(row, dict)]
    expected = [row.position_key for row in risk_rows]
    if len(keys) != len(raw_rows) or len(set(keys)) != len(keys) or set(keys) != set(expected):
        return risk_rows, advice
    overlay_rows = {str(row["position_key"]): row for row in raw_rows if isinstance(row, dict)}
    try:
        merged_rows = [
            _merge_portfolio_risk_overlay(row, overlay_rows[row.position_key], source_prefix=source_prefix)
            for row in risk_rows
        ]
        merged_advice = _merge_rebalance_advice_overlay(
            advice,
            overlay.get("rebalance_advice"),
            source_prefix=source_prefix,
        )
    except (TypeError, ValueError):
        return risk_rows, advice
    return merged_rows, merged_advice


def _merge_portfolio_risk_overlay(
    row: PortfolioRiskRow,
    overlay_row: dict[str, Any] | None,
    *,
    source_prefix: str,
) -> PortfolioRiskRow:
    if not isinstance(overlay_row, dict):
        return row
    data = row.model_dump(mode="json")
    logic_status = _clean_overlay_text(overlay_row.get("logic_status"))
    recommendation = _clean_overlay_text(overlay_row.get("recommendation"))
    if not logic_status or not recommendation:
        raise ValueError("portfolio overlay text fields are missing")
    data["logic_status"] = logic_status
    data["recommendation"] = recommendation
    data["risk_points"] = overlay_row.get("risk_points")
    data["tracking_points"] = overlay_row.get("tracking_points")
    data["sources"] = overlay_row.get("sources")
    research_status = str(overlay_row.get("research_status") or "missing")
    data["research_status"] = research_status if research_status in {"ready", "missing"} else "missing"
    confidence = _bounded_confidence(overlay_row.get("confidence"), fallback=row.confidence)
    data["confidence"] = confidence
    data["source"] = source_prefix
    data["reason"] = (
        "portfolio_risk_research_with_verified_sources"
        if data["research_status"] == "ready"
        else "local_rules_without_live_research"
    )
    data["status"] = AnalysisStatus.READY.value
    return PortfolioRiskRow(**data)


def _merge_rebalance_advice_overlay(
    advice: PortfolioRebalanceAdvice,
    overlay_advice: Any,
    *,
    source_prefix: str,
) -> PortfolioRebalanceAdvice:
    if not isinstance(overlay_advice, dict):
        raise ValueError("rebalance advice is missing")
    cards = _clean_advice_cards(overlay_advice.get("cards"))
    action_today = _clean_overlay_text(overlay_advice.get("action_today"))
    thinking_prompt = _clean_overlay_text(overlay_advice.get("thinking_prompt"))
    if len(cards) != 4 or not action_today or not thinking_prompt:
        raise ValueError("rebalance advice is incomplete")
    return PortfolioRebalanceAdvice(
        cards=cards,
        action_today=action_today,
        thinking_prompt=thinking_prompt,
        confidence=_bounded_confidence(overlay_advice.get("confidence"), fallback=advice.confidence),
        source=source_prefix,
        reason="portfolio_rebalance_research_with_verified_sources",
        status=AnalysisStatus.READY,
        as_of=date.today().isoformat(),
    )


def _clean_overlay_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text[:500] if text else None


def _clean_advice_cards(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != 4:
        return []
    cards: list[dict[str, str]] = []
    expected = [
        ("01", "alert", "组合首要风险"),
        ("02", "search", "优先复核持仓"),
        ("03", "compass", "组合结构与集中度"),
        ("04", "calendar", "未来30天跟踪清单"),
    ]
    for item, (expected_rank, expected_icon, expected_title) in zip(value, expected, strict=False):
        if not isinstance(item, dict):
            continue
        rank = _clean_overlay_text(item.get("rank"))
        icon = _clean_overlay_text(item.get("icon"))
        title = _clean_overlay_text(item.get("title"))
        body = _clean_overlay_text(item.get("body"))
        if (rank, icon, title) == (expected_rank, expected_icon, expected_title) and body:
            cards.append({"rank": rank, "icon": icon, "title": title, "body": body})
    return cards


def _bounded_confidence(value: Any, *, fallback: float | None) -> float:
    parsed = _optional_float(value)
    if parsed is None:
        parsed = fallback if fallback is not None else 0.55
    if parsed > 1:
        parsed = parsed / 100
    return round(max(0.0, min(parsed, 1.0)), 2)


def _ai_theme_weight(positions: list[dict], total: float) -> float:
    if total == 0:
        return 0.0
    value = 0.0
    for position in positions:
        symbol = str(position.get("symbol", "") or "").upper()
        industry = str(position.get("industry", "") or "").upper()
        if symbol in AI_SYMBOL_HINTS or "AI" in industry or "SEMICONDUCTOR" in industry:
            value += abs(_position_value(position))
    return value / total


def _semiconductor_weight(positions: list[dict], total: float) -> float:
    if total == 0:
        return 0.0
    return sum(abs(_position_value(row)) for row in positions if _is_semiconductor(row)) / total


def _cyclical_weight(positions: list[dict], total: float) -> float:
    if total == 0:
        return 0.0
    return sum(abs(_position_value(row)) for row in positions if _is_cyclical(row)) / total


def _is_semiconductor(position: dict) -> bool:
    symbol = str(position.get("symbol", "") or "").upper()
    industry = str(position.get("industry", "") or "").upper()
    return symbol in SEMICONDUCTOR_HINTS or "半导体" in industry or "SEMICONDUCTOR" in industry


def _is_cyclical(position: dict) -> bool:
    symbol = str(position.get("symbol", "") or "").upper()
    industry = str(position.get("industry", "") or "").upper()
    return symbol in CYCLICAL_HINTS or any(keyword in industry for keyword in ("汽车", "航天", "工业", "能源", "CYCLICAL"))


def _market_benchmark_for_positions(positions: list[dict]) -> str:
    total = _positions_total(positions)
    ai_weight = _ai_theme_weight(positions, total)
    semi_weight = _semiconductor_weight(positions, total)
    return MARKET_BENCHMARKS["growth"] if ai_weight + semi_weight >= 0.35 else MARKET_BENCHMARKS["broad"]


def _benchmark_label(symbol: str) -> str:
    labels = {"QQQ": "纳指100代理", "SPY": "标普500代理", "^NDX": "纳指100", "^VIX": "波动率指数"}
    return labels.get(symbol, symbol)


def _weighted_daily_change_pct(positions: list[dict]) -> float:
    total = _positions_total(positions)
    if total == 0:
        return 0.0
    return sum(abs(_position_value(row)) / total * _position_daily_change_pct(row) for row in positions)


def _positive_breadth(positions: list[dict]) -> float:
    rows = [row for row in positions if _position_value(row) != 0]
    if not rows:
        return 0.0
    return sum(1 for row in rows if _position_daily_change_pct(row) > 0) / len(rows)


def _downside_breadth(positions: list[dict]) -> float:
    rows = [row for row in positions if _position_value(row) != 0]
    if not rows:
        return 0.0
    return sum(1 for row in rows if _position_daily_change_pct(row) < 0) / len(rows)


def _daily_loss_at_risk(positions: list[dict]) -> float:
    return sum(min(_to_float(row.get("daily_change")), 0.0) * abs(_to_float(row.get("quantity")) or 1.0) for row in positions)


def _position_daily_change_pct(position: dict) -> float:
    if position.get("daily_change_pct") not in (None, ""):
        return _to_float(position.get("daily_change_pct"))
    current = _to_float(position.get("mark_price_snapshot") or position.get("realtime_price"))
    previous = _to_float(position.get("previous_mark_price_snapshot") or position.get("previous_price"))
    if current and previous:
        return (current - previous) / previous
    return 0.0


def _with_position_daily_changes(current_rows: list[dict], all_rows: list[dict], latest_date: str) -> list[dict]:
    previous_dates = sorted({str(row.get("report_date", "") or "") for row in all_rows if str(row.get("report_date", "") or "") < latest_date})
    if not previous_dates:
        return [dict(row) for row in current_rows]
    previous_date = previous_dates[-1]
    previous_rows = [
        row for row in all_rows
        if str(row.get("report_date", "") or "") == previous_date
        and str(row.get("level_of_detail", "") or "").upper() == "SUMMARY"
    ]
    previous_by_symbol = {str(row.get("symbol", "") or "").upper(): row for row in previous_rows}
    enriched: list[dict] = []
    for row in current_rows:
        item = dict(row)
        symbol = str(item.get("symbol", "") or "").upper()
        previous = previous_by_symbol.get(symbol)
        if previous is not None:
            previous_price = _to_float(previous.get("mark_price_snapshot"))
            current_price = _to_float(item.get("mark_price_snapshot"))
            if previous_price:
                item["previous_mark_price_snapshot"] = previous_price
                item.setdefault("daily_change", round(current_price - previous_price, 6))
                item.setdefault("daily_change_pct", round((current_price - previous_price) / previous_price, 6))
        enriched.append(item)
    return enriched


def _iv_percentile_from_vix(history: list[MarketDataPoint]) -> float | None:
    values = [float(point.close) for point in history if point.close is not None]
    if len(values) < 20:
        return None
    latest = values[-1]
    below_or_equal = sum(1 for value in values if value <= latest)
    return round(below_or_equal / len(values) * 100, 2)


def _market_volume_anomaly(history_by_symbol: dict[str, list[MarketDataPoint]]) -> float | None:
    ratios: list[float] = []
    for symbol in MARKET_VOLUME_SYMBOLS:
        volumes = [float(point.volume) for point in history_by_symbol.get(symbol, []) if point.volume is not None and float(point.volume) > 0]
        if len(volumes) < 21:
            continue
        average = mean(volumes[-21:-1])
        if average > 0:
            ratios.append(volumes[-1] / average)
    if not ratios:
        return None
    return round(mean(ratios), 2)


def _market_volume_symbol_count(history_by_symbol: dict[str, list[MarketDataPoint]]) -> int:
    count = 0
    for symbol in MARKET_VOLUME_SYMBOLS:
        volumes = [float(point.volume) for point in history_by_symbol.get(symbol, []) if point.volume is not None and float(point.volume) > 0]
        if len(volumes) >= 21:
            count += 1
    return count


def _market_volume_anomaly_confidence(history_by_symbol: dict[str, list[MarketDataPoint]]) -> float:
    count = _market_volume_symbol_count(history_by_symbol)
    if count >= 4:
        return 0.82
    if count == 3:
        return 0.78
    if count == 2:
        return 0.72
    if count == 1:
        return 0.62
    return 0.0


def _market_volume_source(history_by_symbol: dict[str, list[MarketDataPoint]]) -> str:
    symbols = [
        symbol
        for symbol in MARKET_VOLUME_SYMBOLS
        if len([point for point in history_by_symbol.get(symbol, []) if point.volume is not None and float(point.volume) > 0]) >= 21
    ]
    return "_".join(symbols) if symbols else "_".join(MARKET_VOLUME_SYMBOLS)


def _put_call_crowding_proxy(*, fear_greed: float, vix: float | None, breadth: float) -> float:
    score = 1.0
    score += (50 - fear_greed) / 180
    score += (0.5 - breadth) * 0.28
    if vix is not None:
        score += max(-0.18, min(0.28, (vix - 20) / 90))
    return round(max(0.55, min(1.65, score)), 2)


def _market_crowding_confidence(*, primary_sentiment: dict[str, Any], vix: float | None, breadth: float) -> float:
    confidence = 0.46
    source = str(primary_sentiment.get("source") or "")
    if source == "cnn_fear_greed":
        confidence = 0.62
    elif source == "longbridge_market_temp":
        confidence = 0.6
    elif source == "longbridge_topic":
        confidence = 0.55
    if vix is not None:
        confidence += 0.08
    if breadth >= 0:
        confidence += 0.04
    return round(min(confidence, 0.74), 2)


def _futu_market_heat(spy: list[MarketDataPoint], qqq: list[MarketDataPoint]) -> dict[str, Any]:
    histories = [history for history in (spy, qqq) if len(history) >= 2]
    if not histories:
        return {"status": "missing_data", "source": "futu_market_heat", "reason": "futu_market_kline_missing"}
    changes = [_latest_change_percent(history) or 0.0 for history in histories]
    volume_ratio = _market_volume_anomaly({"SPY": spy, "QQQ": qqq}) or 1.0
    value = round(max(0.0, min(100.0, 50 + mean(changes) * 3.5 + (volume_ratio - 1) * 18)), 2)
    return {
        "status": "ready",
        "source": "futu_market_heat",
        "value": value,
        "volume_ratio": volume_ratio,
        "reason": "futu_opend_spy_qqq_change_and_volume_proxy",
    }


def _futu_watchlist_heat(histories: list[list[MarketDataPoint]]) -> dict[str, Any]:
    ready = [history for history in histories if len(history) >= 2]
    if not ready:
        return {"status": "missing_data", "source": "futu_watchlist_heat", "reason": "futu_watchlist_kline_missing"}
    changes = [_latest_change_percent(history) or 0.0 for history in ready]
    volume_ratios: list[float] = []
    for history in ready:
        volumes = [float(point.volume) for point in history if point.volume is not None and float(point.volume) > 0]
        if len(volumes) >= 21:
            average = mean(volumes[-21:-1])
            if average > 0:
                volume_ratios.append(volumes[-1] / average)
    volume_ratio = mean(volume_ratios) if volume_ratios else 1.0
    value = round(max(0.0, min(100.0, 50 + mean(changes) * 3 + (volume_ratio - 1) * 16)), 2)
    return {
        "status": "ready",
        "source": "futu_watchlist_heat",
        "value": value,
        "symbols": len(ready),
        "volume_ratio": round(volume_ratio, 2),
        "reason": "futu_opend_watchlist_change_and_volume_proxy_not_community_text",
    }


def _top_n_weight(positions: list[dict], total: float, count: int) -> float:
    if total == 0:
        return 0.0
    rows = sorted(positions, key=lambda row: abs(_position_value(row)), reverse=True)
    return sum(abs(_position_value(row)) for row in rows[:count]) / total


def _theme_cluster_weight(positions: list[dict], total: float) -> float:
    if total == 0:
        return 0.0
    return sum(
        abs(_position_value(row))
        for row in positions
        if _is_semiconductor(row) or _is_cyclical(row) or str(row.get("symbol", "") or "").upper() in AI_SYMBOL_HINTS
    ) / total


def _factor_rows(positions: list[dict], total: float) -> list[dict[str, Any]]:
    ai_weight = _ai_theme_weight(positions, total)
    semi_weight = _semiconductor_weight(positions, total)
    cyclical_weight = _cyclical_weight(positions, total)
    growth_weight = min(1.0, ai_weight + semi_weight + 0.5 * cyclical_weight)
    rows = [
        ("growth", growth_weight, 0.62, "ai_semiconductor_and_cyclical_growth_proxy"),
        ("ai_beta", ai_weight, 0.7, "symbol_and_industry_ai_theme_tagging"),
        ("semiconductor", semi_weight, 0.75, "semiconductor_symbol_or_industry_tagging"),
        ("cyclical", cyclical_weight, 0.55, "cyclical_industry_proxy"),
    ]
    return [{"key": key, "weight": weight, "confidence": confidence, "reason": reason} for key, weight, confidence, reason in rows]


def _factor_label(key: str) -> str:
    labels = {
        "growth": "成长",
        "ai_beta": "智能主题弹性",
        "semiconductor": "半导体",
        "cyclical": "周期/高波动",
    }
    return labels.get(key, key)


def _macro_sensitivity_metrics(positions: list[dict], ai_weight: float) -> dict[str, StandardMetric]:
    total = _positions_total(positions)
    semi_weight = _semiconductor_weight(positions, total)
    cyclical_weight = _cyclical_weight(positions, total)
    rates = min(100.0, 35 + (ai_weight + semi_weight) * 55)
    liquidity = min(100.0, 30 + (ai_weight + cyclical_weight) * 55)
    capex = min(100.0, 25 + (ai_weight + semi_weight) * 65)
    return {
        "rates": _metric(value=round(rates, 2), unit="index", source="portfolio_rules", status=AnalysisStatus.READY, confidence=0.55, reason="growth_and_long_duration_equity_proxy"),
        "liquidity": _metric(value=round(liquidity, 2), unit="index", source="portfolio_rules", status=AnalysisStatus.READY, confidence=0.55, reason="high_beta_growth_proxy"),
        "ai_capex": _metric(value=round(capex, 2), unit="index", source="portfolio_rules", status=AnalysisStatus.READY, confidence=0.65, reason="ai_and_semiconductor_exposure_proxy"),
    }


def _classify_regime(rsi: float, weighted_change: float, breadth: float, ai_weight: float) -> tuple[str, list[str], list[str]]:
    if rsi >= 70 and ai_weight >= 0.35:
        return ("拥挤多头", [f"成长基准相对强弱指数为 {rsi}，且组合智能主题/高弹性暴露偏高"], ["拥挤多头下，智能主题主线预期变化会被组合放大"])
    if rsi >= 65 and weighted_change > 0:
        return ("亢奋动量", [f"市场相对强弱指数为 {rsi}，组合加权日变化为 {round(weighted_change * 100, 2)}%"], ["动量延续依赖风险偏好，回撤时高弹性持仓会同步波动"])
    if rsi <= 32:
        return ("投降区间", [f"市场相对强弱指数为 {rsi}，处于弱势区间"], ["需要确认是否只是技术反弹，避免把短期超卖误读成趋势反转"])
    if breadth < 0.35 and weighted_change < 0:
        return ("恐慌压缩", [f"组合上涨家数占比仅 {round(breadth * 100, 2)}%"], ["风险集中在持仓同步走弱，而不是单只股票波动"])
    if rsi < 48 and ai_weight >= 0.35:
        return ("叙事衰竭", [f"市场相对强弱指数为 {rsi}，但组合主题暴露仍偏集中"], ["智能主题/成长叙事如果降温，组合估值弹性会下降"])
    return ("中性观察", [f"市场相对强弱指数为 {rsi}，组合广度为 {round(breadth * 100, 2)}%"], ["当前信号未形成单边结论，需要继续观察基准趋势和持仓广度"])


def _market_portfolio_impact(
    positions: list[dict],
    benchmark: str,
    rsi: float | None,
    weighted_change: float,
    breadth: float,
) -> list[str]:
    top_symbols = "、".join(_top_symbols(positions, limit=3))
    items = [f"本页市场相对强弱指数使用 {benchmark} 作为持仓相关基准，不再使用单只持仓替代市场。"]
    if top_symbols:
        items.append(f"当前组合影响最大的持仓是 {top_symbols}，市场变化会优先通过这些仓位传导。")
    items.append(f"组合加权日变化为 {round(weighted_change * 100, 2)}%，上涨持仓占比为 {round(breadth * 100, 2)}%。")
    if rsi is not None and rsi >= 65:
        items.append("基准处于偏热区间时，组合中的成长和智能主题弹性仓位更容易放大波动。")
    elif rsi is not None and rsi <= 35:
        items.append("基准处于偏弱区间时，组合首要问题是识别哪些持仓仍能保持相对强势。")
    return items


def _market_opportunities(positions: list[dict], weighted_change: float, breadth: float) -> list[str]:
    if weighted_change < 0 and breadth < 0.4:
        return ["优先观察跌幅最大的高权重持仓是否只是跟随市场回撤，而非基本面恶化。"]
    if weighted_change > 0 and breadth > 0.55:
        return ["组合上涨广度较好，可继续观察主线是否从单一龙头扩散到相关持仓。"]
    return ["当前机会/风险没有单边信号，重点观察高权重持仓与基准是否背离。"]


def _latest_value_and_change(history: list[MarketDataPoint]) -> tuple[float | None, float | None]:
    closes = [float(point.close) for point in history if point.close is not None]
    if not closes:
        return None, None
    if len(closes) == 1:
        return round(closes[-1], 2), None
    return round(closes[-1], 2), round(closes[-1] - closes[-2], 2)


def _latest_change_percent(history: list[MarketDataPoint]) -> float | None:
    closes = [float(point.close) for point in history if point.close is not None]
    if len(closes) < 2 or closes[-2] == 0:
        return None
    return round((closes[-1] / closes[-2] - 1) * 100, 2)


def _fear_greed_proxy(rsi: float | None, weighted_change: float, breadth: float, ai_weight: float) -> float:
    score = 42.0
    if rsi is not None:
        score += (rsi - 50) * 0.42
    score += (breadth - 0.5) * 24
    score += max(-12.0, min(12.0, weighted_change * 500))
    score += min(10.0, ai_weight * 18)
    return round(max(0.0, min(100.0, score)), 2)


def _primary_sentiment(bundle: dict[str, Any]) -> dict[str, Any]:
    cnn = bundle.get("cnn_fear_greed")
    if isinstance(cnn, dict) and cnn.get("status") == "ready" and _number_or_none(cnn.get("value")) is not None:
        return cnn
    longbridge_temp = bundle.get("longbridge_market_temp")
    if isinstance(longbridge_temp, dict) and longbridge_temp.get("status") == "ready" and _number_or_none(longbridge_temp.get("value")) is not None:
        return longbridge_temp
    community = _community_heat_sentiment(bundle.get("longbridge_topics"))
    if community.get("status") == "ready":
        return community
    futu_market = bundle.get("futu_market_heat")
    if isinstance(futu_market, dict) and futu_market.get("status") == "ready" and _number_or_none(futu_market.get("value")) is not None:
        return futu_market
    futu_watchlist = bundle.get("futu_watchlist_heat")
    if isinstance(futu_watchlist, dict) and futu_watchlist.get("status") == "ready" and _number_or_none(futu_watchlist.get("value")) is not None:
        return futu_watchlist
    return {"status": "ready", "source": "portfolio_theme_and_breadth_proxy", "value": None, "reason": "local_proxy_fallback"}


def _attach_sentiment_indicators(metrics: dict[str, StandardMetric], bundle: dict[str, Any], local_fear_greed: float) -> None:
    cnn = bundle.get("cnn_fear_greed") if isinstance(bundle.get("cnn_fear_greed"), dict) else {}
    longbridge_temp = bundle.get("longbridge_market_temp") if isinstance(bundle.get("longbridge_market_temp"), dict) else {}
    community = _community_heat_sentiment(bundle.get("longbridge_topics"))
    futu_market = bundle.get("futu_market_heat") if isinstance(bundle.get("futu_market_heat"), dict) else {}
    futu_watchlist = bundle.get("futu_watchlist_heat") if isinstance(bundle.get("futu_watchlist_heat"), dict) else {}
    if cnn.get("status") == "ready":
        metrics["cnn_fear_greed"] = _metric(
            value=_number_or_none(cnn.get("value")),
            unit="index",
            source="cnn_fear_greed",
            status=AnalysisStatus.READY,
            confidence=0.82,
            reason="cnn_fear_and_greed_index",
        )
    if longbridge_temp.get("status") == "ready":
        metrics["longbridge_market_temp"] = _metric(
            value=_number_or_none(longbridge_temp.get("value")),
            unit="index",
            source="longbridge_market_temp",
            status=AnalysisStatus.READY,
            confidence=0.78,
            reason="longbridge_market_temperature_us",
        )
    if community.get("status") == "ready":
        metrics["longbridge_community_heat"] = _metric(
            value=_number_or_none(community.get("value")),
            unit="index",
            source="longbridge_topic",
            status=AnalysisStatus.READY,
            confidence=0.58,
            reason="longbridge_topic_likes_comments_and_topic_count",
        )
    if futu_market.get("status") == "ready":
        metrics["futu_market_heat"] = _metric(
            value=_number_or_none(futu_market.get("value")),
            unit="index",
            source="futu_market_heat",
            status=AnalysisStatus.READY,
            confidence=0.56,
            reason=str(futu_market.get("reason") or "futu_opend_market_heat_proxy"),
        )
    if futu_watchlist.get("status") == "ready":
        metrics["futu_watchlist_heat"] = _metric(
            value=_number_or_none(futu_watchlist.get("value")),
            unit="index",
            source="futu_watchlist_heat",
            status=AnalysisStatus.READY,
            confidence=0.48,
            reason=str(futu_watchlist.get("reason") or "futu_opend_watchlist_heat_proxy"),
        )
    metrics["local_sentiment_proxy"] = _metric(
        value=local_fear_greed,
        unit="index",
        source="portfolio_theme_and_breadth_proxy",
        status=AnalysisStatus.READY,
        confidence=0.5,
        reason="fallback_proxy_from_rsi_breadth_theme_and_daily_change",
    )


def _sentiment_confidence(sentiment: dict[str, Any], *, fallback: float) -> float:
    source = str(sentiment.get("source") or "")
    if source == "cnn_fear_greed":
        return 0.82
    if source == "longbridge_market_temp":
        return 0.78
    if source == "longbridge_topic":
        return 0.58
    if source == "futu_market_heat":
        return 0.56
    if source == "futu_watchlist_heat":
        return 0.48
    return fallback


def _number_or_none(value: object, fallback: float | None = None) -> float | None:
    if value is None or value == "":
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _community_heat_sentiment(value: object) -> dict[str, Any]:
    rows = value if isinstance(value, list) else []
    ready = [row for row in rows if isinstance(row, dict) and row.get("status") == "ready"]
    if not ready:
        return {"status": "missing_data", "source": "longbridge_topic", "reason": "longbridge_topic_missing"}
    heat = round(mean([_number_or_none(row.get("value"), 0.0) or 0.0 for row in ready]), 2)
    return {
        "status": "ready",
        "source": "longbridge_topic",
        "value": heat,
        "symbols": [row.get("symbol") for row in ready],
        "topic_count": sum(int(_number_or_none(row.get("topic_count"), 0) or 0) for row in ready),
        "likes_count": sum(int(_number_or_none(row.get("likes_count"), 0) or 0) for row in ready),
        "comments_count": sum(int(_number_or_none(row.get("comments_count"), 0) or 0) for row in ready),
    }


def _sentiment_subtitle(sentiment: dict[str, Any]) -> str:
    source = str(sentiment.get("source") or "")
    if source == "cnn_fear_greed":
        return "CNN Fear & Greed 指标"
    if source == "longbridge_market_temp":
        return "长桥美股市场温度"
    if source == "longbridge_topic":
        return "长桥社区讨论热度"
    if source == "futu_market_heat":
        return "富途OpenD市场热度代理"
    if source == "futu_watchlist_heat":
        return "富途OpenD关注热度代理"
    return "本地规则代理，外部情绪不可用"


def _sentiment_source_label(sentiment: dict[str, Any]) -> str:
    source = str(sentiment.get("source") or "")
    if source == "cnn_fear_greed":
        return "CNN Fear & Greed"
    if source == "longbridge_market_temp":
        return "长桥 market-temp US"
    if source == "longbridge_topic":
        return "长桥 topic search"
    if source == "futu_market_heat":
        return "富途OpenD市场热度"
    if source == "futu_watchlist_heat":
        return "富途OpenD关注热度"
    return "本地规则代理"


def _sentiment_reading(sentiment: dict[str, Any], local_fear_greed: float, regime: str) -> str:
    source = str(sentiment.get("source") or "")
    value = _number_or_none(sentiment.get("value"))
    if source == "cnn_fear_greed":
        return f"优先使用 CNN Fear & Greed，当前值为 {value}。本地代理值为 {local_fear_greed}，用于交叉验证组合相关情绪。"
    if source == "longbridge_market_temp":
        return f"CNN 指标暂不可取，使用长桥美股市场温度 {value}。当前市场状态归类为「{regime}」。"
    if source == "longbridge_topic":
        return f"CNN 与市场温度暂不可取，使用长桥社区讨论热度 {value}。该值反映关注持仓附近的话题活跃度。"
    if source == "futu_market_heat":
        return f"使用富途OpenD的 SPY/QQQ 涨跌与成交量代理市场热度 {value}；这不是富途社区原文情绪。"
    if source == "futu_watchlist_heat":
        return f"使用富途OpenD的关注持仓涨跌与成交量代理关注热度 {value}；OpenD 不提供社区文本情绪。"
    return f"外部情绪源暂不可用，当前值为本地代理 {local_fear_greed}，由基准强弱、组合广度、主题拥挤度和日变化合成。"


def _history_source_label(history: list[MarketDataPoint]) -> str:
    if not history:
        return "行情数据不可用"
    source = history[-1].source or "market_data_provider"
    if source == "longbridge":
        return "长桥行情K线"
    if source == "futu_opend":
        return "富途OpenD行情K线"
    if source == "quote_fallback":
        return "通用行情缓存"
    return str(source)


def _market_pulse_rows(
    *,
    benchmark: str,
    history_by_symbol: dict[str, list[MarketDataPoint]],
    rsi: float | None,
    ndx_rsi: float | None,
    fear_greed: float,
    local_fear_greed: float,
    regime: str,
    sentiment_bundle: dict[str, Any],
    iv_percentile: float | None,
    put_call_proxy: float,
    crowding_confidence: float,
    volume_anomaly: float | None,
    volume_confidence: float,
    volume_source: str,
) -> list[dict[str, Any]]:
    primary_sentiment = _primary_sentiment(sentiment_bundle)
    longbridge_temp = sentiment_bundle.get("longbridge_market_temp") if isinstance(sentiment_bundle.get("longbridge_market_temp"), dict) else {}
    community = _community_heat_sentiment(sentiment_bundle.get("longbridge_topics"))
    futu_market = sentiment_bundle.get("futu_market_heat") if isinstance(sentiment_bundle.get("futu_market_heat"), dict) else {}
    futu_watchlist = sentiment_bundle.get("futu_watchlist_heat") if isinstance(sentiment_bundle.get("futu_watchlist_heat"), dict) else {}
    rows: list[dict[str, Any]] = [
        _price_pulse_row(
            key="sp500",
            title="标普500",
            symbol="SPY",
            subtitle="标普500代理基金",
            history=history_by_symbol.get("SPY", []),
            accent="green",
        ),
        _price_pulse_row(
            key="nasdaq100",
            title="纳指100",
            symbol="QQQ",
            subtitle="纳指100代理基金",
            history=history_by_symbol.get("QQQ", []),
            accent="purple",
        ),
        _volatility_pulse_row(history_by_symbol.get("^VIX", [])),
        _score_pulse_row(
            key="benchmark_rsi",
            title="基准相对强弱(14)",
            subtitle="持仓相关基准相对强弱",
            value=rsi,
            unit="index",
            accent="green" if benchmark == "SPY" else "purple",
            playbook=_rsi_playbook(rsi),
            badge=_rsi_badge(rsi),
            reading=_rsi_reading(rsi, _benchmark_label(benchmark)),
            source=f"行情K线：{_benchmark_label(benchmark)}",
            confidence=0.75,
        ),
        _score_pulse_row(
            key="fear_greed",
            title="恐惧与贪婪",
            subtitle=_sentiment_subtitle(primary_sentiment),
            value=fear_greed,
            unit="index",
            accent="gold",
            playbook=_fear_greed_playbook(fear_greed),
            badge=_fear_greed_badge(fear_greed),
            reading=_sentiment_reading(primary_sentiment, local_fear_greed, regime),
            source=_sentiment_source_label(primary_sentiment),
            confidence=_sentiment_confidence(primary_sentiment, fallback=0.55),
        ),
        _score_pulse_row(
            key="iv_percentile",
            title="隐波分位",
            subtitle="VIX 90日分位代理",
            value=iv_percentile,
            unit="percent",
            accent="purple",
            playbook=_percentile_playbook(iv_percentile),
            badge=_percentile_badge(iv_percentile),
            reading=_iv_percentile_reading(iv_percentile),
            source="VIX 90日历史分位",
            confidence=0.72 if iv_percentile is not None else 0.0,
        ),
        _score_pulse_row(
            key="market_crowding",
            title="拥挤度代理",
            subtitle="Put/Call 风格代理",
            value=put_call_proxy,
            unit="ratio",
            accent="gold",
            playbook=_put_call_playbook(put_call_proxy),
            badge=_put_call_badge(put_call_proxy),
            reading="没有直接使用期权链；该值由恐惧贪婪、VIX 和组合上涨广度合成，只用于观察风险偏好是否过热或防守。",
            source="多信号拥挤度代理",
            confidence=crowding_confidence,
        ),
        _score_pulse_row(
            key="volume_anomaly",
            title="成交量异常",
            subtitle="主要美股ETF最新量能 / 20日均量",
            value=volume_anomaly,
            unit="ratio",
            accent="green",
            playbook=_volume_anomaly_playbook(volume_anomaly),
            badge=_volume_anomaly_badge(volume_anomaly),
            reading=_volume_anomaly_reading(volume_anomaly),
            source=f"{volume_source} 成交量",
            confidence=volume_confidence,
        ),
    ]
    if longbridge_temp.get("status") == "ready":
        rows.append(
            _score_pulse_row(
                key="longbridge_market_temp",
                title="长桥市场温度",
                subtitle="长桥美股市场温度",
                value=_number_or_none(longbridge_temp.get("value")),
                unit="index",
                accent="purple",
                playbook=_fear_greed_playbook(_number_or_none(longbridge_temp.get("value"))),
                badge=_fear_greed_badge(_number_or_none(longbridge_temp.get("value"))),
                reading=f"长桥市场温度当前为 {_number_or_none(longbridge_temp.get('value'))}，估值 {_number_or_none(longbridge_temp.get('valuation'))}，情绪 {_number_or_none(longbridge_temp.get('sentiment'))}。",
                source="长桥 market-temp US",
                confidence=0.78,
            )
        )
    if community.get("status") == "ready":
        rows.append(
            _score_pulse_row(
                key="longbridge_community_heat",
                title="长桥社区热度",
                subtitle="高权重持仓社区讨论热度",
                value=_number_or_none(community.get("value")),
                unit="index",
                accent="gold",
                playbook=_fear_greed_playbook(_number_or_none(community.get("value"))),
                badge=_fear_greed_badge(_number_or_none(community.get("value"))),
                reading=f"基于关注持仓的长桥话题数、评论数和点赞数估算，覆盖 {community.get('topic_count', 0)} 条话题。",
                source="长桥 topic search",
                confidence=0.58,
            )
        )
    if futu_market.get("status") == "ready":
        rows.append(
            _score_pulse_row(
                key="futu_market_heat",
                title="富途市场热度",
                subtitle="OpenD 行情代理",
                value=_number_or_none(futu_market.get("value")),
                unit="index",
                accent="purple",
                playbook=_fear_greed_playbook(_number_or_none(futu_market.get("value"))),
                badge=_fear_greed_badge(_number_or_none(futu_market.get("value"))),
                reading="基于富途OpenD读取的 SPY/QQQ 涨跌和成交量估算市场热度；不是富途社区原文情绪。",
                source="富途OpenD SPY/QQQ",
                confidence=0.56,
            )
        )
    if futu_watchlist.get("status") == "ready":
        rows.append(
            _score_pulse_row(
                key="futu_watchlist_heat",
                title="富途关注热度",
                subtitle="关注持仓行情代理",
                value=_number_or_none(futu_watchlist.get("value")),
                unit="index",
                accent="gold",
                playbook=_fear_greed_playbook(_number_or_none(futu_watchlist.get("value"))),
                badge=_fear_greed_badge(_number_or_none(futu_watchlist.get("value"))),
                reading=f"基于富途OpenD读取的 {int(_number_or_none(futu_watchlist.get('symbols'), 0) or 0)} 个关注持仓涨跌与成交量估算；OpenD 不提供社区文本情绪。",
                source="富途OpenD 关注持仓",
                confidence=0.48,
            )
        )
    return rows


def _price_pulse_row(
    *,
    key: str,
    title: str,
    symbol: str,
    subtitle: str,
    history: list[MarketDataPoint],
    accent: str,
) -> dict[str, Any]:
    value, change = _latest_value_and_change(history)
    change_percent = _latest_change_percent(history)
    trend = _trend_from_history(history)
    return {
        "key": key,
        "title": title,
        "symbol": symbol,
        "subtitle": subtitle,
        "value": value,
        "change": change,
        "change_percent": change_percent,
        "unit": "price",
        "accent": accent,
        "badge": _price_badge(change_percent),
        "reading": f"{title} 作为组合市场背景参考。近 20 日趋势为「{trend}」，当日变化用于判断市场风险偏好是否支持当前持仓。",
        "playbook": _price_playbook(change_percent),
        "sparkline": _sparkline_points(history),
        "source": _history_source_label(history),
        "confidence": 0.74 if history else 0.0,
    }


def _volatility_pulse_row(history: list[MarketDataPoint]) -> dict[str, Any]:
    value, change = _latest_value_and_change(history)
    return {
        "key": "vix",
        "title": "波动率指数",
        "symbol": "^VIX",
        "subtitle": "标普500波动率",
        "value": value,
        "change": change,
        "change_percent": _latest_change_percent(history),
        "unit": "index",
        "accent": "green",
        "badge": _vix_badge(value),
        "reading": _vix_reading(value),
        "playbook": _vix_playbook(value),
        "sparkline": _sparkline_points(history),
        "source": _history_source_label(history),
        "confidence": 0.75 if history else 0.0,
    }


def _score_pulse_row(
    *,
    key: str,
    title: str,
    subtitle: str,
    value: float | None,
    unit: str,
    accent: str,
    playbook: list[dict[str, Any]],
    badge: dict[str, str],
    reading: str,
    source: str,
    confidence: float,
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "subtitle": subtitle,
        "value": value,
        "change": None,
        "change_percent": None,
        "unit": unit,
        "accent": accent,
        "badge": badge,
        "reading": reading,
        "playbook": playbook,
        "sparkline": [],
        "source": source,
        "confidence": confidence,
    }


def _sparkline_points(history: list[MarketDataPoint]) -> list[dict[str, float | str]]:
    return [
        {"date": point.date, "value": round(float(point.close), 4)}
        for point in history[-36:]
        if point.close is not None
    ]


def _trend_from_history(history: list[MarketDataPoint]) -> str:
    closes = [float(point.close) for point in history if point.close is not None]
    if len(closes) < 20:
        return "样本不足"
    latest = closes[-1]
    ma20 = mean(closes[-20:])
    ret20 = (latest / closes[-20] - 1) * 100 if closes[-20] else 0.0
    if latest >= ma20 and ret20 >= 3:
        return "上行强化"
    if latest >= ma20:
        return "震荡偏强"
    if ret20 <= -3:
        return "回撤压力"
    return "震荡修复"


def _price_badge(change_percent: float | None) -> dict[str, str]:
    if change_percent is None:
        return {"label": "缺数据", "tone": "neutral"}
    if change_percent <= -1.5:
        return {"label": "快速回撤", "tone": "negative"}
    if change_percent < -0.3:
        return {"label": "小幅回调", "tone": "warning"}
    if change_percent >= 1.5:
        return {"label": "强势上攻", "tone": "positive"}
    if change_percent > 0.3:
        return {"label": "温和走强", "tone": "positive"}
    return {"label": "横盘观察", "tone": "neutral"}


def _price_playbook(change_percent: float | None) -> list[dict[str, Any]]:
    return _band_rows(
        value=change_percent,
        rows=[
            (None, -1.5, "快速回撤", "优先检查高弹性持仓"),
            (-1.5, -0.3, "小幅回调", "观察是否跌破均线"),
            (-0.3, 0.3, "横盘", "等待方向确认"),
            (0.3, 1.5, "温和走强", "观察主线扩散"),
            (1.5, None, "强势上攻", "警惕追高拥挤"),
        ],
    )


def _rsi_badge(value: float | None) -> dict[str, str]:
    if value is None:
        return {"label": "缺数据", "tone": "neutral"}
    if value < 30:
        return {"label": "超卖", "tone": "negative"}
    if value < 50:
        return {"label": "偏弱", "tone": "warning"}
    if value < 70:
        return {"label": "中性", "tone": "neutral"}
    if value < 80:
        return {"label": "偏强", "tone": "positive"}
    return {"label": "超买", "tone": "negative"}


def _rsi_reading(value: float | None, benchmark: str) -> str:
    if value is None:
        return f"{benchmark} 相对强弱暂不可用，市场强弱需要回看价格趋势和组合广度。"
    if value >= 80:
        return f"{benchmark} 相对强弱已进入超买区，代表动量强但回撤敏感度上升，适合谨慎追高。"
    if value >= 70:
        return f"{benchmark} 相对强弱偏强，说明风险偏好仍在，但组合里的高弹性仓位会放大波动。"
    if value >= 50:
        return f"{benchmark} 相对强弱中性偏稳，当前更适合比较持仓相对强弱。"
    if value >= 30:
        return f"{benchmark} 相对强弱偏弱，优先确认组合下跌是市场拖累还是个股风险。"
    return f"{benchmark} 相对强弱超卖，可能出现技术反弹，但需要等待趋势确认。"


def _rsi_playbook(value: float | None) -> list[dict[str, Any]]:
    return _band_rows(
        value=value,
        rows=[
            (None, 30, "超卖", "寻找反弹确认"),
            (30, 50, "偏弱", "减少单边假设"),
            (50, 70, "中性", "常规跟踪"),
            (70, 80, "偏强", "谨慎追高"),
            (80, None, "超买", "部分止盈观察"),
        ],
    )


def _vix_badge(value: float | None) -> dict[str, str]:
    if value is None:
        return {"label": "缺数据", "tone": "neutral"}
    if value < 12:
        return {"label": "极度乐观", "tone": "warning"}
    if value < 20:
        return {"label": "正常波动", "tone": "positive"}
    if value < 30:
        return {"label": "恐惧上升", "tone": "warning"}
    if value < 50:
        return {"label": "市场恐慌", "tone": "negative"}
    return {"label": "极端恐慌", "tone": "negative"}


def _vix_reading(value: float | None) -> str:
    if value is None:
        return "波动率指数暂不可用，波动率判断会降级为价格趋势和组合广度。"
    if value < 12:
        return "波动率指数过低通常代表市场过度放松，反而要警惕突然回撤。"
    if value < 20:
        return "波动率指数处在正常波动区间，市场风险偏好没有明显压力。"
    if value < 30:
        return "波动率指数开始抬升，说明市场对回撤的保护需求增加。"
    return "波动率指数进入恐慌区，组合应优先关注高弹性和拥挤主题的同步回撤。"


def _vix_playbook(value: float | None) -> list[dict[str, Any]]:
    return _band_rows(
        value=value,
        rows=[
            (None, 12, "极度乐观", "谨慎追高"),
            (12, 20, "正常区间", "常规定投"),
            (20, 30, "恐惧上升", "加大观察"),
            (30, 50, "市场恐慌", "控制仓位"),
            (50, None, "极端恐慌", "大胆防守"),
        ],
    )


def _percentile_badge(value: float | None) -> dict[str, str]:
    if value is None:
        return {"label": "缺数据", "tone": "neutral"}
    if value < 25:
        return {"label": "低波动", "tone": "positive"}
    if value < 60:
        return {"label": "正常", "tone": "neutral"}
    if value < 80:
        return {"label": "偏高", "tone": "warning"}
    return {"label": "高波动", "tone": "negative"}


def _percentile_playbook(value: float | None) -> list[dict[str, Any]]:
    return _band_rows(
        value=value,
        rows=[
            (None, 25, "低分位", "警惕松弛"),
            (25, 60, "正常", "常规跟踪"),
            (60, 80, "偏高", "提高防守"),
            (80, None, "高分位", "优先控险"),
        ],
    )


def _iv_percentile_reading(value: float | None) -> str:
    if value is None:
        return "VIX 历史样本不足，暂不能计算隐含波动率分位。"
    if value >= 80:
        return f"VIX 位于近90日较高分位 {value}%，市场保护需求明显升温。"
    if value >= 60:
        return f"VIX 分位 {value}% 偏高，说明回撤保护需求正在抬升。"
    if value <= 25:
        return f"VIX 分位 {value}% 较低，市场偏放松，但低波动也可能隐藏拥挤。"
    return f"VIX 分位 {value}% 处于常态区间，波动率压力暂不突出。"


def _put_call_badge(value: float | None) -> dict[str, str]:
    if value is None:
        return {"label": "缺数据", "tone": "neutral"}
    if value < 0.75:
        return {"label": "偏贪婪", "tone": "warning"}
    if value <= 1.1:
        return {"label": "中性", "tone": "neutral"}
    if value <= 1.35:
        return {"label": "防守上升", "tone": "warning"}
    return {"label": "恐惧偏高", "tone": "negative"}


def _put_call_playbook(value: float | None) -> list[dict[str, Any]]:
    return _band_rows(
        value=value,
        rows=[
            (None, 0.75, "偏贪婪", "谨慎追高"),
            (0.75, 1.1, "中性", "常规跟踪"),
            (1.1, 1.35, "防守上升", "检查风险"),
            (1.35, None, "恐惧偏高", "等待确认"),
        ],
    )


def _volume_anomaly_badge(value: float | None) -> dict[str, str]:
    if value is None:
        return {"label": "缺数据", "tone": "neutral"}
    if value < 0.75:
        return {"label": "缩量", "tone": "neutral"}
    if value < 1.25:
        return {"label": "正常", "tone": "positive"}
    if value < 1.8:
        return {"label": "放量", "tone": "warning"}
    return {"label": "异常放量", "tone": "negative"}


def _volume_anomaly_playbook(value: float | None) -> list[dict[str, Any]]:
    return _band_rows(
        value=value,
        rows=[
            (None, 0.75, "缩量", "信号较弱"),
            (0.75, 1.25, "正常", "常规跟踪"),
            (1.25, 1.8, "放量", "关注扩散"),
            (1.8, None, "异常放量", "检查风险"),
        ],
    )


def _volume_anomaly_reading(value: float | None) -> str:
    if value is None:
        return "SPY/QQQ 成交量样本不足，暂不能计算量能异常。"
    if value >= 1.8:
        return f"SPY/QQQ 最新成交量约为20日均量的 {value} 倍，市场交易拥挤度明显上升。"
    if value >= 1.25:
        return f"SPY/QQQ 最新成交量约为20日均量的 {value} 倍，说明资金分歧或趋势确认正在增强。"
    if value < 0.75:
        return f"SPY/QQQ 最新成交量约为20日均量的 {value} 倍，当前价格信号缺少量能确认。"
    return f"SPY/QQQ 最新成交量约为20日均量的 {value} 倍，量能处于常态。"


def _fear_greed_badge(value: float | None) -> dict[str, str]:
    if value is None:
        return {"label": "缺数据", "tone": "neutral"}
    if value < 25:
        return {"label": "极度恐惧", "tone": "negative"}
    if value < 45:
        return {"label": "恐惧", "tone": "warning"}
    if value <= 55:
        return {"label": "中性", "tone": "neutral"}
    if value <= 75:
        return {"label": "贪婪", "tone": "warning"}
    return {"label": "极度贪婪", "tone": "negative"}


def _fear_greed_playbook(value: float | None) -> list[dict[str, Any]]:
    return _band_rows(
        value=value,
        rows=[
            (None, 25, "极度恐惧", "加倍深入"),
            (25, 45, "恐惧", "加大定投"),
            (45, 55, "中性", "常规定投"),
            (55, 75, "贪婪", "谨慎追高"),
            (75, None, "极度贪婪", "部分止盈"),
        ],
    )


def _band_rows(
    *,
    value: float | None,
    rows: list[tuple[float | None, float | None, str, str]],
) -> list[dict[str, Any]]:
    result = []
    for lower, upper, label, action in rows:
        active = value is not None and (lower is None or value >= lower) and (upper is None or value < upper)
        if value is not None and upper is None and lower is not None:
            active = value >= lower
        result.append(
            {
                "range": _band_label(lower, upper),
                "label": label,
                "action": action,
                "active": active,
            }
        )
    return result


def _band_label(lower: float | None, upper: float | None) -> str:
    if lower is None and upper is not None:
        return f"< {upper:g}"
    if lower is not None and upper is None:
        return f"> {lower:g}"
    if lower is not None and upper is not None:
        return f"{lower:g}-{upper:g}"
    return "-"


def _market_playbook_rows(*, fear_greed: float, rsi: float | None, vix: float | None) -> list[dict[str, Any]]:
    return [
        {"name": "情绪", "value": fear_greed, "badge": _fear_greed_badge(fear_greed), "rows": _fear_greed_playbook(fear_greed)},
        {"name": "强弱", "value": rsi, "badge": _rsi_badge(rsi), "rows": _rsi_playbook(rsi)},
        {"name": "波动", "value": vix, "badge": _vix_badge(vix), "rows": _vix_playbook(vix)},
    ]


def _market_strategy_rows(
    *,
    regime: str,
    rsi: float | None,
    fear_greed: float,
    vix: float | None,
    volume_anomaly: float | None,
    put_call_proxy: float,
) -> list[dict[str, Any]]:
    if regime in {"拥挤多头", "亢奋动量"} or fear_greed >= 65 or (rsi is not None and rsi >= 70):
        posture = "回调观察"
        detail = "等待高权重成长持仓回到更健康位置，避免在拥挤强势区追加同方向风险。"
        tone = "warning"
        sizing = "观察仓/小仓位"
    elif regime in {"投降区间", "恐慌压缩"} or fear_greed <= 35 or (vix is not None and vix >= 30):
        posture = "防守优先"
        detail = "先确认市场压力是否扩散到核心持仓，再寻找相对强势标的。"
        tone = "negative"
        sizing = "防守仓位"
    else:
        posture = "定投继续"
        detail = "长期逻辑未被本地指标否定，重点跟踪组合广度和高权重持仓是否背离。"
        tone = "positive"
        sizing = "常规节奏"
    rsi_text = "-" if rsi is None else f"{rsi:.1f}"
    volume_text = "量能样本不足" if volume_anomaly is None else f"量能 {volume_anomaly}x"
    return [
        {
            "label": "今日市场",
            "value": posture,
            "summary": f"当前QQQ RSI={rsi_text}，市场处于{regime}。建议新仓位控制在{sizing}，已持仓标的今日大跌须区分基本面恶化 vs 市场拖累。",
            "detail": detail,
            "tone": tone,
            "crowding_proxy": put_call_proxy,
            "volume_anomaly": volume_anomaly,
            "context": f"拥挤代理 {put_call_proxy}x，{volume_text}",
        },
    ]


def _weight_change_scatter_chart(positions: list[dict], total: float) -> EChartsPayload:
    points = [
        {
            "name": str(position.get("symbol", "") or "").upper(),
            "x": round(_position_weight(position, total) * 100, 2),
            "y": round(_to_float(position.get("daily_change_pct")) * 100, 2),
            "value": round(abs(_position_value(position)), 2),
        }
        for position in positions
        if position.get("symbol")
    ]
    return EChartsPayload(
        chart_type="scatter",
        title="持仓权重 vs 当日涨跌",
        unit="percent",
        status=AnalysisStatus.READY if points else AnalysisStatus.MISSING_DATA,
        source="portfolio_positions",
        series=[EChartsSeries(name="持仓", points=points)] if points else [],
        options={
            "x_label": "组合权重 %",
            "y_label": "当日涨跌 %",
            "description": "右上代表高权重且上涨，右下代表高权重且拖累组合。",
        },
    )


def _price_correlation_heat_chart(symbols: list[str], histories: dict[str, list[MarketDataPoint]]) -> EChartsPayload:
    ready_symbols = [symbol for symbol in symbols[:5] if len(_daily_returns(histories.get(symbol, []))) >= 20]
    points: list[dict[str, float | int | str | None]] = []
    for x_index, left in enumerate(ready_symbols):
        for y_index, right in enumerate(ready_symbols):
            if left == right:
                value = 1.0
            else:
                value = _return_correlation(histories.get(left, []), histories.get(right, []))
            points.append({"x": x_index, "y": y_index, "name": f"{left}/{right}", "value": None if value is None else round(value * 100, 2)})
    return EChartsPayload(
        chart_type="heatmap",
        title="真实价格相关性热力图",
        unit="percent",
        status=AnalysisStatus.READY if len(ready_symbols) >= 2 else AnalysisStatus.MISSING_DATA,
        source="market_data_provider_daily_returns",
        series=[EChartsSeries(name="价格相关性", points=points)] if len(ready_symbols) >= 2 else [],
        options={
            "x_labels": ready_symbols,
            "y_labels": ready_symbols,
            "min": -100,
            "max": 100,
            "description": "基于前五大持仓最近约90个交易日的日收益率计算 Pearson 相关性。",
        },
    )


def _qqq_beta_chart(
    positions: list[dict],
    total: float,
    symbols: list[str],
    histories: dict[str, list[MarketDataPoint]],
    benchmark_history: list[MarketDataPoint],
) -> EChartsPayload:
    points: list[dict[str, float | int | str | None]] = []
    portfolio_beta = 0.0
    portfolio_weight = 0.0
    for symbol in symbols[:8]:
        position = next((row for row in positions if str(row.get("symbol", "") or "").upper() == symbol), {})
        beta = _return_beta(histories.get(symbol, []), benchmark_history)
        if beta is None:
            continue
        weight = _position_weight(position, total)
        portfolio_beta += beta * weight
        portfolio_weight += weight
        points.append({"name": symbol, "value": round(beta, 2), "weight": round(weight * 100, 2)})
    if points and portfolio_weight > 0:
        points.insert(0, {"name": "组合估算", "value": round(portfolio_beta, 2), "weight": round(portfolio_weight * 100, 2)})
    return EChartsPayload(
        chart_type="bar",
        title="近90日组合 Beta / QQQ 敏感度",
        unit="beta",
        status=AnalysisStatus.READY if points else AnalysisStatus.MISSING_DATA,
        source="market_data_provider_90d_daily_returns",
        series=[EChartsSeries(name="Beta", points=points)] if points else [],
        options={"description": "Beta 使用近90日持仓日收益率相对 QQQ 日收益率的协方差/方差估算，>1 表示这段时间内比 QQQ 更高弹性。"},
    )


def _valuation_crowding_matrix(positions: list[dict], total: float, *, provider_name: str) -> EChartsPayload:
    points: list[dict[str, float | int | str | None]] = []
    if provider_name == "longbridge":
        for position in sorted(positions, key=lambda row: abs(_position_value(row)), reverse=True)[:8]:
            symbol = str(position.get("symbol", "") or "").upper()
            latest = _latest_valuation_rank(symbol)
            percentile = _valuation_percentile(latest)
            if percentile is None:
                continue
            points.append(
                {
                    "name": symbol,
                    "x": round(_position_weight(position, total) * 100, 2),
                    "y": percentile,
                    "value": round(abs(_position_value(position)), 2),
                }
            )
    return EChartsPayload(
        chart_type="scatter",
        title="估值拥挤矩阵",
        unit="percent",
        status=AnalysisStatus.READY if points else AnalysisStatus.MISSING_DATA,
        source="longbridge_valuation_rank",
        series=[EChartsSeries(name="估值位置", points=points)] if points else [],
        options={
            "x_label": "组合权重 %",
            "y_label": "行业估值位置 %",
            "description": "横轴是组合权重，纵轴优先使用长桥 PE 行业位置；PE 缺失时回退到 PB/PS。右上角表示重仓且估值相对拥挤。",
        },
    )


def _position_weight(position: dict, total: float) -> float:
    return 0.0 if total == 0 else abs(_position_value(position)) / total


def _daily_returns(history: list[MarketDataPoint]) -> dict[str, float]:
    closes = [(point.date, float(point.close)) for point in history if point.date and point.close is not None and float(point.close) > 0]
    returns: dict[str, float] = {}
    for index in range(1, len(closes)):
        previous = closes[index - 1][1]
        current = closes[index][1]
        if previous > 0:
            returns[closes[index][0]] = current / previous - 1
    return returns


def _return_correlation(left: list[MarketDataPoint], right: list[MarketDataPoint]) -> float | None:
    left_returns = _daily_returns(left)
    right_returns = _daily_returns(right)
    common = sorted(set(left_returns) & set(right_returns))
    if len(common) < 20:
        return None
    xs = [left_returns[day] for day in common]
    ys = [right_returns[day] for day in common]
    return _correlation(xs, ys)


def _return_beta(stock: list[MarketDataPoint], benchmark: list[MarketDataPoint]) -> float | None:
    stock_returns = _daily_returns(stock)
    benchmark_returns = _daily_returns(benchmark)
    common = sorted(set(stock_returns) & set(benchmark_returns))
    if len(common) < 20:
        return None
    xs = [stock_returns[day] for day in common]
    ys = [benchmark_returns[day] for day in common]
    variance = _variance(ys)
    if variance <= 0:
        return None
    return _covariance(xs, ys) / variance


def _correlation(xs: list[float], ys: list[float]) -> float | None:
    denominator = (_variance(xs) * _variance(ys)) ** 0.5
    if denominator <= 0:
        return None
    return max(-1.0, min(1.0, _covariance(xs, ys) / denominator))


def _covariance(xs: list[float], ys: list[float]) -> float:
    x_mean = mean(xs)
    y_mean = mean(ys)
    return mean([(x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)])


def _variance(values: list[float]) -> float:
    value_mean = mean(values)
    return mean([(value - value_mean) ** 2 for value in values])


@lru_cache(maxsize=256)
def _latest_valuation_rank(symbol: str) -> dict[str, Any]:
    end = date.today()
    start = end - timedelta(days=45)
    ranks = fetch_longbridge_valuation_rank(symbol, start_date=start.isoformat(), end_date=end.isoformat())
    if not ranks:
        return {}
    return ranks[sorted(ranks)[-1]]


def _valuation_percentile(rank: dict[str, Any]) -> float | None:
    for key in ("pe_percentile", "pe_ttm_percentile", "pb_percentile", "ps_percentile"):
        value = _to_float(rank.get(key))
        if value > 0:
            return round(value, 2)
    return None


def _hedge_suggestions(response: PortfolioRiskSection, sector_rows: list[dict]) -> list[str]:
    suggestions = []
    single = _to_float(response.concentration.get("single_name").value if response.concentration.get("single_name") else None)
    ai = _to_float(response.concentration.get("ai_theme").value if response.concentration.get("ai_theme") else None)
    top_sector = sector_rows[0]["industry"] if sector_rows else "未知行业"
    if single >= 25:
        suggestions.append("单一持仓权重偏高，防御思路应优先从降低单票波动贡献开始。")
    if ai >= 40:
        suggestions.append("智能主题暴露较高，需重点跟踪大型云厂商资本开支、半导体需求和高弹性成长股风险偏好。")
    if sector_rows and sector_rows[0]["weight"] >= 0.35:
        suggestions.append(f"{top_sector} 是最大行业来源，可用低相关资产或现金比例来降低主题同步回撤。")
    if not suggestions:
        suggestions.append("当前组合未触发极端集中阈值，防御重点是保持现金缓冲并跟踪高权重持仓事件。")
    return suggestions


def _risk_alerts(
    response: PortfolioRiskSection,
    positions: list[dict],
    sector_rows: list[dict],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    single = _metric_number(response.concentration.get("single_name"))
    ai = _metric_number(response.concentration.get("ai_theme"))
    top3 = _metric_number(response.correlation.get("top3_weight"))
    downside = _metric_number(response.tail_risk.get("downside_breadth"))
    ai_capex = _metric_number(response.macro_sensitivity.get("ai_capex"))
    if single >= 25:
        alerts.append(
            {
                "severity": "high",
                "title": "单票集中度偏高",
                "detail": f"最大单一持仓约 {round(single, 2)}%，单只股票波动会直接改变组合风险。",
                "source": "portfolio_positions",
            }
        )
    if ai >= 40:
        alerts.append(
            {
                "severity": "high",
                "title": "智能主题 / 半导体主题拥挤",
                "detail": f"智能主题暴露约 {round(ai, 2)}%，需重点跟踪云厂商资本开支和半导体周期。",
                "source": "portfolio_theme_rules",
            }
        )
    if top3 >= 60:
        alerts.append(
            {
                "severity": "medium",
                "title": "前三大持仓权重集中",
                "detail": f"前三大持仓合计约 {round(top3, 2)}%，表面分散可能仍由少数股票主导。",
                "source": "portfolio_positions",
            }
        )
    if downside >= 50:
        alerts.append(
            {
                "severity": "medium",
                "title": "下跌广度扩散",
                "detail": f"当前下跌持仓占比约 {round(downside, 2)}%，需要区分市场拖累和个股风险。",
                "source": "portfolio_positions",
            }
        )
    if ai_capex >= 75:
        alerts.append(
            {
                "severity": "medium",
                "title": "智能资本开支敏感度偏高",
                "detail": "组合对智能资本开支预期较敏感，若主线预期降温，估值弹性会被放大。",
                "source": "portfolio_rules",
            }
        )
    if sector_rows and sector_rows[0]["weight"] >= 0.35:
        alerts.append(
            {
                "severity": "medium",
                "title": "行业暴露集中",
                "detail": f"{sector_rows[0]['industry']} 权重约 {round(sector_rows[0]['weight'] * 100, 2)}%，主题同步回撤时分散效果有限。",
                "source": "industry_mapping",
            }
        )
    if not alerts and positions:
        alerts.append(
            {
                "severity": "low",
                "title": "未触发高优先级风险阈值",
                "detail": "当前主要任务是继续跟踪高权重持仓、市场基准和组合广度是否背离。",
                "source": "portfolio_rules",
            }
        )
    return alerts[:6]


def _metric_number(metric: StandardMetric | None) -> float:
    if metric is None or metric.value is None:
        return 0.0
    return _to_float(metric.value)


def _trend_score(history: list[MarketDataPoint], rsi: float | None) -> float | None:
    closes = [float(point.close) for point in history if point.close is not None]
    if len(closes) < 20:
        if rsi is None:
            return None
        return round(max(0.0, min(100.0, rsi)), 2)
    latest = closes[-1]
    ma20 = mean(closes[-20:])
    ma60 = mean(closes[-60:]) if len(closes) >= 60 else ma20
    ret20 = (latest / closes[-20] - 1) if closes[-20] else 0.0
    score = 50
    score += 18 if latest >= ma20 else -12
    score += 12 if latest >= ma60 else -8
    score += max(-15, min(15, ret20 * 100))
    if rsi is not None:
        score += (rsi - 50) * 0.25
    return round(max(0.0, min(100.0, score)), 2)


def _stock_sentiment_score(position: dict | None) -> float | None:
    if not position:
        return None
    daily_change = _to_float(position.get("daily_change_pct")) * 100
    unrealized = _to_float(position.get("unrealized_pnl_snapshot"))
    cost_basis = abs(_to_float(position.get("cost_basis_money")))
    pnl_ratio = 0.0 if cost_basis == 0 else max(-50.0, min(50.0, unrealized / cost_basis * 100))
    score = 50 + daily_change * 3 + pnl_ratio * 0.25
    return round(max(0.0, min(100.0, score)), 2)


def _stock_direction(daily_change_pct: float, rsi: float | None, trend_score: float | None) -> str:
    score = 0
    score += 1 if daily_change_pct > 0.01 else -1 if daily_change_pct < -0.01 else 0
    if rsi is not None:
        score += 1 if rsi >= 55 else -1 if rsi <= 45 else 0
    if trend_score is not None:
        score += 1 if trend_score >= 60 else -1 if trend_score <= 40 else 0
    if score >= 2:
        return "偏多"
    if score <= -2:
        return "偏空"
    return "中性"


def _stock_core_changes(symbol: str, position: dict | None, weight: float, rsi: float | None, trend_score: float | None) -> list[str]:
    if not position:
        return [f"{symbol} 不在当前持仓中，无法计算对组合的真实影响。"]
    daily_pct = round(_to_float(position.get("daily_change_pct")) * 100, 2)
    items = [f"{symbol} 当前组合权重约 {round(weight * 100, 2)}%，最新持仓日变化为 {daily_pct}%。"]
    if rsi is not None:
        items.append(f"{symbol} 相对强弱指数为 {rsi}，用于判断短期强弱，不代表基本面结论。")
    if trend_score is not None:
        items.append(f"趋势分为 {trend_score}，由价格均线、近 20 日收益和相对强弱指数共同计算。")
    return items


def _stock_portfolio_impact(symbol: str, position: dict | None, weight: float) -> list[str]:
    if not position:
        return [f"{symbol} 当前没有持仓权重，对组合没有直接仓位影响。"]
    daily_value_change = _to_float(position.get("daily_change")) * abs(_to_float(position.get("quantity")) or 1.0)
    return [
        f"{symbol} 对组合的主要影响来自 {round(weight * 100, 2)}% 的仓位权重。",
        f"按当前快照估算，单日市值影响约 {round(daily_value_change, 2)}。",
    ]


def _beneficiary_chain(industry: str, symbol: str) -> list[str]:
    text = industry.upper()
    if "半导体" in industry or "SEMICONDUCTOR" in text:
        return ["真正受益环节通常在算力需求、存储、处理器、图形处理器周期、晶圆与设备资本开支之间传导。"]
    if "AI" in text:
        return ["真正受益环节取决于智能应用收入能否覆盖持续资本开支。"]
    if "航天" in industry or symbol == "RKLB":
        return ["真正受益环节在发射订单、卫星制造、国防/商业航天预算兑现。"]
    if symbol == "TSLA":
        return ["真正受益或受损环节在交付、毛利率、自动驾驶叙事和能源业务兑现。"]
    return ["需要结合行业公告和财报数据确认利润池传导，本地数据暂不支持原文判断。"]


def _market_mispricing(symbol: str, daily_change_pct: float, unrealized: float, weight: float) -> list[str]:
    items = []
    if daily_change_pct < -0.03 and unrealized > 0:
        items.append(f"{symbol} 仍有未实现盈利但短期下跌较大，市场可能正在重新定价前期乐观预期。")
    elif daily_change_pct > 0.03 and weight >= 0.1:
        items.append(f"{symbol} 上涨会明显改善组合风险表观，但也可能提高单票依赖。")
    else:
        items.append("当前本地数据不足以判断市场误判，需结合新闻、财报和公告原文。")
    return items


def _stock_watch_signals(symbol: str, industry: str, direction: str | None) -> list[str]:
    signals = ["价格相对 20 日均线是否继续保持", "组合权重是否继续上升", "日内波动是否扩散到同主题持仓"]
    if "半导体" in industry:
        signals.append("半导体同业和智能资本开支预期是否同步变化")
    if symbol == "TSLA":
        signals.append("交付、毛利率与自动驾驶叙事是否支持当前估值")
    if direction == "偏空":
        signals.append("是否出现连续放量下跌或跌破关键均线")
    return signals[:4]


def _stock_risks(symbol: str, weight: float, daily_change_pct: float, sentiment: float | None) -> list[str]:
    risks = []
    if weight >= 0.15:
        risks.append(f"{symbol} 权重较高，单票波动会直接改变组合风险。")
    if daily_change_pct <= -0.03:
        risks.append(f"{symbol} 单日跌幅偏大，需要确认是否有基本面事件。")
    if sentiment is not None and sentiment < 35:
        risks.append("本地情绪代理偏弱，需避免只看未实现盈利而忽略短期风险。")
    return risks or ["当前未触发单票高风险规则，但仍需跟踪价格、财报与同主题持仓联动。"]


def _evidence_links(symbol: str) -> list[dict[str, str]]:
    return [
        {"label": "本地持仓快照", "url": f"/api/positions/{symbol}/detail"},
        {"label": "本地持仓分析接口", "url": f"/api/portfolio-analysis?section=stock&symbol={symbol}"},
    ]


def _market_narrative_metrics(
    response: MarketAnalysisSection,
    positions: list[dict],
    benchmark: str | None,
) -> dict[str, Any]:
    return {
        "market_regime": response.regime.model_dump(mode="json"),
        "benchmark": benchmark,
        "indicators": _metrics_for_narrative(response.indicators),
        "market_pulse": response.market_pulse,
        "playbook": response.playbook,
        "strategy": response.strategy,
        "portfolio_impact": response.portfolio_impact,
        "watch_symbols": response.watch_symbols,
        "opportunities": response.opportunities,
        "risks": response.risks,
        "top_positions": _top_position_rows(positions, _positions_total(positions), limit=5),
    }


def _line_chart(title: str, history: list[MarketDataPoint], *, source: str) -> EChartsPayload:
    points = [
        {"date": point.date, "value": point.close}
        for point in history
        if point.close is not None
    ]
    return EChartsPayload(
        chart_type="line",
        title=title,
        status=AnalysisStatus.READY if points else AnalysisStatus.MISSING_DATA,
        source=source,
        series=[EChartsSeries(name=title, points=points)] if points else [],
    )


def _multi_line_chart(
    title: str,
    series: list[tuple[str, list[MarketDataPoint]]],
    *,
    source: str,
) -> EChartsPayload:
    shaped_series: list[EChartsSeries] = []
    for name, history in series:
        closes = [float(point.close) for point in history if point.close is not None]
        first = closes[0] if closes else 0.0
        points = [
            {
                "date": point.date,
                "value": round((float(point.close) / first - 1) * 100, 2) if first and point.close is not None else None,
            }
            for point in history
            if point.close is not None
        ]
        if points:
            shaped_series.append(EChartsSeries(name=name, points=points[-90:]))
    return EChartsPayload(
        chart_type="line",
        title=title,
        unit="percent",
        status=AnalysisStatus.READY if shaped_series else AnalysisStatus.MISSING_DATA,
        source=source,
        series=shaped_series,
        options={"normalized": True},
    )


def _metrics_for_narrative(metrics: dict[str, StandardMetric]) -> dict[str, Any]:
    return {key: value.model_dump(mode="json") for key, value in metrics.items()}


def _combine_status(statuses: list[AnalysisStatus]) -> AnalysisStatus:
    if any(status == AnalysisStatus.READY for status in statuses):
        return AnalysisStatus.READY
    if any(status == AnalysisStatus.ERROR for status in statuses):
        return AnalysisStatus.ERROR
    return AnalysisStatus.MISSING_DATA


def _active_narrative(
    response: PortfolioAnalysisResponse,
    section: PortfolioAnalysisSectionKey | None,
) -> AINarrativePayload:
    if section == PortfolioAnalysisSectionKey.MARKET:
        return response.sections.market.narrative
    if section == PortfolioAnalysisSectionKey.PORTFOLIO:
        return response.sections.portfolio.narrative
    if section == PortfolioAnalysisSectionKey.STOCK:
        return response.sections.stock.narrative
    for narrative in (
        response.sections.market.narrative,
        response.sections.portfolio.narrative,
        response.sections.stock.narrative,
    ):
        if narrative.status in {AnalysisStatus.READY, AnalysisStatus.ERROR, AnalysisStatus.PENDING}:
            return narrative
    return response.integrations.ai


def _narrative_cache_key(section: PortfolioAnalysisSectionKey, symbol: str | None) -> str:
    if section == PortfolioAnalysisSectionKey.STOCK and symbol:
        return symbol.upper()
    return "default"


def _parse_yyyymmdd(value: str) -> date | None:
    normalized = value.replace("-", "")[:8]
    if len(normalized) != 8 or not normalized.isdigit():
        return None
    try:
        return date(int(normalized[:4]), int(normalized[4:6]), int(normalized[6:8]))
    except ValueError:
        return None
