import type {
  OverviewPositionBeta,
  OverviewResponse,
  OverviewRiskBenchmarkKey,
  OverviewRiskDashboard,
  OverviewRiskMetric,
  OverviewRiskMetricKey,
  OverviewRiskSeverity,
  OverviewRiskWindow,
} from "../../lib/contracts";
import { asNumber, clamp, formatNumber } from "../../lib/format";

export const BENCHMARK_OPTIONS: Array<{ key: OverviewRiskBenchmarkKey; label: string; fullLabel: string }> = [
  { key: "qqq", label: "NDX100", fullLabel: "Nasdaq 100" },
  { key: "nasdaq", label: "NASDAQ", fullLabel: "Nasdaq Composite" },
  { key: "sp500", label: "SPX", fullLabel: "S&P 500" },
];

export const WINDOW_OPTIONS: OverviewRiskWindow[] = [20, 60, 120];

export const SEVERITY_LABELS: Record<OverviewRiskSeverity, string> = {
  healthy: "健康",
  watch: "关注",
  caution: "谨慎",
  alert: "预警",
};

const RISK_METRIC_ORDER: OverviewRiskMetricKey[] = [
  "margin_usage",
  "largest_holding",
  "top3_concentration",
  "downside_breadth",
];

const RISK_LABELS: Record<OverviewRiskMetricKey, string> = {
  margin_usage: "Margin 使用率（估算）",
  largest_holding: "最大持仓",
  top3_concentration: "Top 3 集中度",
  downside_breadth: "下跌广度",
};

export function normalizeRiskDashboard(data: OverviewResponse): OverviewRiskDashboard {
  if (data.risk_dashboard?.metrics?.length) {
    return {
      ...data.risk_dashboard,
      metrics: RISK_METRIC_ORDER.map(
        (key) => data.risk_dashboard?.metrics.find((metric) => metric.key === key) ?? missingMetric(key),
      ),
    };
  }

  const equity = asNumber(data.equity, 0);
  const marketValue = asNumber(data.market_value, 0);
  const cash = asNumber(data.cash, 0);
  const topHoldings = Array.isArray(data.top_holdings) ? data.top_holdings : [];
  const totalHoldingValue = topHoldings.reduce(
    (sum, item) => sum + Math.abs(asNumber(item.market_value ?? item.value, 0)),
    0,
  );
  const sortedValues = topHoldings
    .map((item) => Math.abs(asNumber(item.market_value ?? item.value, 0)))
    .filter((value) => Number.isFinite(value) && value > 0)
    .sort((left, right) => right - left);
  const borrowed = Math.max(0, -cash, marketValue - equity);
  const marginUsage = equity > 0 ? (borrowed / equity) * 100 : null;
  const largestHolding = totalHoldingValue > 0 ? (sortedValues[0] / totalHoldingValue) * 100 : null;
  const top3 = totalHoldingValue > 0
    ? (sortedValues.slice(0, 3).reduce((sum, value) => sum + value, 0) / totalHoldingValue) * 100
    : null;
  const metrics: OverviewRiskMetric[] = [
    fallbackMetric(
      "margin_usage",
      marginUsage,
      "借款金额 / 净值",
      "借款金额相对净值的代理值，不是 IBKR 官方维持保证金占用率。",
    ),
    fallbackMetric("largest_holding", largestHolding, "最大持仓 / 总持仓", "关注集中度与回撤压力"),
    fallbackMetric("top3_concentration", top3, "前三持仓 / 总持仓", "关注集中度与回撤压力"),
    missingMetric("downside_breadth", "等待后端提供前一日持仓价格"),
  ];
  return {
    status: metrics.every((metric) => metric.status === "ready") ? "ready" : "partial",
    highest_severity: maxSeverity(metrics.map((metric) => metric.severity)),
    updated_at: data.valuation_as_of_local ?? data.report_date_iso ?? data.report_date ?? null,
    metrics,
  };
}

export function severityForMetric(key: OverviewRiskMetricKey, value: number): OverviewRiskSeverity {
  if (key === "margin_usage") {
    if (value <= 20) return "healthy";
    if (value <= 30) return "watch";
    if (value <= 50) return "caution";
    return "alert";
  }
  if (key === "largest_holding") {
    if (value <= 15) return "healthy";
    if (value <= 25) return "watch";
    if (value <= 35) return "caution";
    return "alert";
  }
  if (key === "top3_concentration") {
    if (value <= 40) return "healthy";
    if (value <= 60) return "watch";
    if (value <= 75) return "caution";
    return "alert";
  }
  if (value <= 40) return "healthy";
  if (value <= 55) return "watch";
  if (value <= 70) return "caution";
  return "alert";
}

export function formatPercentPoint(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "-";
  return `${value.toFixed(Math.abs(value) >= 10 ? 1 : 2)}%`;
}

export type BetaTone = "low" | "medium" | "high" | "missing";

export function betaTone(value: number | null | undefined): BetaTone {
  if (value === null || value === undefined || !Number.isFinite(value)) return "missing";
  if (value >= 1.5) return "high";
  if (value >= 1.1) return "medium";
  return "low";
}

export function betaToneLabel(tone: BetaTone): string {
  if (tone === "high") return "高 Beta";
  if (tone === "medium") return "中 Beta";
  if (tone === "low") return "低 Beta";
  return "数据不足";
}

export function betaValueClass(value: number | null | undefined): string {
  return `overview-beta-value overview-beta-value--${betaTone(value)}`;
}

export function formatWeightedContribution(
  value: number | null | undefined,
  beta: number | null | undefined,
  weightPct: number | null | undefined,
): string {
  if (typeof value === "number" && Number.isFinite(value)) return formatNumber(value, 3);
  if (typeof beta === "number" && Number.isFinite(beta) && typeof weightPct === "number" && Number.isFinite(weightPct)) {
    return formatNumber(beta * (weightPct / 100), 3);
  }
  return "-";
}

export function statusLabel(status: string | null | undefined): string {
  if (status === "ready") return "已计算";
  if (status === "partial") return "部分可用";
  if (status === "calculating") return "Beta 计算中";
  return "数据不足";
}

export function normalizePositionBeta(value: OverviewPositionBeta["beta"] | undefined): {
  value: number | null;
  weightedContribution?: number | null;
  observations?: number;
  status?: "ready" | "missing_data";
  reason?: string | null;
} {
  if (value && typeof value === "object") {
    return {
      value: value.value,
      weightedContribution: value.weighted_contribution,
      observations: value.observations,
      status: value.status,
      reason: value.reason,
    };
  }
  return { value: typeof value === "number" && Number.isFinite(value) ? value : null };
}

function fallbackMetric(
  key: OverviewRiskMetricKey,
  value: number | null,
  threshold: string,
  action: string,
): OverviewRiskMetric {
  if (value === null || !Number.isFinite(value)) return missingMetric(key);
  const severity = severityForMetric(key, value);
  return {
    key,
    label: RISK_LABELS[key],
    value,
    unit: "percent",
    status: "ready",
    severity,
    threshold_label: threshold,
    progress_pct: clamp(value, 0, 100),
    source: "本地快照",
    reason: "",
    action,
  };
}

function missingMetric(key: OverviewRiskMetricKey, reason = "缺少可计算数据"): OverviewRiskMetric {
  return {
    key,
    label: RISK_LABELS[key],
    value: null,
    unit: "percent",
    status: "missing_data",
    severity: "watch",
    threshold_label: "待计算",
    progress_pct: 0,
    source: "本地数据",
    reason,
    action: "导入更多快照后显示",
  };
}

function maxSeverity(severities: OverviewRiskSeverity[]): OverviewRiskSeverity {
  const order: OverviewRiskSeverity[] = ["healthy", "watch", "caution", "alert"];
  return severities.reduce(
    (highest, item) => order.indexOf(item) > order.indexOf(highest) ? item : highest,
    "healthy",
  );
}
