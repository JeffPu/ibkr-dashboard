import { useEffect, useMemo, useRef } from "react";
import type { EChartsOption } from "echarts";
import * as echarts from "echarts/core";
import { BarChart, CandlestickChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import {
  AriaComponent,
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkPointComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  AriaComponent,
  AxisPointerComponent,
  BarChart,
  CandlestickChart,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkPointComponent,
  LineChart,
  PieChart,
  ScatterChart,
  TooltipComponent,
  CanvasRenderer,
]);

export function EChart({
  option,
  height = 280,
  ariaLabel,
}: {
  option: EChartsOption;
  height?: number;
  ariaLabel: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<ReturnType<typeof echarts.init> | null>(null);
  const theme = useMemo(() => ({
    color: ["#ff3b30", "#111111", "#4b5563", "#d97706", "#047857"],
    textStyle: {
      fontFamily: "\"Aptos\", \"PingFang SC\", \"Noto Sans SC\", sans-serif",
      color: "#20231f",
    },
  }), []);

  useEffect(() => {
    if (!ref.current) return undefined;
    const chart = echarts.init(ref.current, theme);
    chartRef.current = chart;
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
      chartRef.current = null;
    };
  }, [theme]);

  useEffect(() => {
    chartRef.current?.setOption(option, true);
  }, [option]);

  return <div className="echart-canvas" ref={ref} style={{ height }} role="img" aria-label={ariaLabel} tabIndex={0} />;
}
