from dataclasses import dataclass


@dataclass(slots=True)
class TradeRecord:
    trade_id: str
    account_id: str
    symbol: str
    side: str
    quantity: float
    trade_price: float
    trade_date: str = ""
    currency: str = ""
    fifo_pnl_realized: float = 0.0
    ib_commission: float = 0.0
    asset_category: str = ""
    expiry: str = ""
    strike: str = ""
    put_call: str = ""
    multiplier: str = ""
    underlying: str = ""
    open_close_indicator: str = ""
    transaction_type: str = ""
    notes: str = ""
    conid: str = ""
    delta: str = ""
    gamma: str = ""
    theta: str = ""
    vega: str = ""


@dataclass(slots=True)
class CashTransactionRecord:
    transaction_id: str
    level_of_detail: str
    amount: float
    account_id: str = ""
    report_date: str = ""
    date_time: str = ""
    settle_date: str = ""
    currency: str = ""
    transaction_type: str = ""
    description: str = ""


@dataclass(slots=True)
class AccountSnapshotRecord:
    document_id: str
    account_id: str
    report_date: str
    base_currency: str
    cash: str
    stock_market_value: str
    total_equity: str
    net_cash_inflow_daily: str
    realized_pnl_daily: str
    commissions: str
    dividends: str
    interest: str


@dataclass(slots=True)
class PositionSnapshotRecord:
    document_id: str
    account_id: str
    report_date: str
    asset_category: str
    symbol: str
    level_of_detail: str
    quantity: str
    mark_price_snapshot: str
    market_value_snapshot: str
    cost_basis_money: str
    average_cost_price: str
    unrealized_pnl_snapshot: str
    currency: str = ""
    fx_rate_to_base: str = ""
    expiry: str = ""
    strike: str = ""
    put_call: str = ""
    multiplier: str = ""
    underlying: str = ""
    conid: str = ""
    delta: str = ""
    gamma: str = ""
    theta: str = ""
    vega: str = ""


@dataclass(slots=True)
class StatementFundsLineRecord:
    document_id: str
    account_id: str
    report_date: str
    currency: str
    activity_code: str
    transaction_id: str
    amount: str
    date: str = ""
    settle_date: str = ""
    activity_description: str = ""
    level_of_detail: str = ""


@dataclass(slots=True)
class FxRateRecord:
    document_id: str
    rate_date: str
    from_currency: str
    to_currency: str
    rate: str
