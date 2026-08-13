import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../lib/api";
import type {
  OverviewPositionBeta,
  OverviewResponse,
  OverviewRiskBenchmarkKey,
  OverviewRiskSeverity,
  OverviewRiskWarningResponse,
  OverviewRiskWindow,
} from "../../lib/contracts";
import { asNumber, clamp, formatDate, formatNumber } from "../../lib/format";
import { EmptyState, Surface } from "../../components/Primitives";
import {
  BENCHMARK_OPTIONS,
  SEVERITY_LABELS,
  WINDOW_OPTIONS,
  betaTone,
  betaToneLabel,
  betaValueClass,
  formatPercentPoint,
  formatWeightedContribution,
  normalizePositionBeta,
  sortRiskDashboard,
  statusLabel,
} from "./riskCalculations";

interface RiskWarningCacheEntry {
  window: OverviewRiskWindow;
  payload: OverviewRiskWarningResponse;
}

export function OverviewRiskDashboard({ data }: { data: OverviewResponse }) {
  const dashboard = useMemo(() => sortRiskDashboard(data.risk_dashboard), [data.risk_dashboard]);
  if (!dashboard?.metrics.length) {
    return (
      <Surface title="持仓核心风险" className="overview-risk-panel">
        <EmptyState compact title="风险数据不足" detail="后端暂未返回可展示的风险指标。" />
      </Surface>
    );
  }
  return (
    <Surface
      title="持仓核心风险"
      action={<RiskBadge severity={dashboard.highest_severity}>{SEVERITY_LABELS[dashboard.highest_severity]}</RiskBadge>}
      className="overview-risk-panel"
    >
      <div className="overview-risk-board">
        {dashboard.metrics.map((metric) => (
          <article key={metric.key} className={`overview-risk-card overview-risk-card--${metric.severity}`}>
            <div className="overview-risk-card__top">
              <span>{metric.label}</span>
              <RiskBadge severity={metric.severity}>{SEVERITY_LABELS[metric.severity]}</RiskBadge>
            </div>
            <strong>{formatPercentPoint(metric.value)}</strong>
            <div className="overview-risk-progress" aria-label={`${metric.label} ${formatPercentPoint(metric.value)}`}>
              <span style={{ width: `${clamp(asNumber(metric.progress_pct, 0), 0, 100)}%` }} />
            </div>
            <p>{metric.action || metric.threshold_label || "等待更多可追溯数据"}</p>
            {metric.status !== "ready" && metric.reason ? <small>{metric.reason}</small> : null}
          </article>
        ))}
      </div>
    </Surface>
  );
}

export function OverviewBetaStress() {
  const [benchmark, setBenchmark] = useState<OverviewRiskBenchmarkKey>("qqq");
  const [window, setWindow] = useState<OverviewRiskWindow>(60);
  const riskWarningCache = useRef(new Map<OverviewRiskWindow, RiskWarningCacheEntry>());
  const [riskState, setRiskState] = useState<{
    data: OverviewRiskWarningResponse | null;
    loading: boolean;
    error: string | null;
  }>({ data: null, loading: false, error: null });

  const hasCurrentPayload = riskState.data?.window === window
    && (riskState.data?.benchmarks.some((item) => item.key === benchmark) ?? false);
  const loadRiskWarning = useCallback(async (force = false) => {
    const cached = riskWarningCache.current.get(window)?.payload ?? null;
    if (!force && cached?.benchmarks.some((item) => item.key === benchmark)) {
      setRiskState({ data: cached, loading: false, error: null });
      return;
    }
    setRiskState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const payload = await api.overviewRiskWarning({ benchmark, window });
      riskWarningCache.current.set(window, { window, payload });
      setRiskState({ data: payload, loading: false, error: null });
    } catch (error) {
      setRiskState((prev) => ({
        data: prev.data,
        loading: false,
        error: error instanceof Error ? error.message : "unknown error",
      }));
    }
  }, [benchmark, window]);

  useEffect(() => {
    if (!hasCurrentPayload) void loadRiskWarning();
  }, [hasCurrentPayload, loadRiskWarning]);

  const selectedBenchmark = useMemo(
    () => riskState.data?.benchmarks.find((item) => item.key === benchmark) ?? null,
    [benchmark, riskState.data?.benchmarks],
  );
  const benchmarkOption = BENCHMARK_OPTIONS.find((option) => option.key === benchmark) ?? BENCHMARK_OPTIONS[0];
  const betaRows = useMemo(
    () => (riskState.data?.positions ?? [])
      .map((position) => normalizeBetaPosition(position, benchmark))
      .sort((left, right) => Math.abs(asNumber(right.market_value, 0)) - Math.abs(asNumber(left.market_value, 0))),
    [benchmark, riskState.data?.positions],
  );
  const portfolioBeta = selectedBenchmark?.portfolio_beta ?? null;
  const coverage = selectedBenchmark?.coverage_pct ?? null;

  return (
    <Surface
      title="股票组合加权 Beta"
      action={
        <label className="overview-beta-window">
          <span>计算窗口</span>
          <select value={window} onChange={(event) => setWindow(Number(event.target.value) as OverviewRiskWindow)}>
            {WINDOW_OPTIONS.map((item) => <option key={item} value={item}>{item} 日</option>)}
          </select>
        </label>
      }
      className="overview-beta-panel"
    >
      <div className="overview-risk-tabs" role="tablist" aria-label="Beta 基准">
        {BENCHMARK_OPTIONS.map((option) => (
          <button
            key={option.key}
            type="button"
            role="tab"
            aria-selected={benchmark === option.key}
            className={benchmark === option.key ? "active" : ""}
            onClick={() => setBenchmark(option.key)}
          >
            {option.label}
          </button>
        ))}
      </div>
      <div className="overview-beta-update">
        <span>Beta 更新时间：{formatDate(riskState.data?.beta_updated_at)}</span>
        <b>{riskState.loading ? "Beta 计算中" : statusLabel(riskState.data?.status)}</b>
      </div>
      {riskState.error ? (
        <div className="overview-risk-error">
          <strong>数据获取失败</strong><span>{riskState.error}</span>
          <button type="button" onClick={() => void loadRiskWarning(true)}>重试</button>
        </div>
      ) : null}
      <div className="overview-beta-summary">
        <div className="overview-beta-cell">
          <span>股票组合加权 Beta</span>
          <strong>{portfolioBeta === null ? "-" : formatNumber(portfolioBeta, 2)}</strong>
          <small>{benchmarkOption.fullLabel} · {window} 日</small>
        </div>
        <div className="overview-beta-cell">
          <span>有效持仓市值覆盖率</span>
          <strong>{formatPercentPoint(coverage)}</strong>
          <small>仅纳入历史行情充足的股票</small>
        </div>
      </div>
      <div className="overview-risk-table-wrap">
        <table className="overview-risk-table overview-risk-table--detail">
          <thead><tr><th>标的</th><th>股票权重</th><th>个仓 Beta</th><th>加权贡献</th><th>数据状态</th></tr></thead>
          <tbody>
            {betaRows.length ? betaRows.map((position) => (
              <tr key={position.symbol}>
                <td>{position.symbol}</td>
                <td>{formatPercentPoint(position.weight_pct)}</td>
                <td className={betaValueClass(position.beta)}>{position.beta === null ? "-" : formatNumber(position.beta, 2)}</td>
                <td>{formatWeightedContribution(position.weighted_contribution, position.beta, position.weight_pct)}</td>
                <td><BetaStrength value={position.beta} status={position.status} reason={position.reason} /></td>
              </tr>
            )) : <tr><td colSpan={5}>暂无可计算的股票 Beta</td></tr>}
          </tbody>
        </table>
      </div>
      {(selectedBenchmark?.reason || riskState.data?.missing_reasons.length) ? (
        <div className="overview-beta-missing">
          {[selectedBenchmark?.reason, ...(riskState.data?.missing_reasons ?? [])].filter(Boolean).slice(0, 3).map((reason) => (
            <span key={String(reason)}>{reason}</span>
          ))}
        </div>
      ) : null}
      <button type="button" className="overview-beta-refresh" onClick={() => void loadRiskWarning(true)} disabled={riskState.loading}>
        {riskState.loading ? "刷新中" : "刷新 Beta 数据"}
      </button>
    </Surface>
  );
}

function normalizeBetaPosition(position: OverviewPositionBeta, benchmark: OverviewRiskBenchmarkKey) {
  const beta = normalizePositionBeta(position.betas?.[benchmark] ?? position.beta);
  return {
    ...position,
    beta: beta.value,
    weighted_contribution: beta.weightedContribution,
    status: beta.status ?? position.status,
    reason: beta.reason ?? position.reason,
  };
}

function RiskBadge({ severity, children }: { severity: OverviewRiskSeverity; children: string }) {
  return <span className={`overview-risk-badge overview-risk-badge--${severity}`}>{children}</span>;
}

function BetaStrength({ value, status, reason }: { value: number | null | undefined; status: string; reason: string | null | undefined }) {
  const tone = status === "ready" ? betaTone(value) : "missing";
  const width = value === null || value === undefined || !Number.isFinite(value)
    ? 0
    : clamp(Math.abs(value) / 2.2 * 100, 8, 100);
  return (
    <div className="overview-beta-strength">
      <span><i className={`overview-beta-strength__bar overview-beta-strength__bar--${tone}`} style={{ width: `${width}%` }} /></span>
      <small>{tone === "missing" ? reason || "数据不足" : betaToneLabel(tone)}</small>
    </div>
  );
}
