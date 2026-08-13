from dataclasses import dataclass
from datetime import date
from datetime import timedelta
from functools import lru_cache
import importlib
from typing import Protocol

import httpx

from app.services.quote_service import QuoteService
from app.services.quote_service import fetch_benchmark_history
from app.services.quote_service import fetch_longbridge_candles
from app.services.quote_service import fetch_longbridge_quote
from app.utils.longbridge import longbridge_records
from app.utils.longbridge import run_longbridge_json
from app.utils.numbers import optional_float as _float_or_none
from app.utils.records import first_record as _first_record
from app.utils.records import records as _records
from app.utils.symbols import load_futu_module
from app.utils.symbols import normalize_futu_symbol as _normalize_futu_symbol
from app.utils.symbols import normalize_longbridge_symbol as _normalize_longbridge_symbol


@dataclass(slots=True)
class MarketDataPoint:
    date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    source: str = "unknown"


class MarketDataProvider(Protocol):
    name: str

    def get_quote(self, symbol: str) -> dict: ...
    def get_kline_history(self, symbol: str, *, days: int = 90) -> list[MarketDataPoint]: ...
    def get_sentiment(self, symbol: str) -> dict: ...


class QuoteFallbackMarketDataProvider:
    name = "quote_fallback"

    def __init__(self, quote_service: QuoteService | None = None) -> None:
        self._quote_service = quote_service

    def get_quote(self, symbol: str) -> dict:
        if self._quote_service is None:
            return {"status": "unavailable", "symbol": symbol.upper(), "price": None, "source": self.name}
        quote = self._quote_service.get_quote(symbol.upper())
        return {"status": quote.status, "symbol": quote.symbol, "price": quote.price, "source": quote.source}

    def get_kline_history(self, symbol: str, *, days: int = 90) -> list[MarketDataPoint]:
        end_date = date.today()
        start_date = end_date - timedelta(days=max(days * 2, days + 30))
        rows = fetch_benchmark_history(
            symbol.upper(),
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )
        return [
            MarketDataPoint(
                date=str(row.get("date", "")),
                close=_float_or_none(row.get("value")),
                source=str(row.get("source", self.name) or self.name),
            )
            for row in rows[-days:]
            if row.get("date") and _float_or_none(row.get("value")) is not None
        ]

    def get_sentiment(self, symbol: str) -> dict:
        return {"status": "missing_data", "symbol": symbol.upper(), "source": self.name}


class FutuOpenDReadOnlyProvider:
    name = "futu_opend"

    def __init__(self, *, host: str = "127.0.0.1", port: int = 11111) -> None:
        self.host = host
        self.port = port

    def get_quote(self, symbol: str) -> dict:
        code = _normalize_futu_symbol(symbol)
        futu = _load_futu_module()
        if futu is None:
            return _unavailable_quote(code, self.name, "futu_api_package_not_installed")
        try:
            quote_ctx = futu.OpenQuoteContext(host=self.host, port=self.port)
            try:
                ret, data = quote_ctx.get_market_snapshot([code])
            finally:
                quote_ctx.close()
        except Exception as exc:
            return _unavailable_quote(code, self.name, f"futu_snapshot_failed: {exc}")
        if ret != futu.RET_OK:
            return _unavailable_quote(code, self.name, f"futu_snapshot_failed: {data}")
        row = _first_record(data)
        if row is None:
            return _unavailable_quote(code, self.name, "futu_snapshot_empty")
        price = _float_or_none(row.get("last_price"))
        if price is None:
            price = _float_or_none(row.get("close_price_5min"))
        if price is None:
            price = _float_or_none(row.get("prev_close_price"))
        if price is None:
            return _unavailable_quote(code, self.name, "futu_snapshot_missing_price")
        return {
            "status": "ready",
            "symbol": code,
            "price": price,
            "source": self.name,
            "as_of": str(row.get("update_time") or ""),
        }

    def get_kline_history(self, symbol: str, *, days: int = 90) -> list[MarketDataPoint]:
        code = _normalize_futu_symbol(symbol)
        futu = _load_futu_module()
        if futu is None:
            return []
        end_date = date.today()
        lookback_days = max(days * 2, days + 30)
        start_date = end_date - timedelta(days=lookback_days)
        try:
            quote_ctx = futu.OpenQuoteContext(host=self.host, port=self.port)
            try:
                ret, data, _page_req_key = quote_ctx.request_history_kline(
                    code,
                    start=start_date.isoformat(),
                    end=end_date.isoformat(),
                    ktype=futu.KLType.K_DAY,
                    autype=futu.AuType.QFQ,
                    max_count=max(1, min(lookback_days, 1000)),
                )
            finally:
                quote_ctx.close()
        except Exception:
            return []
        if ret != futu.RET_OK:
            return []
        points: list[MarketDataPoint] = []
        for row in _records(data)[-days:]:
            raw_date = str(row.get("time_key") or row.get("date") or row.get("time") or "")[:10]
            if not raw_date:
                continue
            points.append(
                MarketDataPoint(
                    date=raw_date,
                    open=_float_or_none(row.get("open")),
                    high=_float_or_none(row.get("high")),
                    low=_float_or_none(row.get("low")),
                    close=_float_or_none(row.get("close")),
                    volume=_float_or_none(row.get("volume")),
                    source=self.name,
                )
            )
        return points

    def get_sentiment(self, symbol: str) -> dict:
        return {
            "status": "missing_data",
            "symbol": symbol.upper(),
            "source": self.name,
            "reason": "futu_sentiment_unavailable",
        }


class LongbridgeReadOnlyProvider:
    name = "longbridge"

    def get_quote(self, symbol: str) -> dict:
        code = _normalize_longbridge_symbol(symbol)
        price = fetch_longbridge_quote(code)
        if price is None:
            return _unavailable_quote(code, self.name, "longbridge_quote_unavailable")
        return {
            "status": "ready",
            "symbol": code,
            "price": price,
            "source": self.name,
            "as_of": "",
        }

    def get_kline_history(self, symbol: str, *, days: int = 90) -> list[MarketDataPoint]:
        code = _normalize_longbridge_symbol(symbol)
        end_date = date.today()
        start_date = end_date - timedelta(days=max(days * 2, days + 30))
        rows = fetch_longbridge_candles(
            code,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )
        return [
            MarketDataPoint(
                date=str(row.get("date", "")),
                open=_float_or_none(row.get("open")),
                high=_float_or_none(row.get("high")),
                low=_float_or_none(row.get("low")),
                close=_float_or_none(row.get("close")),
                volume=_float_or_none(row.get("volume")),
                source=self.name,
            )
            for row in rows[-days:]
            if row.get("date") and _float_or_none(row.get("close")) is not None
        ]

    def get_sentiment(self, symbol: str) -> dict:
        normalized = str(symbol or "").strip().upper()
        if normalized in {"US", "US_MARKET", "MARKET"}:
            return _fetch_longbridge_market_temperature("US")
        return _fetch_longbridge_topic_sentiment(_normalize_longbridge_symbol(symbol))


def build_market_data_provider(
    settings: object,
    quote_service: QuoteService | None = None,
) -> MarketDataProvider:
    mode = getattr(settings, "futu_connection_mode", "disabled")
    if mode == "local_opend":
        return build_futu_opend_provider(settings)
    if mode == "longbridge":
        return LongbridgeReadOnlyProvider()
    return QuoteFallbackMarketDataProvider(quote_service)


def build_futu_opend_provider(settings: object) -> FutuOpenDReadOnlyProvider:
    return FutuOpenDReadOnlyProvider(
        host=str(getattr(settings, "futu_opend_host", "127.0.0.1")),
        port=int(getattr(settings, "futu_opend_port", 11111)),
    )


def _load_futu_module():
    return load_futu_module(importlib.import_module)


@lru_cache(maxsize=4)
def fetch_cnn_fear_greed() -> dict:
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Referer": "https://www.cnn.com/markets/fear-and-greed",
    }
    try:
        response = httpx.get(url, headers=headers, timeout=8.0)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return {
            "status": "missing_data",
            "symbol": "CNN_FEAR_GREED",
            "source": "cnn_fear_greed",
            "reason": f"cnn_fear_greed_unavailable: {exc}",
        }
    score = _cnn_score(payload)
    if score is None:
        return {
            "status": "missing_data",
            "symbol": "CNN_FEAR_GREED",
            "source": "cnn_fear_greed",
            "reason": "cnn_fear_greed_score_missing",
        }
    return {
        "status": "ready",
        "symbol": "CNN_FEAR_GREED",
        "source": "cnn_fear_greed",
        "value": score,
        "rating": _cnn_rating(payload),
        "as_of": str(payload.get("timestamp") or payload.get("last_update") or ""),
    }


def _unavailable_quote(symbol: str, source: str, reason: str) -> dict:
    return {
        "status": "unavailable",
        "symbol": symbol,
        "price": None,
        "source": source,
        "reason": reason,
    }


@lru_cache(maxsize=8)
def _fetch_longbridge_market_temperature(market: str) -> dict:
    rows = longbridge_records(run_longbridge_json(["market-temp", market, "--format", "json"]))
    if not rows:
        return {
            "status": "missing_data",
            "symbol": f"{market}_MARKET",
            "source": "longbridge_market_temp",
            "reason": "longbridge_market_temp_unavailable",
        }
    values = {_clean_label(row.get("field")): row.get("value") for row in rows if isinstance(row, dict)}
    temperature = _float_or_none(values.get("temperature"))
    sentiment = _float_or_none(values.get("sentiment"))
    valuation = _float_or_none(values.get("valuation"))
    if temperature is None and sentiment is None:
        return {
            "status": "missing_data",
            "symbol": f"{market}_MARKET",
            "source": "longbridge_market_temp",
            "reason": "longbridge_market_temp_missing_value",
        }
    return {
        "status": "ready",
        "symbol": f"{market}_MARKET",
        "source": "longbridge_market_temp",
        "value": temperature if temperature is not None else sentiment,
        "temperature": temperature,
        "sentiment": sentiment,
        "valuation": valuation,
        "description": str(values.get("description") or ""),
    }


@lru_cache(maxsize=128)
def _fetch_longbridge_topic_sentiment(symbol: str) -> dict:
    rows = longbridge_records(run_longbridge_json(["topic", "search", symbol, "--format", "json"]))
    if not rows:
        return {
            "status": "missing_data",
            "symbol": symbol,
            "source": "longbridge_topic",
            "reason": "longbridge_topic_unavailable",
        }
    likes = sum(int(_float_or_none(row.get("likes_count")) or 0) for row in rows if isinstance(row, dict))
    comments = sum(int(_float_or_none(row.get("comments_count")) or 0) for row in rows if isinstance(row, dict))
    count = len([row for row in rows if isinstance(row, dict)])
    heat = min(100.0, count * 10 + likes * 0.12 + comments * 0.18)
    return {
        "status": "ready",
        "symbol": symbol,
        "source": "longbridge_topic",
        "value": round(heat, 2),
        "topic_count": count,
        "likes_count": likes,
        "comments_count": comments,
        "top_topics": [
            {
                "title": str(row.get("title") or ""),
                "url": str(row.get("url") or ""),
                "likes_count": int(_float_or_none(row.get("likes_count")) or 0),
                "comments_count": int(_float_or_none(row.get("comments_count")) or 0),
            }
            for row in rows[:3]
            if isinstance(row, dict)
        ],
    }


def _cnn_score(payload: dict) -> float | None:
    candidates = [
        payload.get("fear_and_greed", {}).get("score") if isinstance(payload.get("fear_and_greed"), dict) else None,
        payload.get("fear_and_greed", {}).get("value") if isinstance(payload.get("fear_and_greed"), dict) else None,
        payload.get("score"),
        payload.get("value"),
    ]
    for candidate in candidates:
        value = _float_or_none(candidate)
        if value is not None:
            return round(value, 2)
    data = payload.get("fear_and_greed_historical", {}).get("data") if isinstance(payload.get("fear_and_greed_historical"), dict) else None
    if isinstance(data, list) and data:
        latest = data[-1]
        if isinstance(latest, dict):
            return _float_or_none(latest.get("y") or latest.get("value"))
    return None


def _cnn_rating(payload: dict) -> str:
    fear_and_greed = payload.get("fear_and_greed")
    if isinstance(fear_and_greed, dict):
        rating = fear_and_greed.get("rating") or fear_and_greed.get("classification")
        if rating:
            return str(rating)
    return ""


def _clean_label(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def calculate_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(closes, closes[1:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))
    recent_gains = gains[-period:]
    recent_losses = losses[-period:]
    average_gain = sum(recent_gains) / period
    average_loss = sum(recent_losses) / period
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    rs = average_gain / average_loss
    return round(100 - (100 / (1 + rs)), 2)
