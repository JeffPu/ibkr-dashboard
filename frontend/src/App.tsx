import { Component, Suspense, lazy, useState } from "react";
import type { ErrorInfo, ReactNode } from "react";
import type { NavItem, PageKey } from "./lib/contracts";
import { Icon } from "./components/Icon";
import { OverviewPage } from "./features/overview/OverviewPage";
import { PositionsPage } from "./features/positions/PositionsPage";
import { PerformancePage } from "./features/performance/PerformancePage";
import { TradesPage } from "./features/trades/TradesPage";
import { SettingsPage } from "./features/settings/SettingsPage";

const PortfolioAnalysisPage = lazy(() =>
  import("./features/portfolio-analysis/PortfolioAnalysisPage").then((module) => ({
    default: module.PortfolioAnalysisPage,
  }))
);

const NAV_ITEMS: NavItem[] = [
  { key: "overview", label: "资产总览", detail: "净值 / 曲线 / 状态", icon: "overview" },
  { key: "positions", label: "持仓明细", detail: "仓位 / 行业 / 成本", icon: "positions" },
  { key: "performance", label: "业绩分析", detail: "收益 / 日历 / 归因", icon: "performance" },
  { key: "trades", label: "交易明细", detail: "交易 / 现金 / 审计", icon: "trades" },
  { key: "portfolioAnalysis", label: "市场分析", detail: "市场状态与风险信号", icon: "analysis" },
  { key: "settings", label: "设置与导入", detail: "凭据 / XML / 集成", icon: "settings" },
];

type NavigateOptions = { expiryStatus?: "all" | "within_30" | "within_7" | "expired" };

const PAGE_RENDERERS: Record<PageKey, (navigate: (page: PageKey, options?: NavigateOptions) => void, options?: NavigateOptions) => JSX.Element> = {
  overview: (navigate) => <OverviewPage onNavigate={navigate} />,
  positions: (_navigate, options) => <PositionsPage initialExpiryFilter={options?.expiryStatus} />,
  performance: () => <PerformancePage />,
  trades: () => <TradesPage />,
  portfolioAnalysis: () => (
    <Suspense fallback={<div className="loading-block"><span /><strong>正在加载市场分析</strong></div>}>
      <PortfolioAnalysisPage />
    </Suspense>
  ),
  settings: () => <SettingsPage />,
};

function App() {
  const [activePage, setActivePage] = useState<PageKey>("overview");
  const [navigateOptions, setNavigateOptions] = useState<NavigateOptions>();
  const [sidebarVisible, setSidebarVisible] = useState(true);
  const renderPage = PAGE_RENDERERS[activePage];
  const navigate = (page: PageKey, options?: NavigateOptions) => {
    setNavigateOptions(options);
    setActivePage(page);
  };

  return (
    <div className={`app-shell ${sidebarVisible ? "" : "app-shell--sidebar-hidden"}`.trim()}>
      <aside className="sidebar">
        <div className="brand-lockup">
          <span>IBKR</span>
          <div>
            <strong>Dashboard</strong>
          </div>
        </div>

        <nav className="nav-list" aria-label="主导航">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={item.key === activePage ? "active" : ""}
              onClick={() => navigate(item.key)}
            >
              <Icon name={item.icon ?? "overview"} wrapClassName="nav-icon" />
              <strong>{item.label}</strong>
            </button>
          ))}
        </nav>

      </aside>

      <main className="main-stage">
        <div className="top-strip">
          <div className="top-strip__title">
            <button
              type="button"
              className="top-strip__menu"
              aria-label={sidebarVisible ? "隐藏侧边栏" : "显示侧边栏"}
              aria-expanded={sidebarVisible}
              onClick={() => setSidebarVisible((visible) => !visible)}
            >
              ☰
            </button>
          </div>
          <div className="top-strip__meta">
            <span><i />Flex 已配置</span>
            <span>数据 本地</span>
          </div>
        </div>
        <AppErrorBoundary resetKey={activePage}>
          {renderPage(navigate, navigateOptions)}
        </AppErrorBoundary>
      </main>
    </div>
  );
}

class AppErrorBoundary extends Component<
  { children: ReactNode; resetKey: PageKey },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("page_render_failed", error, info.componentStack);
  }

  componentDidUpdate(prevProps: { resetKey: PageKey }) {
    if (prevProps.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="error-state">
          <strong>当前板块渲染失败</strong>
          <span>{this.state.error.message || "前端渲染时出现未知错误。"}</span>
          <button type="button" onClick={() => this.setState({ error: null })}>重试</button>
        </div>
      );
    }

    return this.props.children;
  }
}

export default App;
