# IBKR Dashboard 项目笔记

本文件适用于当前目录及其所有子目录。修改本项目时，先阅读本文件，再结合具体文件上下文执行。

## 项目定位

- 这是一个本地运行的 IBKR 投资分析看板，读取 Interactive Brokers Flex XML，分析资产、持仓、业绩、交易记录、出入金记录和组合风险。
- 项目明确只做只读分析，不提供交易、下单、撤单、改单、解锁交易或风控执行能力。新增功能时必须保持这个边界。
- 用户数据和凭据应留在本地。不要把真实 XML、CSV、Excel、`.env`、API Key、Flex Token、Telegram Bot Token 写入代码、文档示例或测试 fixture。

## 技术栈

- 前端：React 18、TypeScript、Vite、ECharts。
- 后端：Python 3.12+、FastAPI、APScheduler、httpx、futu-api。
- 存储：Elasticsearch；测试和临时开发可使用 `ES_BACKEND=in_memory`。
- 容器化：根目录 `docker-compose.yml` 启动 Elasticsearch、后端 API 和前端。

## 目录结构

- `frontend/`：前端应用。入口是 `frontend/src/main.tsx` 和 `frontend/src/App.tsx`。
- `frontend/src/features/`：页面模块，包括资产总览、持仓明细、业绩分析、交易明细、设置与导入、V2 持仓分析。
- `frontend/src/lib/api.ts`：前端请求封装，新增 API 调用优先放这里。
- `frontend/src/lib/contracts.ts`：前后端共享的 TypeScript 响应类型。
- `frontend/src/components/`：通用 UI 组件和图表包装，`components/charts/EChart.tsx` 封装 ECharts。
- `backend/app/main.py`：FastAPI 应用装配、依赖注入、路由注册、存储初始化和定时任务启动。
- `backend/app/api/routes/`：HTTP API 路由层。通过 `set_*` 函数注入 repository/service，测试也依赖此模式。
- `backend/app/services/`：业务逻辑层，包括 XML 导入、行情、设置、对账、持仓分析、AI 摘要、Telegram、MCP 工具。
- `backend/app/repositories/`：Elasticsearch 抽象、HTTP ES client、内存 ES 和各类 repository。
- `backend/app/models/`：领域模型和 XML 解析模型。
- `backend/tests/`：后端 pytest 测试。`conftest.py` 默认设置 `ES_BACKEND=in_memory` 并隔离内存存储。
- `scripts/dev-all.sh`：本机开发一键启动脚本，会准备虚拟环境并安装依赖；默认本机 ES 不可用时，会尝试启动已有 ES 容器或 compose ES 服务，再启动后端/前端。
- `docs/`、`PRD.md`、`IBKR_Dashboard_*.md`：产品需求、发布清单和历史计划资料。
- `promo-assets/`：推广图素材。

## 启动方式

- Docker 用户入口：`docker compose up --build`，前端端口 `5176`，容器内后端对外端口 `8000`。
- 本机开发入口：`npm run dev:all`。
- 本机开发默认后端：`http://127.0.0.1:8085`。
- 本机开发默认前端：`http://127.0.0.1:5176`。
- 本机开发默认连接或尝试启动 `http://127.0.0.1:9200` 的 Elasticsearch；临时无持久化数据开发用 `ES_BACKEND=in_memory npm run dev:all`。
- 前端 Vite 通过 `VITE_API_PROXY_TARGET` 代理 `/api` 和 `/health`，默认指向 `http://127.0.0.1:8085`。

## 常用验证命令

- 标准全量验证：`npm run verify`。该命令先构建前端，再以 `ES_BACKEND=in_memory` 运行后端全量测试；后端优先使用 `backend/.venv/bin/python`，不存在时回退到 `python3`。
- 仅前端验证：`npm run verify:frontend`。
- 仅后端验证：`npm run verify:backend`。
- 后端单测：`cd backend && ES_BACKEND=in_memory pytest -q tests/test_xxx.py`。
- Docker 冒烟：`docker compose up --build` 后检查 `GET /health`、`GET /health/storage`、`GET /api/settings`。

按风险选择验证范围：

- 低风险：纯文档或注释修改，检查格式和相关命令即可。
- 单侧改动：只修改前端时运行 `npm run verify:frontend`；只修改后端时运行 `npm run verify:backend`，开发过程中可先运行相关后端单测。
- 跨前后端、配置、依赖、共享契约或准备交付的改动：运行标准命令 `npm run verify`。
- 长周期改造每个线程只推进一个已确认阶段；阶段结束时留下“决策、改动文件、剩余任务、最后验证基线”的简短交接。

当前前端 `package.json` 没有 lint/test 脚本；前端验证以构建成功为准。

## 后端设计要点

- `backend/app/main.py` 在模块加载时创建共享 `shared_es`、repository、service，并把依赖注入到各路由模块。
- 存储后端由环境变量控制：`ES_HOST` 存在时强制使用 HTTP ES；否则读取 `ES_BACKEND`，可选 `http` 或 `in_memory`。
- 启动时会确保这些索引存在：账户快照、持仓快照、交易记录、现金流水、资金行、汇率、组合收益、对账结果、应用设置、行业映射、AI 分析缓存。
- `/health` 只表示应用进程可用；`/health/storage` 才表示存储初始化状态。
- 存储不可用时，`/api/*` 会返回统一的 `STORAGE_UNAVAILABLE` 错误包。
- XML 解析集中在 `backend/app/services/xml_parser.py`，主要读取 `EquitySummaryByReportDateInBase`、`OpenPosition`、`StatementOfFundsLine`、`ConversionRate`、`Trade`、`CashTransaction`。
- 导入任务在 `IngestionService` 内存维护任务状态，导入结果写入 `RawRepository.upsert_parsed_data`。
- 定时同步通过 `DailySyncScheduler`；Telegram 日报通过 `DailyTimeScheduler`。

## V2 与外部集成

- V2 顶层入口是前端 `portfolioAnalysis` 页面和后端 `/api/portfolio-analysis`。
- V2 分析返回结构必须统一携带状态字段：数据来源、更新时间、置信度、状态和缺失原因。外部数据不可用时应显示缺失，不要编造数值。
- AI provider 支持 `openai`、`minimax`、`deepseek`、`mock`；没有 key、超时或 provider 不支持结构化输出时应安全降级为不可用或本地规则状态。
- 行情 provider 优先长桥，也支持 Finnhub/Yahoo 兜底和通过 `futu-api` 连接本地 Futu OpenD 的只读模式。
- Futu OpenD、长桥、Finnhub、Yahoo、OpenAI、MiniMax、DeepSeek、Telegram、MCP 都只能用于读取、分析或发送分析结果，不能引入交易动作。
- MCP server 在 `backend/app/mcp_server.py`，暴露只读工具给 Claude Desktop、Codex Desktop 等客户端。

## 前端设计要点

- 当前没有路由库，页面切换由 `App.tsx` 中的 `activePage` 状态控制。
- 导航项在 `NAV_ITEMS` 中维护；新增顶层页面时同步更新 `PageKey`、`NAV_ITEMS` 和 `PAGE_RENDERERS`。
- API 请求统一走 `frontend/src/lib/api.ts` 的 `getJson`、`postJson`、`putJson`、`deleteJson`。
- 前后端契约类型优先补充到 `frontend/src/lib/contracts.ts`，避免在页面里散落 `any` 或重复结构。
- 页面样式集中在 `frontend/src/styles.css`，修改视觉时注意移动端和桌面端都不能出现文本溢出或控件重叠。

## 数据与安全约束

- `.gitignore` 已忽略真实投资数据、凭据、本地数据库、虚拟环境、node_modules、缓存和测试输出。
- 根目录已有本地 XML 示例文件，但这类文件按规则不应提交或复制到新文档中。
- 不要运行会清空用户数据的命令，除非用户明确要求，例如 `docker compose down -v`。
- 不要把 `.venv/`、`node_modules/`、`__pycache__/`、`.pytest_cache/`、`.omx/`、`.idea/` 作为源码修改目标。

## 修改建议

- 后端新增 API：先看相邻 `backend/app/api/routes/*.py` 的依赖注入和错误响应模式，再补 service/repository 测试。
- 数据导入或收益口径修改：优先加/改 `backend/tests/` 中对应解析、repository、API 或端到端测试。
- 前端新增页面数据：先扩展 `contracts.ts` 和 `api.ts`，页面只消费封装后的接口。
- 涉及持仓分析的改动：同时检查 `backend/app/api/portfolio_analysis_contracts.py`、`backend/app/services/portfolio_analysis_service.py`、`frontend/src/lib/contracts.ts` 和
  `frontend/src/features/portfolio-analysis/PortfolioAnalysisPage.tsx`。
- 涉及设置项的改动：同时检查 `settings_service.py`、`settings_repository.py`、`routes/settings.py`、`frontend/src/features/settings/SettingsPage.tsx` 和 `SettingsResponse` 类型。
- 完成修改前按风险等级运行对应验证；跨层或交付前使用 `npm run verify`，不要只凭静态阅读宣称完成。
