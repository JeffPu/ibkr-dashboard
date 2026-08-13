import { useEffect, useMemo, useRef, useState } from "react";
import type { EChartsOption } from "echarts";
import { api } from "../../lib/api";
import type { ApiRecord, PositionsResponse } from "../../lib/contracts";
import {
  asArray,
  asNumber,
  asRecord,
  asText,
  deltaClass,
  formatCurrency,
  formatDate,
  formatNumber,
  formatPercent,
  sortRecords,
} from "../../lib/format";
import {
  DataTable,
  DeltaText,
  EmptyState,
  Field,
  LoadingBlock,
  MetricCard,
  PaginationFooter,
  SegmentedControl,
  Surface,
  Toolbar,
} from "../../components/Primitives";
import { EChart } from "../../components/charts/EChart";
import { useApiData } from "../../lib/useApiData";

interface PositionData {
  positions: PositionsResponse | null;
  options: PositionsResponse | null;
  allPositions: PositionsResponse | null;
  allOptions: PositionsResponse | null;
  industry: ApiRecord | null;
  industryMappings: ApiRecord | null;
  overview: ApiRecord | null;
}

type CostMode = "moving" | "adjusted";

const COST_MODE_LABEL: Record<CostMode, string> = {
  moving: "移动加权",
  adjusted: "摊薄成本",
};

const PIE_COLORS = [
  "#226f54",
  "#a05a16",
  "#3d6f9f",
  "#b13a32",
  "#5c5a97",
  "#64713d",
  "#8f4f6f",
  "#2f7d7a",
  "#ad7b2b",
  "#46505a",
  "#7a6a42",
];

type ExpiryFilter = "all" | "within_30" | "within_7" | "expired";

export function PositionsPage({ initialExpiryFilter = "all" }: { initialExpiryFilter?: ExpiryFilter }) {
  const optionSectionRef = useRef<HTMLDivElement | null>(null);
  const [query, setQuery] = useState({ symbol: "", asset_type: "stock", page: 1, page_size: 20 });
  const [optionQuery, setOptionQuery] = useState({ symbol: "", asset_type: "option", expiry_status: initialExpiryFilter, page: 1, page_size: 20 });
  const [sortKey, setSortKey] = useState("realtime_value");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [costMode, setCostMode] = useState<CostMode>("moving");
  const [selected, setSelected] = useState<ApiRecord | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailState, setDetailState] = useState<{ loading: boolean; error: string | null; data: ApiRecord | null }>({
    loading: false,
    error: null,
    data: null,
  });
  const [mappingDraft, setMappingDraft] = useState({ symbol: "", industry: "" });
  const [mappingSaving, setMappingSaving] = useState(false);
  const [mappingMessage, setMappingMessage] = useState<string | null>(null);
  const [copiedContractCode, setCopiedContractCode] = useState("");
  const { state, load } = useApiData<PositionData>(async () => {
    const [positions, options, allPositions, allOptions, industry, overview, industryMappings] = await Promise.all([
      api.positions(query),
      api.positions(optionQuery),
      api.positions({ asset_type: "stock", page: 1, page_size: 100 }),
      api.positions({ asset_type: "option", page: 1, page_size: 100 }),
      api.industryAllocation(),
      api.overview(),
      api.industryMappings(),
    ]);
    return { positions, options, allPositions, allOptions, industry, overview, industryMappings };
  }, [query, optionQuery]);

  useEffect(() => {
    const allLoadedRows = asArray(state.data?.allPositions?.items);
    setSelected((prev) => {
      if (allLoadedRows.length === 0) return null;
      const previousSymbol = asText(prev?.symbol, "").toUpperCase();
      const stillVisible = allLoadedRows.find((row) => asText(row.symbol, "").toUpperCase() === previousSymbol);
      return stillVisible ?? allLoadedRows[0];
    });
  }, [state.data?.allPositions]);

  useEffect(() => {
    if (initialExpiryFilter !== "all") {
      optionSectionRef.current?.scrollIntoView({ block: "start" });
    }
  }, [initialExpiryFilter]);

  const positionData = state.data;
  const currency = asText(positionData?.positions?.display_currency, "USD");
  const allRows = useMemo(() => asArray(positionData?.allPositions?.items), [positionData?.allPositions]);
  const rows = useMemo(() => {
    const sourceRows = query.symbol
      ? allRows.filter((row) => asText(row.symbol, "").toUpperCase() === query.symbol)
      : asArray(positionData?.positions?.items);
    return sortRecords(sourceRows, sortKey, sortDir);
  }, [allRows, positionData?.positions, query.symbol, sortKey, sortDir]);
  const total = asNumber(positionData?.positions?.total, rows.length);
  const optionRows = asArray(positionData?.options?.items);
  const optionTotal = asNumber(positionData?.options?.total, optionRows.length);
  const optionSummary = asRecord(positionData?.options?.summary);
  const overviewSourceValues = asRecord(positionData?.overview?.source_values);
  const cash = asNumber(overviewSourceValues.cash ?? positionData?.overview?.cash, 0);
  const holdingsValue = allRows.reduce((sum, row) => sum + asNumber(row.realtime_value ?? row.market_value_snapshot, 0), 0);
  const equity = asNumber(overviewSourceValues.equity ?? positionData?.overview?.equity, holdingsValue + cash);
  const industryRows = asArray(positionData?.industry?.items);
  const mappingRows = asArray(positionData?.industryMappings?.items);
  const rowSymbols = useMemo(
    () => Array.from(new Set(allRows.map((row) => asText(row.symbol, "").toUpperCase()).filter(Boolean))).sort(),
    [allRows],
  );
  const optionSymbols = useMemo(
    () => Array.from(new Set(asArray(positionData?.allOptions?.items).map((row) => asText(row.underlying_symbol ?? row.underlying, "").toUpperCase()).filter(Boolean))).sort(),
    [positionData?.allOptions],
  );
  const holdingPieRows = useMemo(() => {
    const holdings = allRows
      .map((row) => ({
        label: asText(row.symbol, "UNKNOWN"),
        value: Math.max(0, asNumber(row.realtime_value ?? row.market_value_snapshot, 0)),
      }))
      .filter((row) => row.value > 0);
    return [...holdings, { label: "现金", value: Math.max(0, cash) }];
  }, [allRows, cash]);
  const industryPieRows = useMemo(
    () => industryRows.map((row) => ({ label: asText(row.industry, "Unknown"), value: Math.max(0, asNumber(row.market_value, 0)) })),
    [industryRows],
  );
  const unknownIndustrySymbols = useMemo(
    () => allRows
      .filter((row) => {
        const industry = asText(row.industry, "Unknown").trim();
        return !industry || industry.toLowerCase() === "unknown";
      })
      .map((row) => asText(row.symbol, "").toUpperCase())
      .filter(Boolean)
      .sort(),
    [allRows],
  );
  const mappingBySymbol = useMemo(() => {
    const map = new Map<string, string>();
    for (const row of mappingRows) {
      map.set(asText(row.symbol, "").toUpperCase(), asText(row.industry, ""));
    }
    return map;
  }, [mappingRows]);
  const selectedSymbol = asText(selected?.symbol, "");

  useEffect(() => {
    if (!selectedSymbol) {
      setDetailState({ loading: false, error: null, data: null });
      return;
    }
    let cancelled = false;
    setDetailState((prev) => ({ ...prev, loading: true, error: null }));
    api.positionDetail(selectedSymbol)
      .then((data) => {
        if (!cancelled) setDetailState({ loading: false, error: null, data });
      })
      .catch((error) => {
        if (!cancelled) {
          setDetailState({ loading: false, error: error instanceof Error ? error.message : "unknown error", data: null });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedSymbol]);

  useEffect(() => {
    if (mappingDraft.symbol || !selectedSymbol) return;
    const industry = mappingBySymbol.get(selectedSymbol) ?? asText(selected?.industry, "");
    setMappingDraft({
      symbol: selectedSymbol,
      industry: industry === "Unknown" ? "" : industry,
    });
  }, [mappingBySymbol, mappingDraft.symbol, selected, selectedSymbol]);

  useEffect(() => {
    if (!detailOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDetailOpen(false);
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [detailOpen]);

  const applySort = (key: string) => {
    if (key === sortKey) {
      setSortDir((dir) => (dir === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(key);
    setSortDir("desc");
  };

  const openPositionDetail = (row: ApiRecord) => {
    setSelected(row);
    setDetailOpen(true);
  };

  const selectMappingSymbol = (symbol: string) => {
    const normalized = symbol.toUpperCase();
    const row = allRows.find((item) => asText(item.symbol, "").toUpperCase() === normalized);
    const industry = mappingBySymbol.get(normalized) ?? asText(row?.industry, "");
    setMappingDraft({ symbol: normalized, industry: industry === "Unknown" ? "" : industry });
    setMappingMessage(null);
  };

  const saveMapping = async () => {
    const symbol = mappingDraft.symbol.trim().toUpperCase();
    const industry = mappingDraft.industry.trim();
    if (!symbol || !industry) {
      setMappingMessage("请先选择代码并填写行业。");
      return;
    }
    setMappingSaving(true);
    setMappingMessage(null);
    try {
      await api.saveIndustryMapping(symbol, industry);
      setMappingMessage(`${symbol} 已映射为 ${industry}`);
      await load();
    } catch (error) {
      setMappingMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setMappingSaving(false);
    }
  };

  const deleteMapping = async () => {
    const symbol = mappingDraft.symbol.trim().toUpperCase();
    if (!symbol) {
      setMappingMessage("请先选择代码。");
      return;
    }
    setMappingSaving(true);
    setMappingMessage(null);
    try {
      await api.deleteIndustryMapping(symbol);
      setMappingDraft((prev) => ({ ...prev, industry: "" }));
      setMappingMessage(`${symbol} 的自定义行业已清除`);
      await load();
    } catch (error) {
      setMappingMessage(error instanceof Error ? error.message : "清除失败");
    } finally {
      setMappingSaving(false);
    }
  };

  const renderCost = (row: ApiRecord) => {
    const sourceValues = asRecord(row.source_values);
    const value = costMode === "moving"
      ? sourceValues.cost_price_moving_weighted ?? sourceValues.average_cost_price ?? row.cost_price_moving_weighted ?? row.average_cost_price
      : sourceValues.cost_price_adjusted ?? row.cost_price_adjusted;
    return formatCurrency(value, asText(row.source_currency ?? row.currency, currency));
  };
  const costSortKey = costMode === "moving" ? "cost_price_moving_weighted" : "cost_price_adjusted";

  return (
    <>
      {state.loading && !positionData?.positions ? <LoadingBlock /> : null}
      {state.error ? <div className="inline-error">{state.error}</div> : null}

      <div className="content-grid">
        <Surface title="持仓汇总" className="positions-chart-surface positions-chart-surface--holdings">
          <PieChart
            rows={holdingPieRows}
            emptyTitle="暂无持仓汇总"
            emptyDetail="导入最新持仓快照后显示股票与现金占比。"
          />
        </Surface>
        <Surface title="行业分布" className="positions-chart-surface">
          <PieChart
            rows={industryPieRows}
            emptyTitle="暂无行业分布"
            emptyDetail="导入持仓或设置行业映射后显示。"
          />
          <IndustryMappingEditor
            rowSymbols={rowSymbols}
            mappingDraft={mappingDraft}
            mappingSaving={mappingSaving}
            mappingMessage={mappingMessage}
            onSelectSymbol={selectMappingSymbol}
            onChangeIndustry={(industry) => setMappingDraft((prev) => ({ ...prev, industry }))}
            onSave={saveMapping}
            onDelete={deleteMapping}
          />
          {unknownIndustrySymbols.length ? (
            <div className="unknown-industry-note">
              <span>Unknown</span>
              <strong>{unknownIndustrySymbols.join(" / ")}</strong>
            </div>
          ) : null}
        </Surface>
      </div>

      <Surface
        title="股票持仓"
        className="positions-table-surface"
        action={
          <div className="cost-mode-control">
            <span>成本价：</span>
            <SegmentedControl
              ariaLabel="成本价"
              className="segmented-control--compact"
              options={[
                { value: "moving", label: COST_MODE_LABEL.moving },
                { value: "adjusted", label: COST_MODE_LABEL.adjusted },
              ]}
              value={costMode}
              onChange={(mode) => {
                setCostMode(mode);
                setSortKey(mode === "moving" ? "cost_price_moving_weighted" : "cost_price_adjusted");
              }}
            />
          </div>
        }
      >
        <Toolbar>
          <Field label="股票代码">
            <select value={query.symbol} onChange={(event) => setQuery({ ...query, symbol: event.target.value, page: 1 })}>
              <option value="">全部</option>
              {rowSymbols.map((symbol) => (
                <option value={symbol} key={symbol}>{symbol}</option>
              ))}
            </select>
          </Field>
        </Toolbar>
        <DataTable
          rows={rows}
          onRowClick={openPositionDetail}
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={applySort}
          columns={[
            {
              key: "symbol",
              label: "代码",
              render: (row) => (
                <button
                  type="button"
                  className="link-button"
                  onClick={(event) => {
                    event.stopPropagation();
                    openPositionDetail(row);
                  }}
                >
                  {asText(row.symbol)}
                </button>
              ),
            },
            { key: "industry", label: "行业" },
            { key: "quantity", label: "数量", align: "right", render: (row) => formatNumber(row.quantity, 0) },
            { key: costSortKey, label: "成本价", align: "right", render: renderCost },
            { key: "realtime_price", label: "市价", align: "right", render: (row) => formatCurrency(asRecord(row.source_values).realtime_price ?? row.realtime_price, asText(row.source_currency ?? row.currency, currency)) },
            {
              key: "daily_change_pct",
              label: "日涨跌",
              align: "right",
              render: (row) => (
                <span className={`delta-text ${deltaClass(row.daily_change_pct)}`}>
                  {formatPercent(row.daily_change_pct)}
                </span>
              ),
            },
            { key: "cost_basis_money", label: "成本", align: "right", render: (row) => formatCurrency(row.cost_basis_money, currency) },
            { key: "realtime_value", label: "持仓市值", align: "right", render: (row) => formatCurrency(row.realtime_value, currency) },
            { key: "unrealized_pnl_snapshot", label: "未实现盈亏", align: "right", render: (row) => <DeltaText value={row.unrealized_pnl_snapshot} currency={currency} /> },
            { key: "realized_pnl_total", label: "已实现盈亏", align: "right", render: (row) => <DeltaText value={row.realized_pnl_total} currency={currency} /> },
            { key: "weight", label: "占比", align: "right", render: (row) => <WeightCell value={asNumber(row.realtime_value, 0) / Math.max(equity, 1)} /> },
            { key: "quote_source", label: "价格源", render: (row) => <span className="quote-source-pill">{asText(row.quote_source, "-")}</span> },
          ]}
          empty="暂无股票持仓"
        />
        <PaginationFooter
          className="table-footer"
          page={query.page}
          pageSize={query.page_size}
          total={total}
          onPageChange={(page) => setQuery({ ...query, page })}
          onPageSizeChange={(pageSize) => setQuery({ ...query, page_size: pageSize, page: 1 })}
        />
      </Surface>

      <div ref={optionSectionRef} className="option-section-anchor">
      <Surface title="期权持仓" className="positions-table-surface" >
        {optionTotal > 0 ? (
          <div className="metric-grid metric-grid--compact option-summary-grid">
            <MetricCard label="期权净市值" value={formatCurrency(optionSummary.option_net_market_value, currency)} />
            <MetricCard label="期权未实现盈亏" value={<DeltaText value={optionSummary.option_unrealized_pnl} currency={currency} />} tone={deltaClass(optionSummary.option_unrealized_pnl)} />
            <MetricCard label="30 天内到期持仓" value={formatNumber(optionSummary.expiring_30_contracts, 0)} />
          </div>
        ) : null}
        {optionTotal === 0 && optionQuery.symbol === "" && optionQuery.expiry_status === "all" ? (
          <div className="empty-state empty-state--compact"><strong>暂无期权持仓</strong><span>导入包含 OPT 或 FOP 的 Flex 持仓后显示。</span></div>
        ) : <>
          <Toolbar>
            <Field label="标的代码">
              <select value={optionQuery.symbol} onChange={(event) => setOptionQuery({ ...optionQuery, symbol: event.target.value, page: 1 })}>
                <option value="">全部</option>
                {optionSymbols.map((symbol) => <option value={symbol} key={symbol}>{symbol}</option>)}
              </select>
            </Field>
            <Field label="到期状态">
              <select value={optionQuery.expiry_status} onChange={(event) => setOptionQuery({ ...optionQuery, expiry_status: event.target.value as ExpiryFilter, page: 1 })}>
                <option value="all">全部</option>
                <option value="within_30">30 天内</option>
                <option value="within_7">7 天内</option>
                <option value="expired">已到期 · 待核对</option>
              </select>
            </Field>
          </Toolbar>
        {asText(positionData?.options?.snapshot_date) ? (
          <div className={`option-snapshot-note ${positionData?.options?.is_stale ? "option-snapshot-note--stale" : ""}`}>
            持仓快照 {formatDate(positionData?.options?.snapshot_date)}
            {positionData?.options?.is_stale ? " · 数据可能过期" : ""}
          </div>
        ) : null}
        <DataTable
          rows={optionRows}
          columns={[
            { key: "contract_title", label: "合约", render: (row) => {
              const code = asText(row.raw_contract_code ?? row.symbol);
              return (
                <div className="option-contract-cell">
                  <div className="option-contract-title">
                    <strong>{asText(row.contract_title)}</strong>
                    <div className="option-contract-actions">
                      {row.is_short ? <span className="option-short-label">卖方持仓</span> : null}
                      <button type="button" className="option-contract-copy" onClick={() => { void copyContractCode(code).then((copied) => copied && setCopiedContractCode(code)); }}>
                        {copiedContractCode === code ? "已复制" : "复制合约代码"}
                      </button>
                    </div>
                  </div>
                  {asText(row.contract_data_status) === "incomplete" ? <span>合约数据不完整</span> : null}
                </div>
              );
            } },
            { key: "days_to_expiry", label: "到期状态", render: (row) => <OptionExpiryBadge row={row} /> },
            { key: "quantity", label: "数量", align: "right", render: (row) => formatNumber(row.quantity, 0) },
            { key: "multiplier", label: "乘数", align: "right", render: (row) => formatNumber(row.multiplier, 0) },
            { key: "average_cost_price", label: "均价", align: "right", render: (row) => formatCurrency(row.average_cost_price, asText(row.currency, currency)) },
            { key: "mark_price_snapshot", label: "Flex 标记价", align: "right", render: (row) => formatCurrency(row.mark_price_snapshot, asText(row.currency, currency)) },
            { key: "market_value_snapshot", label: "市值", align: "right", render: (row) => formatCurrency(row.market_value_snapshot, currency) },
            { key: "unrealized_pnl_snapshot", label: "未实现盈亏", align: "right", render: (row) => <DeltaText value={row.unrealized_pnl_snapshot} currency={currency} /> },
          ]}
          empty="暂无期权持仓"
        />
        {optionTotal > 0 ? (
          <PaginationFooter
            className="table-footer"
            page={optionQuery.page}
            pageSize={optionQuery.page_size}
            total={optionTotal}
            onPageChange={(page) => setOptionQuery({ ...optionQuery, page })}
            onPageSizeChange={(pageSize) => setOptionQuery({ ...optionQuery, page_size: pageSize, page: 1 })}
          />
        ) : null}
        </>}
      </Surface>
      </div>

      {detailOpen && selected ? (
        <PositionDetailModal
          selected={selected}
          detail={detailState.data}
          loading={detailState.loading}
          error={detailState.error}
          currency={currency}
          onClose={() => setDetailOpen(false)}
        />
      ) : null}
    </>
  );
}

function OptionExpiryBadge({ row }: { row: ApiRecord }) {
  const days = asNumber(row.days_to_expiry, Number.NaN);
  const status = asText(row.expiry_status, "incomplete");
  const label = status === "expired"
    ? "已到期 · 待核对"
    : Number.isFinite(days)
      ? `${formatNumber(days, 0)} 天`
      : "合约数据不完整";
  return <span className={`option-expiry-badge option-expiry-badge--${asText(row.expiry_risk, "none")}`}>{label}</span>;
}

async function copyContractCode(code: string): Promise<boolean> {
  if (!code) return false;
  try {
    await navigator.clipboard.writeText(code);
    return true;
  } catch {
    return false;
  }
}

function PieChart({
  rows,
  emptyTitle,
  emptyDetail,
}: {
  rows: Array<{ label: string; value: number }>;
  emptyTitle: string;
  emptyDetail: string;
}) {
  const visible = rows.filter((row) => row.value > 0);
  const total = visible.reduce((sum, row) => sum + row.value, 0);
  if (visible.length === 0 || total <= 0) {
    return <EmptyState compact title={emptyTitle} detail={emptyDetail} />;
  }
  const option: EChartsOption = {
    animationDuration: 240,
    aria: { enabled: true, decal: { show: true } },
    color: PIE_COLORS,
    tooltip: {
      trigger: "item",
      renderMode: "richText",
      formatter: (params) => {
        const item = params as { name?: string; value?: unknown; percent?: unknown };
        return `${item.name ?? "-"}\n占比 ${formatNumber(item.percent, 2)}%\n金额 ${formatNumber(item.value, 2)}`;
      },
    },
    legend: {
      type: "scroll",
      orient: "vertical",
      right: 2,
      top: "middle",
      width: "34%",
      textStyle: { color: "#20231f", fontSize: 12, fontWeight: 700 },
      formatter: (name: string) => {
        const row = visible.find((item) => item.label === name);
        return `${name}  ${formatPercent((row?.value ?? 0) / total)}`;
      },
    },
    series: [{
      name: "占比",
      type: "pie",
      radius: ["12%", "72%"],
      center: ["31%", "50%"],
      minAngle: 1,
      avoidLabelOverlap: true,
      label: { show: false },
      emphasis: { scaleSize: 7, label: { show: true, formatter: "{b}\n{d}%", fontWeight: 800 } },
      data: visible.map((row) => ({ name: row.label, value: row.value })),
    }],
  };

  return (
    <div className="pie-panel">
      <EChart
        option={option}
        height={280}
        ariaLabel={`持仓占比饼图：${visible.map((row) => `${row.label} ${formatPercent(row.value / total)}`).join("，")}。图例可选择显示或隐藏。`}
      />
    </div>
  );
}

function IndustryMappingEditor({
  rowSymbols,
  mappingDraft,
  mappingSaving,
  mappingMessage,
  onSelectSymbol,
  onChangeIndustry,
  onSave,
  onDelete,
}: {
  rowSymbols: string[];
  mappingDraft: { symbol: string; industry: string };
  mappingSaving: boolean;
  mappingMessage: string | null;
  onSelectSymbol: (symbol: string) => void;
  onChangeIndustry: (industry: string) => void;
  onSave: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="industry-editor">
      <div className="industry-editor__form">
        <Field label="代码">
          <select value={mappingDraft.symbol} onChange={(event) => onSelectSymbol(event.target.value)}>
            <option value="">选择持仓</option>
            {rowSymbols.map((symbol) => (
              <option value={symbol} key={symbol}>{symbol}</option>
            ))}
          </select>
        </Field>
        <Field label="行业">
          <input
            value={mappingDraft.industry}
            placeholder="例如：航空航天"
            onChange={(event) => onChangeIndustry(event.target.value)}
          />
        </Field>
        <button type="button" onClick={onSave} disabled={mappingSaving}>保存</button>
        <button type="button" onClick={onDelete} disabled={mappingSaving || !mappingDraft.symbol}>清除</button>
      </div>
      {mappingMessage ? <div className="message-bar message-bar--compact">{mappingMessage}</div> : null}
    </div>
  );
}

function WeightCell({ value }: { value: number }) {
  const safeValue = Math.max(0, value);
  return (
    <span className="weight-cell">
      <i style={{ width: `${Math.min(safeValue * 100, 100)}%` }} />
      <b>{formatPercent(safeValue)}</b>
    </span>
  );
}

function PositionDetailModal({
  selected,
  detail,
  loading,
  error,
  currency,
  onClose,
}: {
  selected: ApiRecord;
  detail: ApiRecord | null;
  loading: boolean;
  error: string | null;
  currency: string;
  onClose: () => void;
}) {
  const detailPosition = asRecord(detail?.position);
  const source = Object.keys(detailPosition).length ? detailPosition : selected;
  const sourceValues = asRecord(source.source_values);
  const selectedSourceValues = asRecord(selected.source_values);
  const priceCurrency = asText(detail?.price_currency ?? source.source_currency ?? source.currency, currency);
  const costPrice = sourceValues.cost_price_moving_weighted
    ?? sourceValues.average_cost_price
    ?? sourceValues.cost_price_adjusted
    ?? selectedSourceValues.cost_price_moving_weighted
    ?? selectedSourceValues.average_cost_price
    ?? selectedSourceValues.cost_price_adjusted
    ?? source.cost_price_moving_weighted
    ?? source.average_cost_price
    ?? source.cost_price_adjusted;
  const currentPrice = sourceValues.realtime_price
    ?? sourceValues.mark_price_snapshot
    ?? selectedSourceValues.realtime_price
    ?? selectedSourceValues.mark_price_snapshot
    ?? source.realtime_price
    ?? source.mark_price_snapshot;
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="position-modal" role="dialog" aria-modal="true" aria-labelledby="position-modal-title" onMouseDown={(event) => event.stopPropagation()}>
        <header className="position-modal__header">
          <div>
            <span className="eyebrow">持仓详情</span>
            <h2 id="position-modal-title">{asText(selected.symbol)} K 线与买卖点</h2>
          </div>
          <button type="button" className="modal-close-button" onClick={onClose} aria-label="关闭">×</button>
        </header>
        <div className="detail-grid position-detail-grid">
          <MetricCard label="数量" value={formatNumber(source.quantity ?? selected.quantity, 0)} />
          <MetricCard label="未实现盈亏" value={<DeltaText value={source.unrealized_pnl_snapshot ?? selected.unrealized_pnl_snapshot} currency={currency} />} tone={deltaClass(source.unrealized_pnl_snapshot ?? selected.unrealized_pnl_snapshot)} />
          <MetricCard label="成本价" value={formatCurrency(costPrice, priceCurrency)} />
          <MetricCard label="当前价" value={formatCurrency(currentPrice, priceCurrency)} />
          {loading ? <LoadingBlock label="正在读取个股详情" /> : null}
          {error ? <div className="inline-error">{error}</div> : null}
          <PositionChart detail={detail} currency={priceCurrency} symbol={asText(selected.symbol)} />
        </div>
      </section>
    </div>
  );
}

function PositionChart({ detail, currency, symbol }: { detail: ApiRecord | null; currency: string; symbol: string }) {
  const data = asRecord(detail);
  const history = asArray(data.price_history);
  const markers = asArray(data.markers);
  const trades = asArray(data.trades);
  if (history.length >= 2) {
    return <CandlestickChart history={history} markers={markers} currency={currency} symbol={symbol} />;
  }
  if (markers.length > 0 || trades.length > 0) {
    return <TradeMarkerTimeline markers={markers.length ? markers : trades} currency={currency} symbol={symbol} />;
  }
  return (
    <EmptyState
      title="K 线数据不完整"
      detail="请导入包含 ibkr_symbol_price_history_v1 / PPP0 的历史价格文件。"
    />
  );
}

function CandlestickChart({
  history,
  markers,
  currency,
  symbol,
}: {
  history: ApiRecord[];
  markers: ApiRecord[];
  currency: string;
  symbol: string;
}) {
  const chartHistory = history
    .map((row) => {
      const date = getHistoryDate(row);
      const close = asNumber(row.close, 0);
      return {
        row,
        date,
        open: asNumber(row.open ?? row.close, close),
        close,
        low: asNumber(row.low ?? row.close, close),
        high: asNumber(row.high ?? row.close, close),
        volume: asNumber(row.volume, 0),
      };
    })
    .filter((item) => item.date !== "-")
    .sort((left, right) => left.date.localeCompare(right.date));
  const chartMarkers = markers
    .map((marker) => ({
      date: formatDate(marker.date ?? marker.trade_date_iso ?? marker.trade_date),
      side: asText(marker.side, "").toUpperCase(),
      price: asNumber(marker.price ?? marker.trade_price, 0),
      quantity: Math.abs(asNumber(marker.quantity, 0)),
    }))
    .filter((marker) => marker.date !== "-" && (marker.side === "BUY" || marker.side === "SELL"));
  const dates = new Set(chartHistory.map((item) => item.date));
  const visibleMarkers = chartMarkers.filter((marker) => dates.has(marker.date));
  const markersByDate = new Map<string, typeof chartMarkers>();
  for (const marker of visibleMarkers) {
    markersByDate.set(marker.date, [...(markersByDate.get(marker.date) ?? []), marker]);
  }
  const prices = chartHistory
    .flatMap((item) => [item.high, item.low, item.open, item.close])
    .concat(visibleMarkers.map((marker) => marker.price))
    .filter(Number.isFinite);
  const rawMin = Math.min(...(prices.length ? prices : [0]));
  const rawMax = Math.max(...(prices.length ? prices : [1]));
  const rawRange = rawMax - rawMin || Math.max(Math.abs(rawMax) * 0.02, 1);
  const min = rawMin - rawRange * 0.04;
  const max = rawMax + rawRange * 0.04;
  const historyByDate = new Map(chartHistory.map((item) => [item.date, item]));
  const startPercent = chartHistory.length > 60 ? Math.max(0, 100 - (60 / chartHistory.length) * 100) : 0;
  const option: EChartsOption = {
    animationDuration: 200,
    aria: { enabled: true, decal: { show: true } },
    axisPointer: { link: [{ xAxisIndex: [0, 1] }] },
    grid: [
      { left: 22, right: 22, top: 12, height: "58%", containLabel: true },
      { left: 22, right: 22, top: "72%", height: "10%", containLabel: true },
    ],
    tooltip: {
      trigger: "axis",
      renderMode: "richText",
      axisPointer: { type: "cross" },
      formatter: (params) => {
        const items = Array.isArray(params) ? params : [params];
        const date = asText((items[0] as { axisValue?: unknown }).axisValue, "");
        const item = historyByDate.get(date);
        if (!item) return formatDate(date);
        const dailyReturn = item.open ? (item.close - item.open) / item.open : null;
        const lines = [
          date,
          `开盘价 ${formatCurrency(item.open, currency)}`,
          `最高价 ${formatCurrency(item.high, currency)}`,
          `最低价 ${formatCurrency(item.low, currency)}`,
          `收盘价 ${formatCurrency(item.close, currency)}`,
          `涨跌幅 ${dailyReturn === null ? "-" : formatPercent(dailyReturn)}`,
          `成交量 ${formatNumber(item.volume, 0)}`,
          `市盈率 ${formatPeRatio(item.row)}`,
          `PE行业位置 ${formatPeIndustryPosition(item.row)}`,
          ...(markersByDate.get(date) ?? []).map((marker) => {
            const label = marker.side === "BUY" ? "买入" : "卖出";
            return `${label} ${formatNumber(marker.quantity, 0)} @ ${formatCurrency(marker.price, currency)}`;
          }),
        ];
        return lines.join("\n");
      },
    },
    xAxis: [
      {
        type: "category",
        data: chartHistory.map((item) => item.date),
        boundaryGap: true,
        axisLine: { lineStyle: { color: "rgba(32,35,31,0.24)" } },
        axisLabel: { color: "#697067", hideOverlap: true },
      },
      {
        type: "category",
        gridIndex: 1,
        data: chartHistory.map((item) => item.date),
        boundaryGap: true,
        axisLabel: { show: false },
        axisTick: { show: false },
      },
    ],
    yAxis: [
      {
        scale: true,
        splitNumber: 4,
        axisLabel: { color: "#697067", formatter: (value: number) => formatCurrency(value, currency) },
        splitLine: { lineStyle: { color: "rgba(32,35,31,0.1)", type: "dashed" } },
      },
      {
        gridIndex: 1,
        scale: true,
        axisLabel: { show: false },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], start: startPercent, end: 100, minValueSpan: 2 },
      { type: "slider", xAxisIndex: [0, 1], start: startPercent, end: 100, bottom: 4, height: 18, brushSelect: false },
    ],
    series: [
      {
        name: "K 线",
        type: "candlestick",
        data: chartHistory.map((item) => [item.open, item.close, item.low, item.high]),
        itemStyle: {
          color: "#0f7a4d",
          color0: "#c23a32",
          borderColor: "#0f7a4d",
          borderColor0: "#c23a32",
        },
      },
      {
        name: "成交量",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: chartHistory.map((item) => ({
          value: item.volume,
          itemStyle: { color: item.close >= item.open ? "rgba(15,122,77,0.32)" : "rgba(194,58,50,0.32)" },
        })),
      },
      {
        name: "买入",
        type: "scatter",
        data: visibleMarkers.filter((marker) => marker.side === "BUY").map((marker) => [marker.date, marker.price, marker.quantity]),
        symbolSize: 18,
        itemStyle: { color: "#0f7a4d", borderColor: "#fff", borderWidth: 1.5 },
        label: { show: true, formatter: "B", color: "#fff", fontSize: 9, fontWeight: 900 },
        z: 5,
      },
      {
        name: "卖出",
        type: "scatter",
        data: visibleMarkers.filter((marker) => marker.side === "SELL").map((marker) => [marker.date, marker.price, marker.quantity]),
        symbolSize: 18,
        itemStyle: { color: "#c23a32", borderColor: "#fff", borderWidth: 1.5 },
        label: { show: true, formatter: "S", color: "#fff", fontSize: 9, fontWeight: 900 },
        z: 5,
      },
    ],
  };

  return (
    <div className="position-chart">
      <div className="position-chart__header">
        <strong>{symbol} K 线</strong>
        <span>{formatCurrency(min, currency)} - {formatCurrency(max, currency)}</span>
      </div>
      <EChart
        option={option}
        height={360}
        ariaLabel={`${symbol} K 线、成交量和买卖点图；悬停可查看开高低收、涨跌幅、成交量、市盈率、行业位置和交易详情，可缩放和平移`}
      />
    </div>
  );
}

function getHistoryDate(row: ApiRecord) {
  return formatDate(row.date_iso ?? row.date ?? row.price_date ?? row.report_date);
}

function formatPeRatio(row: ApiRecord) {
  const value = asNumber(
    row.pe_ratio
      ?? row.pe
      ?? row.trailing_pe
      ?? row.pe_ttm
      ?? row.price_earnings_ratio
      ?? row.price_earnings,
    Number.NaN,
  );
  if (!Number.isFinite(value)) return "缺失";
  if (value <= 0) return "亏损";
  return `${formatNumber(value, 2)}x`;
}

function formatPeIndustryPosition(row: ApiRecord) {
  const rank = asNumber(row.pe_rank ?? row.pe_ttm_rank, Number.NaN);
  const total = asNumber(row.pe_total ?? row.pe_ttm_total, Number.NaN);
  const percentile = asNumber(row.pe_percentile ?? row.pe_ttm_percentile, Number.NaN);
  if (Number.isFinite(rank) && Number.isFinite(total) && total > 0) {
    const suffix = Number.isFinite(percentile) ? ` · ${formatNumber(percentile, 1)}%` : "";
    return `${formatNumber(rank, 0)}/${formatNumber(total, 0)}${suffix}`;
  }
  return "-";
}

function TradeMarkerTimeline({
  markers,
  currency,
  symbol,
}: {
  markers: ApiRecord[];
  currency: string;
  symbol: string;
}) {
  const visible = markers
    .map((marker) => ({
      date: formatDate(marker.date ?? marker.trade_date_iso ?? marker.trade_date),
      side: asText(marker.side, "").toUpperCase(),
      price: asNumber(marker.price ?? marker.trade_price, 0),
      quantity: asNumber(marker.quantity, 0),
    }))
    .filter((marker) => marker.side === "BUY" || marker.side === "SELL");
  if (visible.length === 0) {
    return <EmptyState title="K 线数据不完整" detail="已有持仓，但暂未找到可标注的交易记录。" />;
  }
  const prices = visible.map((marker) => marker.price);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const option: EChartsOption = {
    animationDuration: 200,
    aria: { enabled: true, decal: { show: true } },
    grid: { left: 22, right: 22, top: 14, bottom: 26, containLabel: true },
    tooltip: {
      trigger: "axis",
      renderMode: "richText",
      formatter: (params) => {
        const items = Array.isArray(params) ? params : [params];
        const index = asNumber((items[0] as { dataIndex?: unknown }).dataIndex, 0);
        const marker = visible[index];
        if (!marker) return "";
        const label = marker.side === "BUY" ? "买入" : "卖出";
        return `${marker.date}\n${label}价 ${formatCurrency(marker.price, currency)}\n${label}数量 ${formatNumber(marker.quantity, 0)}`;
      },
    },
    xAxis: {
      type: "category",
      data: visible.map((marker) => marker.date),
      axisLabel: { color: "#697067", hideOverlap: true },
    },
    yAxis: {
      type: "value",
      scale: true,
      min,
      max: min === max ? max + 1 : max,
      axisLabel: { color: "#697067", formatter: (value: number) => formatCurrency(value, currency) },
      splitLine: { lineStyle: { color: "rgba(32,35,31,0.1)", type: "dashed" } },
    },
    series: [
      {
        name: "成交价格",
        type: "line",
        data: visible.map((marker) => marker.price),
        showSymbol: false,
        lineStyle: { color: "#11140f", width: 2.4 },
      },
      {
        name: "买入",
        type: "scatter",
        data: visible.map((marker) => marker.side === "BUY" ? marker.price : null),
        symbolSize: 18,
        itemStyle: { color: "#0f7a4d", borderColor: "#fff", borderWidth: 1.5 },
        label: { show: true, formatter: "B", color: "#fff", fontSize: 9, fontWeight: 900 },
      },
      {
        name: "卖出",
        type: "scatter",
        data: visible.map((marker) => marker.side === "SELL" ? marker.price : null),
        symbolSize: 18,
        itemStyle: { color: "#c23a32", borderColor: "#fff", borderWidth: 1.5 },
        label: { show: true, formatter: "S", color: "#fff", fontSize: 9, fontWeight: 900 },
      },
    ],
  };
  return (
    <div className="position-chart position-chart--timeline">
      <div className="position-chart__header">
        <strong>{symbol} 买卖点</strong>
        <span>历史 K 线待导入</span>
      </div>
      <EChart option={option} height={260} ariaLabel={`${symbol} 买卖点时间线；悬停可查看日期、方向、价格和数量`} />
    </div>
  );
}
