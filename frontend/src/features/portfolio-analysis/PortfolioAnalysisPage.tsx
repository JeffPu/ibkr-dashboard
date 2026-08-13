import { api } from "../../lib/api";
import type { ApiRecord, MarketAnalysisSection, PortfolioAnalysisResponse, StandardMetric } from "../../lib/contracts";
import { formatNumber, recordArray, recordBool, recordNumber, recordObject, recordText } from "../../lib/format";
import { useApiData } from "../../lib/useApiData";
import { Icon } from "../../components/Icon";
import { DataState, MetricCard, StatusPill } from "../../components/Primitives";

export function PortfolioAnalysisPage() {
  const { state, load } = useApiData<PortfolioAnalysisResponse>(() => api.portfolioAnalysis());

  return (
    <div className="portfolio-analysis-page">
      <DataState loading={state.loading} error={state.error} data={state.data} onRetry={load}>
        {(data) => <MarketPanel data={data} />}
      </DataState>
    </div>
  );
}

function MarketPanel({ data }: { data: PortfolioAnalysisResponse }) {
  const market = data.market;
  const strategy = market.strategy ?? [];
  const strategySummary = strategy[0] ?? {};
  const pulse = market.market_pulse ?? [];
  const dateText = new Date().toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit", weekday: "short" });
  return (
    <div className="market-pulse-page market-workbench">
      <section className="market-pulse-hero">
        <div>
          <span className="market-pulse-kicker">每日市场脉搏</span>
          <h2>{String(market.regime.value ?? "待判断")}</h2>
        </div>
        <div className="market-pulse-hero__meta">
          <StatusPill tone={market.status === "ready" ? "positive" : "neutral"}>{statusLabel(market.status)}</StatusPill>
          <span>{dateText}</span>
        </div>
      </section>

      <MarketKpis market={market} />

      <div className="market-brief-grid">
        <section className={`market-today-brief market-today-brief--${recordText(strategySummary, "tone", "neutral")}`}>
          <div className="market-today-brief__headline">
            <span>今日市场</span>
            <strong>{marketTodaySummary(market, strategySummary)}</strong>
          </div>
        </section>

        <section className="market-side-brief">
          <InsightList title="组合影响" items={market.portfolio_impact.slice(0, 3)} compact />
          <InsightList title="机会 / 风险" items={[...market.opportunities, ...market.risks].slice(0, 3)} compact />
          {market.watch_symbols.length ? (
            <div className="market-watch-strip">
              {market.watch_symbols.slice(0, 6).map((symbol) => <span key={symbol}>{symbol}</span>)}
            </div>
          ) : null}
        </section>
      </div>

      <div className="market-pulse-grid">
        {pulse.length ? pulse.map((item) => <MarketPulseCard key={recordText(item, "key", recordText(item, "title", ""))} item={item} />) : (
          <MetricCard
            label="市场状态"
            value={marketMetricValue(market.regime)}
            tone={market.regime.status === "ready" ? "positive" : "neutral"}
            hint={statusLabel(market.regime.status)}
          />
        )}
      </div>
    </div>
  );
}

function MarketKpis({ market }: { market: MarketAnalysisSection }) {
  const items = [
    { icon: "radar", label: "市场状态", value: String(market.regime.value ?? "-"), tone: market.regime.status === "ready" ? "accent" : "neutral" },
    { icon: "target", label: "RSI", value: marketMetricValue(market.indicators.rsi), tone: metricNumber(market.indicators.rsi) >= 70 ? "negative" : metricNumber(market.indicators.rsi) <= 35 ? "positive" : "neutral" },
    { icon: "spark", label: "恐惧贪婪", value: marketMetricValue(market.indicators.fear_greed ?? market.indicators.cnn_fear_greed), tone: "accent" },
    { icon: "alert", label: "VIX", value: marketMetricValue(market.indicators.vix), tone: metricNumber(market.indicators.vix) >= 25 ? "negative" : "positive" },
    { icon: "database", label: "上涨广度", value: marketMetricValue(market.indicators.breadth), tone: metricNumber(market.indicators.breadth) >= 50 ? "positive" : "negative" },
    { icon: "compass", label: "组合日变", value: marketMetricValue(market.indicators.portfolio_weighted_change), tone: metricNumber(market.indicators.portfolio_weighted_change) >= 0 ? "positive" : "negative" },
  ];
  return (
    <div className="market-kpi-strip">
      {items.map((item) => (
        <div className={`market-kpi market-kpi--${item.tone}`} key={item.label}>
          <div>
            <Icon className="analysis-icon" name={item.icon} />
            <span>{item.label}</span>
          </div>
          <strong>{item.value}</strong>
        </div>
      ))}
    </div>
  );
}

function marketMetricValue(metric: StandardMetric | undefined): string {
  if (!metric || metric.value === null || metric.value === undefined || metric.value === "") return "-";
  if (typeof metric.value === "string") return metric.value;
  const value = formatNumber(Number(metric.value));
  return metric.unit === "percent" ? `${value}%` : value;
}

function metricNumber(metric: StandardMetric | undefined): number {
  const value = Number(metric?.value);
  return Number.isFinite(value) ? value : 0;
}

function marketTodaySummary(market: MarketAnalysisSection, strategy: ApiRecord): string {
  const explicit = recordText(strategy, "summary", "");
  if (explicit) return explicit;
  const rsi = market.indicators.rsi?.value;
  const rsiText = typeof rsi === "number" ? formatNumber(rsi) : rsi === null || rsi === undefined ? "-" : String(rsi);
  const benchmark = benchmarkFromSource(market.indicators.rsi?.source ?? "QQQ");
  const regime = String(market.regime.value ?? "待判断");
  const sizing = regime === "拥挤多头" || regime === "亢奋动量" ? "观察仓/小仓位" : regime === "投降区间" || regime === "恐慌压缩" ? "防守仓位" : "常规节奏";
  return `当前${benchmark} RSI=${rsiText}，市场处于${regime}。建议新仓位控制在${sizing}，已持仓标的今日大跌须区分基本面恶化 vs 市场拖累。`;
}

function benchmarkFromSource(source: string): string {
  if (source.includes("SPY")) return "SPY";
  if (source.includes("QQQ")) return "QQQ";
  if (source.includes("^NDX")) return "NDX";
  return "QQQ";
}

function MarketPulseCard({ item }: { item: ApiRecord }) {
  const value = recordNumber(item, "value");
  const change = recordNumber(item, "change");
  const changePercent = recordNumber(item, "change_percent");
  const badge = recordObject(item, "badge");
  const playbook = recordArray(item, "playbook");
  const sparkline = recordArray(item, "sparkline");
  const accent = recordText(item, "accent", "green");
  const tone = recordText(badge, "tone", "neutral");
  return (
    <article className={`market-pulse-card market-pulse-card--${accent}`}>
      <div className="market-pulse-card__top">
        <div>
          <span className="market-pulse-card__bar" />
          <h3>{recordText(item, "title", "-")} <small>· {recordText(item, "symbol", "")}</small></h3>
          <p>{recordText(item, "subtitle", "")}</p>
        </div>
        <span className={`market-chip market-chip--${tone}`}>{recordText(badge, "label", "观察")}</span>
      </div>
      <div className="market-pulse-card__value">
        <strong>{value === null ? "-" : formatNumber(value)}</strong>
        {change !== null || changePercent !== null ? (
          <span className={(changePercent ?? change ?? 0) < 0 ? "negative" : "positive"}>
            {change !== null ? `${change > 0 ? "+" : ""}${formatNumber(change)}` : ""}
            {changePercent !== null ? ` / ${changePercent > 0 ? "+" : ""}${formatNumber(changePercent)}%` : ""}
          </span>
        ) : null}
      </div>
      {sparkline.length ? <MiniSparkline points={sparkline} /> : <ThresholdBand rows={playbook} />}
      <p className="market-pulse-card__reading">{recordText(item, "reading", "")}</p>
      <div className="market-pulse-card__source">
        <span>{recordText(item, "source", "来源未注明")}</span>
        <em>置信度 {Math.round((recordNumber(item, "confidence") ?? 0) * 100)}%</em>
      </div>
      {playbook.length ? <PlaybookRows rows={playbook} /> : null}
    </article>
  );
}

function MiniSparkline({ points }: { points: ApiRecord[] }) {
  const values = points.map((point) => recordNumber(point, "value")).filter((value): value is number => value !== null);
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(max - min, 0.0001);
  const d = values
    .map((value, index) => {
      const x = (index / Math.max(values.length - 1, 1)) * 100;
      const y = 36 - ((value - min) / range) * 32;
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
  return (
    <svg className="market-sparkline" viewBox="0 0 100 42" preserveAspectRatio="none" aria-hidden="true">
      <path d={d} />
      <circle cx="100" cy={36 - ((values[values.length - 1] - min) / range) * 32} r="2.4" />
    </svg>
  );
}

function ThresholdBand({ rows }: { rows: ApiRecord[] }) {
  if (!rows.length) return null;
  return (
    <div className="threshold-band">
      {rows.map((row, index) => (
        <span key={`${recordText(row, "range", "")}-${index}`} className={recordBool(row, "active") ? "active" : ""} />
      ))}
    </div>
  );
}

function PlaybookRows({ rows }: { rows: ApiRecord[] }) {
  return (
    <div className="market-playbook-rows">
      {rows.map((row, index) => (
        <div key={`${recordText(row, "range", "")}-${index}`} className={recordBool(row, "active") ? "active" : ""}>
          <span>{recordText(row, "range", "-")}</span>
          <strong>{recordText(row, "label", "-")}</strong>
          <em>{recordText(row, "action", "-")}</em>
        </div>
      ))}
    </div>
  );
}

function InsightList({ title, items, compact = false }: { title: string; items: string[]; compact?: boolean }) {
  if (!items.length) return null;
  return (
    <div className={`analysis-insights ${compact ? "analysis-insights--compact" : ""}`}>
      <strong>{title}</strong>
      <ul>
        {items.map((item) => <li key={item}>{item}</li>)}
      </ul>
    </div>
  );
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    ready: "已就绪",
    pending: "更新中",
    missing_data: "缺数据",
    stale: "需更新",
    unavailable: "不可用",
    error: "错误",
  };
  return labels[status] ?? status;
}
