from app.repositories.raw_repository import RawRepository
from app.utils.numbers import to_float

SEVERITY_ORDER = {"healthy": 0, "watch": 1, "caution": 2, "alert": 3}
SEVERITY_LABELS = {
    "healthy": "健康",
    "watch": "关注",
    "caution": "警戒",
    "alert": "预警",
}


def position_market_value(position: dict) -> float:
    return to_float(
        position.get("market_value")
        or position.get("market_value_snapshot")
        or position.get("position_value")
    )


def position_quantity(position: dict) -> float:
    return to_float(position.get("quantity", position.get("position", 0)))


def daily_loss_at_risk(current_positions: list[dict], all_positions: list[dict], latest_date: str) -> float | None:
    rows = _with_position_daily_changes(current_positions, all_positions, latest_date)
    losses = [
        min(change, 0.0)
        for change in (_position_daily_change_amount(row) for row in rows)
        if change is not None
    ]
    if not losses:
        return None
    return round(sum(losses), 2)


def missing_risk_dashboard(updated_at: str | None) -> dict:
    metrics = [
        _risk_metric(
            key=key,
            label=label,
            value=None,
            severity="watch",
            threshold_label=threshold,
            source=source,
            reason="missing_overview_data",
            action="请先导入 IBKR Flex XML 后再查看风险指标。",
            progress_limit=limit,
        )
        for key, label, threshold, source, limit in [
            ("margin_usage", "Margin 使用率（估算）", "≤20% 安全 / ≤30% 警戒", "ibkr_account_snapshots_v1", 30),
            ("largest_holding", "最大单仓", "≤15% 安全 / ≤25% 关注", "ibkr_position_snapshots_v1", 25),
            ("top3_concentration", "前三仓合计", "≤40% 安全 / ≤60% 关注", "ibkr_position_snapshots_v1", 60),
            ("downside_breadth", "今日下跌广度", "≤40%", "ibkr_position_snapshots_v1", 40),
        ]
    ]
    return {
        "status": "missing_data",
        "highest_severity": "watch",
        "highest_severity_label": SEVERITY_LABELS["watch"],
        "updated_at": updated_at,
        "metrics": metrics,
    }


def build_risk_dashboard(
    *,
    equity: float,
    cash: float,
    market_value: float,
    positions: list[dict],
    all_positions: list[dict],
    report_date: str,
    updated_at: str | None,
) -> dict:
    if equity <= 0 and not positions:
        return missing_risk_dashboard(updated_at)

    total_holding_value = sum(abs(position_market_value(position)) for position in positions)
    if total_holding_value <= 1e-9:
        total_holding_value = abs(market_value)
    sorted_values = sorted(
        [abs(position_market_value(position)) for position in positions if abs(position_market_value(position)) > 1e-9],
        reverse=True,
    )
    borrowed_amount = max(-cash, market_value - equity, 0.0)
    margin_usage = (borrowed_amount / equity * 100) if equity > 0 else None
    largest_holding = (sorted_values[0] / total_holding_value * 100) if total_holding_value > 0 and sorted_values else None
    top3 = (sum(sorted_values[:3]) / total_holding_value * 100) if total_holding_value > 0 and sorted_values else None
    downside_breadth, downside_reason = _downside_breadth_pct(positions, all_positions, report_date)

    metrics = [
        _risk_metric(
            key="margin_usage",
            label="Margin 使用率（估算）",
            value=margin_usage,
            severity=_severity_for_margin_usage(margin_usage),
            threshold_label="≤20% 安全 / ≤30% 警戒",
            source="ibkr_account_snapshots_v1",
            reason="max_negative_cash_or_signed_all_positions_above_equity_divided_by_equity",
            action=(
                "借款金额相对净值的代理值偏高；这不是 IBKR 官方维持保证金占用率。"
                if margin_usage is not None and margin_usage > 20
                else "借款金额相对净值的代理值；这不是 IBKR 官方维持保证金占用率。"
            ),
            progress_limit=30,
        ),
        _risk_metric(
            key="largest_holding",
            label="最大单仓",
            value=largest_holding,
            severity=_severity_for_largest_holding(largest_holding),
            threshold_label="≤15% 安全 / ≤25% 关注",
            source="ibkr_position_snapshots_v1",
            reason="largest_abs_position_market_value_divided_by_total_positions",
            action=(
                "最大单仓超过安全线，跟踪该仓是否持续扩大。"
                if largest_holding is not None and largest_holding > 15
                else "单仓集中度处于安全线内。"
            ),
            progress_limit=25,
        ),
        _risk_metric(
            key="top3_concentration",
            label="前三仓合计",
            value=top3,
            severity=_severity_for_top3(top3),
            threshold_label="≤40% 安全 / ≤60% 警戒",
            source="ibkr_position_snapshots_v1",
            reason="largest_three_abs_positions_divided_by_total_positions",
            action=(
                "前三仓集中度较高，避免新增同主题敞口。"
                if top3 is not None and top3 > 40
                else "前三仓集中度未触发关注线。"
            ),
            progress_limit=60,
        ),
        _risk_metric(
            key="downside_breadth",
            label="今日下跌广度",
            value=downside_breadth,
            severity=_severity_for_downside_breadth(downside_breadth),
            threshold_label="≤40%",
            source="ibkr_position_snapshots_v1",
            reason=downside_reason,
            action=(
                "多数持仓同步下跌，优先区分市场拖累和个股风险。"
                if downside_breadth is not None and downside_breadth > 40
                else "下跌广度未触发预警线。"
            ),
            progress_limit=40,
        ),
    ]
    ready_count = sum(1 for metric in metrics if metric["status"] == "ready")
    highest = max(metrics, key=lambda item: SEVERITY_ORDER.get(str(item.get("severity")), 0))["severity"]
    return {
        "status": "ready" if ready_count == len(metrics) else "partial" if ready_count else "missing_data",
        "highest_severity": highest,
        "highest_severity_label": SEVERITY_LABELS.get(str(highest), str(highest)),
        "updated_at": updated_at,
        "metrics": metrics,
    }


def load_latest_position_rows(raw_repository: RawRepository, account_id: str, report_date: str) -> tuple[list[dict], list[dict]]:
    all_positions = raw_repository.es.search(
        index="ibkr_position_snapshots_v1",
        size=10000,
        term_filters={"account_id": account_id} if account_id else None,
    )
    positions = [p for p in all_positions if str(p.get("report_date", "")) == str(report_date)]
    if not positions and report_date:
        older_dates = sorted(
            {
                str(p.get("report_date", ""))
                for p in all_positions
                if p.get("report_date") and str(p.get("report_date", "")) <= str(report_date)
            }
        )
        if older_dates:
            fallback_date = older_dates[-1]
            positions = [p for p in all_positions if str(p.get("report_date", "")) == fallback_date]
    latest_positions = [
        p for p in positions
        if str(p.get("level_of_detail", "") or "").upper() == "SUMMARY"
    ]
    if latest_positions:
        return latest_positions, all_positions

    lot_positions = [
        p for p in positions
        if str(p.get("level_of_detail", "") or "").upper() == "LOT" or not p.get("level_of_detail")
    ]
    aggregated: dict[str, dict] = {}
    for position in lot_positions:
        symbol = str(position.get("symbol", "") or "").upper()
        if not symbol:
            continue
        bucket = aggregated.setdefault(
            symbol,
            {
                "symbol": symbol,
                "report_date": position.get("report_date"),
                "level_of_detail": "SUMMARY",
                "quantity": 0.0,
                "market_value_snapshot": 0.0,
                "unrealized_pnl_snapshot": 0.0,
            },
        )
        bucket["quantity"] += position_quantity(position)
        bucket["market_value_snapshot"] += position_market_value(position)
        bucket["unrealized_pnl_snapshot"] += to_float(
            position.get("unrealized_pnl_snapshot", position.get("fifo_pnl_unrealized", 0))
        )
    return list(aggregated.values()), all_positions


def _risk_metric(
    *,
    key: str,
    label: str,
    value: float | None,
    severity: str,
    threshold_label: str,
    source: str,
    reason: str,
    action: str,
    progress_limit: float,
) -> dict:
    status = "ready" if value is not None else "missing_data"
    progress_pct = None
    if value is not None and progress_limit > 0:
        progress_pct = round(max(0.0, min(abs(value) / progress_limit * 100, 100.0)), 2)
    return {
        "key": key,
        "label": label,
        "value": round(value, 2) if value is not None else None,
        "unit": "percent",
        "status": status,
        "severity": severity,
        "severity_label": SEVERITY_LABELS.get(severity, severity),
        "threshold_label": threshold_label,
        "progress_pct": progress_pct,
        "source": source,
        "reason": reason,
        "action": action,
    }


def _severity_for_margin_usage(value: float | None) -> str:
    if value is None:
        return "watch"
    if value <= 20:
        return "healthy"
    if value <= 30:
        return "caution"
    return "alert"


def _severity_for_largest_holding(value: float | None) -> str:
    if value is None:
        return "watch"
    if value <= 15:
        return "healthy"
    if value <= 25:
        return "watch"
    if value <= 35:
        return "caution"
    return "alert"


def _severity_for_top3(value: float | None) -> str:
    if value is None:
        return "watch"
    if value <= 40:
        return "healthy"
    if value <= 60:
        return "watch"
    if value <= 75:
        return "caution"
    return "alert"


def _severity_for_downside_breadth(value: float | None) -> str:
    if value is None:
        return "watch"
    if value <= 40:
        return "healthy"
    if value <= 60:
        return "watch"
    return "alert"


def _position_current_price(position: dict) -> float:
    return to_float(position.get("mark_price_snapshot") or position.get("realtime_price"))


def _position_daily_change_pct(position: dict) -> float | None:
    if position.get("daily_change_pct") not in (None, ""):
        return to_float(position.get("daily_change_pct"))
    current = _position_current_price(position)
    previous = to_float(position.get("previous_mark_price_snapshot") or position.get("previous_price"))
    if current and previous:
        return (current - previous) / previous
    if position.get("daily_change") not in (None, ""):
        amount = to_float(position.get("daily_change"))
        if amount > 0:
            return 0.000001
        if amount < 0:
            return -0.000001
        return 0.0
    return None


def _position_daily_change_amount(position: dict) -> float | None:
    current = _position_current_price(position)
    previous = to_float(position.get("previous_mark_price_snapshot") or position.get("previous_price"))
    quantity = abs(position_quantity(position)) or 1.0
    if current and previous:
        return (current - previous) * quantity
    if position.get("daily_change") not in (None, ""):
        return to_float(position.get("daily_change")) * quantity
    return None


def _with_position_daily_changes(current_rows: list[dict], all_rows: list[dict], latest_date: str) -> list[dict]:
    previous_dates = sorted(
        {
            str(row.get("report_date", "") or "")
            for row in all_rows
            if str(row.get("report_date", "") or "") < latest_date
        }
    )
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
            previous_price = _position_current_price(previous)
            current_price = _position_current_price(item)
            if previous_price:
                item["previous_mark_price_snapshot"] = previous_price
                if current_price:
                    item.setdefault("daily_change", round(current_price - previous_price, 6))
                    item.setdefault("daily_change_pct", round((current_price - previous_price) / previous_price, 6))
        enriched.append(item)
    return enriched


def _downside_breadth_pct(current_positions: list[dict], all_positions: list[dict], latest_date: str) -> tuple[float | None, str]:
    rows = [
        row for row in _with_position_daily_changes(current_positions, all_positions, latest_date)
        if abs(position_market_value(row)) > 1e-9
    ]
    if not rows:
        return None, "no_active_positions"
    known_changes = [
        change for change in (_position_daily_change_pct(row) for row in rows)
        if change is not None
    ]
    if not known_changes:
        return None, "previous_position_prices_unavailable"
    downside = sum(1 for change in known_changes if change < 0)
    return downside / len(known_changes) * 100, f"{downside}/{len(known_changes)} positions_down"
