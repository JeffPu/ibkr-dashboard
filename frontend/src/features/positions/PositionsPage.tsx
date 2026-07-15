import { useCallback, useEffect, useMemo, useState } from "react";
import type { MouseEvent, WheelEvent } from "react";
import { api } from "../../lib/api";
import type { ApiRecord } from "../../lib/contracts";
import {
  asArray,
  asNumber,
  asRecord,
  asText,
  clamp,
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
  PageHeader,
  PaginationFooter,
  SegmentedControl,
  StatusPill,
  Surface,
  Toolbar,
} from "../../components/Primitives";
import { useApiData } from "../../lib/useApiData";

interface PositionData {
  positions: ApiRecord | null;
  options: ApiRecord | null;
  allPositions: ApiRecord | null;
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
  const { state, load } = useApiData<PositionData>(async () => {
    const [positions, options, allPositions, industry, overview, industryMappings] = await Promise.all([
      api.positions(query),
      api.positions(optionQuery),
      api.positions({ asset_type: "stock", page: 1, page_size: 100 }),
      api.industryAllocation(),
      api.overview(),
      api.industryMappings(),
    ]);
    return { positions, options, allPositions, industry, overview, industryMappings };
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

  const positionData = state.data;
  const currency = asText(positionData?.positions?.display_currency, "USD");
  const rows = useMemo(
    () => sortRecords(asArray(positionData?.positions?.items), sortKey, sortDir),
    [positionData?.positions, sortKey, sortDir],
  );
  const allRows = useMemo(() => asArray(positionData?.allPositions?.items), [positionData?.allPositions]);
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
      <PageHeader
        eyebrow="持仓明细"
        title="股票、期权、行业与个股轨迹"
        meta={
          <>
            <StatusPill tone={asText(positionData?.positions?.valuation_mode) === "realtime" ? "positive" : "neutral"}>
              {asText(positionData?.positions?.valuation_mode, "snapshot")}
            </StatusPill>
            <button type="button" onClick={load}>刷新</button>
          </>
        }
      />

      {state.loading && !positionData?.positions ? <LoadingBlock /> : null}
      {state.error ? <div className="inline-error">{state.error}</div> : null}

      <div className="content-grid">
        <Surface title="持仓汇总" className="positions-chart-surface positions-chart-surface--holdings">
          <PieChart
            rows={holdingPieRows}
            totalOverride={equity}
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
          <button type="button" onClick={load}>查询</button>
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

      <Surface title="期权持仓" className="positions-table-surface" >
        {optionTotal > 0 ? (
          <div className="metric-grid metric-grid--compact option-summary-grid">
            <MetricCard label="期权净市值" value={formatCurrency(optionSummary.option_net_market_value, currency)} />
            <MetricCard label="期权未实现盈亏" value={<DeltaText value={optionSummary.option_unrealized_pnl} currency={currency} />} tone={deltaClass(optionSummary.option_unrealized_pnl)} />
            <MetricCard label="30 天内到期持仓" value={`${formatNumber(optionSummary.expiring_30_contracts, 0)}（空仓 ${formatNumber(optionSummary.expiring_30_short_contracts, 0)}）`} />
          </div>
        ) : null}
        <Toolbar>
          <Field label="标的代码">
            <input
              value={optionQuery.symbol}
              placeholder="如 AAPL"
              onChange={(event) => setOptionQuery({ ...optionQuery, symbol: event.target.value.toUpperCase(), page: 1 })}
            />
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
            { key: "contract_title", label: "合约", render: (row) => <div className="option-contract-cell"><strong>{asText(row.contract_title)}</strong><small>{asText(row.raw_contract_code ?? row.symbol)}</small>{asText(row.contract_data_status) === "incomplete" ? <span>合约数据不完整</span> : null}</div> },
            { key: "days_to_expiry", label: "到期状态", render: (row) => <OptionExpiryBadge row={row} /> },
            { key: "quantity", label: "数量", align: "right", render: (row) => <>{formatNumber(row.quantity, 0)}{row.is_short ? <small className="option-short-label">卖方持仓</small> : null}</> },
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
            page={optionQuery.page}
            pageSize={optionQuery.page_size}
            total={optionTotal}
            onPageChange={(page) => setOptionQuery({ ...optionQuery, page })}
            onPageSizeChange={(pageSize) => setOptionQuery({ ...optionQuery, page_size: pageSize, page: 1 })}
          />
        ) : null}
      </Surface>

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

function PieChart({
  rows,
  totalOverride,
  emptyTitle,
  emptyDetail,
}: {
  rows: Array<{ label: string; value: number }>;
  totalOverride?: number;
  emptyTitle: string;
  emptyDetail: string;
}) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; width: number; height: number } | null>(null);
  const visible = rows.filter((row) => row.value > 0);
  const total = totalOverride && totalOverride > 0
    ? totalOverride
    : visible.reduce((sum, row) => sum + row.value, 0);
  if (visible.length === 0 || total <= 0) {
    return <EmptyState compact title={emptyTitle} detail={emptyDetail} />;
  }
  const radius = 118;
  const center = 130;
  const active = visible[activeIndex] ?? visible[0];
  let cursor = -90;
  const slices = visible.map((row, index) => {
    const percent = row.value / total;
    const angle = Math.max(0, percent * 360);
    const slice = describePieSlice(center, center, radius, cursor, cursor + angle);
    cursor += angle;
    return { ...row, percent, color: PIE_COLORS[index % PIE_COLORS.length], path: slice, index };
  });
  const showTooltip = (index: number, event: MouseEvent<SVGPathElement>) => {
    const bounds = event.currentTarget.ownerSVGElement?.getBoundingClientRect();
    if (!bounds) {
      return;
    }
    setActiveIndex(index);
    setTooltip({
      x: event.clientX - bounds.left,
      y: event.clientY - bounds.top,
      width: bounds.width,
      height: bounds.height,
    });
  };
  const tooltipX = tooltip ? Math.max(72, Math.min(tooltip.x, tooltip.width - 72)) : 0;
  const tooltipY = tooltip ? Math.max(44, Math.min(tooltip.y, tooltip.height - 18)) : 0;

  return (
    <div className="pie-panel" onMouseLeave={() => setTooltip(null)}>
      <div className="pie-chart-shell">
        <svg viewBox="0 0 260 260" role="img" aria-label="占比饼图">
          <circle cx={center} cy={center} r={radius} className="pie-track" />
          {slices.map((slice) => (
            <path
              key={`${slice.label}-${slice.index}`}
              d={slice.path}
              fill={slice.color}
              className={activeIndex === slice.index ? "pie-slice pie-slice--active" : "pie-slice"}
              onMouseEnter={(event) => showTooltip(slice.index, event)}
              onMouseMove={(event) => showTooltip(slice.index, event)}
              onFocus={() => setActiveIndex(slice.index)}
              tabIndex={0}
            >
              <title>{slice.label} {formatPercent(slice.percent)}</title>
            </path>
          ))}
        </svg>
        {tooltip ? (
          <div className="pie-tooltip" style={{ left: tooltipX, top: tooltipY }}>
            <strong>{active.label}</strong>
            <span>{formatPercent(active.value / total)}</span>
          </div>
        ) : null}
      </div>
      <div className="pie-legend">
        {slices.slice(0, 11).map((slice) => (
          <button type="button" key={slice.label} onMouseEnter={() => setActiveIndex(slice.index)} onClick={() => setActiveIndex(slice.index)}>
            <i style={{ background: slice.color }} />
            <span>{slice.label}</span>
            <strong>{formatPercent(slice.percent)}</strong>
          </button>
        ))}
      </div>
    </div>
  );
}

function describePieSlice(
  cx: number,
  cy: number,
  radius: number,
  startAngle: number,
  endAngle: number,
) {
  const safeEndAngle = endAngle - startAngle >= 359.99 ? startAngle + 359.99 : endAngle;
  const start = polarToCartesian(cx, cy, radius, safeEndAngle);
  const end = polarToCartesian(cx, cy, radius, startAngle);
  const largeArcFlag = safeEndAngle - startAngle <= 180 ? "0" : "1";
  return [
    "M", cx, cy,
    "L", start.x, start.y,
    "A", radius, radius, 0, largeArcFlag, 0, end.x, end.y,
    "Z",
  ].join(" ");
}

function polarToCartesian(cx: number, cy: number, radius: number, angleInDegrees: number) {
  const angleInRadians = (angleInDegrees - 90) * Math.PI / 180;
  return {
    x: cx + radius * Math.cos(angleInRadians),
    y: cy + radius * Math.sin(angleInRadians),
  };
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
  const [zoom, setZoom] = useState(1);
  const [panStart, setPanStart] = useState(0);
  const [hovered, setHovered] = useState<{
    index: number;
    x: number;
    y: number;
    width: number;
    height: number;
  } | null>(null);
  const width = 920;
  const height = 360;
  const padding = { top: 24, right: 28, bottom: 36, left: 64 };
  const volumeHeight = 48;
  const volumeGap = 18;
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom - volumeHeight - volumeGap;
  const volumeTop = padding.top + innerHeight + volumeGap;
  const maxZoom = Math.max(1, Math.min(12, history.length / 8));
  const effectiveZoom = clamp(zoom, 1, maxZoom);
  const visibleCount = Math.max(2, Math.min(history.length, Math.round(history.length / effectiveZoom)));
  const maxStart = Math.max(0, history.length - visibleCount);
  const startIndex = Math.round(clamp(panStart, 0, maxStart));
  const visibleHistory = history.slice(startIndex, startIndex + visibleCount);
  const chartMarkers = markers
    .map((marker, index) => ({
      marker,
      index,
      date: formatDate(marker.date ?? marker.trade_date_iso ?? marker.trade_date),
      side: asText(marker.side, "").toUpperCase(),
      price: asNumber(marker.price ?? marker.trade_price, 0),
      quantity: Math.abs(asNumber(marker.quantity, 0)),
    }))
    .filter((marker) => marker.date !== "-" && (marker.side === "BUY" || marker.side === "SELL"));
  const visibleDates = new Set(visibleHistory.map(getHistoryDate));
  const visibleMarkers = chartMarkers.filter((marker) => visibleDates.has(marker.date));
  const markersByDate = new Map<string, typeof chartMarkers>();
  for (const marker of visibleMarkers) {
    markersByDate.set(marker.date, [...(markersByDate.get(marker.date) ?? []), marker]);
  }
  const prices = visibleHistory.flatMap((row) => [
    asNumber(row.high ?? row.close, 0),
    asNumber(row.low ?? row.close, 0),
    asNumber(row.open ?? row.close, 0),
    asNumber(row.close, 0),
  ]).concat(visibleMarkers.map((marker) => marker.price)).filter(Number.isFinite);
  const rawMin = Math.min(...(prices.length ? prices : [0]));
  const rawMax = Math.max(...(prices.length ? prices : [1]));
  const rawRange = rawMax - rawMin || Math.max(Math.abs(rawMax) * 0.02, 1);
  const min = rawMin - rawRange * 0.04;
  const max = rawMax + rawRange * 0.04;
  const range = max - min || 1;
  const maxVolume = Math.max(...visibleHistory.map((row) => asNumber(row.volume, 0)), 1);
  const xFor = (index: number) => padding.left + (visibleHistory.length === 1 ? innerWidth / 2 : (index / (visibleHistory.length - 1)) * innerWidth);
  const yFor = (price: unknown) => padding.top + (1 - (asNumber(price, min) - min) / range) * innerHeight;
  const candleWidth = clamp(innerWidth / Math.max(visibleHistory.length, 1) * 0.58, 7, 18);
  const hoverBandWidth = Math.max(candleWidth + 8, innerWidth / Math.max(visibleHistory.length, 1));
  const dateIndex = new Map(visibleHistory.map((row, index) => [getHistoryDate(row), index]));
  const hoveredRow = hovered ? visibleHistory[hovered.index] : null;
  const hoveredDate = hoveredRow ? getHistoryDate(hoveredRow) : "";
  const hoveredMarkers = hoveredDate ? markersByDate.get(hoveredDate) ?? [] : [];
  const hoveredOpen = asNumber(hoveredRow?.open ?? hoveredRow?.close, 0);
  const hoveredClose = asNumber(hoveredRow?.close, 0);
  const hoveredReturn = hoveredOpen ? (hoveredClose - hoveredOpen) / hoveredOpen : null;
  const tooltipY = hovered ? clamp(hovered.y, 96, hovered.height - 44) : 0;
  const tooltipSide = hovered && hovered.x > hovered.width - 260 ? " kline-tooltip--left" : "";

  const showCandleTooltip = (index: number, event: MouseEvent<SVGRectElement>) => {
    const bounds = event.currentTarget.closest(".position-chart")?.getBoundingClientRect();
    if (!bounds) return;
    setHovered({
      index,
      x: event.clientX - bounds.left,
      y: event.clientY - bounds.top,
      width: bounds.width,
      height: bounds.height,
    });
  };

  const handleWheel = (event: WheelEvent<HTMLDivElement>) => {
    if (history.length <= 2) return;
    event.preventDefault();
    const isHorizontal = Math.abs(event.deltaX) > Math.abs(event.deltaY);
    if (isHorizontal && effectiveZoom > 1) {
      setPanStart((current) => clamp(current + (event.deltaX / Math.max(innerWidth, 1)) * visibleCount, 0, maxStart));
      setHovered(null);
      return;
    }

    const svgBounds = event.currentTarget.querySelector("svg")?.getBoundingClientRect();
    const chartLeft = svgBounds ? svgBounds.left + (padding.left / width) * svgBounds.width : event.currentTarget.getBoundingClientRect().left;
    const chartWidth = svgBounds ? (innerWidth / width) * svgBounds.width : event.currentTarget.getBoundingClientRect().width;
    const pointerRatio = clamp((event.clientX - chartLeft) / Math.max(chartWidth, 1), 0, 1);
    const anchorIndex = startIndex + pointerRatio * Math.max(visibleCount - 1, 1);
    const nextZoom = clamp(effectiveZoom * (event.deltaY < 0 ? 1.18 : 0.84), 1, maxZoom);
    const nextVisibleCount = Math.max(2, Math.min(history.length, Math.round(history.length / nextZoom)));
    const nextMaxStart = Math.max(0, history.length - nextVisibleCount);
    setZoom(nextZoom);
    setPanStart(clamp(anchorIndex - pointerRatio * Math.max(nextVisibleCount - 1, 1), 0, nextMaxStart));
    setHovered(null);
  };

  return (
    <div className="position-chart" onWheel={handleWheel} onMouseLeave={() => setHovered(null)}>
      <div className="position-chart__header">
        <strong>{symbol} K 线</strong>
        <span>{formatCurrency(min, currency)} - {formatCurrency(max, currency)}</span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${symbol} K 线`}>
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
          const y = padding.top + ratio * innerHeight;
          const value = max - ratio * range;
          return (
            <g key={ratio}>
              <line x1={padding.left} x2={width - padding.right} y1={y} y2={y} className="chart-grid-line" />
              <text x={padding.left - 10} y={y + 4} className="chart-axis-label" textAnchor="end">{formatCurrency(value, currency)}</text>
            </g>
          );
        })}
        {visibleHistory.map((row, index) => {
          const open = asNumber(row.open ?? row.close, 0);
          const close = asNumber(row.close, 0);
          const high = asNumber(row.high ?? close, close);
          const low = asNumber(row.low ?? close, close);
          const x = xFor(index);
          const yOpen = yFor(open);
          const yClose = yFor(close);
          const top = Math.min(yOpen, yClose);
          const bodyHeight = Math.max(2, Math.abs(yClose - yOpen));
          const tone = close >= open ? "positive" : "negative";
          return (
            <g key={`${getHistoryDate(row)}-${index}`} className={`candle candle--${tone}`}>
              <title>{getHistoryDate(row)} 收盘 {formatCurrency(close, currency)}</title>
              <line x1={x} x2={x} y1={yFor(high)} y2={yFor(low)} />
              <rect x={x - candleWidth / 2} y={top} width={candleWidth} height={bodyHeight} rx="1.5" />
            </g>
          );
        })}
        <line x1={padding.left} x2={width - padding.right} y1={volumeTop + volumeHeight} y2={volumeTop + volumeHeight} className="chart-grid-line" />
        {visibleHistory.map((row, index) => {
          const close = asNumber(row.close, 0);
          const open = asNumber(row.open ?? close, close);
          const volume = asNumber(row.volume, 0);
          const barHeight = Math.max(1, (volume / maxVolume) * volumeHeight);
          const x = xFor(index);
          const tone = close >= open ? "positive" : "negative";
          return (
            <rect
              key={`volume-${getHistoryDate(row)}-${index}`}
              className={`volume-bar volume-bar--${tone}`}
              x={x - candleWidth / 2}
              y={volumeTop + volumeHeight - barHeight}
              width={candleWidth}
              height={barHeight}
              rx="1"
            />
          );
        })}
        {visibleMarkers.map((marker) => {
          const exactIndex = dateIndex.get(marker.date);
          if (exactIndex === undefined) return null;
          const x = xFor(exactIndex);
          const y = yFor(marker.price);
          const label = marker.side === "BUY" ? "B" : "S";
          return (
            <g key={`${marker.side}-${marker.date}-${marker.index}`} className={`trade-marker trade-marker--${marker.side === "BUY" ? "buy" : "sell"}`}>
              <title>{marker.date} {marker.side} {formatNumber(marker.quantity, 0)} @ {formatCurrency(marker.price, currency)}</title>
              <circle cx={x} cy={y} r="8" />
              <text x={x} y={y + 3} textAnchor="middle">{label}</text>
            </g>
          );
        })}
        {hovered ? (
          <line x1={xFor(hovered.index)} x2={xFor(hovered.index)} y1={padding.top} y2={volumeTop + volumeHeight} className="kline-crosshair" />
        ) : null}
        {visibleHistory.map((row, index) => {
          const x = xFor(index);
          return (
            <rect
              key={`hover-${getHistoryDate(row)}-${index}`}
              className="kline-hover-band"
              x={x - hoverBandWidth / 2}
              y={padding.top}
              width={hoverBandWidth}
              height={volumeTop + volumeHeight - padding.top}
              onMouseEnter={(event) => showCandleTooltip(index, event)}
              onMouseMove={(event) => showCandleTooltip(index, event)}
            />
          );
        })}
      </svg>
      {hovered && hoveredRow ? (
        <div className={`kline-tooltip${tooltipSide}`} style={{ left: hovered.x, top: tooltipY }}>
          <strong>{hoveredDate}</strong>
          <div><span>开盘价</span><b>{formatCurrency(hoveredOpen, currency)}</b></div>
          <div><span>收盘价</span><b>{formatCurrency(hoveredClose, currency)}</b></div>
          <div><span>涨跌幅</span><b className={`delta-text ${deltaClass(hoveredReturn ?? 0)}`}>{hoveredReturn === null ? "-" : formatPercent(hoveredReturn)}</b></div>
          <div><span>成交量</span><b>{formatNumber(asNumber(hoveredRow.volume, 0), 0)}</b></div>
          <div><span>市盈率</span><b>{formatPeRatio(hoveredRow)}</b></div>
          <div><span>PE行业位置</span><b>{formatPeIndustryPosition(hoveredRow)}</b></div>
          {hoveredMarkers.map((marker) => {
            const label = marker.side === "BUY" ? "买入" : "卖出";
            return (
              <div className="kline-tooltip__trade" key={`${marker.side}-${marker.date}-${marker.index}`}>
                <span>{label}价</span>
                <b>{formatCurrency(marker.price, currency)}</b>
                <span>{label}数量</span>
                <b>{formatNumber(marker.quantity, 0)}</b>
              </div>
            );
          })}
        </div>
      ) : null}
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
  const width = 920;
  const height = 260;
  const padding = { top: 28, right: 28, bottom: 42, left: 64 };
  const prices = visible.map((marker) => marker.price);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const range = max - min || 1;
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;
  const xFor = (index: number) => padding.left + (visible.length === 1 ? innerWidth / 2 : (index / (visible.length - 1)) * innerWidth);
  const yFor = (price: number) => padding.top + (1 - (price - min) / range) * innerHeight;
  const points = visible.map((marker, index) => `${xFor(index)},${yFor(marker.price)}`).join(" ");
  return (
    <div className="position-chart position-chart--timeline">
      <div className="position-chart__header">
        <strong>{symbol} 买卖点</strong>
        <span>历史 K 线待导入</span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${symbol} 买卖点`}>
        {[0, 0.5, 1].map((ratio) => {
          const y = padding.top + ratio * innerHeight;
          const value = max - ratio * range;
          return (
            <g key={ratio}>
              <line x1={padding.left} x2={width - padding.right} y1={y} y2={y} className="chart-grid-line" />
              <text x={padding.left - 10} y={y + 4} className="chart-axis-label" textAnchor="end">{formatCurrency(value, currency)}</text>
            </g>
          );
        })}
        <polyline points={points} fill="none" className="trade-timeline-line" />
        {visible.map((marker, index) => {
          const x = xFor(index);
          const y = yFor(marker.price);
          const label = marker.side === "BUY" ? "B" : "S";
          return (
            <g key={`${marker.side}-${marker.date}-${index}`} className={`trade-marker trade-marker--${marker.side === "BUY" ? "buy" : "sell"}`}>
              <title>{marker.date} {marker.side} {formatNumber(marker.quantity, 0)} @ {formatCurrency(marker.price, currency)}</title>
              <circle cx={x} cy={y} r="8" />
              <text x={x} y={y + 3} textAnchor="middle">{label}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
