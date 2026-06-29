from xml.etree import ElementTree as ET

from app.models.domain import ParsedXmlData
from app.models.xml_models import (
    AccountSnapshotRecord,
    CashTransactionRecord,
    FxRateRecord,
    PositionSnapshotRecord,
    StatementFundsLineRecord,
    TradeRecord,
)


def _first_attr(node: ET.Element, *names: str, default: str = "") -> str:
    for name in names:
        value = node.attrib.get(name)
        if value not in (None, ""):
            return value
    return default


def _option_fields(node: ET.Element) -> dict[str, str]:
    return {
        "expiry": _first_attr(node, "expiry", "expiration", "expirationDate", "lastTradeDateOrContractMonth"),
        "strike": _first_attr(node, "strike", "strikePrice"),
        "put_call": _first_attr(node, "putCall", "put/call", "right", "callPut"),
        "multiplier": _first_attr(node, "multiplier"),
        "underlying": _first_attr(node, "underlyingSymbol", "underlying", "underlyingConid"),
        "delta": _first_attr(node, "delta"),
        "gamma": _first_attr(node, "gamma"),
        "theta": _first_attr(node, "theta"),
        "vega": _first_attr(node, "vega"),
    }


def parse_xml_file(path: str) -> ParsedXmlData:
    tree = ET.parse(path)
    root = tree.getroot()
    return parse_xml_content(root)


def parse_xml_string(xml_text: str) -> ParsedXmlData:
    root = ET.fromstring(xml_text)
    return parse_xml_content(root)


def parse_xml_content(root: ET.Element) -> ParsedXmlData:
    parsed = ParsedXmlData()

    change_in_nav_by_key = {
        (
            node.attrib.get("accountId", ""),
            node.attrib.get("toDate") or node.attrib.get("fromDate", ""),
        ): node.attrib
        for node in root.iter("ChangeInNAV")
    }

    for node in root.iter("EquitySummaryByReportDateInBase"):
        account_id = node.attrib.get("accountId", "")
        report_date = node.attrib.get("reportDate", "")
        change_in_nav = change_in_nav_by_key.get((account_id, report_date), {})
        parsed.account_snapshots.append(
            AccountSnapshotRecord(
                document_id=f"{account_id}_{report_date}",
                account_id=account_id,
                report_date=report_date,
                base_currency=node.attrib.get("currency", ""),
                cash=node.attrib.get("cash", "0"),
                stock_market_value=node.attrib.get("stock", "0"),
                total_equity=node.attrib.get("total", "0"),
                net_cash_inflow_daily=change_in_nav.get("depositsWithdrawals", "0"),
                realized_pnl_daily=change_in_nav.get("realized", "0"),
                commissions=change_in_nav.get("commissions", "0"),
                dividends=change_in_nav.get("dividends", "0"),
                interest=change_in_nav.get("interest", "0"),
            )
        )

    for node in root.iter("OpenPosition"):
        account_id = node.attrib.get("accountId", "")
        report_date = node.attrib.get("reportDate", "")
        asset_category = node.attrib.get("assetCategory", "")
        symbol = node.attrib.get("symbol", "")
        level_of_detail = node.attrib.get("levelOfDetail", "")
        document_parts = [account_id, report_date, asset_category, symbol, level_of_detail]
        if level_of_detail == "LOT":
            document_parts.append(
                node.attrib.get("originatingTransactionID")
                or node.attrib.get("openDateTime")
                or node.attrib.get("holdingPeriodDateTime")
                or "unknown"
            )
        parsed.positions.append(
            PositionSnapshotRecord(
                document_id="_".join(document_parts),
                account_id=account_id,
                report_date=report_date,
                asset_category=asset_category,
                symbol=symbol,
                level_of_detail=level_of_detail,
                quantity=node.attrib.get("position", "0"),
                mark_price_snapshot=node.attrib.get("markPrice", "0"),
                market_value_snapshot=node.attrib.get("positionValue", "0"),
                cost_basis_money=node.attrib.get("costBasisMoney", "0"),
                average_cost_price=node.attrib.get("costBasisPrice") or node.attrib.get("openPrice", "0"),
                unrealized_pnl_snapshot=node.attrib.get("fifoPnlUnrealized", "0"),
                currency=node.attrib.get("currency", ""),
                fx_rate_to_base=node.attrib.get("fxRateToBase", ""),
                **_option_fields(node),
            )
        )

    for node in root.iter("StatementOfFundsLine"):
        account_id = node.attrib.get("accountId", "")
        report_date = node.attrib.get("reportDate", "")
        currency = node.attrib.get("currency", "")
        activity_code = node.attrib.get("activityCode", "")
        transaction_id = node.attrib.get("transactionID", "")
        parsed.statement_funds_lines.append(
            StatementFundsLineRecord(
                document_id=f"{account_id}_{report_date}_{currency}_{activity_code}_{transaction_id}",
                account_id=account_id,
                report_date=report_date,
                currency=currency,
                activity_code=activity_code,
                transaction_id=transaction_id,
                amount=node.attrib.get("amount", "0"),
                date=node.attrib.get("date", ""),
                settle_date=node.attrib.get("settleDate", ""),
                activity_description=node.attrib.get("activityDescription", ""),
                level_of_detail=node.attrib.get("levelOfDetail", ""),
            )
        )

    for node in root.iter("ConversionRate"):
        rate_date = node.attrib.get("reportDate", "")
        from_currency = node.attrib.get("fromCurrency", "")
        to_currency = node.attrib.get("toCurrency", "")
        parsed.fx_rates.append(
            FxRateRecord(
                document_id=f"{rate_date}_{from_currency}_{to_currency}",
                rate_date=rate_date,
                from_currency=from_currency,
                to_currency=to_currency,
                rate=node.attrib.get("rate", "0"),
            )
        )

    for node in root.iter("Trade"):
        trade_date = (
            node.attrib.get("tradeDate")
            or node.attrib.get("dateTime")
            or node.attrib.get("reportDate")
            or ""
        )
        parsed.trades.append(
            TradeRecord(
                trade_id=node.attrib.get("tradeID", ""),
                account_id=node.attrib.get("accountId", ""),
                symbol=node.attrib.get("symbol", ""),
                side=node.attrib.get("buySell", ""),
                quantity=float(node.attrib.get("quantity", "0") or 0),
                trade_price=float(node.attrib.get("tradePrice", "0") or 0),
                trade_date=trade_date,
                currency=node.attrib.get("currency", ""),
                fifo_pnl_realized=float(node.attrib.get("fifoPnlRealized", "0") or 0),
                ib_commission=float(node.attrib.get("ibCommission", "0") or 0),
                **_option_fields(node),
            )
        )

    for node in root.iter("CashTransaction"):
        detail = node.attrib.get("levelOfDetail", "")
        if detail != "DETAIL":
            continue
        parsed.cash_transactions.append(
            CashTransactionRecord(
                transaction_id=node.attrib.get("transactionID", ""),
                level_of_detail=detail,
                amount=float(node.attrib.get("amount", "0") or 0),
                account_id=node.attrib.get("accountId", ""),
                report_date=node.attrib.get("reportDate", ""),
                date_time=node.attrib.get("dateTime", ""),
                settle_date=node.attrib.get("settleDate", ""),
                currency=node.attrib.get("currency", ""),
                transaction_type=node.attrib.get("type", ""),
                description=node.attrib.get("description", ""),
            )
        )

    return parsed
