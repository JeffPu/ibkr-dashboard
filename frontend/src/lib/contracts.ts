export type ApiRecord = Record<string, unknown>;

export type PageKey =
  | "overview"
  | "positions"
  | "performance"
  | "trades"
  | "settings"
  | "portfolioAnalysis";

export type QueryValue = string | number | boolean | null | undefined;

export type RequestQuery = Record<string, QueryValue>;

export interface ListResponse {
  filters?: ApiRecord;
  display_currency?: string;
  valuation_mode?: string;
  items?: ApiRecord[];
  total?: number;
  summary?: ApiRecord;
  monthly_stats?: ApiRecord[];
}

export type PositionRecord = ApiRecord & {
  asset_category?: string;
  symbol?: string;
  underlying_symbol?: string;
  report_date?: string;
  quantity?: number;
  market_value_snapshot?: number;
};

export type OptionPositionRecord = PositionRecord & {
  contract_key: string;
  contract_title: string;
  raw_contract_code: string;
  expiry?: string;
  strike?: string;
  put_call?: string;
  multiplier?: number;
  days_to_expiry: number | null;
  expiry_status: "expired" | "within_1" | "within_7" | "within_30" | "later" | "incomplete";
  expiry_risk: "expired" | "urgent" | "warning" | "watch" | "none";
  contract_data_status: "complete" | "incomplete";
  is_short: boolean;
  quote_source?: "snapshot";
};

export interface OptionPositionSummary {
  option_net_market_value: number;
  option_unrealized_pnl: number;
  expiring_30_contracts: number;
  expiring_30_short_contracts: number;
}

export interface PositionsResponse extends Omit<ListResponse, "items" | "summary"> {
  items?: PositionRecord[] | OptionPositionRecord[];
  summary?: OptionPositionSummary;
  snapshot_date?: string | null;
  is_stale?: boolean;
  account_base_currency?: string;
  currency_conversion?: ApiRecord;
  source?: "ibkr_flex_xml";
  updated_at?: string | null;
  missing_reason?: string | null;
}

export type TradeRecord = ApiRecord & {
  asset_category?: string;
  symbol?: string;
  underlying_symbol?: string;
  contract_key?: string;
  contract_title?: string;
  raw_contract_code?: string;
  expiry?: string;
  strike?: string;
  put_call?: string;
  multiplier?: number;
  open_close_indicator?: string;
  transaction_type?: string;
  notes?: string;
};

export interface TradesResponse extends Omit<ListResponse, "items"> {
  items?: TradeRecord[];
  account_base_currency?: string;
  currency_conversion?: ApiRecord;
  source?: "ibkr_flex_xml";
  updated_at?: string | null;
  missing_reason?: string | null;
}

export type AnalysisStatus =
  | "ready"
  | "pending"
  | "missing_data"
  | "stale"
  | "unavailable"
  | "error";

export type PortfolioAnalysisSectionKey = "market" | "portfolio" | "stock";

export interface StandardMetric {
  value: string | number | boolean | null;
  unit: string | null;
  source: string;
  as_of: string | null;
  confidence: number | null;
  status: AnalysisStatus;
  reason: string | null;
}

export interface EChartsSeries {
  name: string;
  points: Array<Record<string, string | number | null>>;
}

export interface EChartsPayload {
  chart_type: string;
  title: string;
  unit: string | null;
  status: AnalysisStatus;
  source: string;
  as_of: string | null;
  series: EChartsSeries[];
  options: ApiRecord;
}

export interface AINarrativePayload {
  provider: string;
  model: string | null;
  status: AnalysisStatus;
  summary: string | null;
  bullets: string[];
  risks: string[];
  source_metrics: string[];
  as_of: string | null;
  confidence: number | null;
  reason: string | null;
}

export interface TelegramStatusPayload {
  enabled: boolean;
  status: AnalysisStatus;
  allowlisted_chat_ids_count: number;
  schedule: string | null;
  last_delivery_at: string | null;
  source: string;
  as_of: string | null;
  reason: string | null;
}

export interface MCPToolPayload {
  tool: string;
  status: AnalysisStatus;
  data: ApiRecord;
  generated_at: string | null;
  warnings: string[];
}

export interface MarketAnalysisSection {
  status: AnalysisStatus;
  regime: StandardMetric;
  indicators: Record<string, StandardMetric>;
  market_pulse: ApiRecord[];
  playbook: ApiRecord[];
  strategy: ApiRecord[];
  portfolio_impact: string[];
  watch_symbols: string[];
  opportunities: string[];
  reasons: string[];
  risks: string[];
  charts: EChartsPayload[];
  narrative: AINarrativePayload;
}

export interface PortfolioRiskRow {
  position_key: string;
  symbol: string;
  current_price: number | null;
  weight_pct: number;
  unrealized_pnl: number | null;
  logic_status: string;
  recommendation: string;
  risk_points: Array<{
    severity: "high" | "medium" | "low";
    title: string;
    detail: string;
    evidence_ids: string[];
  }>;
  tracking_points: Array<{
    item: string;
    why: string;
    trigger: string;
    horizon: "7d" | "30d" | "quarterly";
    evidence_ids: string[];
  }>;
  sources: Array<{
    id: string;
    title: string;
    url: string;
    published_at: string | null;
    source_type: "filing" | "company_ir" | "exchange" | "financial_media" | "other";
  }>;
  research_status: "ready" | "missing";
  status: AnalysisStatus;
  confidence: number | null;
  source: string;
  as_of: string | null;
  reason: string | null;
}

export interface PortfolioRebalanceAdvice {
  cards: Array<{
    rank: "01" | "02" | "03" | "04";
    icon: "alert" | "search" | "compass" | "calendar" | "check";
    title: string;
    body: string;
  }>;
  action_today: string | null;
  thinking_prompt: string | null;
  status: AnalysisStatus;
  source: string;
  as_of: string | null;
  confidence: number | null;
  reason: string | null;
}

export interface PortfolioRefreshJob {
  job_id: string;
  status: "accepted" | "running" | "ready" | "fallback" | "error";
  stage: "accepted" | "preparing_inputs" | "researching_web" | "analyzing_risks" | "persisting" | "ready" | "fallback" | "error";
  section: PortfolioAnalysisSectionKey;
  symbol: string | null;
  message: string;
  started_at: string;
  updated_at: string;
  completed_positions: number;
  total_positions: number;
  stage_durations_ms: Record<string, number>;
  reason: string | null;
  failed_stage: PortfolioRefreshJob["stage"] | null;
}

export interface PortfolioRiskSection {
  status: AnalysisStatus;
  concentration: Record<string, StandardMetric>;
  factor_exposure: Record<string, StandardMetric>;
  correlation: Record<string, StandardMetric>;
  tail_risk: Record<string, StandardMetric>;
  macro_sensitivity: Record<string, StandardMetric>;
  alerts: ApiRecord[];
  hedge_suggestions: string[];
  greeks: Record<string, StandardMetric>;
  expiration_risk: Record<string, StandardMetric>;
  advisor_facts: StandardMetric[];
  risk_rows: PortfolioRiskRow[];
  rebalance_advice: PortfolioRebalanceAdvice;
  analysis_meta: ApiRecord;
  charts: EChartsPayload[];
  narrative: AINarrativePayload;
}

export interface StockSelectionOption {
  symbol: string;
  label: string;
  weight_pct: number;
  market_value: number | null;
  quantity: number | null;
  source: string;
}

export interface StockResearchMemo {
  status: AnalysisStatus;
  symbol: string | null;
  one_line_view: string | null;
  position_role: string | null;
  logic_status: string | null;
  ai_relevance: string | null;
  holding_thesis: string[];
  facts: string[];
  inferences: string[];
  portfolio_impact: string[];
  key_risks: string[];
  tracking_questions: string[];
  invalidation_signals: string[];
  read_only_suggestion: string | null;
  source: string;
  as_of: string | null;
  confidence: number | null;
  reason: string | null;
}

export interface StockAnalysisSection {
  status: AnalysisStatus;
  symbol: string | null;
  available_symbols: StockSelectionOption[];
  memo: StockResearchMemo;
  profile: Record<string, StandardMetric>;
  indicators: Record<string, StandardMetric>;
  direction: string | null;
  core_changes: string[];
  portfolio_impact: string[];
  beneficiaries: string[];
  market_mispricing: string[];
  watch_signals: string[];
  evidence_links: Array<{ label: string; url: string }>;
  risks: string[];
  charts: EChartsPayload[];
  narrative: AINarrativePayload;
}

export interface PortfolioAnalysisResponse {
  status: AnalysisStatus;
  active_section: PortfolioAnalysisSectionKey | null;
  generated_at: string | null;
  display_currency: string;
  valuation_mode: string;
  request: {
    section: PortfolioAnalysisSectionKey | null;
    symbol: string | null;
  };
  sections: {
    market: MarketAnalysisSection;
    portfolio: PortfolioRiskSection;
    stock: StockAnalysisSection;
  };
  integrations: {
    ai: AINarrativePayload;
    telegram: TelegramStatusPayload;
    mcp_tools: MCPToolPayload[];
  };
  links: Record<string, string>;
}

export type OverviewStatus = "ready" | "missing_data" | "stale" | "partial";

export type OverviewBenchmarkStatus = "ready" | "pending" | "unavailable";

export type OverviewRiskSeverity = "healthy" | "watch" | "caution" | "alert";

export type OverviewRiskMetricKey =
  | "margin_usage"
  | "largest_holding"
  | "top3_concentration"
  | "downside_breadth";

export type OverviewRiskBenchmarkKey = "qqq" | "nasdaq" | "sp500";

export type OverviewRiskWindow = 20 | 60 | 120;

export type OverviewRiskWarningStatus = "ready" | "calculating" | "partial" | "missing_data";

export interface OverviewRiskMetric {
  key: OverviewRiskMetricKey;
  label: string;
  value: number | null;
  unit: "percent";
  status: "ready" | "missing_data";
  severity: OverviewRiskSeverity;
  threshold_label: string;
  progress_pct: number | null;
  source: string;
  reason: string;
  action: string;
}

export interface OverviewRiskDashboard {
  status: "ready" | "missing_data" | "partial";
  highest_severity: OverviewRiskSeverity;
  updated_at: string | null;
  metrics: OverviewRiskMetric[];
}

export interface OverviewBenchmarkBeta {
  key: OverviewRiskBenchmarkKey;
  label: string;
  symbol: string;
  status: OverviewRiskWarningStatus;
  portfolio_beta: number | null;
  source?: string;
  reason?: string | null;
  updated_at?: string | null;
  valid_positions?: number;
  missing_positions?: number;
  coverage_pct?: number;
}

export interface OverviewPositionBetaValue {
  value: number | null;
  weighted_contribution?: number | null;
  observations?: number;
  status?: "ready" | "missing_data";
  reason?: string | null;
}

export interface OverviewPositionBeta {
  symbol: string;
  name?: string | null;
  weight_pct: number | null;
  market_value: number | null;
  beta: number | OverviewPositionBetaValue | null;
  benchmark_key?: OverviewRiskBenchmarkKey;
  betas?: Partial<Record<OverviewRiskBenchmarkKey, OverviewPositionBetaValue | number | null>>;
  status: "ready" | "missing_data";
  source: string;
  reason: string | null;
}

export interface OverviewRiskWarningResponse {
  status: OverviewRiskWarningStatus;
  selected_benchmark: OverviewRiskBenchmarkKey;
  window: OverviewRiskWindow;
  total_market_value: number;
  equity: number;
  beta_updated_at: string | null;
  benchmarks: OverviewBenchmarkBeta[];
  positions: OverviewPositionBeta[];
  sources: string[];
  missing_reasons: string[];
}

export interface OverviewConcentrationPreview {
  status: "ready" | "missing_data";
  positions_count: number;
  top_holding_symbol: string | null;
  top_holding_weight_pct: number | null;
  top5_weight_pct: number | null;
  label: string;
}

export interface OverviewUiSummary {
  status: OverviewStatus;
  status_label: string;
  valuation_mode: "snapshot" | "realtime";
  valuation_label: string;
  valuation_as_of: string | null;
  valuation_as_of_local: string | null;
  report_date_iso: string | null;
  last_successful_sync_at: string | null;
  last_successful_sync_at_local: string | null;
  data_source_label: string;
  quote_source_label: string;
  positions_count: number;
  benchmark_status: OverviewBenchmarkStatus;
  warnings: string[];
  reasons: string[];
  concentration_preview: OverviewConcentrationPreview;
}

export interface OverviewResponse extends ApiRecord {
  report_date?: string | null;
  report_date_iso?: string | null;
  valuation_as_of?: string | null;
  valuation_as_of_local?: string | null;
  valuation_date_iso?: string | null;
  valuation_mode?: string;
  display_currency?: string;
  equity?: number;
  cash?: number;
  market_value?: number;
  daily_change?: number;
  daily_return?: number | null;
  realized_pnl?: number;
  unrealized_pnl?: number;
  total_pnl?: number;
  twr_ytd?: number | null;
  mwrr_ytd?: number | null;
  mwrr_all_time?: number | null;
  dividends?: number;
  interest?: number;
  commissions?: number;
  positions_count?: number;
  top_holdings?: ApiRecord[];
  equity_curve?: ApiRecord[];
  asset_flow_events?: ApiRecord[];
  benchmark_series?: ApiRecord[];
  asset_metric_rows?: ApiRecord[];
  recent_trades?: ApiRecord[];
  ai_summary?: ApiRecord;
  net_value_curve?: ApiRecord;
  ui_summary?: OverviewUiSummary;
  risk_dashboard?: OverviewRiskDashboard;
  option_expiration_alerts?: {
    items: Array<{
      contract_key: string;
      contract_title: string;
      raw_contract_code: string;
      days_to_expiry: number;
      expiry_status: string;
      expiry_risk: string;
      is_short: boolean;
      snapshot_date: string | null;
      is_stale: boolean;
    }>;
    total: number;
    remaining_count: number;
    snapshot_date: string | null;
    is_stale: boolean;
    source: "ibkr_flex_xml";
    updated_at: string | null;
    missing_reason: string | null;
  };
}

export type AiProvider = "openai" | "minimax" | "deepseek" | "mock";

export type FutuConnectionMode = "disabled" | "local_opend" | "longbridge";

export interface AiModelOption {
  value: string;
  label: string;
}

export interface AiModelProviderCatalog {
  provider: AiProvider;
  default_model: string;
  models: AiModelOption[];
}

export interface AiModelCatalogResponse {
  providers: AiModelProviderCatalog[];
}

export interface SettingsResponse {
  base_currency: string;
  timezone: string;
  finnhub_api_key: string;
  flex_token: string;
  flex_query_id: string;
  pull_frequency_minutes: number;
  display_realtime_prices: boolean;
  ai_provider: AiProvider;
  ai_model: string;
  openai_api_key: string;
  minimax_api_key: string;
  minimax_base_url: string;
  deepseek_api_key: string;
  deepseek_base_url: string;
  tavily_api_key: string;
  futu_connection_mode: FutuConnectionMode;
  futu_opend_host: string;
  futu_opend_port: number;
  telegram_bot_token: string;
  telegram_allowlisted_chat_ids: string[];
  telegram_reports_enabled: boolean;
  telegram_daily_report_time: string;
  mcp_server_enabled: boolean;
  report_cache_enabled: boolean;
  report_cache_ttl_minutes: number;
  last_successful_sync_at: string | null;
  last_successful_sync_date: string | null;
  last_successful_sync_at_local?: string | null;
}

export type SettingsUpdatePayload = Partial<SettingsResponse>;

export interface ImportContentFile {
  filename: string;
  content: string;
}

export interface ImportTaskResponse {
  task_id: string;
  task_url: string;
  run_url: string;
  accepted_files?: number;
  status?: string;
  files?: string[];
  summaries?: ApiRecord[];
  errors?: string[];
  total_files?: number;
  processed_files?: number;
  progress?: number;
}

export interface PageState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

export interface NavItem {
  key: PageKey;
  label: string;
  detail: string;
  icon?: string;
}
