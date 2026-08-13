import type { EChartsOption } from "echarts";
import { asNumber, asText, formatCurrency, formatInteger } from "../../lib/format";

export type TradeCountOptionRow = {
  key: string;
  label: string;
  trade_count: number;
  trade_notional_abs: number;
};

export function buildTradeCountChartOption({
  rows,
  currency,
}: {
  rows: TradeCountOptionRow[];
  currency: string;
}): EChartsOption {
  const maxCount = Math.max(...rows.map((row) => row.trade_count), 1);
  return {
    animationDuration: 240,
    grid: { left: 32, right: 8, top: 12, bottom: 24, containLabel: true },
    tooltip: {
      trigger: "axis",
      borderColor: "#20231f",
      backgroundColor: "rgba(255,255,255,0.98)",
      textStyle: { color: "#20231f", fontSize: 12, fontWeight: 700 },
      axisPointer: { type: "shadow", shadowStyle: { color: "rgba(32,35,31,0.06)" } },
      formatter: (params) => {
        const item = Array.isArray(params) ? params[0] : params;
        const row = rows[asNumber((item as { dataIndex?: unknown }).dataIndex, -1)];
        const count = asNumber((item as { value?: unknown }).value, 0);
        return [
          `<strong>${row?.key ?? asText((item as { axisValue?: unknown }).axisValue, "")}</strong>`,
          `交易笔数 ${formatInteger(count)}`,
          row ? `交易额 ${formatCurrency(row.trade_notional_abs, currency)}` : "",
        ].filter(Boolean).join("<br/>");
      },
    },
    xAxis: {
      type: "category",
      data: rows.map((row) => row.label),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: "#20231f" } },
      axisLabel: { color: "#5d6558", fontSize: 11, fontWeight: 800, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      min: 0,
      max: maxCount,
      splitNumber: 3,
      axisLabel: { color: "#5d6558", fontSize: 11, fontWeight: 800, formatter: (value: number) => formatInteger(value) },
      splitLine: { lineStyle: { color: "rgba(32,35,31,0.13)", type: "dashed" } },
    },
    series: [{
      name: "交易笔数",
      type: "bar",
      data: rows.map((row) => row.trade_count),
      barMaxWidth: 22,
      itemStyle: {
        color: "#20231f",
        borderRadius: [4, 4, 0, 0],
      },
      emphasis: {
        itemStyle: { color: "#4b5147" },
      },
    }],
  };
}
