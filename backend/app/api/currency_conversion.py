from app.repositories.raw_repository import RawRepository
from app.utils.dates import compact_report_date as _compact_report_date
from app.utils.numbers import to_float as _to_float


def normalize_currency_code(value: object, fallback: str = "USD") -> str:
    text = str(value or "").strip().upper()
    return text or fallback


def _fx_currency_variants(value: object) -> list[str]:
    code = normalize_currency_code(value)
    if code in {"RMB", "CNY"}:
        return ["CNY", "CNH"]
    if code == "CNH":
        return ["CNH", "CNY"]
    return [code]


def _latest_pair_fx_rate(
    *,
    raw_repository: RawRepository,
    from_currency: str,
    to_currency: str,
    report_date: str,
) -> dict | None:
    rows = raw_repository.es.search(
        index="ibkr_fx_rates_v1",
        size=10000,
        term_filters={"from_currency": from_currency, "to_currency": to_currency},
    )
    report_key = _compact_report_date(report_date)
    candidates: list[dict] = []
    for row in rows:
        rate = _to_float(row.get("rate"))
        if rate <= 0:
            continue
        row_date = str(row.get("rate_date", row.get("report_date", "")) or "")
        row_key = _compact_report_date(row_date)
        if report_key and row_key and row_key > report_key:
            continue
        candidates.append({**row, "rate": rate, "_date_key": row_key})
    if not candidates:
        return None
    candidates.sort(key=lambda row: str(row.get("_date_key", "")), reverse=True)
    return candidates[0]


def _find_pair_fx_rate(
    *,
    raw_repository: RawRepository,
    from_currencies: list[str],
    to_currencies: list[str],
    report_date: str,
) -> dict | None:
    for from_currency in from_currencies:
        for to_currency in to_currencies:
            row = _latest_pair_fx_rate(
                raw_repository=raw_repository,
                from_currency=from_currency,
                to_currency=to_currency,
                report_date=report_date,
            )
            if row is not None:
                return row
    return None


def resolve_display_fx(
    *,
    raw_repository: RawRepository,
    source_currency: str,
    display_currency: str,
    report_date: str,
) -> dict:
    source_code = normalize_currency_code(source_currency)
    display_code = normalize_currency_code(display_currency)
    source_variants = _fx_currency_variants(source_code)
    display_variants = _fx_currency_variants(display_code)
    if set(source_variants) & set(display_variants):
        return {
            "status": "identity",
            "source_currency": source_code,
            "display_currency": display_code,
            "fx_source_currency": source_variants[0],
            "fx_target_currency": display_variants[0],
            "rate": 1.0,
            "rate_date": None,
        }

    direct = _find_pair_fx_rate(
        raw_repository=raw_repository,
        from_currencies=source_variants,
        to_currencies=display_variants,
        report_date=report_date,
    )
    if direct is not None:
        return {
            "status": "converted",
            "source_currency": source_code,
            "display_currency": display_code,
            "fx_source_currency": str(direct.get("from_currency", source_variants[0])),
            "fx_target_currency": str(direct.get("to_currency", display_variants[0])),
            "rate": _to_float(direct.get("rate")),
            "rate_date": direct.get("rate_date"),
        }

    inverse = _find_pair_fx_rate(
        raw_repository=raw_repository,
        from_currencies=display_variants,
        to_currencies=source_variants,
        report_date=report_date,
    )
    if inverse is not None:
        inverse_rate = _to_float(inverse.get("rate"))
        if inverse_rate > 0:
            return {
                "status": "converted",
                "source_currency": source_code,
                "display_currency": display_code,
                "fx_source_currency": str(inverse.get("to_currency", source_variants[0])),
                "fx_target_currency": str(inverse.get("from_currency", display_variants[0])),
                "rate": 1 / inverse_rate,
                "rate_date": inverse.get("rate_date"),
            }

    source_to_usd = _find_pair_fx_rate(
        raw_repository=raw_repository,
        from_currencies=source_variants,
        to_currencies=["USD"],
        report_date=report_date,
    )
    display_to_usd = _find_pair_fx_rate(
        raw_repository=raw_repository,
        from_currencies=display_variants,
        to_currencies=["USD"],
        report_date=report_date,
    )
    if source_code == "USD":
        source_to_usd = {
            "from_currency": "USD",
            "to_currency": "USD",
            "rate": 1.0,
            "rate_date": None,
        }
    if display_code == "USD":
        display_to_usd = {
            "from_currency": "USD",
            "to_currency": "USD",
            "rate": 1.0,
            "rate_date": None,
        }
    if source_to_usd is not None and display_to_usd is not None:
        display_rate = _to_float(display_to_usd.get("rate"))
        if display_rate > 0:
            return {
                "status": "converted",
                "source_currency": source_code,
                "display_currency": display_code,
                "fx_source_currency": str(source_to_usd.get("from_currency", source_variants[0])),
                "fx_target_currency": str(display_to_usd.get("from_currency", display_variants[0])),
                "rate": _to_float(source_to_usd.get("rate")) / display_rate,
                "rate_date": display_to_usd.get("rate_date") or source_to_usd.get("rate_date"),
            }

    return {
        "status": "missing_rate",
        "source_currency": source_code,
        "display_currency": display_code,
        "fx_source_currency": source_variants[0],
        "fx_target_currency": display_variants[0],
        "rate": 1.0,
        "rate_date": None,
    }


def resolve_position_display_fx(
    *,
    raw_repository: RawRepository,
    position: dict,
    account_base_currency: str,
    display_currency: str,
) -> dict:
    account_code = normalize_currency_code(account_base_currency)
    display_code = normalize_currency_code(display_currency)
    source_code = normalize_currency_code(position.get("currency"), account_code)
    report_date = str(position.get("report_date", "") or "")
    if source_code == display_code:
        return resolve_display_fx(
            raw_repository=raw_repository,
            source_currency=source_code,
            display_currency=display_code,
            report_date=report_date,
        )

    rate_to_base = _to_float(
        position.get("fx_rate_to_base", position.get("fxRateToBase"))
    )
    if source_code != account_code and rate_to_base > 0:
        account_to_display = resolve_display_fx(
            raw_repository=raw_repository,
            source_currency=account_code,
            display_currency=display_code,
            report_date=report_date,
        )
        if account_to_display["status"] != "missing_rate":
            return {
                "status": "converted",
                "source_currency": source_code,
                "display_currency": display_code,
                "fx_source_currency": source_code,
                "fx_target_currency": display_code,
                "rate": rate_to_base * _to_float(account_to_display.get("rate")),
                "rate_date": account_to_display.get("rate_date") or report_date or None,
            }

    return resolve_display_fx(
        raw_repository=raw_repository,
        source_currency=source_code,
        display_currency=display_code,
        report_date=report_date,
    )


def convert_position_money_values(position: dict, conversion: dict) -> dict:
    row = dict(position)
    money_keys = (
        "mark_price_snapshot",
        "market_value_snapshot",
        "position_value",
        "cost_basis_money",
        "cost_basis_adjusted",
        "average_cost_price",
        "cost_basis_price",
        "cost_price_moving_weighted",
        "cost_price_adjusted",
        "unrealized_pnl_snapshot",
        "fifo_pnl_unrealized",
        "realized_pnl_total",
        "previous_mark_price_snapshot",
        "previous_price",
        "daily_change",
        "realtime_price",
        "realtime_value",
        "market_value",
        "unrealized_pnl",
    )
    source_values = {key: row[key] for key in money_keys if key in row}
    rate = _to_float(conversion.get("rate")) or 1.0
    for key in source_values:
        row[key] = convert_money(row[key], rate)
    row["source_values"] = source_values
    row["source_currency"] = conversion["source_currency"]
    row["display_currency"] = conversion["display_currency"]
    row["currency_conversion"] = {**conversion, "rate": round(rate, 8)}
    return row


def convert_money(value: object, rate: float) -> float:
    return round(_to_float(value) * rate, 2)
