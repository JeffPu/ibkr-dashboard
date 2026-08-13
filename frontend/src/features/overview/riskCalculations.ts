import type {
  OverviewPositionBeta,
  OverviewRiskBenchmarkKey,
  OverviewRiskDashboard,
  OverviewRiskMetricKey,
  OverviewRiskSeverity,
  OverviewRiskWindow,
} from "../../lib/contracts";
import { formatNumber } from "../../lib/format";

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

export function sortRiskDashboard(dashboard: OverviewRiskDashboard | undefined): OverviewRiskDashboard | null {
  if (!dashboard || !Array.isArray(dashboard.metrics)) return null;
  const order = new Map(RISK_METRIC_ORDER.map((key, index) => [key, index]));
  return {
    ...dashboard,
    metrics: [...dashboard.metrics].sort(
      (left, right) => (order.get(left.key) ?? RISK_METRIC_ORDER.length) - (order.get(right.key) ?? RISK_METRIC_ORDER.length),
    ),
  };
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
