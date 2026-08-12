from fastapi import APIRouter

from app.api.portfolio_analysis_contracts import MarketAnalysisPayload
from app.api.portfolio_analysis_contracts import MarketAnalysisResponse
from app.api.portfolio_analysis_contracts import PortfolioAnalysisSectionKey
from app.api.response_models import STORAGE_UNAVAILABLE_OPENAPI_RESPONSE
from app.repositories.raw_repository import RawRepository
from app.services.industry_mapping_service import IndustryMappingService
from app.services.market_data_provider import build_market_data_provider
from app.services.portfolio_analysis_service import PortfolioAnalysisService
from app.services.quote_service import QuoteService
from app.services.settings_service import SettingsService


router = APIRouter()
_settings_service: SettingsService = SettingsService()
_raw_repository: RawRepository | object | None = None
_quote_service: QuoteService | None = None
_industry_mapping_service: IndustryMappingService | None = None


def set_settings_service(service: SettingsService) -> None:
    global _settings_service
    _settings_service = service


def set_raw_repository(repository: RawRepository | object | None) -> None:
    global _raw_repository
    _raw_repository = repository


def set_quote_service(service: QuoteService | None) -> None:
    global _quote_service
    _quote_service = service


def set_industry_mapping_service(service: IndustryMappingService | None) -> None:
    global _industry_mapping_service
    _industry_mapping_service = service


@router.get(
    "/api/portfolio-analysis",
    response_model=MarketAnalysisResponse,
    responses=STORAGE_UNAVAILABLE_OPENAPI_RESPONSE,
)
def get_market_analysis() -> MarketAnalysisResponse:
    analysis = _build_service().get_analysis(section=PortfolioAnalysisSectionKey.MARKET, allow_ai=False)
    market = analysis.sections.market
    return MarketAnalysisResponse(
        status=market.status,
        generated_at=analysis.generated_at,
        display_currency=analysis.display_currency,
        valuation_mode=analysis.valuation_mode,
        market=MarketAnalysisPayload.model_validate(market.model_dump(exclude={"narrative"})),
        links={
            "self": "/api/portfolio-analysis",
            "settings_url": "/api/settings",
        },
    )


def _build_service() -> PortfolioAnalysisService:
    settings = _settings_service.get()
    provider = build_market_data_provider(settings, _quote_service)
    return PortfolioAnalysisService(
        raw_repository=_raw_repository,
        settings_service=_settings_service,
        market_data_provider=provider,
        industry_mapping_service=_industry_mapping_service,
    )
