import { ChangeEvent, useCallback, useEffect, useState } from "react";
import { api } from "../../lib/api";
import type {
  AiModelProviderCatalog,
  AiProvider,
  ApiRecord,
  ImportTaskResponse,
  SettingsResponse,
  SettingsUpdatePayload,
} from "../../lib/contracts";
import { asArray, asNumber, asText, formatDateTimeMinute, formatInteger } from "../../lib/format";
import { useApiData } from "../../lib/useApiData";
import { DataState, DataTable, EmptyState, Field, MetricCard, PageHeader, StatusPill, Surface } from "../../components/Primitives";

interface SettingsForm {
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
  futu_connection_mode: "disabled" | "local_opend" | "longbridge";
  futu_opend_host: string;
  futu_opend_port: number;
  telegram_bot_token: string;
  telegram_allowlisted_chat_ids: string;
  telegram_reports_enabled: boolean;
  telegram_daily_report_time: string;
  mcp_server_enabled: boolean;
  report_cache_enabled: boolean;
  report_cache_ttl_minutes: number;
}

const defaultForm: SettingsForm = {
  base_currency: "USD",
  timezone: "America/New_York",
  finnhub_api_key: "",
  flex_token: "",
  flex_query_id: "",
  pull_frequency_minutes: 60,
  display_realtime_prices: false,
  ai_provider: "openai",
  ai_model: "gpt-5-mini",
  openai_api_key: "",
  minimax_api_key: "",
  minimax_base_url: "https://api.minimaxi.com/v1",
  deepseek_api_key: "",
  deepseek_base_url: "https://api.deepseek.com",
  futu_connection_mode: "disabled",
  futu_opend_host: "127.0.0.1",
  futu_opend_port: 11111,
  telegram_bot_token: "",
  telegram_allowlisted_chat_ids: "",
  telegram_reports_enabled: false,
  telegram_daily_report_time: "08:30",
  mcp_server_enabled: false,
  report_cache_enabled: true,
  report_cache_ttl_minutes: 60,
};

const currencyOptions = [
  { value: "USD", label: "USD" },
  { value: "HKD", label: "HKD" },
  { value: "CNY", label: "CNY" },
  { value: "KRW", label: "KRW" },
];

const timezoneOptions = [
  { value: "Asia/Shanghai", label: "中国北京" },
  { value: "America/New_York", label: "美国纽约" },
];

const frequencyOptions = [
  { value: 30, label: "30 分钟" },
  { value: 60, label: "1 小时" },
  { value: 360, label: "6 小时" },
  { value: 720, label: "12 小时" },
  { value: 1440, label: "1 天" },
];

function hasMaskedValue(value: string): boolean {
  return value.includes("*");
}

function parseTelegramChatIds(value: string): string[] {
  return value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function defaultAiModel(provider: AiProvider, catalog: Partial<Record<AiProvider, AiModelProviderCatalog>>): string {
  return catalog[provider]?.default_model ?? "";
}

function normalizeAiProviderChange(
  nextProvider: AiProvider,
  form: SettingsForm,
  catalog: Partial<Record<AiProvider, AiModelProviderCatalog>>,
): SettingsForm {
  const options = catalog[nextProvider]?.models ?? [];
  const nextModel = options.some((option) => option.value === form.ai_model) ? form.ai_model : defaultAiModel(nextProvider, catalog);
  return { ...form, ai_provider: nextProvider, ai_model: nextModel };
}

function aiProviderConfigured(data: SettingsResponse): boolean {
  if (data.ai_provider === "mock") return true;
  if (data.ai_provider === "minimax") return Boolean(data.minimax_api_key);
  if (data.ai_provider === "deepseek") return Boolean(data.deepseek_api_key);
  return Boolean(data.openai_api_key);
}

function aiProviderStatusText(data: SettingsResponse): string {
  if (data.ai_provider === "mock") return "Mock AI";
  if (data.ai_provider === "minimax") return data.minimax_api_key ? "MiniMax 已配置" : "待配置 MiniMax";
  if (data.ai_provider === "deepseek") return data.deepseek_api_key ? "DeepSeek 已配置" : "待配置 DeepSeek";
  return data.openai_api_key ? "OpenAI 已配置" : "待配置 OpenAI";
}

function settingsToForm(data: SettingsResponse): SettingsForm {
  return {
    base_currency: asText(data.base_currency, "USD"),
    timezone: asText(data.timezone, "America/New_York"),
    finnhub_api_key: asText(data.finnhub_api_key, ""),
    flex_token: asText(data.flex_token, ""),
    flex_query_id: asText(data.flex_query_id, ""),
    pull_frequency_minutes: asNumber(data.pull_frequency_minutes, 60),
    display_realtime_prices: Boolean(data.display_realtime_prices),
    ai_provider: data.ai_provider ?? "openai",
    ai_model: asText(data.ai_model, ""),
    openai_api_key: asText(data.openai_api_key, ""),
    minimax_api_key: asText(data.minimax_api_key, ""),
    minimax_base_url: asText(data.minimax_base_url, "https://api.minimaxi.com/v1"),
    deepseek_api_key: asText(data.deepseek_api_key, ""),
    deepseek_base_url: asText(data.deepseek_base_url, "https://api.deepseek.com"),
    futu_connection_mode: data.futu_connection_mode ?? "disabled",
    futu_opend_host: asText(data.futu_opend_host, "127.0.0.1"),
    futu_opend_port: asNumber(data.futu_opend_port, 11111),
    telegram_bot_token: asText(data.telegram_bot_token, ""),
    telegram_allowlisted_chat_ids: data.telegram_allowlisted_chat_ids.join("\n"),
    telegram_reports_enabled: Boolean(data.telegram_reports_enabled),
    telegram_daily_report_time: asText(data.telegram_daily_report_time, "08:30"),
    mcp_server_enabled: Boolean(data.mcp_server_enabled),
    report_cache_enabled: Boolean(data.report_cache_enabled),
    report_cache_ttl_minutes: asNumber(data.report_cache_ttl_minutes, 60),
  };
}

export function SettingsPage() {
  const [form, setForm] = useState<SettingsForm>(defaultForm);
  const [aiModelCatalog, setAiModelCatalog] = useState<Partial<Record<AiProvider, AiModelProviderCatalog>>>({});
  const [showIntegrationGuide, setShowIntegrationGuide] = useState(false);
  const [message, setMessage] = useState("");
  const [importResult, setImportResult] = useState<ImportTaskResponse | null>(null);
  const { state, load } = useApiData<SettingsResponse>(async () => {
    const data = await api.settings();
    setForm(settingsToForm(data));
    return data;
  });

  const loadAiModels = useCallback(async () => {
    try {
      const data = await api.aiModels();
      const catalog = Object.fromEntries(data.providers.map((item) => [item.provider, item])) as Partial<Record<AiProvider, AiModelProviderCatalog>>;
      setAiModelCatalog(catalog);
    } catch {
      setAiModelCatalog({});
    }
  }, []);

  useEffect(() => {
    void loadAiModels();
  }, [loadAiModels]);

  useEffect(() => {
    const options = aiModelCatalog[form.ai_provider]?.models ?? [];
    if (!options.length || options.some((option) => option.value === form.ai_model)) return;
    setForm((current) => ({ ...current, ai_model: defaultAiModel(current.ai_provider, aiModelCatalog) }));
  }, [aiModelCatalog, form.ai_model, form.ai_provider]);

  const save = async () => {
    setMessage("正在保存设置...");
    try {
      const payload: SettingsUpdatePayload = {
        base_currency: form.base_currency,
        timezone: form.timezone,
        finnhub_api_key: form.finnhub_api_key,
        flex_token: form.flex_token,
        flex_query_id: form.flex_query_id,
        pull_frequency_minutes: form.pull_frequency_minutes,
        display_realtime_prices: form.display_realtime_prices,
        ai_provider: form.ai_provider,
        ai_model: form.ai_model || defaultAiModel(form.ai_provider, aiModelCatalog),
        openai_api_key: form.openai_api_key,
        minimax_api_key: form.minimax_api_key,
        minimax_base_url: form.minimax_base_url,
        deepseek_api_key: form.deepseek_api_key,
        deepseek_base_url: form.deepseek_base_url,
        futu_connection_mode: form.futu_connection_mode,
        futu_opend_host: form.futu_opend_host,
        futu_opend_port: form.futu_opend_port,
        telegram_bot_token: form.telegram_bot_token,
        telegram_allowlisted_chat_ids: parseTelegramChatIds(form.telegram_allowlisted_chat_ids),
        telegram_reports_enabled: form.telegram_reports_enabled,
        telegram_daily_report_time: form.telegram_daily_report_time,
        mcp_server_enabled: form.mcp_server_enabled,
        report_cache_enabled: form.report_cache_enabled,
        report_cache_ttl_minutes: form.report_cache_ttl_minutes,
      };
      if (hasMaskedValue(form.finnhub_api_key)) delete payload.finnhub_api_key;
      if (hasMaskedValue(form.flex_token)) delete payload.flex_token;
      if (hasMaskedValue(form.openai_api_key)) delete payload.openai_api_key;
      if (hasMaskedValue(form.minimax_api_key)) delete payload.minimax_api_key;
      if (hasMaskedValue(form.deepseek_api_key)) delete payload.deepseek_api_key;
      if (hasMaskedValue(form.telegram_bot_token)) delete payload.telegram_bot_token;
      await api.saveSettings(payload);
      setMessage("设置已保存");
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    }
  };

  const runAction = async (label: string, action: () => Promise<ApiRecord>) => {
    setMessage(`${label}...`);
    try {
      const result = await action();
      setMessage(`${label}完成：${asText(result.status ?? result.message ?? result.ok, "ok")}`);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `${label}失败`);
    }
  };

  const handleXmlFiles = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    if (files.length === 0) return;
    setMessage("正在读取 XML 文件...");
    try {
      const payload = await Promise.all(files.map(async (file) => ({ filename: file.name, content: await file.text() })));
      const created = await api.createImportTask(payload);
      const run = await api.runImportTask(created.run_url);
      setImportResult(run);
      setMessage(`导入任务完成：${asText(run.status, "completed")}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "导入失败");
    } finally {
      event.target.value = "";
    }
  };

  const currentAiModelOptions = aiModelCatalog[form.ai_provider]?.models
    ?? (form.ai_model ? [{ value: form.ai_model, label: form.ai_model }] : []);

  return (
    <DataState loading={state.loading} error={state.error} data={state.data} onRetry={load}>
      {(data) => (
        <>
          <PageHeader
            eyebrow="设置与导入"
            title="本地分析控制台"
            meta={
              <>
                <StatusPill tone={data.flex_query_id ? "positive" : "neutral"}>{data.flex_query_id ? "Flex 已配置" : "待配置 Flex"}</StatusPill>
                <StatusPill tone={aiProviderConfigured(data) ? "positive" : "neutral"}>
                  {aiProviderStatusText(data)}
                </StatusPill>
                <StatusPill>{data.ai_model}</StatusPill>
                <StatusPill>同步 {formatDateTimeMinute(data.last_successful_sync_at_local ?? data.last_successful_sync_at)}</StatusPill>
                <button type="button" onClick={load}>刷新</button>
                <button type="button" onClick={save}>保存全部</button>
              </>
            }
          />

          {message ? <div className="message-bar">{message}</div> : null}

          <div className="settings-layout settings-console">
            <div className="settings-console-grid">
              <Surface title="系统参数" className="settings-panel">
                <div className="settings-compact-grid">
                  <Field label="基础币种">
                    <select value={form.base_currency} onChange={(event) => setForm({ ...form, base_currency: event.target.value })}>
                      {currencyOptions.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="时区">
                    <select value={form.timezone} onChange={(event) => setForm({ ...form, timezone: event.target.value })}>
                      {timezoneOptions.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="拉取频率">
                    <select value={form.pull_frequency_minutes} onChange={(event) => setForm({ ...form, pull_frequency_minutes: Number(event.target.value) })}>
                      {frequencyOptions.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="缓存有效期">
                    <input
                      type="number"
                      min={1}
                      value={form.report_cache_ttl_minutes}
                      onChange={(event) => setForm({ ...form, report_cache_ttl_minutes: Number(event.target.value) })}
                    />
                  </Field>
                </div>
              </Surface>

              <Surface title="XML 导入" className="settings-panel settings-import-surface">
                <div className="xml-import-panel">
                  <Field label="选择 Flex XML">
                    <input type="file" accept=".xml,text/xml" multiple onChange={handleXmlFiles} />
                  </Field>
                </div>
                {importResult ? (
                  <div className="metric-grid metric-grid--compact settings-import-metrics">
                    <MetricCard label="任务状态" value={asText(importResult.status)} tone={importResult.status === "completed" ? "positive" : "neutral"} />
                    <MetricCard label="处理文件" value={`${formatInteger(importResult.processed_files)} / ${formatInteger(importResult.total_files)}`} />
                    <MetricCard label="进度" value={`${Math.round(asNumber(importResult.progress, 0) * 100)}%`} />
                  </div>
                ) : (
                  <EmptyState title="等待导入" detail="选择 XML 后开始解析" compact />
                )}
              </Surface>

              <Surface title="同步与数据刷新" className="settings-panel settings-panel--actions">
                <div className="action-grid settings-action-grid">
                  <button type="button" onClick={() => runAction("每日同步", api.runDailySync)}>运行每日同步</button>
                  <button type="button" onClick={() => runAction("IBKR 连通性测试", api.testIbkr)}>测试 IBKR</button>
                  <button type="button" onClick={() => runAction("Finnhub 连通性测试", api.testFinnhub)}>测试 Finnhub</button>
                  {form.futu_connection_mode === "local_opend" ? (
                    <button type="button" onClick={() => runAction("Futu OpenD 连通性测试", api.testFutu)}>测试 Futu OpenD</button>
                  ) : null}
                  {form.futu_connection_mode === "longbridge" ? (
                    <button type="button" onClick={() => runAction("长桥连通性测试", api.testLongbridge)}>测试长桥</button>
                  ) : null}
                  <button type="button" onClick={() => runAction("刷新报价", api.refreshQuotes)}>刷新报价</button>
                  <button type="button" onClick={() => runAction("刷新历史K线", api.refreshMarketHistory)}>刷新历史K线</button>
                </div>
                <div className="settings-sync-status">
                  <span>上次同步</span>
                  <strong>{formatDateTimeMinute(data.last_successful_sync_at_local ?? data.last_successful_sync_at)}</strong>
                </div>
              </Surface>

              <Surface title="数据来源" className="settings-panel">
                <div className="settings-compact-grid">
                  <Field label="Flex Token">
                    <input value={form.flex_token} onChange={(event) => setForm({ ...form, flex_token: event.target.value })} />
                  </Field>
                  <Field label="Flex Query ID">
                    <input value={form.flex_query_id} onChange={(event) => setForm({ ...form, flex_query_id: event.target.value })} />
                  </Field>
                  <Field label="Finnhub API Key">
                    <input value={form.finnhub_api_key} onChange={(event) => setForm({ ...form, finnhub_api_key: event.target.value })} />
                  </Field>
                  <Field label="行情 Provider">
                    <select value={form.futu_connection_mode} onChange={(event) => setForm({ ...form, futu_connection_mode: event.target.value as SettingsForm["futu_connection_mode"] })}>
                      <option value="disabled">关闭</option>
                      <option value="longbridge">长桥</option>
                      <option value="local_opend">本地 OpenD</option>
                    </select>
                  </Field>
                  {form.futu_connection_mode === "local_opend" ? (
                    <>
                      <Field label="OpenD Host">
                        <input value={form.futu_opend_host} onChange={(event) => setForm({ ...form, futu_opend_host: event.target.value })} />
                      </Field>
                      <Field label="OpenD Port">
                        <input
                          type="number"
                          min={1}
                          max={65535}
                          value={form.futu_opend_port}
                          onChange={(event) => setForm({ ...form, futu_opend_port: Number(event.target.value) })}
                        />
                      </Field>
                    </>
                  ) : null}
                </div>
              </Surface>

              <Surface title="AI Provider" className="settings-panel">
                <div className="settings-compact-grid">
                  <Field label="AI Provider">
                    <select
                      value={form.ai_provider}
                      onChange={(event) => setForm(normalizeAiProviderChange(event.target.value as AiProvider, form, aiModelCatalog))}
                    >
                      <option value="openai">OpenAI</option>
                      <option value="minimax">MiniMax</option>
                      <option value="deepseek">DeepSeek</option>
                      <option value="mock">Mock</option>
                    </select>
                  </Field>
                  <Field label="AI 模型">
                    <select value={form.ai_model} onChange={(event) => setForm({ ...form, ai_model: event.target.value })}>
                      {currentAiModelOptions.length === 0 ? <option value="">加载模型列表</option> : null}
                      {currentAiModelOptions.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </Field>
                  {form.ai_provider === "openai" ? (
                    <Field label="OpenAI API Key">
                      <input value={form.openai_api_key} onChange={(event) => setForm({ ...form, openai_api_key: event.target.value })} />
                    </Field>
                  ) : null}
                  {form.ai_provider === "minimax" ? (
                    <>
                      <Field label="MiniMax API Key">
                        <input value={form.minimax_api_key} onChange={(event) => setForm({ ...form, minimax_api_key: event.target.value })} />
                      </Field>
                      <Field label="MiniMax Base URL">
                        <input value={form.minimax_base_url} onChange={(event) => setForm({ ...form, minimax_base_url: event.target.value })} />
                      </Field>
                    </>
                  ) : null}
                  {form.ai_provider === "deepseek" ? (
                    <>
                      <Field label="DeepSeek API Key">
                        <input value={form.deepseek_api_key} onChange={(event) => setForm({ ...form, deepseek_api_key: event.target.value })} />
                      </Field>
                      <Field label="DeepSeek Base URL">
                        <input value={form.deepseek_base_url} onChange={(event) => setForm({ ...form, deepseek_base_url: event.target.value })} />
                      </Field>
                    </>
                  ) : null}
                </div>
              </Surface>

              <Surface
                title="MCP 与 Telegram"
                className="settings-panel"
                action={<button type="button" className="settings-guide-button" onClick={() => setShowIntegrationGuide(true)}>指南</button>}
              >
                <div className="settings-compact-grid">
                  <Field label="Telegram Bot Token">
                    <input value={form.telegram_bot_token} onChange={(event) => setForm({ ...form, telegram_bot_token: event.target.value })} />
                  </Field>
                  <Field label="Telegram 日报时间">
                    <input
                      type="time"
                      value={form.telegram_daily_report_time}
                      onChange={(event) => setForm({ ...form, telegram_daily_report_time: event.target.value })}
                    />
                  </Field>
                  <Field label="Telegram Chat IDs">
                    <textarea
                      value={form.telegram_allowlisted_chat_ids}
                      onChange={(event) => setForm({ ...form, telegram_allowlisted_chat_ids: event.target.value })}
                      rows={3}
                    />
                  </Field>
                </div>
                <div className="settings-switch-grid settings-switch-row">
                  <label className="switch-field switch-field--wide">
                    <input
                      type="checkbox"
                      checked={form.telegram_reports_enabled}
                      onChange={(event) => setForm({ ...form, telegram_reports_enabled: event.target.checked })}
                    />
                    <span className="switch-control" />
                    <span>
                      <strong>Telegram 日报</strong>
                      <small>按配置时间发送只读摘要</small>
                    </span>
                  </label>
                  <label className="switch-field switch-field--wide">
                    <input
                      type="checkbox"
                      checked={form.mcp_server_enabled}
                      onChange={(event) => setForm({ ...form, mcp_server_enabled: event.target.checked })}
                    />
                    <span className="switch-control" />
                    <span>
                      <strong>MCP Server</strong>
                      <small>暴露只读工具入口</small>
                    </span>
                  </label>
                </div>
              </Surface>

              {importResult ? (
                <Surface title="导入摘要" className="settings-panel settings-summary-panel">
                  <DataTable
                    rows={asArray(importResult?.summaries)}
                    columns={[
                      { key: "file_path", label: "文件", render: () => "本地 XML" },
                      { key: "record_counts", label: "映射记录", render: (row) => {
                        const counts = row.record_counts && typeof row.record_counts === "object" ? row.record_counts as ApiRecord : {};
                        return Object.entries(counts).map(([key, value]) => `${key}:${value}`).join(" / ");
                      } },
                    ]}
                    empty="暂无导入摘要"
                  />
                </Surface>
              ) : null}
            </div>
          </div>
          {showIntegrationGuide ? <IntegrationGuideModal onClose={() => setShowIntegrationGuide(false)} /> : null}
        </>
      )}
    </DataState>
  );
}

function IntegrationGuideModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section className="settings-guide-modal" role="dialog" aria-modal="true" aria-labelledby="settings-guide-title" onClick={(event) => event.stopPropagation()}>
        <div className="position-modal__header settings-guide-modal__header">
          <div>
            <span className="eyebrow">集成指南</span>
            <h2 id="settings-guide-title">MCP 与 Telegram</h2>
          </div>
          <button type="button" className="modal-close-button" aria-label="关闭指南" onClick={onClose}>×</button>
        </div>

        <div className="settings-guide-grid">
          <div className="settings-guide-section">
            <div className="settings-guide-card">
              <strong>Telegram 配置步骤</strong>
              <ol>
                <li>在 Telegram 搜索 BotFather，发送 /newbot 创建机器人，按提示设置名称和 username。</li>
                <li>BotFather 返回的 token 形如 123456:ABC-DEF，把它填入 Telegram Bot Token。</li>
                <li>给新 Bot 发一条消息；如果要发到群，把 Bot 加入群并在群里发一条消息。</li>
                <li>在浏览器打开 https://api.telegram.org/bot&lt;你的TOKEN&gt;/getUpdates，找到 message.chat.id。</li>
                <li>把 chat.id 填入 Telegram Chat IDs。多个会话用换行或逗号分隔，群组 ID 通常是负数。</li>
                <li>打开 Telegram 日报，设置日报时间，点击页面右上角保存全部。</li>
              </ol>
            </div>
            <div className="settings-guide-card">
              <strong>日报说明</strong>
              <p>日报会发送当前持仓分析状态摘要。它依赖本地已导入的数据、AI/分析缓存和 Telegram 配置；如果 Chat IDs 为空或日报开关关闭，定时任务不会发送。</p>
            </div>
          </div>
          <div className="settings-guide-section">
            <div className="settings-guide-card">
              <strong>Telegram 指令</strong>
              <div className="settings-command-list">
                <div><code>/overview</code><span>账户净值、日期和基础币种</span></div>
                <div><code>/summary</code><span>与 /overview 相同的概览别名</span></div>
                <div><code>/positions</code><span>当前主要持仓列表</span></div>
                <div><code>/risk</code><span>组合集中度和风险摘要</span></div>
                <div><code>/cashflow</code><span>现金流水记录数与合计</span></div>
                <div><code>/cash</code><span>与 /cashflow 相同的别名</span></div>
                <div><code>/market</code><span>当前市场状态判断</span></div>
                <div><code>/report</code><span>立即生成一次日报文本</span></div>
              </div>
            </div>
            <div className="settings-guide-card">
              <strong>指令权限</strong>
              <p>只有 Chat IDs 允许列表中的会话可以使用这些指令。未加入允许列表会返回 forbidden；不在列表中的指令会返回 unsupported_command。</p>
            </div>
          </div>
          <div className="settings-guide-section settings-guide-section--wide">
            <div className="settings-guide-card">
              <strong>MCP 配置与使用</strong>
              <ol>
                <li>打开 MCP Server，点击保存全部。</li>
                <li>在支持 MCP 的客户端中新增 stdio server，命令指向本项目后端的 mcp_server 模块。</li>
                <li>可用工具包括 get_account_overview、list_positions、get_position_detail、get_portfolio_risk、get_market_regime、get_performance_summary、list_cash_flows、get_wheel_snapshot。</li>
                <li>MCP 工具全部只读，只读取本地 ES 与分析服务，不提供交易、下单、撤单或账户修改能力。</li>
              </ol>
            </div>
            <div className="settings-guide-card">
              <strong>排查顺序</strong>
              <ul>
                <li>Telegram 收不到消息：先检查 Bot Token，再检查 Chat ID 是否来自 getUpdates，最后确认日报开关和保存状态。</li>
                <li>Telegram 指令 forbidden：把当前会话的 chat.id 加到 Telegram Chat IDs 后保存。</li>
                <li>日报内容缺数据：先导入 XML 或运行每日同步，再刷新持仓分析。</li>
                <li>MCP 连不上：确认后端本地服务可启动、MCP Server 已启用，并重启 MCP 客户端连接。</li>
              </ul>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
