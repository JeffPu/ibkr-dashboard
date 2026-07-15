from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.api.time_normalization import normalize_date_to_iso
from app.utils.dates import parse_iso_date
from app.utils.numbers import to_float


OPTION_ASSET_CATEGORIES = {"OPT", "FOP"}


def is_option(row: dict) -> bool:
    return str(row.get("asset_category") or "").upper() in OPTION_ASSET_CATEGORIES


def contract_key(row: dict) -> str:
    category = str(row.get("asset_category") or "OPT").upper()
    symbol = str(row.get("symbol") or "").upper()
    underlying = str(row.get("underlying_symbol") or row.get("underlying") or symbol).upper()
    expiry = str(row.get("expiry") or "")
    strike = str(row.get("strike") or "")
    put_call = str(row.get("put_call") or "").upper()
    source_id = str(row.get("conid") or row.get("document_id") or symbol)
    identity = ":".join((underlying, expiry, strike, put_call))
    if expiry and strike and put_call:
        return f"{category}:{identity}"
    return f"{category}:INCOMPLETE:{source_id}"


def contract_title(row: dict) -> str:
    symbol = str(row.get("underlying_symbol") or row.get("underlying") or row.get("symbol") or "-").upper()
    expiry = normalize_date_to_iso(row.get("expiry")) or "到期日缺失"
    strike = str(row.get("strike") or "行权价缺失")
    put_call = str(row.get("put_call") or "").upper()
    right = "Call" if put_call in {"C", "CALL"} else "Put" if put_call in {"P", "PUT"} else "Call/Put 缺失"
    return f"{symbol} · {expiry} · {strike} {right}"


def today_in_timezone(timezone_name: str) -> date:
    try:
        return datetime.now(ZoneInfo(timezone_name)).date()
    except Exception:
        return date.today()


def decorate_option(row: dict, *, timezone_name: str, today: date | None = None) -> dict:
    decorated = dict(row)
    decorated["underlying_symbol"] = str(row.get("underlying_symbol") or row.get("underlying") or "").upper()
    decorated["raw_contract_code"] = str(row.get("symbol") or "")
    decorated["contract_key"] = contract_key(row)
    decorated["contract_title"] = contract_title(row)
    expiry = parse_iso_date(row.get("expiry"))
    complete = bool(expiry and row.get("strike") not in (None, "") and row.get("put_call") not in (None, ""))
    decorated["contract_data_status"] = "complete" if complete else "incomplete"
    decorated["days_to_expiry"] = None
    decorated["expiry_status"] = "incomplete"
    decorated["expiry_risk"] = "none"
    if expiry is not None:
        days = (expiry - (today or today_in_timezone(timezone_name))).days
        decorated["days_to_expiry"] = days
        if days < 0:
            decorated["expiry_status"] = "expired"
            decorated["expiry_risk"] = "expired"
        elif days <= 1:
            decorated["expiry_status"] = "within_1"
            decorated["expiry_risk"] = "urgent"
        elif days <= 7:
            decorated["expiry_status"] = "within_7"
            decorated["expiry_risk"] = "warning"
        elif days <= 30:
            decorated["expiry_status"] = "within_30"
            decorated["expiry_risk"] = "watch"
        else:
            decorated["expiry_status"] = "later"
    decorated["is_short"] = to_float(row.get("quantity", row.get("position"))) < 0
    return decorated


def matches_expiry_filter(row: dict, expiry_status: str) -> bool:
    days = row.get("days_to_expiry")
    if expiry_status == "all":
        return True
    if expiry_status == "expired":
        return isinstance(days, int) and days < 0
    if expiry_status == "within_7":
        return isinstance(days, int) and 0 <= days <= 7
    if expiry_status == "within_30":
        return isinstance(days, int) and 0 <= days <= 30
    return False


def sort_options(rows: list[dict], *, alerts_only: bool = False) -> list[dict]:
    def key(row: dict) -> tuple:
        days = row.get("days_to_expiry")
        incomplete = days is None
        expired_rank = 0 if isinstance(days, int) and days < 0 and alerts_only else 1
        expiry = normalize_date_to_iso(row.get("expiry")) or "9999-12-31"
        return (incomplete, expired_rank, expiry, 0 if row.get("is_short") else 1, row.get("contract_key", ""))

    return sorted(rows, key=key)


def snapshot_freshness(rows: list[dict], *, timezone_name: str) -> tuple[str | None, bool]:
    dates = [parse_iso_date(row.get("report_date")) for row in rows]
    snapshot = max((value for value in dates if value is not None), default=None)
    if snapshot is None:
        return None, False
    return snapshot.isoformat(), (today_in_timezone(timezone_name) - snapshot).days > 3


def option_summary(rows: list[dict]) -> dict:
    expiring = {
        str(row.get("contract_key"))
        for row in rows
        if isinstance(row.get("days_to_expiry"), int) and 0 <= int(row["days_to_expiry"]) <= 30
    }
    expiring_short = {
        str(row.get("contract_key"))
        for row in rows
        if row.get("is_short")
        and isinstance(row.get("days_to_expiry"), int)
        and 0 <= int(row["days_to_expiry"]) <= 30
    }
    return {
        "option_net_market_value": round(sum(to_float(row.get("market_value_snapshot", row.get("position_value"))) for row in rows), 2),
        "option_unrealized_pnl": round(sum(to_float(row.get("unrealized_pnl_snapshot", row.get("fifo_pnl_unrealized"))) for row in rows), 2),
        "expiring_30_contracts": len(expiring),
        "expiring_30_short_contracts": len(expiring_short),
    }


def build_expiration_alerts(rows: list[dict], *, timezone_name: str, limit: int) -> dict:
    decorated = [decorate_option(row, timezone_name=timezone_name) for row in rows if is_option(row) and abs(to_float(row.get("quantity", row.get("position")))) > 1e-9]
    alerts = [
        row
        for row in decorated
        if row.get("contract_data_status") == "complete"
        and isinstance(row.get("days_to_expiry"), int)
        and int(row["days_to_expiry"]) <= 30
    ]
    alerts = sort_options(alerts, alerts_only=True)
    snapshot_date, is_stale = snapshot_freshness(decorated, timezone_name=timezone_name)
    items = []
    for row in alerts[:limit]:
        items.append(
            {
                "contract_key": row["contract_key"],
                "contract_title": row["contract_title"],
                "raw_contract_code": row["raw_contract_code"],
                "days_to_expiry": row["days_to_expiry"],
                "expiry_status": row["expiry_status"],
                "expiry_risk": row["expiry_risk"],
                "is_short": row["is_short"],
                "snapshot_date": snapshot_date,
                "is_stale": is_stale,
            }
        )
    return {
        "items": items,
        "total": len(alerts),
        "remaining_count": max(0, len(alerts) - len(items)),
        "snapshot_date": snapshot_date,
        "is_stale": is_stale,
    }
