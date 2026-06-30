from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel
from pydantic import Field


class AnalysisStatus(str, Enum):
    READY = "ready"
    PENDING = "pending"
    MISSING_DATA = "missing_data"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class PortfolioAnalysisSectionKey(str, Enum):
    MARKET = "market"
    PORTFOLIO = "portfolio"
    STOCK = "stock"


class StandardMetric(BaseModel):
    value: float | int | str | bool | None = None
    unit: str | None = None
    source: str
    as_of: str | None = None
    confidence: float | None = None
    status: AnalysisStatus
    reason: str | None = None


class EChartsSeries(BaseModel):
    name: str
    points: list[dict[str, float | int | str | None]] = Field(default_factory=list)


class EChartsPayload(BaseModel):
    chart_type: str
    title: str
    unit: str | None = None
    status: AnalysisStatus
    source: str
    as_of: str | None = None
    series: list[EChartsSeries] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)


class AINarrativePayload(BaseModel):
    provider: str
    model: str | None = None
    status: AnalysisStatus
    summary: str | None = None
    bullets: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    source_metrics: list[str] = Field(default_factory=list)
    as_of: str | None = None
    confidence: float | None = None
    reason: str | None = None


class TelegramStatusPayload(BaseModel):
    enabled: bool
    status: AnalysisStatus
    allowlisted_chat_ids_count: int
    schedule: str | None = None
    last_delivery_at: str | None = None
    source: str
    as_of: str | None = None
    reason: str | None = None


class MCPToolPayload(BaseModel):
    tool: str
    status: AnalysisStatus
    data: dict[str, Any] = Field(default_factory=dict)
    generated_at: str | None = None
    warnings: list[str] = Field(default_factory=list)


class PortfolioRiskPoint(BaseModel):
    severity: Literal["high", "medium", "low"]
    title: str
    detail: str
    evidence_ids: list[str] = Field(default_factory=list)


class PortfolioTrackingPoint(BaseModel):
    item: str
    why: str
    trigger: str
    horizon: Literal["7d", "30d", "quarterly"]
    evidence_ids: list[str] = Field(default_factory=list)


class PortfolioResearchSource(BaseModel):
    id: str
    title: str
    url: str
    published_at: str | None = None
    source_type: Literal["filing", "company_ir", "exchange", "financial_media", "other"] = "other"


class PortfolioAdviceCard(BaseModel):
    rank: Literal["01", "02", "03", "04"]
    icon: Literal["alert", "search", "compass", "calendar", "check"]
    title: str
    body: str


class PortfolioRiskRow(BaseModel):
    position_key: str
    symbol: str
    current_price: float | None = None
    weight_pct: float
    unrealized_pnl: float | None = None
    logic_status: str
    recommendation: str
    risk_points: list[PortfolioRiskPoint] = Field(default_factory=list)
    tracking_points: list[PortfolioTrackingPoint] = Field(default_factory=list)
    sources: list[PortfolioResearchSource] = Field(default_factory=list)
    research_status: Literal["ready", "missing"] = "missing"
    status: AnalysisStatus
    confidence: float | None = None
    source: str
    as_of: str | None = None
    reason: str | None = None


class PortfolioRebalanceAdvice(BaseModel):
    cards: list[PortfolioAdviceCard] = Field(default_factory=list)
    action_today: str | None = None
    thinking_prompt: str | None = None
    status: AnalysisStatus
    source: str
    as_of: str | None = None
    confidence: float | None = None
    reason: str | None = None


class StockSelectionOption(BaseModel):
    symbol: str
    label: str
    weight_pct: float
    market_value: float | None = None
    quantity: float | None = None
    source: str = "portfolio_positions"


class StockResearchMemo(BaseModel):
    status: AnalysisStatus
    symbol: str | None = None
    one_line_view: str | None = None
    position_role: str | None = None
    logic_status: str | None = None
    ai_relevance: str | None = None
    holding_thesis: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    portfolio_impact: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    tracking_questions: list[str] = Field(default_factory=list)
    invalidation_signals: list[str] = Field(default_factory=list)
    read_only_suggestion: str | None = None
    source: str
    as_of: str | None = None
    confidence: float | None = None
    reason: str | None = None


class MarketAnalysisSection(BaseModel):
    status: AnalysisStatus
    regime: StandardMetric
    indicators: dict[str, StandardMetric] = Field(default_factory=dict)
    market_pulse: list[dict[str, Any]] = Field(default_factory=list)
    playbook: list[dict[str, Any]] = Field(default_factory=list)
    strategy: list[dict[str, Any]] = Field(default_factory=list)
    portfolio_impact: list[str] = Field(default_factory=list)
    watch_symbols: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    charts: list[EChartsPayload] = Field(default_factory=list)
    narrative: AINarrativePayload


class PortfolioRiskSection(BaseModel):
    status: AnalysisStatus
    concentration: dict[str, StandardMetric] = Field(default_factory=dict)
    factor_exposure: dict[str, StandardMetric] = Field(default_factory=dict)
    correlation: dict[str, StandardMetric] = Field(default_factory=dict)
    tail_risk: dict[str, StandardMetric] = Field(default_factory=dict)
    macro_sensitivity: dict[str, StandardMetric] = Field(default_factory=dict)
    alerts: list[dict[str, Any]] = Field(default_factory=list)
    hedge_suggestions: list[str] = Field(default_factory=list)
    greeks: dict[str, StandardMetric] = Field(default_factory=dict)
    expiration_risk: dict[str, StandardMetric] = Field(default_factory=dict)
    advisor_facts: list[StandardMetric] = Field(default_factory=list)
    risk_rows: list[PortfolioRiskRow] = Field(default_factory=list)
    rebalance_advice: PortfolioRebalanceAdvice
    analysis_meta: dict[str, Any] = Field(default_factory=dict)
    charts: list[EChartsPayload] = Field(default_factory=list)
    narrative: AINarrativePayload


class StockAnalysisSection(BaseModel):
    status: AnalysisStatus
    symbol: str | None = None
    available_symbols: list[StockSelectionOption] = Field(default_factory=list)
    memo: StockResearchMemo
    profile: dict[str, StandardMetric] = Field(default_factory=dict)
    indicators: dict[str, StandardMetric] = Field(default_factory=dict)
    direction: str | None = None
    core_changes: list[str] = Field(default_factory=list)
    portfolio_impact: list[str] = Field(default_factory=list)
    beneficiaries: list[str] = Field(default_factory=list)
    market_mispricing: list[str] = Field(default_factory=list)
    watch_signals: list[str] = Field(default_factory=list)
    evidence_links: list[dict[str, str]] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    charts: list[EChartsPayload] = Field(default_factory=list)
    narrative: AINarrativePayload


class PortfolioAnalysisSections(BaseModel):
    market: MarketAnalysisSection
    portfolio: PortfolioRiskSection
    stock: StockAnalysisSection


class PortfolioAnalysisIntegrations(BaseModel):
    ai: AINarrativePayload
    telegram: TelegramStatusPayload
    mcp_tools: list[MCPToolPayload] = Field(default_factory=list)


class PortfolioAnalysisRequestEcho(BaseModel):
    section: PortfolioAnalysisSectionKey | None = None
    symbol: str | None = None


class PortfolioAnalysisResponse(BaseModel):
    status: AnalysisStatus
    active_section: PortfolioAnalysisSectionKey | None = None
    generated_at: str | None = None
    display_currency: str
    valuation_mode: str
    request: PortfolioAnalysisRequestEcho
    sections: PortfolioAnalysisSections
    integrations: PortfolioAnalysisIntegrations
    links: dict[str, str] = Field(default_factory=dict)


def missing_metric(*, source: str, reason: str, unit: str | None = None) -> StandardMetric:
    return StandardMetric(
        value=None,
        unit=unit,
        source=source,
        as_of=None,
        confidence=0.0,
        status=AnalysisStatus.MISSING_DATA,
        reason=reason,
    )


def unavailable_ai_narrative(*, reason: str, provider: str = "openai") -> AINarrativePayload:
    return AINarrativePayload(
        provider=provider,
        model=None,
        status=AnalysisStatus.UNAVAILABLE,
        summary=None,
        bullets=[],
        risks=[],
        source_metrics=[],
        as_of=None,
        confidence=0.0,
        reason=reason,
    )


def empty_chart(*, chart_type: str, title: str, source: str, unit: str | None = None) -> EChartsPayload:
    return EChartsPayload(
        chart_type=chart_type,
        title=title,
        unit=unit,
        status=AnalysisStatus.MISSING_DATA,
        source=source,
        as_of=None,
        series=[],
        options={},
    )


def build_empty_portfolio_analysis_response(
    *,
    display_currency: str,
    valuation_mode: str,
    section: PortfolioAnalysisSectionKey | None = None,
    symbol: str | None = None,
    ai_provider: str = "openai",
    telegram_enabled: bool = False,
    telegram_allowlisted_chat_ids_count: int = 0,
    telegram_schedule: str | None = None,
) -> PortfolioAnalysisResponse:
    missing_external_data = "external_market_data_not_configured"
    missing_portfolio_data = "portfolio_risk_pipeline_not_configured"
    missing_stock_data = "stock_analysis_pipeline_not_configured"
    ai_unavailable = unavailable_ai_narrative(
        provider=ai_provider,
        reason=f"{ai_provider}_api_key_not_configured",
    )

    market = MarketAnalysisSection(
        status=AnalysisStatus.MISSING_DATA,
        regime=missing_metric(source="market_data_provider", reason=missing_external_data),
        indicators={
            "rsi": missing_metric(source="market_data_provider", reason=missing_external_data, unit="index"),
            "portfolio_weighted_change": missing_metric(source="portfolio_positions", reason=missing_portfolio_data, unit="percent"),
            "breadth": missing_metric(source="portfolio_positions", reason=missing_portfolio_data, unit="percent"),
            "iv_percentile": missing_metric(source="market_data_provider", reason=missing_external_data, unit="percent"),
            "put_call_ratio": missing_metric(source="market_data_provider", reason=missing_external_data, unit="ratio"),
            "news_heat": missing_metric(source="market_data_provider", reason=missing_external_data, unit="index"),
            "volume_anomaly": missing_metric(source="market_data_provider", reason=missing_external_data, unit="ratio"),
        },
        reasons=[],
        risks=["market analysis requires a configured read-only market data provider"],
        charts=[
            empty_chart(chart_type="gauge", title="相对强弱指数", source="market_data_provider", unit="index"),
            empty_chart(chart_type="line", title="市场趋势", source="market_data_provider"),
        ],
        narrative=ai_unavailable,
    )

    portfolio = PortfolioRiskSection(
        status=AnalysisStatus.MISSING_DATA,
        concentration={
            "sector": missing_metric(source="portfolio_positions", reason=missing_portfolio_data, unit="percent"),
            "single_name": missing_metric(source="portfolio_positions", reason=missing_portfolio_data, unit="percent"),
            "ai_theme": missing_metric(source="portfolio_positions", reason=missing_portfolio_data, unit="percent"),
        },
        factor_exposure={
            "growth": missing_metric(source="portfolio_positions", reason=missing_portfolio_data, unit="percent"),
            "ai_beta": missing_metric(source="portfolio_positions", reason=missing_portfolio_data, unit="percent"),
            "semiconductor": missing_metric(source="portfolio_positions", reason=missing_portfolio_data, unit="percent"),
            "cyclical": missing_metric(source="portfolio_positions", reason=missing_portfolio_data, unit="percent"),
        },
        correlation={
            "top3_weight": missing_metric(source="portfolio_positions", reason=missing_portfolio_data, unit="percent"),
            "theme_cluster": missing_metric(source="portfolio_positions", reason=missing_portfolio_data, unit="percent"),
        },
        tail_risk={
            "daily_loss_at_risk": missing_metric(source="portfolio_positions", reason=missing_portfolio_data, unit=display_currency),
            "downside_breadth": missing_metric(source="portfolio_positions", reason=missing_portfolio_data, unit="percent"),
        },
        macro_sensitivity={
            "rates": missing_metric(source="portfolio_positions", reason=missing_portfolio_data, unit="index"),
            "liquidity": missing_metric(source="portfolio_positions", reason=missing_portfolio_data, unit="index"),
            "ai_capex": missing_metric(source="portfolio_positions", reason=missing_portfolio_data, unit="index"),
        },
        greeks={},
        expiration_risk={},
        advisor_facts=[],
        risk_rows=[],
        rebalance_advice=PortfolioRebalanceAdvice(
            status=AnalysisStatus.MISSING_DATA,
            source="portfolio_positions",
            confidence=0.0,
            reason=missing_portfolio_data,
        ),
        analysis_meta={
            "source": "portfolio_positions",
            "status": AnalysisStatus.MISSING_DATA.value,
            "reason": missing_portfolio_data,
            "confidence": 0.0,
        },
        charts=[
            empty_chart(chart_type="scatter", title="持仓权重 vs 当日涨跌", source="portfolio_positions", unit="percent"),
        ],
        narrative=ai_unavailable,
    )

    stock = StockAnalysisSection(
        status=AnalysisStatus.MISSING_DATA,
        symbol=symbol,
        available_symbols=[],
        memo=StockResearchMemo(
            status=AnalysisStatus.MISSING_DATA,
            symbol=symbol,
            source="portfolio_positions",
            confidence=0.0,
            reason=missing_stock_data,
        ),
        profile={},
        indicators={},
        risks=["stock analysis requires a symbol and configured read-only market data provider"],
        charts=[],
        narrative=ai_unavailable,
    )

    return PortfolioAnalysisResponse(
        status=AnalysisStatus.MISSING_DATA,
        active_section=section,
        generated_at=None,
        display_currency=display_currency,
        valuation_mode=valuation_mode,
        request=PortfolioAnalysisRequestEcho(section=section, symbol=symbol),
        sections=PortfolioAnalysisSections(
            market=market,
            portfolio=portfolio,
            stock=stock,
        ),
        integrations=PortfolioAnalysisIntegrations(
            ai=ai_unavailable,
            telegram=TelegramStatusPayload(
                enabled=telegram_enabled,
                status=AnalysisStatus.UNAVAILABLE,
                allowlisted_chat_ids_count=telegram_allowlisted_chat_ids_count,
                schedule=telegram_schedule,
                last_delivery_at=None,
                source="telegram_settings",
                as_of=None,
                reason="telegram_bot_not_configured",
            ),
            mcp_tools=[],
        ),
        links={
            "self": "/api/portfolio-analysis",
            "settings_url": "/api/settings",
        },
    )
