from collections.abc import Callable
from threading import Event, Lock, Thread
from typing import Any, Protocol

import httpx

from app.api.portfolio_analysis_contracts import PortfolioAnalysisSectionKey
from app.services.market_data_provider import MarketDataProvider
from app.services.portfolio_analysis_service import PortfolioAnalysisService
from app.services.settings_service import SettingsService
from app.services.option_expiration import build_expiration_alerts
from app.utils.numbers import first_number
from app.utils.numbers import positive_float_or_none
from app.utils.numbers import to_float as _to_float


class TelegramHttpClient(Protocol):
    def get(self, url: str, *, params: dict[str, object], timeout: float) -> httpx.Response: ...

    def post(self, url: str, *, json: dict[str, object], timeout: float) -> httpx.Response: ...


class TelegramDeliveryService:
    def __init__(self, *, bot_token: str, client: TelegramHttpClient | None = None) -> None:
        self._bot_token = bot_token.strip()
        self._client = client or httpx.Client()

    def send_message(self, *, chat_id: str, text: str) -> dict[str, object]:
        if not self._bot_token:
            return {"ok": False, "status": "missing_bot_token"}
        try:
            response = self._client.post(
                f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
                timeout=10.0,
            )
        except httpx.HTTPError:
            return {"ok": False, "status": "delivery_failed"}
        if response.status_code >= 400:
            return {"ok": False, "status": "telegram_api_error", "status_code": response.status_code}
        return {"ok": True, "status": "sent"}


class TelegramCommandService:
    def __init__(
        self,
        *,
        settings_service: SettingsService,
        analysis_service: PortfolioAnalysisService,
        raw_repository: object | None = None,
        market_data_provider: MarketDataProvider | None = None,
    ) -> None:
        self._settings_service = settings_service
        self._analysis_service = analysis_service
        self._raw_repository = raw_repository
        self._market_data_provider = market_data_provider

    def handle_command(self, *, chat_id: str, text: str) -> dict[str, object]:
        settings = self._settings_service.get()
        normalized_chat_id = str(chat_id).strip()
        if normalized_chat_id not in settings.telegram_allowlisted_chat_ids:
            return {"ok": False, "status": "forbidden", "message": "该会话不在允许列表中"}
        normalized_text = text.strip()
        command = _normalize_command(normalized_text)
        if command in {"/overview", "/summary"}:
            return {"ok": True, "status": "ready", "message": self._overview_message()}
        if command == "/positions":
            return {"ok": True, "status": "ready", "message": self._positions_message()}
        if command == "/risk":
            analysis = self._analysis_service.get_analysis(section=PortfolioAnalysisSectionKey.PORTFOLIO, allow_ai=False)
            concentration = analysis.sections.portfolio.concentration
            return {
                "ok": True,
                "status": analysis.sections.portfolio.status,
                "message": "组合风险："
                + ", ".join(
                    f"{_metric_label(key)}={metric.value}{_unit_label(metric.unit)}"
                    for key, metric in concentration.items()
                ),
            }
        if command in {"/cashflow", "/cash"}:
            return {"ok": True, "status": "ready", "message": self._cashflow_message()}
        if command == "/market":
            analysis = self._analysis_service.get_analysis(section=PortfolioAnalysisSectionKey.MARKET, allow_ai=False)
            return {
                "ok": True,
                "status": analysis.sections.market.status,
                "message": f"市场状态：{analysis.sections.market.regime.value or '缺少行情数据'}",
            }
        if command == "/report":
            message, status = self.build_daily_report_message()
            return {"ok": True, "status": status, "message": message}
        return {
            "ok": False,
            "status": "unsupported_command",
            "message": "暂不支持该只读命令。可用命令：/summary、/positions、/risk、/cash、/market、/report。",
        }

    def build_daily_report_message(self) -> tuple[str, str]:
        analysis = self._analysis_service.get_analysis(allow_ai=False)
        expiration_text = self._option_expiration_report()
        return (
            (
                "市场分析日报："
                f"整体={_status_label(analysis.status.value)}；"
                f"市场={_status_label(analysis.sections.market.status.value)}；"
                f"组合={_status_label(analysis.sections.portfolio.status.value)}；"
                f"个股={_status_label(analysis.sections.stock.status.value)}"
                f"{expiration_text}"
            ),
            analysis.status.value,
        )

    def _option_expiration_report(self) -> str:
        if self._raw_repository is None:
            return ""
        latest = self._raw_repository.get_latest_account_snapshot()
        if not latest:
            return ""
        rows = self._raw_repository.es.search(
            index="ibkr_position_snapshots_v1",
            size=10000,
            term_filters={
                "account_id": str(latest.get("account_id") or ""),
                "report_date": str(latest.get("report_date") or ""),
            },
        )
        alerts = build_expiration_alerts(
            rows,
            timezone_name=self._settings_service.get().timezone,
            limit=10,
        )
        if not alerts["items"]:
            return "\n\n期权到期提醒：暂无近期或待核对持仓"
        lines = ["\n\n期权到期提醒："]
        if alerts["is_stale"]:
            lines.append(f"数据可能过期（快照 {alerts['snapshot_date']}）")
        for item in alerts["items"]:
            days = int(item["days_to_expiry"])
            remaining = "已到期 · 待核对" if days < 0 else f"剩余 {days} 天"
            side = " · 卖方持仓" if item["is_short"] else ""
            lines.append(
                f"- {item['contract_title']} · {remaining}{side} · 快照 {item['snapshot_date']}"
            )
        if alerts["remaining_count"]:
            lines.append(f"另有 {alerts['remaining_count']} 个，请到看板查看")
        return "\n".join(lines)

    def deliver_daily_report(self, delivery_service: TelegramDeliveryService) -> dict[str, object]:
        settings = self._settings_service.get()
        if not settings.telegram_reports_enabled:
            return {"status": "disabled", "sent": 0, "failed": 0, "results": []}
        if not settings.telegram_allowlisted_chat_ids:
            return {"status": "missing_chat_ids", "sent": 0, "failed": 0, "results": []}
        message, _status = self.build_daily_report_message()
        results = [
            {
                "chat_id": chat_id,
                **delivery_service.send_message(chat_id=chat_id, text=message),
            }
            for chat_id in settings.telegram_allowlisted_chat_ids
        ]
        sent = sum(1 for result in results if result.get("ok") is True)
        failed = len(results) - sent
        return {
            "status": "sent" if failed == 0 else "partial_failure",
            "sent": sent,
            "failed": failed,
            "results": results,
        }

    def _overview_message(self) -> str:
        latest = self._raw_repository.get_latest_account_snapshot() if self._raw_repository is not None else None
        if not latest:
            return "账户概览不可用：没有账户快照"
        return (
            f"账户概览 {latest.get('report_date')}："
            f"净值={latest.get('total_equity')} {latest.get('base_currency')}"
        )

    def _positions_message(self) -> str:
        if self._raw_repository is None:
            return "持仓不可用：存储尚未配置"
        rows = self._raw_repository.es.search(index="ibkr_position_snapshots_v1", size=5)
        if not rows:
            return "持仓不可用：没有当前持仓"
        symbols = ", ".join(str(row.get("symbol", "")) for row in rows if row.get("symbol"))
        return f"主要持仓：{symbols}"

    def _cashflow_message(self) -> str:
        if self._raw_repository is None:
            return "现金流不可用：存储尚未配置"
        rows = self._raw_repository.es.search(index="ibkr_stmt_funds_lines_v1", size=100)
        total = sum(_to_float(row.get("amount")) for row in rows)
        return f"现金流记录数={len(rows)}，合计={round(total, 2)}"

    def _price_history_context(self, positions: object) -> list[dict[str, Any]]:
        if not isinstance(positions, list):
            return []
        symbols = [
            str(row.get("symbol") or "").upper()
            for row in positions
            if isinstance(row, dict) and str(row.get("symbol") or "").strip()
        ]
        results = []
        for symbol in list(dict.fromkeys(symbols))[:5]:
            cached_points = _cached_price_history(self._raw_repository, symbol=symbol, limit=30)
            provider_points = []
            if self._market_data_provider is not None:
                try:
                    provider_points = [
                        {
                            "date": point.date,
                            "close": point.close,
                            "source": point.source,
                        }
                        for point in self._market_data_provider.get_kline_history(symbol, days=30)
                        if point.close is not None
                    ]
                except Exception:
                    provider_points = []
            points = _merge_history_points(cached_points, provider_points)[-30:]
            results.append(
                {
                    "symbol": symbol,
                    "status": "ready" if points else "missing_data",
                    "source": _history_source_label(cached_points=cached_points, provider_points=provider_points),
                    "points": points[-10:],
                    "summary": _price_history_summary(points),
                }
            )
        return results


class TelegramUpdatePollingService:
    def __init__(
        self,
        *,
        settings_service: SettingsService,
        command_service_factory: Callable[[], TelegramCommandService],
        delivery_service_factory: Callable[[str], TelegramDeliveryService],
        client: TelegramHttpClient | None = None,
        poll_interval_seconds: float = 3.0,
    ) -> None:
        self._settings_service = settings_service
        self._command_service_factory = command_service_factory
        self._delivery_service_factory = delivery_service_factory
        self._client = client or httpx.Client()
        self._poll_interval_seconds = poll_interval_seconds
        self._offset: int | None = None
        self._stop_event = Event()
        self._lock = Lock()
        self._thread: Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = Thread(target=self._run, name="telegram-update-poller", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            self._thread = None
            self._stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)

    def is_enabled(self) -> bool:
        settings = self._settings_service.get()
        return bool(settings.telegram_bot_token and settings.telegram_allowlisted_chat_ids)

    def poll_once(self) -> dict[str, object]:
        settings = self._settings_service.get()
        bot_token = settings.telegram_bot_token.strip()
        if not bot_token:
            return {"ok": False, "status": "missing_bot_token", "processed": 0, "sent": 0}
        if not settings.telegram_allowlisted_chat_ids:
            return {"ok": False, "status": "missing_chat_ids", "processed": 0, "sent": 0}
        params: dict[str, object] = {
            "limit": 20,
            "timeout": 0,
            "allowed_updates": ["message", "edited_message"],
        }
        if self._offset is not None:
            params["offset"] = self._offset
        try:
            response = self._client.get(
                f"https://api.telegram.org/bot{bot_token}/getUpdates",
                params=params,
                timeout=15.0,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return {"ok": False, "status": "poll_failed", "processed": 0, "sent": 0}
        if payload.get("ok") is not True:
            return {"ok": False, "status": "telegram_api_error", "processed": 0, "sent": 0}
        updates = payload.get("result") or []
        if not isinstance(updates, list):
            return {"ok": False, "status": "invalid_updates_payload", "processed": 0, "sent": 0}
        processed = 0
        sent = 0
        for update in updates:
            if not isinstance(update, dict):
                continue
            update_id = _to_int(update.get("update_id"))
            if update_id is not None:
                self._offset = max(self._offset or 0, update_id + 1)
            result = self._process_update(update=update)
            if result.get("processed") is True:
                processed += 1
            if result.get("sent") is True:
                sent += 1
        return {"ok": True, "status": "polled", "processed": processed, "sent": sent}

    def _run(self) -> None:
        while not self._stop_event.is_set():
            if self.is_enabled():
                self.poll_once()
            self._stop_event.wait(self._poll_interval_seconds)

    def _process_update(self, *, update: dict[str, Any]) -> dict[str, bool]:
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return {"processed": False, "sent": False}
        chat = message.get("chat")
        if not isinstance(chat, dict) or chat.get("id") is None:
            return {"processed": False, "sent": False}
        text = str(message.get("text") or "").strip()
        if not text:
            return {"processed": False, "sent": False}
        chat_id = str(chat.get("id"))
        service = self._command_service_factory()
        command_result = service.handle_command(chat_id=chat_id, text=text)
        if command_result.get("ok") is not True:
            if command_result.get("status") == "forbidden":
                return {"processed": True, "sent": False}
        message_text = str(command_result.get("message") or "").strip()
        if not message_text:
            return {"processed": True, "sent": False}
        delivery = self._delivery_service_factory(self._settings_service.get().telegram_bot_token)
        sent = delivery.send_message(chat_id=chat_id, text=message_text).get("ok") is True
        return {"processed": True, "sent": sent}


def _to_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _top_position_context(repository: object | None, *, limit: int) -> list[dict[str, Any]]:
    if repository is None or not hasattr(repository, "es"):
        return []
    try:
        rows = repository.es.search(
            index="ibkr_position_snapshots_v1",
            size=5000,
            sort_field="report_date",
            descending=True,
        )
    except Exception:
        return []
    position_rows = [row for row in rows if isinstance(row, dict)]
    latest_report_date = _latest_report_date(position_rows)
    if latest_report_date:
        position_rows = [
            row
            for row in position_rows
            if str(row.get("report_date") or row.get("report_date_iso") or "") == latest_report_date
        ]
    realized_by_symbol = _realized_pnl_by_symbol(repository)
    normalized = [
        _position_context_row(row, realized_pnl=realized_by_symbol.get(str(row.get("symbol") or "").upper()))
        for row in position_rows
    ]
    normalized = [row for row in normalized if row.get("symbol")]
    normalized.sort(key=lambda row: abs(_to_float(row.get("market_value"))), reverse=True)
    return normalized[:limit]


def _latest_report_date(rows: list[dict[str, Any]]) -> str:
    dates = [
        str(row.get("report_date") or row.get("report_date_iso") or "")
        for row in rows
        if row.get("report_date") or row.get("report_date_iso")
    ]
    return max(dates) if dates else ""


def _position_context_row(row: dict[str, Any], *, realized_pnl: float | None) -> dict[str, Any]:
    market_value = _first_float(
        row,
        ("market_value", "market_value_snapshot", "position_value", "value"),
    )
    quantity = _first_float(row, ("quantity", "position", "shares"))
    mark_price = _first_float(row, ("mark_price_snapshot", "mark_price", "current_price", "price"))
    average_cost = _first_float(row, ("average_cost_price", "cost_basis_price", "avg_cost"))
    cost_basis = _first_float(row, ("cost_basis_money", "cost_basis", "cost"))
    unrealized_pnl_raw = first_number(
        row,
        (
            "unrealized_pnl",
            "unrealized_pnl_snapshot",
            "fifo_pnl_unrealized",
            "unrealized_profit_loss",
        ),
    )
    unrealized_pnl = round(unrealized_pnl_raw, 6) if unrealized_pnl_raw is not None else None
    return {
        "symbol": str(row.get("symbol") or "").upper(),
        "report_date": row.get("report_date") or row.get("report_date_iso"),
        "currency": row.get("currency"),
        "quantity": quantity,
        "mark_price": mark_price,
        "average_cost": average_cost,
        "cost_basis": cost_basis,
        "market_value": market_value,
        "unrealized_pnl": unrealized_pnl,
        "unrealized_pnl_source_field": _first_present_key(
            row,
            (
                "unrealized_pnl",
                "unrealized_pnl_snapshot",
                "fifo_pnl_unrealized",
                "unrealized_profit_loss",
            ),
        ),
        "realized_pnl": realized_pnl,
    }


def _realized_pnl_by_symbol(repository: object | None) -> dict[str, float]:
    if repository is None or not hasattr(repository, "es"):
        return {}
    try:
        trades = repository.es.search(index="ibkr_trade_records_v1", size=5000)
    except Exception:
        return {}
    totals: dict[str, float] = {}
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        symbol = str(trade.get("symbol") or "").upper()
        if not symbol:
            continue
        totals[symbol] = totals.get(symbol, 0.0) + _to_float(trade.get("fifo_pnl_realized"))
    return {symbol: round(value, 6) for symbol, value in totals.items()}


def _cached_price_history(repository: object | None, *, symbol: str, limit: int) -> list[dict[str, Any]]:
    if repository is None or not hasattr(repository, "es"):
        return []
    rows: list[dict[str, Any]] = []
    for index in ("ibkr_symbol_price_history_v1", "market_symbol_price_history_v1"):
        try:
            found = repository.es.search(
                index=index,
                size=max(limit * 2, limit),
                term_filters={"symbol": symbol.upper()},
            )
        except Exception:
            found = []
        for row in found:
            if not isinstance(row, dict):
                continue
            close = _first_float(row, ("close", "close_price", "value", "last", "last_price"))
            date_value = row.get("date_iso") or row.get("date") or row.get("price_date") or row.get("report_date")
            if close is None or not date_value:
                continue
            rows.append(
                {
                    "date": str(date_value)[:10],
                    "close": close,
                    "source": str(row.get("source") or index),
                }
            )
    return _dedupe_history_points(rows)[-limit:]


def _first_float(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key not in row:
            continue
        value = positive_float_or_none(row.get(key), digits=6)
        if value is not None:
            return value
    return None


def _first_present_key(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if row.get(key) is not None:
            return key
    return None


def _merge_history_points(*point_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for points in point_lists:
        rows.extend(points)
    return _dedupe_history_points(rows)


def _dedupe_history_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    for point in points:
        date_key = str(point.get("date") or "")[:10]
        close = positive_float_or_none(point.get("close"), digits=6)
        if not date_key or close is None:
            continue
        by_date[date_key] = {
            "date": date_key,
            "close": close,
            "source": str(point.get("source") or "unknown"),
        }
    return [by_date[key] for key in sorted(by_date)]


def _history_source_label(*, cached_points: list[dict[str, Any]], provider_points: list[dict[str, Any]]) -> str:
    sources = []
    if cached_points:
        sources.append("local_cache")
    if provider_points:
        provider_sources = sorted({str(point.get("source") or "provider") for point in provider_points})
        sources.extend(provider_sources)
    return "+".join(dict.fromkeys(sources)) if sources else "missing"


def _price_history_summary(points: list[dict[str, Any]]) -> dict[str, Any]:
    if not points:
        return {"points": 0}
    first = points[0]
    last = points[-1]
    first_close = positive_float_or_none(first.get("close"), digits=6)
    last_close = positive_float_or_none(last.get("close"), digits=6)
    change_pct = None
    if first_close is not None and last_close is not None and first_close:
        change_pct = round((last_close - first_close) / first_close * 100, 4)
    closes = [positive_float_or_none(point.get("close"), digits=6) for point in points]
    valid_closes = [value for value in closes if value is not None]
    return {
        "points": len(points),
        "start_date": first.get("date"),
        "end_date": last.get("date"),
        "start_close": first_close,
        "end_close": last_close,
        "change_pct": change_pct,
        "min_close": min(valid_closes) if valid_closes else None,
        "max_close": max(valid_closes) if valid_closes else None,
    }


def _status_value(status: object) -> str:
    value = getattr(status, "value", status)
    return str(value or "error")


def _normalize_command(text: str) -> str:
    if not text.strip().startswith("/"):
        return ""
    command = text.strip().split()[0].lower()
    if "@" in command:
        command = command.split("@", 1)[0]
    return command


def _status_label(status: str) -> str:
    labels = {
        "ready": "已就绪",
        "pending": "生成中",
        "missing_data": "缺数据",
        "stale": "需更新",
        "unavailable": "不可用",
        "error": "错误",
    }
    return labels.get(status, status)


def _metric_label(key: str) -> str:
    labels = {
        "sector": "行业集中度",
        "single_name": "单票集中度",
        "ai_theme": "智能主题",
    }
    return labels.get(key, key)


def _unit_label(unit: str | None) -> str:
    if unit == "percent":
        return "%"
    return unit or ""
