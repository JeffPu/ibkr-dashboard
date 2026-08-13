from dataclasses import dataclass
from datetime import date
from datetime import timedelta
from datetime import timezone, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
from threading import RLock
from time import monotonic
from typing import Callable
import httpx

from app.utils.dates import parse_iso_date as _parse_iso_date
from app.utils.longbridge import first_longbridge_record as _first_longbridge_record
from app.utils.longbridge import longbridge_records as _longbridge_records
from app.utils.longbridge import run_longbridge_json
from app.utils.numbers import first_number as _first_number
from app.utils.records import records as _records
from app.utils.symbols import load_futu_module as _load_futu_module
from app.utils.symbols import normalize_futu_symbol as _normalize_futu_symbol
from app.utils.symbols import normalize_longbridge_symbol as _normalize_longbridge_symbol

_HISTORY_CACHE_TTL_SECONDS = 30 * 60
_history_cache: dict[tuple[str, str, str, str], tuple[float, list[dict]]] = {}
_YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
_YAHOO_CHART_HOSTS = (
    "query1.finance.yahoo.com",
    "query2.finance.yahoo.com",
)
_NASDAQ_HISTORY_MAP = {
    "^GSPC": ("SPY", "etf", "nasdaq_spy_proxy"),
    "GSPC": ("SPY", "etf", "nasdaq_spy_proxy"),
    "SP500": ("SPY", "etf", "nasdaq_spy_proxy"),
    "^IXIC": ("COMP", "index", "nasdaq"),
    "IXIC": ("COMP", "index", "nasdaq"),
    "COMP": ("COMP", "index", "nasdaq"),
    "^NDX": ("NDX", "index", "nasdaq"),
    "NDX": ("NDX", "index", "nasdaq"),
    "QQQ": ("QQQ", "etf", "nasdaq"),
}
_DEFAULT_HISTORY_CACHE: "MarketHistoryCache | None" = None


@dataclass(slots=True)
class QuoteResult:
    symbol: str
    price: float
    source: str
    is_realtime: bool
    as_of: str | None = None


class QuoteService:
    def __init__(
        self,
        primary_fetcher: Callable[[str], float | None],
        secondary_fetcher: Callable[[str], float | None],
        snapshot_fetcher: Callable[[str], float],
    ) -> None:
        self.primary_fetcher = primary_fetcher
        self.secondary_fetcher = secondary_fetcher
        self.snapshot_fetcher = snapshot_fetcher

    def get_latest_quote(self, symbol: str) -> QuoteResult:
        primary_price = self.primary_fetcher(symbol)
        if primary_price is not None:
            quote = QuoteResult(
                symbol=symbol,
                price=primary_price,
                source="primary",
                is_realtime=True,
                as_of=datetime.now(timezone.utc).isoformat(),
            )
            return quote

        secondary_price = self.secondary_fetcher(symbol)
        if secondary_price is not None:
            quote = QuoteResult(
                symbol=symbol,
                price=secondary_price,
                source="secondary",
                is_realtime=True,
                as_of=datetime.now(timezone.utc).isoformat(),
            )
            return quote

        return self.get_snapshot_quote(symbol)

    def get_snapshot_quote(self, symbol: str) -> QuoteResult:
        snapshot_price = self.snapshot_fetcher(symbol)
        return QuoteResult(symbol=symbol, price=snapshot_price, source="snapshot", is_realtime=False)

    def refresh_quotes(self, symbols: list[str]) -> dict[str, int]:
        refreshed = 0
        failed = 0
        for symbol in symbols:
            if not symbol:
                continue
            try:
                quote = self.get_latest_quote(symbol)
                if quote.is_realtime:
                    refreshed += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
        return {"total": len(symbols), "refreshed": refreshed, "failed": failed}


class MarketHistoryCache:
    """Persistent cache for immutable daily history ranges."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else _default_history_cache_path()
        self._lock = RLock()

    def get_points(self, symbol: str, start_date: str, end_date: str) -> list[dict]:
        start = _parse_iso_date(start_date)
        end = _parse_iso_date(end_date)
        if start is None or end is None or start > end:
            return []
        with self._lock:
            entry = self._symbol_entry(self._read(), symbol)
            points = []
            for point_date, point in entry.get("points", {}).items():
                parsed_date = _parse_iso_date(str(point_date))
                if parsed_date is None or parsed_date < start or parsed_date > end:
                    continue
                value = _coerce_history_value(point.get("value"))
                if value is None:
                    continue
                points.append(
                    {
                        "date": parsed_date.isoformat(),
                        "value": value,
                        "source": str(point.get("source", "cache") or "cache"),
                    }
                )
            points.sort(key=lambda point: str(point.get("date", "")))
            return points

    def missing_ranges(self, symbol: str, start_date: str, end_date: str) -> list[tuple[str, str]]:
        start = _parse_iso_date(start_date)
        end = _parse_iso_date(end_date)
        if start is None or end is None or start > end:
            return []
        with self._lock:
            entry = self._symbol_entry(self._read(), symbol)
            coverage = _normalize_coverage(entry.get("coverage", []))
        return [
            (missing_start.isoformat(), missing_end.isoformat())
            for missing_start, missing_end in _subtract_coverage(start, end, coverage)
        ]

    def store_range(self, symbol: str, start_date: str, end_date: str, points: list[dict]) -> None:
        start = _parse_iso_date(start_date)
        end = _parse_iso_date(end_date)
        if start is None or end is None or start > end or not points:
            return
        with self._lock:
            payload = self._read()
            entry = self._symbol_entry(payload, symbol)
            stored_points = entry.setdefault("points", {})
            for point in points:
                point_date = _parse_iso_date(str(point.get("date", "")))
                value = _coerce_history_value(point.get("value"))
                if point_date is None or value is None:
                    continue
                stored_points[point_date.isoformat()] = {
                    "date": point_date.isoformat(),
                    "value": value,
                    "source": str(point.get("source", "external") or "external"),
                }
            coverage = _normalize_coverage(entry.get("coverage", []))
            coverage.append((start, end))
            entry["coverage"] = [
                {"start": item_start.isoformat(), "end": item_end.isoformat()}
                for item_start, item_end in _merge_coverage(coverage)
            ]
            self._write(payload)

    def _symbol_entry(self, payload: dict, symbol: str) -> dict:
        symbols = payload.setdefault("symbols", {})
        symbol_key = symbol.upper()
        entry = symbols.setdefault(symbol_key, {"points": {}, "coverage": []})
        if not isinstance(entry.get("points"), dict):
            entry["points"] = {}
        if not isinstance(entry.get("coverage"), list):
            entry["coverage"] = []
        return entry

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "symbols": {}}
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "symbols": {}}
        if not isinstance(payload, dict):
            return {"version": 1, "symbols": {}}
        payload.setdefault("version", 1)
        if not isinstance(payload.get("symbols"), dict):
            payload["symbols"] = {}
        return payload

    def _write(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        tmp_path.replace(self.path)


def _default_history_cache_path() -> Path:
    configured = (
        os.getenv("IBKR_DASHBOARD_HISTORY_CACHE_PATH")
        or os.getenv("MARKET_HISTORY_CACHE_PATH")
    )
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "ibkr-dashboard" / "market-history.json"


def _default_market_history_cache() -> MarketHistoryCache:
    global _DEFAULT_HISTORY_CACHE
    if _DEFAULT_HISTORY_CACHE is None:
        _DEFAULT_HISTORY_CACHE = MarketHistoryCache()
    return _DEFAULT_HISTORY_CACHE


def _coerce_history_value(value: object) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    return round(price, 4)


def _normalize_coverage(items: object) -> list[tuple[date, date]]:
    if not isinstance(items, list):
        return []
    coverage: list[tuple[date, date]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        start = _parse_iso_date(str(item.get("start", "")))
        end = _parse_iso_date(str(item.get("end", "")))
        if start is None or end is None or start > end:
            continue
        coverage.append((start, end))
    return _merge_coverage(coverage)


def _merge_coverage(items: list[tuple[date, date]]) -> list[tuple[date, date]]:
    if not items:
        return []
    merged: list[tuple[date, date]] = []
    for start, end in sorted(items):
        if not merged or start > merged[-1][1] + timedelta(days=1):
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _subtract_coverage(
    start: date,
    end: date,
    coverage: list[tuple[date, date]],
) -> list[tuple[date, date]]:
    missing: list[tuple[date, date]] = []
    cursor = start
    for covered_start, covered_end in coverage:
        if covered_end < cursor:
            continue
        if covered_start > end:
            break
        if covered_start > cursor:
            missing.append((cursor, min(covered_start - timedelta(days=1), end)))
        cursor = max(cursor, covered_end + timedelta(days=1))
        if cursor > end:
            break
    if cursor <= end:
        missing.append((cursor, end))
    return missing


def fetch_finnhub_quote(
    symbol: str,
    *,
    api_key: str,
    client: httpx.Client | None = None,
) -> float | None:
    if not api_key:
        return None
    owns_client = client is None
    client = client or httpx.Client(timeout=3.0)
    try:
        response = client.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": symbol, "token": api_key},
        )
        if response.status_code >= 400:
            return None
        payload = response.json()
        current_price = payload.get("c")
        if current_price is None:
            return None
        return float(current_price)
    except Exception:
        return None
    finally:
        if owns_client:
            client.close()


def fetch_yahoo_quote(symbol: str, *, client: httpx.Client | None = None) -> float | None:
    owns_client = client is None
    client = client or httpx.Client(timeout=3.0)
    try:
        response = client.get(
            "https://query1.finance.yahoo.com/v7/finance/quote",
            params={"symbols": symbol},
            headers=_YAHOO_HEADERS,
        )
        if response.status_code >= 400:
            return None
        payload = response.json()
        results = payload.get("quoteResponse", {}).get("result", [])
        if not results:
            return None
        price = results[0].get("regularMarketPrice")
        if price is None:
            return None
        return float(price)
    except Exception:
        return None
    finally:
        if owns_client:
            client.close()


def fetch_longbridge_quote(symbol: str) -> float | None:
    rows = _run_longbridge_json(["quote", _normalize_longbridge_symbol(symbol), "--format", "json"], timeout=12.0)
    row = _first_longbridge_record(rows)
    if row is None:
        return None
    return _first_number(row, ("last", "last_done", "last_price", "price", "close", "prev_close"))


def _date_to_unix(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp())


def _run_longbridge_json(args: list[str], *, timeout: float) -> object:
    return run_longbridge_json(
        args,
        timeout=timeout,
        executable_resolver=shutil.which,
        runner=subprocess.run,
    )


def _normalize_history_points(
    *,
    timestamps: list,
    closes: list,
    source: str,
) -> list[dict]:
    points: list[dict] = []
    for timestamp, close in zip(timestamps, closes, strict=False):
        if close is None:
            continue
        try:
            price = float(close)
            unix_timestamp = int(timestamp)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        points.append(
            {
                "date": datetime.fromtimestamp(unix_timestamp, tz=timezone.utc).date().isoformat(),
                "value": round(price, 4),
                "source": source,
            }
        )
    points.sort(key=lambda point: str(point.get("date", "")))
    return points


def _parse_market_number(value: object) -> float | None:
    raw = str(value or "").strip()
    if not raw or raw in {"--", "N/A"}:
        return None
    try:
        return float(raw.replace("$", "").replace(",", ""))
    except ValueError:
        return None


def _format_nasdaq_date(value: date) -> str:
    return value.strftime("%Y-%m-%d")


def fetch_finnhub_history(
    symbol: str,
    *,
    api_key: str,
    start_date: str,
    end_date: str,
    client: httpx.Client | None = None,
) -> list[dict]:
    if not api_key:
        return []
    start = _parse_iso_date(start_date)
    end = _parse_iso_date(end_date)
    if start is None or end is None or start > end:
        return []
    owns_client = client is None
    client = client or httpx.Client(timeout=5.0)
    try:
        response = client.get(
            "https://finnhub.io/api/v1/stock/candle",
            params={
                "symbol": symbol,
                "resolution": "D",
                "from": _date_to_unix(start),
                "to": _date_to_unix(end + timedelta(days=1)),
                "token": api_key,
            },
        )
        if response.status_code >= 400:
            return []
        payload = response.json()
        if payload.get("s") != "ok":
            return []
        return _normalize_history_points(
            timestamps=payload.get("t", []),
            closes=payload.get("c", []),
            source="finnhub",
        )
    except Exception:
        return []
    finally:
        if owns_client:
            client.close()


def fetch_yahoo_history(
    symbol: str,
    *,
    start_date: str,
    end_date: str,
    client: httpx.Client | None = None,
) -> list[dict]:
    start = _parse_iso_date(start_date)
    end = _parse_iso_date(end_date)
    if start is None or end is None or start > end:
        return []
    owns_client = client is None
    client = client or httpx.Client(timeout=5.0)
    try:
        payload = None
        params = {
            "period1": _date_to_unix(start),
            "period2": _date_to_unix(end + timedelta(days=1)),
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
        for host in _YAHOO_CHART_HOSTS:
            response = client.get(
                f"https://{host}/v8/finance/chart/{symbol}",
                params=params,
                headers=_YAHOO_HEADERS,
            )
            if response.status_code < 400:
                payload = response.json()
                break
        if payload is None:
            return []
        result = (payload.get("chart", {}).get("result") or [None])[0]
        if not result:
            return []
        timestamps = result.get("timestamp", [])
        adjclose_rows = result.get("indicators", {}).get("adjclose", [])
        quote_rows = result.get("indicators", {}).get("quote", [])
        closes = []
        if adjclose_rows and isinstance(adjclose_rows[0], dict):
            closes = adjclose_rows[0].get("adjclose", [])
        if not closes and quote_rows and isinstance(quote_rows[0], dict):
            closes = quote_rows[0].get("close", [])
        return _normalize_history_points(
            timestamps=timestamps,
            closes=closes,
            source="yahoo",
        )
    except Exception:
        return []
    finally:
        if owns_client:
            client.close()


def fetch_yahoo_candles(
    symbol: str,
    *,
    start_date: str,
    end_date: str,
    client: httpx.Client | None = None,
) -> list[dict]:
    start = _parse_iso_date(start_date)
    end = _parse_iso_date(end_date)
    if start is None or end is None or start > end:
        return []
    owns_client = client is None
    client = client or httpx.Client(timeout=5.0)
    try:
        payload = None
        params = {
            "period1": _date_to_unix(start),
            "period2": _date_to_unix(end + timedelta(days=1)),
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
        for host in _YAHOO_CHART_HOSTS:
            response = client.get(
                f"https://{host}/v8/finance/chart/{symbol}",
                params=params,
                headers=_YAHOO_HEADERS,
            )
            if response.status_code < 400:
                payload = response.json()
                break
        if payload is None:
            return []
        result = (payload.get("chart", {}).get("result") or [None])[0]
        if not result:
            return []
        timestamps = result.get("timestamp", [])
        quote_rows = result.get("indicators", {}).get("quote", [])
        if not quote_rows or not isinstance(quote_rows[0], dict):
            return []
        quote = quote_rows[0]
        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])
        volumes = quote.get("volume", [])
        points: list[dict] = []
        for index, timestamp in enumerate(timestamps):
            close = _list_get(closes, index)
            if close is None:
                continue
            try:
                close_value = float(close)
                unix_timestamp = int(timestamp)
            except (TypeError, ValueError):
                continue
            if close_value <= 0:
                continue
            open_value = _coerce_float(_list_get(opens, index), close_value)
            high_value = _coerce_float(_list_get(highs, index), close_value)
            low_value = _coerce_float(_list_get(lows, index), close_value)
            volume_value = _coerce_float(_list_get(volumes, index), 0.0)
            day = datetime.fromtimestamp(unix_timestamp, tz=timezone.utc).date().isoformat()
            points.append(
                {
                    "date": day,
                    "date_iso": day,
                    "open": round(open_value, 4),
                    "high": round(high_value, 4),
                    "low": round(low_value, 4),
                    "close": round(close_value, 4),
                    "volume": int(volume_value),
                    "source": "yahoo",
                }
            )
        points.sort(key=lambda point: str(point.get("date", "")))
        return points
    except Exception:
        return []
    finally:
        if owns_client:
            client.close()


def fetch_longbridge_candles(
    symbol: str,
    *,
    start_date: str,
    end_date: str,
) -> list[dict]:
    start = _parse_iso_date(start_date)
    end = _parse_iso_date(end_date)
    if start is None or end is None or start > end:
        return []
    lookback_days = max((end - start).days + 1, 1)
    rows = _run_longbridge_json(
        [
            "kline",
            _normalize_longbridge_symbol(symbol),
            "--period",
            "day",
            "--count",
            str(min(max(lookback_days * 2, lookback_days + 30), 1200)),
            "--format",
            "json",
        ],
        timeout=30.0,
    )
    points: list[dict] = []
    for row in _longbridge_records(rows):
        if not isinstance(row, dict):
            continue
        point_date = _parse_iso_date(str(row.get("time") or row.get("date") or ""))
        if point_date is None or point_date < start or point_date > end:
            continue
        close = _first_number(row, ("close", "last", "prev_close"))
        if close is None or close <= 0:
            continue
        day = point_date.isoformat()
        points.append(
            {
                "date": day,
                "date_iso": day,
                "open": round(_coerce_float(row.get("open"), close), 4),
                "high": round(_coerce_float(row.get("high"), close), 4),
                "low": round(_coerce_float(row.get("low"), close), 4),
                "close": round(close, 4),
                "volume": int(_coerce_float(row.get("volume"), 0.0)),
                "source": "longbridge",
            }
        )
    points.sort(key=lambda point: str(point.get("date", "")))
    return points


def fetch_longbridge_valuation_rank(
    symbol: str,
    *,
    start_date: str,
    end_date: str,
) -> dict[str, dict]:
    start = _parse_iso_date(start_date)
    end = _parse_iso_date(end_date)
    if start is None or end is None or start > end:
        return {}
    payload = _run_longbridge_json(
        [
            "valuation-rank",
            _normalize_longbridge_symbol(symbol),
            "--start",
            start.strftime("%Y%m%d"),
            "--end",
            end.strftime("%Y%m%d"),
            "--format",
            "json",
        ],
        timeout=30.0,
    )
    if not isinstance(payload, dict):
        return {}
    by_date: dict[str, dict] = {}
    for metric in ("pe", "pe_ttm", "pb", "ps", "dvd"):
        rows = payload.get(metric)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            point_date = _longbridge_rank_date(row.get("timestamp"))
            if point_date is None or point_date < start or point_date > end:
                continue
            rank = _coerce_positive_int(row.get("rank"))
            total = _coerce_positive_int(row.get("total"))
            if rank is None or total is None:
                continue
            day = point_date.isoformat()
            entry = by_date.setdefault(day, {"date": day, "valuation_source": "longbridge_valuation_rank"})
            entry[f"{metric}_rank"] = rank
            entry[f"{metric}_total"] = total
            entry[f"{metric}_percentile"] = round(rank / total * 100, 2)
    return by_date


def fetch_longbridge_history(
    symbol: str,
    *,
    start_date: str,
    end_date: str,
) -> list[dict]:
    return [
        {
            "date": str(point.get("date", "")),
            "value": point.get("close"),
            "source": "longbridge",
        }
        for point in fetch_longbridge_candles(symbol, start_date=start_date, end_date=end_date)
        if point.get("date") and point.get("close") is not None
    ]


def fetch_futu_candles(
    symbol: str,
    *,
    start_date: str,
    end_date: str,
    host: str = "127.0.0.1",
    port: int = 11111,
) -> list[dict]:
    start = _parse_iso_date(start_date)
    end = _parse_iso_date(end_date)
    if start is None or end is None or start > end:
        return []
    futu = _load_futu_module()
    if futu is None:
        return []
    try:
        quote_ctx = futu.OpenQuoteContext(host=host, port=port)
        try:
            ret, data, _page_req_key = quote_ctx.request_history_kline(
                _normalize_futu_symbol(symbol),
                start=start.isoformat(),
                end=end.isoformat(),
                ktype=futu.KLType.K_DAY,
                autype=futu.AuType.QFQ,
                fields=futu.KL_FIELD.ALL_REAL,
                max_count=1000,
            )
        finally:
            quote_ctx.close()
    except Exception:
        return []
    if ret != futu.RET_OK:
        return []
    points: list[dict] = []
    for row in _records(data):
        point_date = _parse_iso_date(str(row.get("time_key") or row.get("date") or row.get("time") or ""))
        if point_date is None or point_date < start or point_date > end:
            continue
        close = _first_number(row, ("close", "last", "last_price"))
        if close is None or close <= 0:
            continue
        day = point_date.isoformat()
        point = {
            "date": day,
            "date_iso": day,
            "open": round(_coerce_float(row.get("open"), close), 4),
            "high": round(_coerce_float(row.get("high"), close), 4),
            "low": round(_coerce_float(row.get("low"), close), 4),
            "close": round(close, 4),
            "volume": int(_coerce_float(row.get("volume"), 0.0)),
            "source": "futu_opend",
        }
        for source_key, target_key in (
            ("pe_ratio", "pe_ratio"),
            ("turnover_rate", "turnover_rate"),
            ("turnover", "turnover"),
            ("change_rate", "change_rate"),
            ("last_close", "last_close"),
        ):
            value = _first_number(row, (source_key,))
            if value is not None:
                point[target_key] = round(value, 6)
        points.append(point)
    points.sort(key=lambda point: str(point.get("date", "")))
    return points


def fetch_nasdaq_candles(
    symbol: str,
    *,
    start_date: str,
    end_date: str,
    client: httpx.Client | None = None,
) -> list[dict]:
    start = _parse_iso_date(start_date)
    end = _parse_iso_date(end_date)
    if start is None or end is None or start > end:
        return []

    owns_client = client is None
    client = client or httpx.Client(timeout=8.0)
    try:
        response = client.get(
            f"https://api.nasdaq.com/api/quote/{symbol.upper()}/historical",
            params={
                "assetclass": "stocks",
                "fromdate": _format_nasdaq_date(start),
                "todate": _format_nasdaq_date(end),
                "limit": "9999",
            },
            headers=_YAHOO_HEADERS,
        )
        if response.status_code >= 400:
            return []
        payload = response.json()
        rows = (
            payload.get("data", {})
            .get("tradesTable", {})
            .get("rows", [])
        )
        points: list[dict] = []
        for row in rows:
            try:
                point_date = datetime.strptime(str(row.get("date", "")), "%m/%d/%Y").date()
            except ValueError:
                continue
            if point_date < start or point_date > end:
                continue
            close = _parse_market_number(row.get("close"))
            if close is None or close <= 0:
                continue
            open_value = _parse_market_number(row.get("open")) or close
            high_value = _parse_market_number(row.get("high")) or close
            low_value = _parse_market_number(row.get("low")) or close
            volume_value = _parse_market_number(row.get("volume")) or 0.0
            day = point_date.isoformat()
            points.append(
                {
                    "date": day,
                    "date_iso": day,
                    "open": round(open_value, 4),
                    "high": round(high_value, 4),
                    "low": round(low_value, 4),
                    "close": round(close, 4),
                    "volume": int(volume_value),
                    "source": "nasdaq",
                }
            )
        points.sort(key=lambda point: str(point.get("date", "")))
        return points
    except Exception:
        return []
    finally:
        if owns_client:
            client.close()


def _list_get(values: object, index: int) -> object | None:
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]


def _coerce_float(value: object, fallback: float) -> float:
    try:
        if value is None:
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def fetch_nasdaq_history(
    symbol: str,
    *,
    start_date: str,
    end_date: str,
    client: httpx.Client | None = None,
) -> list[dict]:
    start = _parse_iso_date(start_date)
    end = _parse_iso_date(end_date)
    if start is None or end is None or start > end:
        return []

    mapped = _NASDAQ_HISTORY_MAP.get(symbol.upper())
    if mapped is None:
        return []
    nasdaq_symbol, asset_class, source = mapped

    owns_client = client is None
    client = client or httpx.Client(timeout=8.0)
    try:
        response = client.get(
            f"https://api.nasdaq.com/api/quote/{nasdaq_symbol}/historical",
            params={
                "assetclass": asset_class,
                "fromdate": _format_nasdaq_date(start),
                "todate": _format_nasdaq_date(end),
                "limit": "9999",
            },
            headers=_YAHOO_HEADERS,
        )
        if response.status_code >= 400:
            return []
        payload = response.json()
        rows = (
            payload.get("data", {})
            .get("tradesTable", {})
            .get("rows", [])
        )
        points = []
        for row in rows:
            try:
                point_date = datetime.strptime(str(row.get("date", "")), "%m/%d/%Y").date()
            except ValueError:
                continue
            if point_date < start or point_date > end:
                continue
            close = _parse_market_number(row.get("close"))
            if close is None or close <= 0:
                continue
            points.append(
                {
                    "date": point_date.isoformat(),
                    "value": round(close, 4),
                    "source": source,
                }
            )
        points.sort(key=lambda point: str(point.get("date", "")))
        return points
    except Exception:
        return []
    finally:
        if owns_client:
            client.close()


def fetch_benchmark_history(
    symbol: str,
    *,
    start_date: str,
    end_date: str,
    finnhub_api_key: str = "",
    client: httpx.Client | None = None,
    history_cache: MarketHistoryCache | None = None,
) -> list[dict]:
    cache_key = (
        symbol.upper(),
        start_date,
        end_date,
        "benchmark",
    )
    if client is None:
        cached = _history_cache.get(cache_key)
        if cached is not None and monotonic() - cached[0] < _HISTORY_CACHE_TTL_SECONDS:
            return [dict(point) for point in cached[1]]

    persistent_cache = history_cache
    if persistent_cache is None and client is None:
        persistent_cache = _default_market_history_cache()
    if persistent_cache is not None:
        missing_ranges = persistent_cache.missing_ranges(symbol, start_date, end_date)
        attempted_persistent_fetch = bool(missing_ranges)
        if not missing_ranges:
            cached_points = persistent_cache.get_points(symbol, start_date, end_date)
            if cached_points:
                if client is None:
                    _history_cache[cache_key] = (monotonic(), [dict(point) for point in cached_points])
                return cached_points
        for missing_start, missing_end in missing_ranges:
            fetched_points = _fetch_benchmark_history_uncached(
                symbol,
                start_date=missing_start,
                end_date=missing_end,
                finnhub_api_key=finnhub_api_key,
                client=client,
            )
            persistent_cache.store_range(symbol, missing_start, missing_end, fetched_points)
        cached_points = persistent_cache.get_points(symbol, start_date, end_date)
        if cached_points:
            if client is None:
                _history_cache[cache_key] = (monotonic(), [dict(point) for point in cached_points])
            return cached_points
        if attempted_persistent_fetch:
            return []

    points = _fetch_benchmark_history_uncached(
        symbol,
        start_date=start_date,
        end_date=end_date,
        finnhub_api_key=finnhub_api_key,
        client=client,
    )
    if client is None and points:
        _history_cache[cache_key] = (monotonic(), [dict(point) for point in points])
    return points


def refresh_longbridge_history_cache(
    symbols: list[str],
    *,
    start_date: str,
    end_date: str,
    history_cache: MarketHistoryCache | None = None,
) -> dict[str, object]:
    cache = history_cache or _default_market_history_cache()
    results: list[dict[str, object]] = []
    for symbol in _dedupe_symbols(symbols):
        points = fetch_longbridge_history(symbol, start_date=start_date, end_date=end_date)
        if points:
            cache.store_range(symbol, start_date, end_date, points)
        results.append(
            {
                "symbol": symbol,
                "points": len(points),
                "source": "longbridge" if points else "unavailable",
            }
        )
    _clear_market_history_memory_cache()
    return {
        "status": "ok",
        "start_date": start_date,
        "end_date": end_date,
        "symbols": [result["symbol"] for result in results],
        "total_symbols": len(results),
        "refreshed_symbols": sum(1 for result in results if int(result["points"]) > 0),
        "points": sum(int(result["points"]) for result in results),
        "results": results,
        "cache_path": str(cache.path),
    }


def _fetch_benchmark_history_uncached(
    symbol: str,
    *,
    start_date: str,
    end_date: str,
    finnhub_api_key: str = "",
    client: httpx.Client | None = None,
) -> list[dict]:
    start = _parse_iso_date(start_date)
    end = _parse_iso_date(end_date)
    minimum_points = 1 if start is not None and end is not None and start == end else 2
    points: list[dict] = []
    if client is None:
        points = fetch_longbridge_history(
            symbol,
            start_date=start_date,
            end_date=end_date,
        )
    if len(points) >= minimum_points:
        return points
    points = fetch_finnhub_history(
        symbol,
        api_key=finnhub_api_key,
        start_date=start_date,
        end_date=end_date,
        client=client,
    )
    if len(points) < minimum_points:
        points = fetch_yahoo_history(
            symbol,
            start_date=start_date,
            end_date=end_date,
            client=client,
        )
    if len(points) < minimum_points:
        points = fetch_nasdaq_history(
            symbol,
            start_date=start_date,
            end_date=end_date,
            client=client,
        )
    return points


def _longbridge_rank_date(value: object) -> date | None:
    try:
        timestamp = int(str(value))
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).date()


def _coerce_positive_int(value: object) -> int | None:
    try:
        parsed = int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _dedupe_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for symbol in symbols:
        cleaned = str(symbol or "").strip().upper()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def _clear_market_history_memory_cache() -> None:
    _history_cache.clear()
