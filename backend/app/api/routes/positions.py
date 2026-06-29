from datetime import date
from datetime import timedelta

from fastapi import APIRouter

from app.api.currency_conversion import convert_money
from app.api.currency_conversion import convert_position_money_values
from app.api.currency_conversion import normalize_currency_code
from app.api.currency_conversion import resolve_position_display_fx
from app.api.response_models import STORAGE_UNAVAILABLE_OPENAPI_RESPONSE
from app.api.time_normalization import normalize_date_to_iso
from app.repositories.raw_repository import RawRepository
from app.services.industry_mapping_service import IndustryMappingService
from app.services.quote_service import QuoteService
from app.services.quote_service import fetch_futu_candles
from app.services.quote_service import fetch_longbridge_candles
from app.services.quote_service import fetch_longbridge_valuation_rank
from app.services.quote_service import fetch_nasdaq_candles
from app.services.quote_service import fetch_yahoo_candles
from app.services.account_currency import resolve_account_base_currency as _resolve_account_base_currency
from app.services.settings_service import SettingsService
from app.utils.dates import compact_date as _compact_date
from app.utils.numbers import optional_float as _optional_float
from app.utils.numbers import to_float as _to_float

router = APIRouter()
_raw_repository: RawRepository | object | None = None
_quote_service: QuoteService | None = None
_industry_mapping_service: IndustryMappingService | None = None
_settings_service: SettingsService = SettingsService()


def set_raw_repository(repository: object | None) -> None:
    global _raw_repository
    _raw_repository = repository


def set_quote_service(service: QuoteService | None) -> None:
    global _quote_service
    _quote_service = service


def set_industry_mapping_service(service: IndustryMappingService | None) -> None:
    global _industry_mapping_service
    _industry_mapping_service = service


def set_settings_service(service: SettingsService) -> None:
    global _settings_service
    _settings_service = service


@router.get("/api/positions", responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE)
def list_positions(symbol: str | None = None, page: int = 1, page_size: int = 20) -> dict:
    use_realtime = _settings_service.get().display_realtime_prices
    currency_context = _resolve_currency_context([])
    display_currency = currency_context["display_currency"]
    if _raw_repository is None:
        return {
            "filters": {"symbol": symbol, "page": max(page, 1), "page_size": max(min(page_size, 100), 1)},
            "account_base_currency": currency_context["account_base_currency"],
            "display_currency": display_currency,
            "currency_conversion": currency_context["currency_conversion"],
            "valuation_mode": "realtime" if use_realtime else "snapshot",
            "items": [],
            "total": 0,
        }
    normalized_page = max(page, 1)
    normalized_page_size = max(min(page_size, 100), 1)
    all_items = _list_current_positions(symbol=symbol)
    offset = (normalized_page - 1) * normalized_page_size
    items = all_items[offset : offset + normalized_page_size]
    currency_context = _resolve_currency_context(all_items)
    display_currency = currency_context["display_currency"]
    enriched = _enrich_positions(items, display_currency=display_currency)
    effective_realtime = any(bool(item.get("is_realtime")) for item in enriched)
    return {
        "filters": {"symbol": symbol, "page": normalized_page, "page_size": normalized_page_size},
        "account_base_currency": currency_context["account_base_currency"],
        "display_currency": display_currency,
        "currency_conversion": currency_context["currency_conversion"],
        "valuation_mode": "realtime" if (use_realtime or effective_realtime) else "snapshot",
        "items": enriched,
        "total": len(all_items),
    }


@router.get(
    "/api/positions/industry-allocation",
    responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE,
)
def get_industry_allocation() -> dict:
    use_realtime = _settings_service.get().display_realtime_prices
    currency_context = _resolve_currency_context([])
    display_currency = currency_context["display_currency"]
    if _raw_repository is None:
        return {
            "account_base_currency": currency_context["account_base_currency"],
            "display_currency": display_currency,
            "currency_conversion": currency_context["currency_conversion"],
            "valuation_mode": "realtime" if use_realtime else "snapshot",
            "items": [],
            "total_market_value": 0.0,
        }
    items = _list_current_positions()
    currency_context = _resolve_currency_context(items)
    display_currency = currency_context["display_currency"]
    enriched = _enrich_positions(items, display_currency=display_currency)
    effective_realtime = any(bool(item.get("is_realtime")) for item in enriched)
    grouped: dict[str, float] = {}
    for pos in enriched:
        industry = str(pos.get("industry") or "Unknown")
        grouped[industry] = grouped.get(industry, 0.0) + float(pos.get("realtime_value", 0) or 0)
    total_market_value = sum(grouped.values())
    rows = []
    for industry, market_value in grouped.items():
        weight = 0.0 if total_market_value == 0 else market_value / total_market_value
        rows.append(
            {
                "industry": industry,
                "market_value": round(market_value, 2),
                "weight": round(weight, 6),
            }
        )
    rows.sort(key=lambda row: row["market_value"], reverse=True)
    return {
        "account_base_currency": currency_context["account_base_currency"],
        "display_currency": display_currency,
        "currency_conversion": currency_context["currency_conversion"],
        "valuation_mode": "realtime" if (use_realtime or effective_realtime) else "snapshot",
        "items": rows,
        "total_market_value": round(total_market_value, 2),
    }


@router.get("/api/positions/{symbol}/detail", responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE)
def get_position_detail(symbol: str) -> dict:
    currency_context = _resolve_currency_context([])
    display_currency = currency_context["display_currency"]
    normalized_symbol = symbol.upper()
    if _raw_repository is None:
        return {
            "symbol": normalized_symbol,
            "display_currency": display_currency,
            "position": None,
            "trades": [],
            "markers": [],
            "price_history": [],
            "price_history_status": "storage_unavailable",
        }
    position_items = _list_current_positions(symbol=normalized_symbol)
    currency_context = _resolve_currency_context(position_items)
    display_currency = currency_context["display_currency"]
    position = _enrich_positions(position_items, display_currency=display_currency)
    price_currency = normalize_currency_code(
        position_items[0].get("currency") if position_items else None,
        display_currency,
    )
    trades = _list_symbol_trades(normalized_symbol, fx_rate=currency_context["rate"])
    price_history = _list_symbol_price_history(normalized_symbol, fx_rate=currency_context["rate"])
    price_history = _merge_price_history(
        price_history,
        _fetch_missing_symbol_price_history(
            normalized_symbol,
            local_history=price_history,
            trades=trades,
            positions=position_items,
            fx_rate=currency_context["rate"],
        ),
    )
    return {
        "symbol": normalized_symbol,
        "account_base_currency": currency_context["account_base_currency"],
        "display_currency": display_currency,
        "price_currency": price_currency,
        "currency_conversion": currency_context["currency_conversion"],
        "position": position[0] if position else None,
        "trades": trades,
        "markers": [
            {
                "date": trade.get("trade_date_iso") or trade.get("trade_date"),
                "side": trade.get("side"),
                "price": trade.get("trade_price"),
                "quantity": trade.get("quantity"),
                "trade_id": trade.get("trade_id"),
            }
            for trade in trades
            if trade.get("side") in {"BUY", "SELL"}
        ],
        "price_history": price_history,
        "price_history_status": "ready" if price_history else "awaiting_ibkr_symbol_price_history_v1",
    }


def _identity_currency_conversion(source_currency: str, display_currency: str) -> dict:
    source_code = normalize_currency_code(source_currency)
    display_code = normalize_currency_code(display_currency)
    return {
        "status": "identity" if source_code == display_code else "missing_rate",
        "source_currency": source_code,
        "display_currency": display_code,
        "fx_source_currency": source_code,
        "fx_target_currency": display_code,
        "rate": 1.0,
        "rate_date": None,
    }


def _resolve_currency_context(items: list[dict]) -> dict:
    account_base_currency = _resolve_account_base_currency(_raw_repository, items)
    conversion = _identity_currency_conversion(account_base_currency, account_base_currency)
    rate = float(conversion.get("rate") or 1.0)
    return {
        "account_base_currency": account_base_currency,
        "display_currency": account_base_currency,
        "currency_conversion": {**conversion, "rate": round(rate, 8)},
        "rate": rate,
    }


def _list_current_positions(symbol: str | None = None) -> list[dict]:
    if _raw_repository is None:
        return []
    scoped_rows = _get_current_position_snapshot_rows()
    current_rows = _select_current_position_rows(scoped_rows)
    current_rows = _decorate_position_metrics(current_rows)
    if symbol:
        normalized_symbol = symbol.upper()
        current_rows = [
            row
            for row in current_rows
            if str(row.get("symbol", "") or "").upper() == normalized_symbol
        ]
    current_rows.sort(
        key=lambda row: abs(
            _to_float(row.get("market_value_snapshot", row.get("realtime_value", 0)))
        ),
        reverse=True,
    )
    return current_rows


def _get_current_position_snapshot_rows() -> list[dict]:
    latest = _raw_repository.get_latest_account_snapshot() if _raw_repository is not None else None
    account_id = str((latest or {}).get("account_id", "") or "")
    report_date = str((latest or {}).get("report_date", "") or "")
    if account_id and report_date:
        rows = _raw_repository.es.search(
            index="ibkr_position_snapshots_v1",
            size=10000,
            term_filters={"account_id": account_id, "report_date": report_date},
        )
        if rows:
            return rows

    filters = {"account_id": account_id} if account_id else None
    candidates = _raw_repository.es.search(
        index="ibkr_position_snapshots_v1",
        size=10000,
        term_filters=filters,
    )
    if not candidates:
        return []
    if report_date:
        report_key = _compact_date(report_date)
        same_date_rows = [
            row for row in candidates if _compact_date(row.get("report_date")) == report_key
        ]
        if same_date_rows:
            return same_date_rows
        fallback_dates = sorted(
            {
                _compact_date(row.get("report_date"))
                for row in candidates
                if _compact_date(row.get("report_date"))
                and _compact_date(row.get("report_date")) <= report_key
            }
        )
    else:
        fallback_dates = sorted(
            {
                _compact_date(row.get("report_date"))
                for row in candidates
                if _compact_date(row.get("report_date"))
            }
        )
    if not fallback_dates:
        return candidates
    fallback_date = fallback_dates[-1]
    return [row for row in candidates if _compact_date(row.get("report_date")) == fallback_date]


def _select_current_position_rows(rows: list[dict]) -> list[dict]:
    summary_rows = [
        dict(row)
        for row in rows
        if str(row.get("level_of_detail", "") or "").upper() == "SUMMARY"
    ]
    if summary_rows:
        by_symbol: dict[str, dict] = {}
        for row in sorted(
            summary_rows,
            key=lambda item: _compact_date(item.get("report_date")),
            reverse=True,
        ):
            symbol = str(row.get("symbol", "") or "").upper()
            if not symbol or symbol in by_symbol:
                continue
            row["symbol"] = symbol
            by_symbol[symbol] = row
        return list(by_symbol.values())

    aggregated: dict[str, dict] = {}
    for row in rows:
        symbol = str(row.get("symbol", "") or "").upper()
        if not symbol:
            continue
        bucket = aggregated.setdefault(
            symbol,
            {
                "account_id": row.get("account_id"),
                "report_date": row.get("report_date"),
                "asset_category": row.get("asset_category"),
                "symbol": symbol,
                "currency": row.get("currency"),
                "fx_rate_to_base": row.get("fx_rate_to_base", row.get("fxRateToBase")),
                "level_of_detail": "SUMMARY_AGGREGATED",
                "quantity": 0.0,
                "market_value_snapshot": 0.0,
                "cost_basis_money": 0.0,
                "unrealized_pnl_snapshot": 0.0,
            },
        )
        bucket["quantity"] = _to_float(bucket.get("quantity")) + _to_float(
            row.get("quantity", row.get("position"))
        )
        bucket["market_value_snapshot"] = _to_float(bucket.get("market_value_snapshot")) + _to_float(
            row.get("market_value_snapshot", row.get("position_value"))
        )
        bucket["cost_basis_money"] = _to_float(bucket.get("cost_basis_money")) + _to_float(
            row.get("cost_basis_money")
        )
        bucket["unrealized_pnl_snapshot"] = _to_float(bucket.get("unrealized_pnl_snapshot")) + _to_float(
            row.get("unrealized_pnl_snapshot", row.get("fifo_pnl_unrealized"))
        )
    for row in aggregated.values():
        quantity = _to_float(row.get("quantity"))
        if quantity:
            row["mark_price_snapshot"] = round(_to_float(row.get("market_value_snapshot")) / quantity, 6)
            row["average_cost_price"] = round(_to_float(row.get("cost_basis_money")) / quantity, 6)
    return list(aggregated.values())


def _decorate_position_metrics(items: list[dict]) -> list[dict]:
    latest = _raw_repository.get_latest_account_snapshot() if _raw_repository is not None else None
    account_id = str((latest or {}).get("account_id", "") or "")
    report_date = str((latest or {}).get("report_date", "") or "")
    realized_by_symbol = _build_realized_pnl_by_symbol(account_id=account_id, report_date=report_date)
    previous_prices = _build_previous_prices_by_symbol(account_id=account_id, report_date=report_date)
    decorated: list[dict] = []
    for item in items:
        row = dict(item)
        symbol = str(row.get("symbol", "") or "").upper()
        row["symbol"] = symbol
        quantity = _to_float(row.get("quantity", row.get("position")))
        cost_basis = _to_float(row.get("cost_basis_money"))
        average_cost = _to_float(row.get("average_cost_price", row.get("cost_basis_price")))
        if average_cost == 0 and quantity:
            average_cost = cost_basis / quantity
        realized_pnl = realized_by_symbol.get(symbol, 0.0)
        adjusted_cost_basis = cost_basis - realized_pnl
        row["average_cost_price"] = round(average_cost, 6)
        row["cost_price_moving_weighted"] = round(average_cost, 6)
        row["realized_pnl_total"] = round(realized_pnl, 2)
        row["cost_basis_adjusted"] = round(adjusted_cost_basis, 2)
        row["cost_price_adjusted"] = round(adjusted_cost_basis / quantity, 6) if quantity else 0.0
        previous_price = previous_prices.get(symbol)
        if previous_price is not None:
            row["previous_mark_price_snapshot"] = previous_price
        decorated.append(row)
    return decorated


def _build_realized_pnl_by_symbol(*, account_id: str, report_date: str) -> dict[str, float]:
    if _raw_repository is None:
        return {}
    filters = {"account_id": account_id} if account_id else None
    trades = _raw_repository.es.search(
        index="ibkr_trade_records_v1",
        size=10000,
        term_filters=filters,
    )
    report_key = _compact_date(report_date)
    grouped: dict[str, float] = {}
    for trade in trades:
        trade_key = _compact_date(trade.get("trade_date"))
        if report_key and trade_key and trade_key > report_key:
            continue
        symbol = str(trade.get("symbol", "") or "").upper()
        if not symbol:
            continue
        grouped[symbol] = grouped.get(symbol, 0.0) + _to_float(trade.get("fifo_pnl_realized"))
    return grouped


def _build_previous_prices_by_symbol(*, account_id: str, report_date: str) -> dict[str, float]:
    if _raw_repository is None or not report_date:
        return {}
    report_key = _compact_date(report_date)
    filters = {"account_id": account_id} if account_id else None
    candidates = _raw_repository.es.search(
        index="ibkr_position_snapshots_v1",
        size=10000,
        term_filters=filters,
    )
    older_dates = sorted(
        {
            _compact_date(row.get("report_date"))
            for row in candidates
            if _compact_date(row.get("report_date"))
            and _compact_date(row.get("report_date")) < report_key
        }
    )
    if not older_dates:
        return {}
    previous_date = older_dates[-1]
    previous_rows = [
        row for row in candidates if _compact_date(row.get("report_date")) == previous_date
    ]
    selected = _select_current_position_rows(previous_rows)
    prices: dict[str, float] = {}
    for row in selected:
        symbol = str(row.get("symbol", "") or "").upper()
        if symbol:
            prices[symbol] = _to_float(row.get("mark_price_snapshot"))
    return prices


def _list_symbol_trades(symbol: str, *, fx_rate: float) -> list[dict]:
    if _raw_repository is None:
        return []
    trades = _raw_repository.es.search(
        index="ibkr_trade_records_v1",
        size=2000,
        term_filters={"symbol": symbol.upper()},
    )
    rows: list[dict] = []
    for trade in trades:
        row = dict(trade)
        row["symbol"] = str(row.get("symbol", "") or "").upper()
        row["side"] = str(row.get("side", "") or "").upper()
        row["trade_date_iso"] = normalize_date_to_iso(row.get("trade_date"))
        row["trade_price_source"] = row.get("trade_price")
        row["trade_price"] = convert_money(row.get("trade_price"), fx_rate)
        row["fifo_pnl_realized_source"] = row.get("fifo_pnl_realized")
        row["fifo_pnl_realized"] = convert_money(row.get("fifo_pnl_realized"), fx_rate)
        row["ib_commission_source"] = row.get("ib_commission")
        row["ib_commission"] = convert_money(row.get("ib_commission"), fx_rate)
        rows.append(row)
    rows.sort(key=lambda row: _compact_date(row.get("trade_date")), reverse=False)
    return rows


def _list_symbol_price_history(symbol: str, *, fx_rate: float) -> list[dict]:
    if _raw_repository is None:
        return []
    normalized_rows: list[dict] = []
    try:
        rows = _raw_repository.es.search(
            index="ibkr_symbol_price_history_v1",
            size=2000,
            term_filters={"symbol": symbol.upper()},
        )
    except RuntimeError as exc:
        if "404" in str(exc):
            rows = []
        else:
            raise
    normalized_rows.extend(_normalize_symbol_price_rows(rows, symbol=symbol, fx_rate=fx_rate))
    cached_rows = _list_cached_market_price_history(symbol, fx_rate=fx_rate)
    return _merge_price_history(normalized_rows, cached_rows)


def _list_cached_market_price_history(symbol: str, *, fx_rate: float) -> list[dict]:
    if _raw_repository is None:
        return []
    try:
        rows = _raw_repository.es.search(
            index="market_symbol_price_history_v1",
            size=5000,
            term_filters={"symbol": symbol.upper()},
        )
    except RuntimeError as exc:
        if "404" in str(exc):
            return []
        raise
    return _normalize_symbol_price_rows(rows, symbol=symbol, fx_rate=fx_rate)


def _normalize_symbol_price_rows(rows: list[dict], *, symbol: str, fx_rate: float) -> list[dict]:
    normalized_rows: list[dict] = []
    for row in rows:
        normalized = dict(row)
        normalized["symbol"] = str(normalized.get("symbol", "") or "").upper()
        if normalized["symbol"] and normalized["symbol"] != symbol.upper():
            continue
        normalized["date_iso"] = normalize_date_to_iso(
            normalized.get("date")
            or normalized.get("price_date")
            or normalized.get("report_date")
        )
        price_aliases = {
            "open": ["open", "open_price"],
            "high": ["high", "high_price"],
            "low": ["low", "low_price"],
            "close": ["close", "close_price"],
        }
        for key, aliases in price_aliases.items():
            source_value = next((normalized.get(alias) for alias in aliases if alias in normalized), None)
            if source_value is not None:
                normalized[f"{key}_source"] = source_value
                normalized[key] = convert_money(source_value, fx_rate)
        passthrough_aliases = {
            "volume": ["volume", "trade_volume", "turnover_volume"],
            "pe_ratio": ["pe_ratio", "pe", "trailing_pe", "pe_ttm", "price_earnings_ratio", "price_earnings"],
            "pe_rank": ["pe_rank", "pe_ttm_rank"],
            "pe_total": ["pe_total", "pe_ttm_total"],
            "pe_percentile": ["pe_percentile", "pe_ttm_percentile"],
        }
        for key, aliases in passthrough_aliases.items():
            source_value = next((normalized.get(alias) for alias in aliases if normalized.get(alias) is not None), None)
            if source_value is not None:
                normalized[key] = source_value
        normalized_rows.append(normalized)
    normalized_rows.sort(key=lambda row: _compact_date(row.get("date_iso")), reverse=False)
    return normalized_rows


def _fetch_missing_symbol_price_history(
    symbol: str,
    *,
    local_history: list[dict],
    trades: list[dict],
    positions: list[dict],
    fx_rate: float,
) -> list[dict]:
    if not _needs_external_price_history(local_history=local_history, trades=trades):
        return []
    start_date, end_date = _resolve_external_history_range(
        local_history=local_history,
        trades=trades,
        positions=positions,
    )
    if not start_date or not end_date:
        return []
    candles = fetch_longbridge_candles(symbol, start_date=start_date, end_date=end_date)
    futu_candles = _fetch_futu_symbol_price_history(symbol, start_date=start_date, end_date=end_date)
    candles = _merge_external_candles(candles, futu_candles)
    if len(candles) < 2:
        candles = fetch_nasdaq_candles(symbol, start_date=start_date, end_date=end_date)
    if len(candles) < 2:
        candles = fetch_yahoo_candles(symbol, start_date=start_date, end_date=end_date)
    valuation_by_date = fetch_longbridge_valuation_rank(
        symbol,
        start_date=start_date,
        end_date=end_date,
    )
    raw_rows: list[dict] = []
    normalized: list[dict] = []
    for candle in candles:
        row = dict(candle)
        row["symbol"] = symbol.upper()
        row["date_iso"] = normalize_date_to_iso(row.get("date_iso") or row.get("date"))
        row.update(valuation_by_date.get(str(row["date_iso"]), {}))
        raw_rows.append(dict(row))
        for key in ["open", "high", "low", "close"]:
            source_value = row.get(key)
            row[f"{key}_source"] = source_value
            row[key] = convert_money(source_value, fx_rate)
        normalized.append(row)
    normalized.sort(key=lambda row: _compact_date(row.get("date_iso")), reverse=False)
    _cache_external_symbol_price_history(symbol, raw_rows)
    return normalized


def _fetch_futu_symbol_price_history(symbol: str, *, start_date: str, end_date: str) -> list[dict]:
    settings = _settings_service.get()
    if settings.futu_connection_mode == "disabled":
        return []
    return fetch_futu_candles(
        symbol,
        start_date=start_date,
        end_date=end_date,
        host=settings.futu_opend_host,
        port=settings.futu_opend_port,
    )


def _merge_external_candles(primary: list[dict], enrichment: list[dict]) -> list[dict]:
    if not enrichment:
        return primary
    if not primary:
        return enrichment
    merged: dict[str, dict] = {}
    for row in primary:
        key = _compact_date(row.get("date_iso") or row.get("date"))
        if key:
            merged[key] = dict(row)
    for row in enrichment:
        key = _compact_date(row.get("date_iso") or row.get("date"))
        if not key:
            continue
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(row)
            continue
        for field in ("volume", "pe_ratio", "turnover_rate", "turnover", "change_rate", "last_close"):
            if existing.get(field) is None and row.get(field) is not None:
                existing[field] = row[field]
        if row.get("source"):
            existing["enrichment_source"] = row.get("source")
    rows = list(merged.values())
    rows.sort(key=lambda row: _compact_date(row.get("date_iso") or row.get("date")), reverse=False)
    return rows


def _cache_external_symbol_price_history(symbol: str, rows: list[dict]) -> None:
    if _raw_repository is None or not rows:
        return
    try:
        _raw_repository.es.ensure_index("market_symbol_price_history_v1")
    except AttributeError:
        return
    for row in rows:
        date_iso = normalize_date_to_iso(row.get("date_iso") or row.get("date"))
        if not date_iso:
            continue
        cache_row = dict(row)
        cache_row["symbol"] = symbol.upper()
        cache_row["date_iso"] = date_iso
        cache_row["date"] = date_iso
        cache_row.setdefault("source", row.get("source") or row.get("enrichment_source") or "external")
        try:
            _raw_repository.es.update(
                index="market_symbol_price_history_v1",
                id=f"{symbol.upper()}_{date_iso}",
                doc=cache_row,
                doc_as_upsert=True,
            )
        except Exception:
            return


def _needs_external_price_history(*, local_history: list[dict], trades: list[dict]) -> bool:
    if not trades:
        return len(local_history) == 0
    if not local_history:
        return True
    if not _history_has_market_enrichment(local_history):
        return True
    local_dates = {
        _compact_date(row.get("date_iso") or row.get("date") or row.get("price_date") or row.get("report_date"))
        for row in local_history
    }
    trade_dates = {
        _compact_date(trade.get("trade_date_iso") or trade.get("trade_date") or trade.get("date"))
        for trade in trades
    }
    trade_dates.discard("")
    return any(trade_date not in local_dates for trade_date in trade_dates)


def _history_has_market_enrichment(rows: list[dict]) -> bool:
    has_volume = any(row.get("volume") is not None or row.get("trade_volume") is not None for row in rows)
    has_pe = any(
        row.get(key) is not None
        for row in rows
        for key in ("pe_ratio", "pe", "trailing_pe", "pe_ttm", "price_earnings_ratio", "price_earnings")
    )
    return has_volume and has_pe


def _resolve_external_history_range(
    *,
    local_history: list[dict],
    trades: list[dict],
    positions: list[dict],
) -> tuple[str | None, str | None]:
    date_keys: list[str] = []
    for trade in trades:
        trade_key = _compact_date(trade.get("trade_date_iso") or trade.get("trade_date") or trade.get("date"))
        if trade_key:
            date_keys.append(trade_key)
    for position in positions:
        report_key = _compact_date(position.get("report_date") or position.get("report_date_iso"))
        if report_key:
            date_keys.append(report_key)
    for row in local_history:
        history_key = _compact_date(row.get("date_iso") or row.get("date") or row.get("price_date") or row.get("report_date"))
        if history_key:
            date_keys.append(history_key)
    if not date_keys:
        end = date.today()
        start = end - timedelta(days=365)
        return start.isoformat(), end.isoformat()
    start_key = min(date_keys)
    end_key = max(date_keys)
    start_iso = normalize_date_to_iso(start_key)
    end_iso = normalize_date_to_iso(end_key)
    start = _iso_to_date(start_iso)
    if start is not None:
        start_iso = (start - timedelta(days=7)).isoformat()
    return start_iso, end_iso


def _merge_price_history(local_history: list[dict], external_history: list[dict]) -> list[dict]:
    if not external_history:
        return local_history
    merged: dict[str, dict] = {}
    for row in external_history:
        key = _compact_date(row.get("date_iso") or row.get("date"))
        if key:
            merged[key] = row
    for row in local_history:
        key = _compact_date(row.get("date_iso") or row.get("date") or row.get("price_date") or row.get("report_date"))
        if key:
            external_row = merged.get(key, {})
            merged_row = {**external_row, **row}
            for field in (
                "volume",
                "valuation_source",
                "pe_rank",
                "pe_total",
                "pe_percentile",
                "pe_ttm_rank",
                "pe_ttm_total",
                "pe_ttm_percentile",
                "pb_rank",
                "pb_total",
                "pb_percentile",
                "ps_rank",
                "ps_total",
                "ps_percentile",
            ):
                if merged_row.get(field) is None and external_row.get(field) is not None:
                    merged_row[field] = external_row[field]
            merged[key] = merged_row
    rows = list(merged.values())
    rows.sort(key=lambda row: _compact_date(row.get("date_iso") or row.get("date")), reverse=False)
    return rows


def _iso_to_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        parts = [int(part) for part in value[:10].split("-")]
        return date(parts[0], parts[1], parts[2])
    except (TypeError, ValueError, IndexError):
        return None


def _enrich_positions(items: list[dict], *, display_currency: str) -> list[dict]:
    use_realtime = _settings_service.get().display_realtime_prices
    enriched = []
    quote_cache: dict[str, tuple[float, bool, str]] = {}
    for item in items:
        pos = dict(item)
        sym = pos.get("symbol", "")
        quantity = float(pos.get("quantity", 0) or 0)
        pos["report_date_iso"] = normalize_date_to_iso(pos.get("report_date"))

        if _quote_service and sym:
            cached = quote_cache.get(sym)
            if cached is None:
                quote = (
                    _quote_service.get_latest_quote(sym)
                    if use_realtime
                    else _quote_service.get_snapshot_quote(sym)
                )
                cached = (quote.price, quote.is_realtime, quote.source)
                quote_cache[sym] = cached
            price, is_realtime, source = cached
            pos["realtime_price"] = price
            pos["realtime_value"] = round(price * quantity, 2)
            pos["is_realtime"] = is_realtime
            pos["quote_source"] = source
        else:
            snapshot_price = float(pos.get("mark_price_snapshot", 0) or 0)
            pos["realtime_price"] = snapshot_price
            pos["realtime_value"] = round(snapshot_price * quantity, 2)
            pos["is_realtime"] = False
            pos["quote_source"] = "snapshot"
        previous_price = _optional_float(pos.get("previous_mark_price_snapshot"))
        if previous_price is not None and previous_price != 0:
            daily_change = float(pos.get("realtime_price", 0) or 0) - previous_price
            pos["daily_change"] = round(daily_change, 6)
            pos["daily_change_pct"] = round(daily_change / previous_price, 6)
        else:
            pos["daily_change"] = 0.0
            pos["daily_change_pct"] = 0.0
        conversion = resolve_position_display_fx(
            raw_repository=_raw_repository,
            position=pos,
            account_base_currency=display_currency,
            display_currency=display_currency,
        )
        pos = convert_position_money_values(pos, conversion)
        pos["industry"] = (
            _industry_mapping_service.get(sym)
            if _industry_mapping_service is not None and sym
            else "Unknown"
        ) or "Unknown"

        enriched.append(pos)
    return enriched
