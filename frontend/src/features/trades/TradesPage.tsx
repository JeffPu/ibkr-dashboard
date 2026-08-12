import { useState } from "react";
import { api } from "../../lib/api";
import type { ApiRecord, TradesResponse } from "../../lib/contracts";
import { useApiData } from "../../lib/useApiData";
import {
  asArray,
  asNumber,
  asRecord,
  asText,
  deltaClass,
  formatCurrency,
  formatDate,
  formatDateTimeMinute,
  formatInteger,
} from "../../lib/format";
import { DataState, DataTable, DeltaText, Field, MetricCard, PaginationFooter, SegmentedControl, Surface } from "../../components/Primitives";

type TradeQuery = {
  symbol: string;
  asset_type: string;
  side: string;
  start_date: string;
  end_date: string;
  page: number;
  page_size: number;
};

type CashFlowQuery = {
  currency: string;
  flow_type: string;
  start_date: string;
  end_date: string;
  page: number;
  page_size: number;
};

type TradesTab = "trades" | "cash";

export function TradesPage() {
  const [activeTab, setActiveTab] = useState<TradesTab>("trades");

  return (
    <div className="trades-page">
      <SegmentedControl
        ariaLabel="交易明细分类"
        className="trades-tabs segmented-control--compact"
        options={[{ value: "trades", label: "交易记录" }, { value: "cash", label: "出入金流水" }]}
        value={activeTab}
        onChange={setActiveTab}
      />

      {activeTab === "trades" ? <TradeRecordsPanel /> : <CashFlowsPanel />}
    </div>
  );
}

function TradeRecordsPanel() {
  const [query, setQuery] = useState<TradeQuery>({ symbol: "", asset_type: "", side: "", start_date: "", end_date: "", page: 1, page_size: 20 });
  const { state, load } = useApiData<TradesResponse>(() => api.trades(query), [query]);

  return (
    <Surface title="交易流水" className="trades-surface">
      <div className="module-filter-bar module-filter-bar--trade">
        <Field label="开始时间">
          <input type="date" value={query.start_date} onChange={(event) => setQuery({ ...query, start_date: event.target.value, page: 1 })} />
        </Field>
        <Field label="结束时间">
          <input type="date" value={query.end_date} onChange={(event) => setQuery({ ...query, end_date: event.target.value, page: 1 })} />
        </Field>
        <Field label="标的／合约">
          <input value={query.symbol} placeholder="AAPL 或原始合约代码" onChange={(event) => setQuery({ ...query, symbol: event.target.value.toUpperCase(), page: 1 })} />
        </Field>
        <Field label="资产类型">
          <select value={query.asset_type} onChange={(event) => setQuery({ ...query, asset_type: event.target.value, page: 1 })}>
            <option value="">全部</option>
            <option value="stock">股票</option>
            <option value="option">期权</option>
          </select>
        </Field>
        <Field label="买入/卖出">
          <select value={query.side} onChange={(event) => setQuery({ ...query, side: event.target.value, page: 1 })}>
            <option value="">全部</option>
            <option value="BUY">买入</option>
            <option value="SELL">卖出</option>
          </select>
        </Field>
      </div>

      <DataState loading={state.loading} error={state.error} data={state.data} onRetry={load}>
        {(data) => <TradeRecordsContent data={data} query={query} setQuery={setQuery} />}
      </DataState>
    </Surface>
  );
}

function TradeRecordsContent({
  data,
  query,
  setQuery,
}: {
  data: TradesResponse;
  query: TradeQuery;
  setQuery: (query: TradeQuery) => void;
}) {
  const summary = asRecord(data.summary);
  const rows = asArray(data.items);
  const total = asNumber(data.total, rows.length);

  return (
    <>
      <div className="metric-grid metric-grid--compact trades-kpi-grid trades-kpi-grid--trade">
        <MetricCard label="成交笔数" value={formatInteger(summary.trade_count)} />
        <MetricCard label="买入笔数" value={formatInteger(summary.buy_count)} tone="positive" />
        <MetricCard label="卖出笔数" value={formatInteger(summary.sell_count)} tone="negative" />
        <MetricCard label="佣金" value={formatCurrency(summary.commission_abs_sum, asText(data.display_currency, "USD"))} tone="negative" />
        <MetricCard label="已实现盈亏" value={<DeltaText value={summary.realized_pnl_sum} currency={asText(data.display_currency, "USD")} />} tone={deltaClass(summary.realized_pnl_sum)} />
      </div>

      <DataTable
        rows={rows}
        columns={[
          { key: "trade_date", label: "成交时间", render: (row) => formatDateTimeMinute(row.trade_date ?? row.trade_date_iso) },
          { key: "symbol", label: "标的／合约", render: (row) => asText(row.asset_category) === "OPT" || asText(row.asset_category) === "FOP" ? <div className="option-contract-cell"><strong>{asText(row.contract_title)}</strong><small>{asText(row.raw_contract_code ?? row.symbol)}</small></div> : asText(row.symbol) },
          { key: "asset_category", label: "资产", render: (row) => asText(row.asset_category) === "OPT" || asText(row.asset_category) === "FOP" ? "期权" : "股票" },
          { key: "open_close_indicator", label: "开平", render: (row) => formatOpenClose(row.open_close_indicator) },
          { key: "transaction_type", label: "交易事件", render: (row) => formatTradeEvent(row) },
          { key: "side", label: "方向", render: (row) => <DirectionPill value={asText(row.side)} buyLabel="买入" sellLabel="卖出" /> },
          { key: "trade_price", label: "成交价", align: "right", render: (row) => formatCurrency(row.trade_price, getTradeCurrency(row)) },
          { key: "quantity", label: "数量", align: "right", render: (row) => formatQuantity(row.quantity) },
          { key: "ib_commission", label: "佣金", align: "right", render: (row) => formatCurrency(Math.abs(asNumber(row.ib_commission, 0)), getTradeCurrency(row)) },
          { key: "notional_abs", label: "成交金额", align: "right", render: (row) => formatCurrency(row.notional_abs, getTradeCurrency(row)) },
          { key: "fifo_pnl_realized", label: "已实现盈亏", align: "right", render: (row) => <DeltaText value={row.fifo_pnl_realized} currency={getTradeCurrency(row)} /> },
        ]}
        empty="暂无交易记录"
      />

      <PaginationFooter
        page={query.page}
        pageSize={query.page_size}
        total={total}
        onPageChange={(page) => setQuery({ ...query, page })}
        onPageSizeChange={(pageSize) => setQuery({ ...query, page_size: pageSize, page: 1 })}
      />
    </>
  );
}

function CashFlowsPanel() {
  const [query, setQuery] = useState<CashFlowQuery>({ currency: "", flow_type: "", start_date: "", end_date: "", page: 1, page_size: 20 });
  const { state, load } = useApiData<ApiRecord>(() => api.cashFlows(query), [query]);

  return (
    <Surface title="出入金流水" className="trades-surface">
      <div className="module-filter-bar module-filter-bar--cash">
        <Field label="开始时间">
          <input type="date" value={query.start_date} onChange={(event) => setQuery({ ...query, start_date: event.target.value, page: 1 })} />
        </Field>
        <Field label="结束时间">
          <input type="date" value={query.end_date} onChange={(event) => setQuery({ ...query, end_date: event.target.value, page: 1 })} />
        </Field>
        <Field label="币种">
          <input value={query.currency} placeholder="USD" onChange={(event) => setQuery({ ...query, currency: event.target.value.toUpperCase(), page: 1 })} />
        </Field>
        <Field label="入金/出金">
          <select value={query.flow_type} onChange={(event) => setQuery({ ...query, flow_type: event.target.value, page: 1 })}>
            <option value="">全部</option>
            <option value="inflow">入金</option>
            <option value="outflow">出金</option>
          </select>
        </Field>
      </div>

      <DataState loading={state.loading} error={state.error} data={state.data} onRetry={load}>
        {(data) => <CashFlowsContent data={data} query={query} setQuery={setQuery} />}
      </DataState>
    </Surface>
  );
}

function CashFlowsContent({
  data,
  query,
  setQuery,
}: {
  data: ApiRecord;
  query: CashFlowQuery;
  setQuery: (query: CashFlowQuery) => void;
}) {
  const summary = asRecord(data.summary);
  const byCurrency = asRecord(summary.by_currency);
  const currencyRows = Object.entries(byCurrency).map(([currency, value]) => ({ currency, ...asRecord(value) }));
  const rows = asArray(data.items);
  const total = asNumber(data.total, rows.length);

  return (
    <>
      <div className="metric-grid metric-grid--compact trades-kpi-grid trades-kpi-grid--cash">
        <MetricCard label="流水总笔数" value={formatInteger(summary.flow_count ?? total)} />
        <MetricCard label="入金笔数" value={formatInteger(summary.inflow_count)} tone="positive" />
        <MetricCard label="出金笔数" value={formatInteger(summary.outflow_count)} tone="negative" />
      </div>

      <div className="currency-breakdown">
        {currencyRows.length === 0 ? (
          <div className="empty-state empty-state--compact">
            <strong>暂无币种统计</strong>
            <span>导入或筛选出入金流水后显示。</span>
          </div>
        ) : currencyRows.map((row) => {
          const record = asRecord(row);
          const currency = asText(record.currency, "USD");
          return (
            <div className="currency-summary" key={currency}>
              <strong>{currency}</strong>
              <span>累计入金 {formatCurrency(record.inflow_amount, currency)}</span>
              <span>累计出金 {formatCurrency(Math.abs(asNumber(record.outflow_amount, 0)), currency)}</span>
              <span>净流入 <DeltaText value={record.net_amount} currency={currency} /></span>
            </div>
          );
        })}
      </div>

      <DataTable
        rows={rows}
        columns={[
          { key: "report_date", label: "发生时间", render: (row) => formatDateTimeMinute(row.date_time ?? row.report_date ?? row.report_date_iso) },
          { key: "currency", label: "币种" },
          { key: "flow_type", label: "方向", render: (row) => <DirectionPill value={asText(row.flow_type)} buyLabel="入金" sellLabel="出金" inflow /> },
          { key: "amount", label: "金额", align: "right", render: (row) => <DeltaText value={row.amount} currency={getCashCurrency(row)} /> },
          { key: "settlement_date", label: "结算日", render: (row) => formatDate(row.settlement_date_iso ?? row.settlement_date ?? row.settle_date ?? row.report_date_iso ?? row.report_date) },
          { key: "transaction_id", label: "流水号", render: (row) => asText(row.transaction_id ?? row.document_id) },
        ]}
        empty="暂无出入金记录"
      />

      <PaginationFooter
        page={query.page}
        pageSize={query.page_size}
        total={total}
        onPageChange={(page) => setQuery({ ...query, page })}
        onPageSizeChange={(pageSize) => setQuery({ ...query, page_size: pageSize, page: 1 })}
      />
    </>
  );
}

function DirectionPill({
  value,
  buyLabel,
  sellLabel,
  inflow = false,
}: {
  value: string;
  buyLabel: string;
  sellLabel: string;
  inflow?: boolean;
}) {
  const normalized = value.toUpperCase();
  const positive = normalized === "BUY" || normalized === "INFLOW";
  const label = positive ? buyLabel : normalized === "SELL" || normalized === "OUTFLOW" ? sellLabel : value;
  return <span className={`direction-pill direction-pill--${positive || inflow && normalized === "INFLOW" ? "positive" : "negative"}`}>{label}</span>;
}

function getTradeCurrency(row: ApiRecord): string {
  return asText(row.notional_currency ?? row.currency, "USD");
}

function getCashCurrency(row: ApiRecord): string {
  return asText(row.currency, "USD");
}

function formatQuantity(value: unknown): string {
  const parsed = asNumber(value, Number.NaN);
  if (!Number.isFinite(parsed)) return "-";
  const absolute = Math.abs(parsed);
  if (Number.isInteger(absolute)) return formatInteger(absolute);
  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: 4,
    minimumFractionDigits: 0,
  }).format(absolute);
}

function formatOpenClose(value: unknown): string {
  const text = asText(value).toUpperCase();
  if (text === "O" || text === "OPEN") return "开仓";
  if (text === "C" || text === "CLOSE") return "平仓";
  return "-";
}

function formatTradeEvent(row: ApiRecord): string {
  const transactionType = asText(row.transaction_type);
  const notes = asText(row.notes);
  const explicitEvent = `${transactionType} ${notes}`.toLowerCase();
  if (explicitEvent.includes("assign") || explicitEvent.includes("指派")) return "被指派";
  if (explicitEvent.includes("exercise") || explicitEvent.includes("行权")) return "行权";
  return transactionType || notes || "-";
}
