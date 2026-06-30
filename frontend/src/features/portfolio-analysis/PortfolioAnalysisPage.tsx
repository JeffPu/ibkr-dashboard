import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../../lib/api";
import type {
  ApiRecord,
  EChartsPayload,
  PageState,
  PortfolioAnalysisResponse,
  PortfolioRefreshJob,
  PortfolioRiskRow,
  PortfolioAnalysisSectionKey,
  StandardMetric,
} from "../../lib/contracts";
import { asNumber, deltaClass, formatCurrency, formatNumber, recordArray, recordBool, recordNumber, recordObject, recordText } from "../../lib/format";
import { Icon } from "../../components/Icon";
import { DataState, EmptyState, LoadingBlock, MetricCard, PageHeader, StatusPill, Surface } from "../../components/Primitives";
import { EChart } from "../../components/charts/EChart";
import { buildPortfolioAnalysisChartOption } from "../../components/charts/chartOptions";

const tabs: Array<{ key: PortfolioAnalysisSectionKey; label: string }> = [
  { key: "market", label: "市场分析" },
  { key: "portfolio", label: "持仓分析" },
];

type AIRefreshPhase = "submitting" | "preparing" | "researching" | "analyzing" | "persisting" | "complete" | "fallback" | "error";

interface AIRefreshProgress {
  phase: AIRefreshPhase;
  message: string;
  completedPositions: number;
  totalPositions: number;
  stageDurationsMs: Record<string, number>;
  reason: string | null;
  failedStage: PortfolioRefreshJob["stage"] | null;
}

export function PortfolioAnalysisPage() {
  const [section, setSection] = useState<PortfolioAnalysisSectionKey>("portfolio");
  const [state, setState] = useState<PageState<PortfolioAnalysisResponse>>({ data: null, loading: true, error: null });
  const [aiRefreshing, setAiRefreshing] = useState(false);
  const [aiProgress, setAiProgress] = useState<AIRefreshProgress | null>(null);
  const [aiProgressOpen, setAiProgressOpen] = useState(false);
  const responseCache = useRef<Map<string, PortfolioAnalysisResponse>>(new Map());

  const load = useCallback(async (options?: { showLoading?: boolean; force?: boolean; skipCache?: boolean }) => {
    const showLoading = options?.showLoading ?? true;
    const cacheKey = section;
    const cached = responseCache.current.get(cacheKey);
    if (cached && !options?.force && !options?.skipCache) {
      setState({ data: cached, loading: false, error: null });
      return cached;
    }
    if (showLoading) setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const data = await api.portfolioAnalysis({ section });
      responseCache.current.set(cacheKey, data);
      setState({ data, loading: false, error: null });
      return data;
    } catch (error) {
      setState((prev) => ({ ...prev, loading: false, error: error instanceof Error ? error.message : "unknown error" }));
      return null;
    }
  }, [section]);

  useEffect(() => {
    void load();
  }, [load]);

  const refreshAI = useCallback(async () => {
    setAiRefreshing(true);
    setAiProgressOpen(true);
    setAiProgress({
      phase: "submitting",
      message: "正在提交当前分区与最新持仓快照。",
      completedPositions: 0,
      totalPositions: 0,
      stageDurationsMs: {},
      reason: null,
      failedStage: null,
    });
    try {
      let job = await api.refreshPortfolioAnalysisNarrative({ section });
      setAiProgress(aiRefreshProgress(job));
      for (let attempt = 0; attempt < 240; attempt += 1) {
        if (["ready", "fallback", "error"].includes(job.status)) {
          await load({ showLoading: false, skipCache: true });
          setAiRefreshing(false);
          return;
        }
        await wait(1000);
        job = await api.portfolioAnalysisRefreshJob(job.job_id);
        setAiProgress(aiRefreshProgress(job));
      }
      throw new Error("AI 分析任务超过 4 分钟仍未结束");
    } catch (error) {
      setAiRefreshing(false);
      const message = error instanceof Error ? error.message : "unknown error";
      setAiProgress({
        phase: "error",
        message: `刷新失败：${message}`,
        completedPositions: 0,
        totalPositions: 0,
        stageDurationsMs: {},
        reason: message,
        failedStage: "accepted",
      });
      setState((prev) => ({ ...prev, error: message }));
    }
  }, [load, section]);

  const selectedCacheKey = section;
  const cachedData = responseCache.current.get(selectedCacheKey) ?? null;
  const currentData = responseMatchesCurrentRequest(state.data, section)
    ? state.data
    : cachedData;
  const blockingLoading = state.loading && !currentData;

  return (
    <PortfolioAnalysisShell
      section={section}
      setSection={setSection}
      data={currentData}
      loading={blockingLoading}
      reload={() => void load({ force: true })}
      refreshAI={() => void refreshAI()}
      aiRefreshing={aiRefreshing}
    >
      <DataState loading={blockingLoading} error={state.error} data={currentData} onRetry={load}>
        {(data) => (
          <>
            {section === "market" ? <MarketPanel data={data} /> : null}
            {section === "portfolio" ? <PortfolioPanel data={data} /> : null}
          </>
        )}
      </DataState>
      {aiProgressOpen && aiProgress ? (
        <AIRefreshModal
          progress={aiProgress}
          onClose={() => setAiProgressOpen(false)}
          onRetry={() => void refreshAI()}
        />
      ) : null}
    </PortfolioAnalysisShell>
  );
}

function aiRefreshProgress(job: PortfolioRefreshJob): AIRefreshProgress {
  const phaseByStage: Record<string, AIRefreshPhase> = {
    accepted: "submitting",
    preparing_inputs: "preparing",
    researching_web: "researching",
    analyzing_risks: "analyzing",
    persisting: "persisting",
    ready: "complete",
    fallback: "fallback",
    error: "error",
  };
  return {
    phase: phaseByStage[job.stage] ?? "error",
    message: job.message,
    completedPositions: job.completed_positions,
    totalPositions: job.total_positions,
    stageDurationsMs: job.stage_durations_ms,
    reason: job.reason ? aiFallbackLabel(job.reason) : null,
    failedStage: job.failed_stage,
  };
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function AIRefreshModal({
  progress,
  onClose,
  onRetry,
}: {
  progress: AIRefreshProgress;
  onClose: () => void;
  onRetry: () => void;
}) {
  const finished = ["complete", "fallback", "error"].includes(progress.phase);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previousFocus?.focus();
    };
  }, [onClose]);
  const stages = [
    ["accepted", "提交分析任务", "后台已受理，不在请求线程中等待模型"],
    ["preparing_inputs", "整理持仓输入", "读取全部持仓并生成稳定 position key"],
    ["researching_web", "联网检索证据", "DeepSeek 规划问题，Tavily 返回可追溯来源"],
    ["analyzing_risks", "生成风险分析", "逐项整理风险、跟踪条件和组合建议"],
    ["persisting", "校验并更新页面", "全量契约校验通过后才保存并应用"],
  ];
  return (
    <div className="modal-backdrop ai-progress-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className={`ai-progress-modal ai-progress-modal--${progress.phase}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ai-progress-title"
        aria-describedby="ai-progress-message"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="ai-progress-modal__header">
          <div>
            <span className="eyebrow">READ-ONLY AI RUN</span>
            <h2 id="ai-progress-title">AI 分析进度</h2>
          </div>
          <button ref={closeButtonRef} type="button" className="modal-close-button" onClick={onClose} aria-label="关闭AI分析过程">×</button>
        </header>

        <div className="ai-progress-status" aria-live="polite">
          <span className="ai-progress-status__signal" aria-hidden="true" />
          <div>
            <small>{aiRefreshPhaseLabel(progress.phase)}</small>
            <strong id="ai-progress-message">{progress.message}</strong>
            {progress.totalPositions > 0 ? (
              <small>已研究 {progress.completedPositions} / {progress.totalPositions} 个持仓</small>
            ) : null}
            {progress.reason ? <small title={progress.reason}>原因：{progress.reason}</small> : null}
          </div>
        </div>

        <ol className="ai-progress-steps">
          {stages.map(([stage, title, detail], index) => {
            const stepState = aiRefreshStepState(progress.phase, index, progress.failedStage);
            return (
              <li className={`ai-progress-step ai-progress-step--${stepState}`} key={title}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div>
                  <strong>{title}</strong>
                  <small>{detail}</small>
                  {progress.stageDurationsMs[stage] !== undefined ? (
                    <small>耗时 {formatDuration(progress.stageDurationsMs[stage])}</small>
                  ) : null}
                  {stepState === "error" ? (
                    <button type="button" className="ai-progress-step__retry" onClick={onRetry}>重试此步骤</button>
                  ) : null}
                </div>
                <i aria-hidden="true">{stepState === "done" ? "✓" : stepState === "error" ? "!" : ""}</i>
              </li>
            );
          })}
        </ol>

        <footer className="ai-progress-modal__footer">
          <span>只读分析 · 不包含下单动作</span>
          {finished ? <button type="button" onClick={onClose}>完成</button> : <small>关闭弹窗不会中断后台任务</small>}
        </footer>
      </section>
    </div>
  );
}

function aiRefreshPhaseLabel(phase: AIRefreshPhase): string {
  if (phase === "submitting") return "正在连接分析服务";
  if (phase === "preparing") return "正在整理持仓";
  if (phase === "researching") return "正在联网检索";
  if (phase === "analyzing") return "正在分析风险";
  if (phase === "persisting") return "正在校验结果";
  if (phase === "complete") return "分析完成";
  if (phase === "fallback") return "已安全降级";
  return "刷新失败";
}

function aiRefreshStepState(
  phase: AIRefreshPhase,
  index: number,
  failedStage: PortfolioRefreshJob["stage"] | null,
): "waiting" | "active" | "done" | "error" {
  const activeIndex = { submitting: 0, preparing: 1, researching: 2, analyzing: 3, persisting: 4 }[phase];
  if (activeIndex !== undefined) return index < activeIndex ? "done" : index === activeIndex ? "active" : "waiting";
  if (phase === "complete") return "done";
  const failedIndex = {
    accepted: 0,
    preparing_inputs: 1,
    researching_web: 2,
    analyzing_risks: 3,
    persisting: 4,
    ready: 4,
    fallback: 4,
    error: 4,
  }[failedStage ?? "error"];
  return index < failedIndex ? "done" : index === failedIndex ? "error" : "waiting";
}

function formatDuration(milliseconds: number): string {
  return milliseconds < 1000 ? `${milliseconds} ms` : `${(milliseconds / 1000).toFixed(1)} s`;
}

function PortfolioAnalysisShell({
  section,
  setSection,
  data,
  loading,
  reload,
  refreshAI,
  aiRefreshing,
  children,
}: {
  section: PortfolioAnalysisSectionKey;
  setSection: (section: PortfolioAnalysisSectionKey) => void;
  data: PortfolioAnalysisResponse | null;
  loading: boolean;
  reload: () => void;
  refreshAI: () => void;
  aiRefreshing: boolean;
  children: ReactNode;
}) {
  const aiPill = sectionAiPill(data, section, aiRefreshing);
  const aiBusy = aiRefreshing || aiPill.label === "AI生成中";
  return (
    <>
      <PageHeader
        eyebrow="V2 / 持仓智能"
        title="持仓分析"
        meta={
          <>
            <StatusPill tone={data?.status === "ready" ? "positive" : "neutral"}>{data ? statusLabel(data.status) : "加载中"}</StatusPill>
            <StatusPill>{data?.display_currency ?? "-"}</StatusPill>
            <StatusPill tone={aiPill.tone}>{aiPill.label}</StatusPill>
            {loading ? <StatusPill tone="neutral">正在加载</StatusPill> : null}
            <button type="button" onClick={reload} disabled={loading}>{loading ? "刷新中" : "刷新数据"}</button>
            <button type="button" onClick={refreshAI} disabled={loading || aiBusy}>{aiBusy ? aiPill.label : "刷新AI"}</button>
          </>
        }
      />

      <div className="analysis-tabs" role="tablist" aria-label="持仓分析分区">
        {tabs.map((item) => (
          <button
            key={item.key}
            type="button"
            className={section === item.key ? "active" : ""}
            onClick={() => setSection(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {loading && !data ? (
        <LoadingBlock label="正在读取持仓分析数据" />
      ) : children}
    </>
  );
}

function responseMatchesCurrentRequest(
  data: PortfolioAnalysisResponse | null,
  section: PortfolioAnalysisSectionKey,
): data is PortfolioAnalysisResponse {
  if (!data) return false;
  const responseSection = data.request?.section ?? data.active_section ?? null;
  if (responseSection && responseSection !== section) return false;
  return true;
}

function sectionAiPill(
  data: PortfolioAnalysisResponse | null,
  section: PortfolioAnalysisSectionKey,
  refreshing: boolean,
): { label: string; tone: "neutral" | "positive" | "negative" | "accent" } {
  if (refreshing) return { label: "AI刷新中", tone: "accent" };
  if (!data) return { label: "AI待加载", tone: "neutral" };
  if (section === "portfolio") {
    const meta = data.sections.portfolio.analysis_meta ?? {};
    const status = recordText(meta, "ai_overlay_status", "unavailable");
    const provider = recordText(meta, "ai_overlay_provider", "");
    const reason = recordText(meta, "ai_overlay_reason", "");
    const localFallback = provider === "local_rules" || reason.startsWith("fallback_after_");
    if (status === "ready" && !localFallback) return { label: `${aiProviderLabel(provider)} 已覆盖`, tone: "positive" };
    if (status === "pending") return { label: "AI生成中", tone: "accent" };
    return { label: aiFallbackLabel(reason), tone: "negative" };
  }
  const narrative = data.sections.market.narrative;
  if (narrative.status === "ready" && narrative.provider !== "mock") return { label: `${aiProviderLabel(narrative.provider)} 已覆盖`, tone: "positive" };
  if (narrative.status === "pending") return { label: "AI生成中", tone: "accent" };
  return { label: aiFallbackLabel(narrative.reason ?? ""), tone: narrative.provider === "mock" ? "accent" : "negative" };
}

function MarketPanel({ data }: { data: PortfolioAnalysisResponse }) {
  const market = data.sections.market;
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
          <MetricFromContract label="市场状态" metric={market.regime} />
        )}
      </div>

    </div>
  );
}

function MarketKpis({ market }: { market: PortfolioAnalysisResponse["sections"]["market"] }) {
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
  if (metric.unit === "percent") return `${value}%`;
  return value;
}

function metricNumber(metric: StandardMetric | undefined): number {
  const value = Number(metric?.value);
  return Number.isFinite(value) ? value : 0;
}

function marketTodaySummary(market: PortfolioAnalysisResponse["sections"]["market"], strategy: ApiRecord): string {
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

function PortfolioPanel({ data }: { data: PortfolioAnalysisResponse }) {
  const portfolio = data.sections.portfolio;
  const priorityAlerts = (portfolio.alerts ?? []).filter((alert) => ["high", "medium"].includes(recordText(alert, "severity", "low")));
  const chart = portfolio.charts.slice(0, 1);
  return (
    <div className="analysis-layout portfolio-workbench">
      <PortfolioRiskKpis portfolio={portfolio} />

      <div className="portfolio-workbench__top">
        <Surface title="组合风险" className="portfolio-analysis-surface">
          <RiskAlertSummary alerts={priorityAlerts} />
        </Surface>

        <Surface title="风险图表" className="portfolio-analysis-surface portfolio-chart-surface">
          <ChartGrid charts={chart} currency={data.display_currency} />
        </Surface>
      </div>

      <Surface title="持仓风险分析" className="portfolio-analysis-surface">
        <PortfolioAIStatus portfolio={portfolio} />
        <PortfolioRiskTable rows={portfolio.risk_rows ?? []} currency={data.display_currency} />
        <AnalysisMeta meta={portfolio.analysis_meta} />
      </Surface>

      <Surface title="调仓建议" className="portfolio-analysis-surface">
        <RebalanceAdvicePanel advice={portfolio.rebalance_advice} />
      </Surface>
    </div>
  );
}

function PortfolioRiskKpis({ portfolio }: { portfolio: PortfolioAnalysisResponse["sections"]["portfolio"] }) {
  const alerts = portfolio.alerts ?? [];
  const rows = portfolio.risk_rows ?? [];
  const maxWeight = rows.reduce((max, row) => Math.max(max, row.weight_pct ?? 0), 0);
  const aiReady = rows.filter((row) => row.research_status === "ready").length;
  const aiCoverage = rows.length ? aiReady / rows.length * 100 : null;
  const meta = portfolio.analysis_meta ?? {};
  const externalReady = recordBool(meta, "external_ready");
  const modelRisks = rows.flatMap((row) => row.risk_points ?? []);
  const high = externalReady
    ? modelRisks.filter((risk) => risk.severity === "high").length
    : alerts.filter((alert) => recordText(alert, "severity", "low") === "high").length;
  const medium = externalReady
    ? modelRisks.filter((risk) => risk.severity === "medium").length
    : alerts.filter((alert) => recordText(alert, "severity", "low") === "medium").length;
  const coverageLabel = rows.length && aiCoverage !== null
    ? `${aiReady}/${rows.length} (${formatNumber(aiCoverage, 0)}%) · 缺${rows.length - aiReady}`
    : "-";
  const items = [
    { icon: "alert", label: "高优先级风险", value: formatNumber(high, 0), tone: high > 0 ? "negative" : "positive" },
    { icon: "radar", label: "中优先级风险", value: formatNumber(medium, 0), tone: medium > 0 ? "accent" : "positive" },
    { icon: "target", label: "最大单票权重", value: rows.length ? `${formatNumber(maxWeight)}%` : "-", tone: maxWeight >= 25 ? "negative" : maxWeight >= 15 ? "accent" : "neutral" },
    { icon: "spark", label: "联网研究覆盖", value: coverageLabel, tone: aiCoverage && aiCoverage >= 80 ? "positive" : "neutral" },
    { icon: "database", label: "研究来源", value: externalReady ? "可追溯" : "待补充", tone: externalReady ? "positive" : "accent" },
  ];
  return (
    <div className="portfolio-kpi-strip">
      {items.map((item) => (
        <div className={`portfolio-kpi-card portfolio-kpi-card--${item.tone}`} key={item.label}>
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

function MetricFromContract({ label, metric }: { label: string; metric: StandardMetric }) {
  const value = metric.unit === "percent"
    ? `${formatNumber(metric.value)}%`
    : metric.unit && ["USD", "HKD", "CNY", "RMB", "KRW"].includes(metric.unit)
      ? formatCurrency(metric.value, metric.unit)
      : metric.value === null
        ? "-"
        : `${formatNumber(metric.value)}${metric.unit && metric.unit !== "index" && metric.unit !== "ratio" ? ` ${metric.unit}` : ""}`;
  const tone = metric.status === "ready" ? "positive" : metric.status === "error" ? "negative" : "neutral";
  return (
    <MetricCard
      label={label}
      value={typeof metric.value === "string" ? metric.value : value}
      tone={tone}
      hint={metricHint(metric)}
    />
  );
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

function RiskAlertGrid({ alerts }: { alerts: ApiRecord[] }) {
  if (!alerts.length) return null;
  return (
    <div className="risk-alert-grid">
      {alerts.map((alert, index) => (
        <article className={`risk-alert risk-alert--${recordText(alert, "severity", "low")}`} key={`${recordText(alert, "title", "alert")}-${index}`}>
          <span>{severityLabel(recordText(alert, "severity", "low"))}</span>
          <strong>{recordText(alert, "title", "风险提示")}</strong>
          <p>{recordText(alert, "detail", "")}</p>
        </article>
      ))}
    </div>
  );
}

function RiskAlertSummary({ alerts }: { alerts: ApiRecord[] }) {
  if (!alerts.length) {
    return <EmptyState compact title="暂无高/中优先级风险" detail="当前组合未触发需要优先处理的集中度或主题风险。" />;
  }
  return (
    <div className="risk-summary-list">
      {alerts.map((alert, index) => (
        <div className={`risk-summary risk-summary--${recordText(alert, "severity", "medium")}`} key={`${recordText(alert, "title", "alert")}-${index}`}>
          <span><Icon className="analysis-icon" name={recordText(alert, "severity", "medium") === "high" ? "alert" : "radar"} />{severityLabel(recordText(alert, "severity", "medium"))}</span>
          <strong>{recordText(alert, "title", "风险提示")}</strong>
          <p>{recordText(alert, "detail", "")}</p>
        </div>
      ))}
    </div>
  );
}

function PortfolioRiskTable({ rows, currency }: { rows: PortfolioRiskRow[]; currency: string }) {
  if (!rows.length) return <EmptyState compact title="暂无持仓风险分析" detail="导入最新持仓快照后显示逐项风险校验。" />;
  return (
    <div className="portfolio-risk-table-wrap">
      <table className="portfolio-risk-table">
        <thead>
          <tr>
            <th>标的</th>
            <th>当前价</th>
            <th>权重</th>
            <th>浮盈亏</th>
            <th>逻辑状态</th>
            <th>风险与跟踪</th>
            <th>建议与来源</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.position_key}>
              <td><strong>{row.symbol}</strong></td>
              <td>{formatCurrency(row.current_price, currency)}</td>
              <td>{formatNumber(row.weight_pct)}%</td>
              <td className={deltaClass(row.unrealized_pnl)}>
                {formatCurrency(row.unrealized_pnl, currency)}
              </td>
              <td>
                <div className="risk-cell-with-evidence">
                  <span>{row.logic_status}</span>
                  <small>{row.research_status === "ready" ? "已完成联网研究" : "当前为本地规则结果"}</small>
                </div>
              </td>
              <td>
                <details className="portfolio-risk-details">
                  <summary>{row.risk_points.length} 项风险</summary>
                  <ul>
                    {row.risk_points.map((risk, index) => (
                      <li key={`${risk.title}-${index}`}>
                        <strong className={`risk-severity risk-severity--${risk.severity}`}>{risk.title}</strong>
                        <span>{risk.detail}</span>
                        {risk.evidence_ids.length ? <small>依据：{risk.evidence_ids.join("、")}</small> : null}
                      </li>
                    ))}
                  </ul>
                </details>
                <details className="portfolio-risk-details">
                  <summary>{row.tracking_points.length} 项跟踪</summary>
                  <ul>
                    {row.tracking_points.map((item, index) => (
                      <li key={`${item.item}-${index}`}>
                        <strong>{item.item} · {trackingHorizonLabel(item.horizon)}</strong>
                        <span>{item.why}</span>
                        <small>重评条件：{item.trigger}</small>
                      </li>
                    ))}
                  </ul>
                </details>
              </td>
              <td>
                <div className="risk-cell-with-evidence">
                  <span>{row.recommendation}</span>
                  {row.sources.length ? (
                    <details className="portfolio-risk-details portfolio-risk-sources">
                      <summary>{row.sources.length} 个研究来源</summary>
                      <ul>
                        {row.sources.map((source) => (
                          <li key={source.id}>
                            <a href={source.url} target="_blank" rel="noreferrer">{source.id} · {source.title}</a>
                            {source.published_at ? <small>{source.published_at}</small> : null}
                          </li>
                        ))}
                      </ul>
                    </details>
                  ) : null}
                  <small title={row.reason ?? undefined}>
                    {sourceLabel(row.source)} · 置信度 {Math.round((row.confidence ?? 0) * 100)}%
                  </small>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PortfolioAIStatus({ portfolio }: { portfolio: PortfolioAnalysisResponse["sections"]["portfolio"] }) {
  const meta = portfolio.analysis_meta ?? {};
  const rowSources = (portfolio.risk_rows ?? []).map((row) => row.source).join("+");
  const adviceSource = portfolio.rebalance_advice?.source ?? "";
  const combinedSource = `${rowSources}+${adviceSource}`;
  const inferredReady = isModelAiSource(combinedSource);
  const inferredRules = combinedSource.includes("portfolio_ai_rules");
  const status = recordText(meta, "ai_overlay_status", inferredReady ? "ready" : inferredRules ? "unavailable" : "missing_data");
  const provider = recordText(meta, "ai_overlay_provider", providerFromSource(combinedSource));
  const reason = recordText(meta, "ai_overlay_reason", inferredRules ? "structured_ai_overlay_missing_from_response" : "");
  const localFallback = provider === "local_rules" || reason.startsWith("fallback_after_");
  const title = status === "ready" && !localFallback
    ? `AI判断：${aiProviderLabel(provider)} 结构化输出已应用`
    : status === "pending"
      ? `AI判断：${aiProviderLabel(provider)} 正在生成，当前临时显示本地规则校验`
      : `AI判断：${aiFallbackLabel(reason)}，当前显示本地规则校验`;
  const detail = status === "ready" && !localFallback
    ? "表格中的逻辑状态、风险、跟踪项、来源和组合建议来自 DeepSeek 联网研究；价格、权重和盈亏仍来自 IBKR。"
    : status === "pending"
      ? "页面会自动刷新结果。模型没有返回前，规则结果只作为临时占位。"
      : "这不是联网模型结论。DeepSeek 或 Tavily 不可用时，页面继续显示本地只读规则结果。";
  return (
    <div className={`portfolio-ai-status portfolio-ai-status--${status === "ready" && !localFallback ? "ready" : status === "pending" ? "pending" : "fallback"}`}>
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function RebalanceAdvicePanel({ advice }: { advice: PortfolioAnalysisResponse["sections"]["portfolio"]["rebalance_advice"] }) {
  if (!advice || advice.status !== "ready") {
    return <EmptyState compact title="暂无调仓建议" detail="缺少持仓或外部研究上下文时不生成建议。" />;
  }
  const cards = advice.cards ?? [];
  return (
    <div className="rebalance-advice">
      <div className="rebalance-card-grid">
        {cards.slice(0, 4).map((card, index) => (
          <article className="rebalance-card" key={card.rank}>
            <div className="rebalance-card__top">
              <span>{normalizeAdviceRank(card.rank, index)}</span>
              <Icon className="analysis-icon" name={card.icon || adviceIconForIndex(index)} />
            </div>
            <strong>{card.title || adviceTitleForIndex(index)}</strong>
            <p>{card.body || "-"}</p>
          </article>
        ))}
      </div>
      <div className="rebalance-command-strip">
        <Icon className="analysis-icon" name="check" />
        <strong>{advice.action_today || "先核实证据，不生成交易动作。"}</strong>
        {advice.thinking_prompt ? <span>{advice.thinking_prompt}</span> : null}
      </div>
      <p className="rebalance-footer-line">
        只读分析 · 不包含下单动作 · {sourceLabel(advice.source)} · 置信度 {Math.round((advice.confidence ?? 0) * 100)}%{advice.as_of ? ` · ${advice.as_of}` : ""}
      </p>
    </div>
  );
}

function normalizeAdviceRank(rank: string, index: number): string {
  const digits = rank.replace(/\D/g, "");
  return digits ? digits.padStart(2, "0").slice(-2) : `0${index + 1}`;
}

function adviceIconForIndex(index: number): string {
  return ["alert", "search", "compass", "calendar"][index] ?? "check";
}

function adviceTitleForIndex(index: number): string {
  return ["组合首要风险", "优先复核持仓", "组合结构与集中度", "未来30天跟踪清单"][index] ?? "建议";
}

function trackingHorizonLabel(horizon: "7d" | "30d" | "quarterly"): string {
  return { "7d": "7天", "30d": "30天", quarterly: "季度" }[horizon];
}

function AnalysisMeta({ meta }: { meta: ApiRecord }) {
  if (!Object.keys(meta).length) return null;
  const missing = Array.isArray(meta.missing_reasons) ? meta.missing_reasons.map(String) : [];
  const overlayStatus = recordText(meta, "ai_overlay_status", "unavailable");
  const overlayProvider = recordText(meta, "ai_overlay_provider", "");
  const overlayReason = recordText(meta, "ai_overlay_reason", "");
  const localFallback = overlayProvider === "local_rules" || overlayReason.startsWith("fallback_after_");
  return (
    <div className={`analysis-meta-line analysis-meta-line--${overlayStatus === "ready" && !localFallback ? "ai-ready" : "ai-fallback"}`}>
      <span>
        {overlayStatus === "ready" && !localFallback
          ? `AI判断：${aiProviderLabel(overlayProvider)} 结构化输出`
          : `AI判断：${aiFallbackLabel(overlayReason)}，当前显示本地规则校验`}
      </span>
      <span>
        {sourceLabel(recordText(meta, "source", "portfolio_positions"))}
        {" · "}
        置信度 {Math.round((recordNumber(meta, "confidence") ?? 0) * 100)}%
        {recordBool(meta, "external_ready") ? " · 联网来源可追溯" : " · 联网研究来源不足"}
        {missing.length ? ` · 缺口：${missing.slice(0, 2).join("、")}` : ""}
      </span>
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

function ChartGrid({ charts, currency }: { charts: EChartsPayload[]; currency: string }) {
  if (!charts.length) return <EmptyState compact title="暂无图表" detail="当前分析没有返回可视化 payload。" />;
  return (
    <div className="analysis-chart-grid">
      {charts.map((chart, index) => (
        <div className="analysis-chart-card" key={`${chart.title}-${index}`}>
          {chart.options.description ? <p>{String(chart.options.description)}</p> : null}
          {chart.status === "ready" && chart.series.length ? (
            <EChart option={buildPortfolioAnalysisChartOption(chart, currency)} height={chart.chart_type === "gauge" ? 240 : 300} />
          ) : (
            <EmptyState compact title={chart.title} detail={`${statusLabel(chart.status)} · ${sourceLabel(chart.source)}`} />
          )}
        </div>
      ))}
    </div>
  );
}

function severityLabel(severity: string): string {
  const labels: Record<string, string> = {
    high: "高优先级",
    medium: "中优先级",
    low: "观察",
  };
  return labels[severity] ?? "观察";
}

function metricLabel(key: string): string {
  const labels: Record<string, string> = {
    rsi: "相对强弱指数",
    ndx_rsi: "纳指100强弱",
    fear_greed: "恐惧贪婪",
    vix: "波动率指数",
    vix_change: "波动率日变化",
    portfolio_weighted_change: "组合日变化",
    breadth: "上涨广度",
    iv_percentile: "隐波分位",
    put_call_ratio: "拥挤度代理",
    news_heat: "新闻/主题热度",
    volume_anomaly: "成交量异常",
    sector: "行业集中度",
    single_name: "单票集中度",
    ai_theme: "智能主题",
    growth: "成长因子",
    ai_beta: "智能主题弹性",
    semiconductor: "半导体",
    cyclical: "周期/高波动",
    top3_weight: "前三大权重",
    theme_cluster: "主题簇相关",
    daily_loss_at_risk: "单日损失估算",
    downside_breadth: "下跌广度",
    rates: "利率敏感",
    liquidity: "流动性敏感",
    ai_capex: "智能资本开支敏感",
    last_price: "最新价",
    portfolio_weight: "组合权重",
    daily_change: "日变化",
    unrealized_pnl: "未实现盈亏",
    trend_score: "趋势分",
    sentiment: "情绪",
  };
  return labels[key] ?? key;
}

function metricHint(metric: StandardMetric): string {
  return `${statusLabel(metric.status)} · 数据来源：${sourceLabel(metric.source)}`;
}

function sourceLabel(source: string): string {
  if (!source) return "未注明";
  if (source.includes("local_rules_structured_ai") || source.includes("portfolio_ai_rules")) return "本地规则校验";
  if (source.includes("mock_structured_ai") || source.includes("mock")) return "本地模拟";
  if (isModelAiSource(source)) return "结构化模型分析";
  if (source.includes("portfolio_positions")) return "当前持仓快照";
  if (source.includes("portfolio_theme_proxy")) return "持仓主题代理";
  if (source.includes("market_data_provider")) return "行情数据提供方";
  if (source.includes("cnn_fear_greed")) return "CNN Fear & Greed";
  if (source.includes("longbridge_market_temp")) return "长桥市场温度";
  if (source.includes("longbridge_topic")) return "长桥社区";
  if (source.includes("futu_market_heat")) return "富途市场热度";
  if (source.includes("futu_watchlist_heat")) return "富途关注热度";
  if (source.includes("market_crowding_proxy")) return "本地拥挤度代理";
  if (source.includes("futu")) return "富途行情";
  if (source.includes("longbridge")) return "长桥行情";
  if (source.includes("yahoo")) return "雅虎行情";
  if (source.includes("finnhub")) return "Finnhub 行情";
  if (source.includes("nasdaq")) return "纳斯达克行情";
  if (source.includes("openai")) return "OpenAI";
  if (source.includes("minimax")) return "MiniMax";
  if (source.includes("deepseek")) return "DeepSeek";
  return "本地规则";
}

function isModelAiSource(source: string): boolean {
  return source.includes("openai_structured_ai") || source.includes("minimax_structured_ai") || source.includes("deepseek_structured_ai");
}

function aiProviderLabel(provider: string): string {
  if (provider === "openai") return "OpenAI";
  if (provider === "minimax") return "MiniMax";
  if (provider === "deepseek") return "DeepSeek";
  if (provider === "mock") return "Mock AI";
  if (provider === "local_rules") return "本地规则";
  return provider || "模型";
}

function aiFallbackLabel(reason: string): string {
  if (!reason) return "未返回结构化模型结果";
  if (reason.includes("429") || reason.toLowerCase().includes("too many requests")) return "模型或搜索服务限流/额度不足";
  if (reason.includes("tavily")) return "Tavily 联网搜索未配置或不可用";
  if (reason.includes("search_unauthorized")) return "Tavily 密钥无效或无权限";
  if (reason.includes("search_rate_limited")) return "Tavily 搜索限流或额度不足";
  if (reason.includes("search_timed_out")) return "Tavily 搜索超时";
  if (reason.includes("search_partial")) return "部分持仓未取得可靠联网来源";
  if (reason.includes("deepseek_unauthorized")) return "DeepSeek 密钥无效或无权限";
  if (reason.includes("deepseek_insufficient_balance")) return "DeepSeek 余额不足";
  if (reason.includes("deepseek_rate_limited")) return "DeepSeek 请求限流";
  if (reason.includes("deepseek_tool_choice_unsupported")) return "当前 DeepSeek 模型不支持强制工具调用";
  if (reason.includes("deepseek_context_length_exceeded")) return "DeepSeek 上下文长度超限";
  if (reason.includes("deepseek_model_not_found")) return "DeepSeek 模型不可用";
  if (reason.includes("deepseek_invalid_request")) return "DeepSeek 请求参数不兼容";
  if (reason.includes("deepseek_service_unavailable")) return "DeepSeek 服务暂时不可用";
  if (reason.includes("deepseek_portfolio_output_truncated")) return "DeepSeek 输出过长并被截断";
  if (reason.includes("deepseek_portfolio_invalid_response")) return "DeepSeek 返回内容不是有效结构化结果";
  if (reason.includes("refresh_job_deadline_exceeded")) return "分析超过服务端总时限";
  if (reason.includes("total_budget_exceeded")) return "分析超过服务端总时限";
  if (reason.includes("portfolio_overlay_persistence_failed")) return "分析结果保存失败";
  if (reason.includes("api_key_not_configured")) return "模型密钥未配置";
  if (reason.includes("web_research_incomplete")) return "部分持仓未取得可靠联网来源";
  if (reason.includes("invalid_portfolio_overlay")) return "模型结果未通过全量字段校验";
  if (reason.includes("timed_out")) return "模型调用超时";
  if (reason.includes("manual_refresh")) return "等待手动刷新AI";
  if (reason.includes("failed")) return "模型生成失败";
  if (reason.includes("does_not_support")) return "当前 provider 不支持结构化输出";
  if (reason.includes("missing_from_response")) return "后端未返回结构化AI状态";
  if (reason.includes("in_progress")) return "模型仍在生成";
  return reason;
}

function providerFromSource(source: string): string {
  if (source.includes("openai")) return "openai";
  if (source.includes("minimax")) return "minimax";
  if (source.includes("deepseek")) return "deepseek";
  if (source.includes("mock")) return "mock";
  return "";
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    ready: "已就绪",
    pending: "生成中",
    missing_data: "缺数据",
    stale: "需更新",
    unavailable: "不可用",
    error: "错误",
  };
  return labels[status] ?? status;
}
