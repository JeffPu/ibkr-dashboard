import type {
  AiModelCatalogResponse,
  ImportContentFile,
  ImportTaskResponse,
  OverviewResponse,
  OverviewRiskWarningResponse,
  PortfolioAnalysisResponse,
  PortfolioRefreshJob,
  RequestQuery,
  SettingsResponse,
  SettingsUpdatePayload,
} from "./contracts";

function buildQuery(query?: RequestQuery): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) as unknown : null;
  if (!response.ok) {
    const message =
      body && typeof body === "object" && "message" in body
        ? String((body as { message?: unknown }).message)
        : `${response.status} ${response.statusText}`;
    throw new Error(message);
  }
  return body as T;
}

export function getJson<T>(path: string, query?: RequestQuery): Promise<T> {
  return requestJson<T>(`${path}${buildQuery(query)}`);
}

export function postJson<T>(path: string, payload?: unknown): Promise<T> {
  return requestJson<T>(path, {
    method: "POST",
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
}

export function putJson<T>(path: string, payload: unknown): Promise<T> {
  return requestJson<T>(path, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function deleteJson<T>(path: string): Promise<T> {
  return requestJson<T>(path, {
    method: "DELETE",
  });
}

export const api = {
  overview: () => getJson<OverviewResponse>("/api/overview"),
  overviewBenchmarks: (query?: RequestQuery) => getJson<Record<string, unknown>>("/api/overview/benchmarks", query),
  overviewRiskWarning: (query?: RequestQuery) =>
    getJson<OverviewRiskWarningResponse>("/api/overview/risk-warning", query),
  positions: (query?: RequestQuery) => getJson<Record<string, unknown>>("/api/positions", query),
  portfolioAnalysis: (query?: RequestQuery) =>
    getJson<PortfolioAnalysisResponse>("/api/portfolio-analysis", query),
  refreshPortfolioAnalysisNarrative: (query?: RequestQuery) =>
    postJson<PortfolioRefreshJob>(`/api/portfolio-analysis/narrative/refresh${buildQuery(query)}`, {}),
  portfolioAnalysisRefreshJob: (jobId: string) =>
    getJson<PortfolioRefreshJob>(`/api/portfolio-analysis/narrative/refresh/${encodeURIComponent(jobId)}`),
  positionDetail: (symbol: string) => getJson<Record<string, unknown>>(`/api/positions/${encodeURIComponent(symbol)}/detail`),
  industryAllocation: () => getJson<Record<string, unknown>>("/api/positions/industry-allocation"),
  industryMappings: (query?: RequestQuery) => getJson<Record<string, unknown>>("/api/industry-mappings", query),
  saveIndustryMapping: (symbol: string, industry: string) =>
    putJson<Record<string, unknown>>(`/api/industry-mappings/${encodeURIComponent(symbol)}`, { industry }),
  deleteIndustryMapping: (symbol: string) =>
    deleteJson<Record<string, unknown>>(`/api/industry-mappings/${encodeURIComponent(symbol)}`),
  performance: (query?: RequestQuery) => getJson<Record<string, unknown>>("/api/performance", query),
  trades: (query?: RequestQuery) => getJson<Record<string, unknown>>("/api/trades", query),
  cashFlows: (query?: RequestQuery) => getJson<Record<string, unknown>>("/api/cash-flows", query),
  settings: () => getJson<SettingsResponse>("/api/settings"),
  aiModels: () => getJson<AiModelCatalogResponse>("/api/settings/ai-models"),
  saveSettings: (payload: SettingsUpdatePayload) => putJson<SettingsResponse>("/api/settings", payload),
  createImportTask: (files: ImportContentFile[]) =>
    postJson<ImportTaskResponse>("/api/import/tasks/content/create", { files }),
  runImportTask: (runUrl: string) => postJson<ImportTaskResponse>(runUrl),
  runDailySync: () => postJson<Record<string, unknown>>("/api/settings/daily-sync/run"),
  testFinnhub: () => postJson<Record<string, unknown>>("/api/settings/data-sources/finnhub/test", {}),
  testFutu: () => postJson<Record<string, unknown>>("/api/settings/data-sources/futu/test", {}),
  testLongbridge: () => postJson<Record<string, unknown>>("/api/settings/data-sources/longbridge/test", {}),
  refreshQuotes: () => postJson<Record<string, unknown>>("/api/settings/data-sources/quotes/refresh"),
  refreshMarketHistory: () => postJson<Record<string, unknown>>("/api/settings/data-sources/history/refresh", {}),
  testIbkr: () => postJson<Record<string, unknown>>("/api/settings/data-sources/ibkr/test"),
};
