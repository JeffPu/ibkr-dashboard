import type { ReactNode } from "react";
import type { ApiRecord } from "../lib/contracts";
import { asText, deltaClass, formatCurrency, formatNumber } from "../lib/format";

export function Surface({
  title,
  subtitle,
  action,
  children,
  className = "",
}: {
  title?: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`surface ${className}`.trim()}>
      {(title || subtitle || action) && (
        <div className="surface__header">
          <div>
            {title ? <h2>{title}</h2> : null}
            {subtitle ? <p>{subtitle}</p> : null}
          </div>
          {action ? <div className="surface__action">{action}</div> : null}
        </div>
      )}
      {children}
    </section>
  );
}

export function MetricCard({
  label,
  value,
  hint,
  tone = "neutral",
  variant,
  className = "",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "neutral" | "positive" | "negative" | "accent";
  variant?: "compact";
  className?: string;
}) {
  return (
    <div className={`metric-card metric-card--${tone} ${variant ? `metric-card--${variant}` : ""} ${className}`.trim()}>
      <span>{label}</span>
      <strong>{value}</strong>
      {hint ? <small>{hint}</small> : null}
    </div>
  );
}

export function StatusPill({
  children,
  tone = "neutral",
  className = "",
}: {
  children: ReactNode;
  tone?: "neutral" | "positive";
  className?: string;
}) {
  return <span className={`status-pill status-pill--${tone} ${className}`.trim()}>{children}</span>;
}

export function EmptyState({
  title,
  detail,
  compact = false,
}: {
  title: string;
  detail: string;
  compact?: boolean;
}) {
  return (
    <div className={`empty-state ${compact ? "empty-state--compact" : ""}`}>
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="error-state">
      <strong>数据读取失败</strong>
      <span>{message}</span>
      {onRetry ? <button type="button" onClick={onRetry}>重试</button> : null}
    </div>
  );
}

export function LoadingBlock({ label = "正在读取数据" }: { label?: string }) {
  return (
    <div className="loading-block">
      <span />
      <strong>{label}</strong>
    </div>
  );
}

export function DataState<T>({
  loading,
  error,
  data,
  onRetry,
  children,
}: {
  loading: boolean;
  error: string | null;
  data: T | null;
  onRetry: () => void;
  children: (data: T) => ReactNode;
}) {
  if (loading && !data) return <LoadingBlock />;
  if (error && !data) return <ErrorState message={error} onRetry={onRetry} />;
  if (!data) return <EmptyState title="暂无数据" detail="接口暂未返回可展示的数据。" />;
  return <>{children(data)}</>;
}

export function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

export function Toolbar({ children }: { children: ReactNode }) {
  return <div className="toolbar">{children}</div>;
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  className = "",
  ariaLabel,
}: {
  options: Array<{ value: T; label: string }>;
  value: T;
  onChange: (value: T) => void;
  className?: string;
  ariaLabel?: string;
}) {
  return (
    <div className={`segmented-control ${className}`.trim()} role="group" aria-label={ariaLabel}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={value === option.value ? "active" : ""}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function PaginationFooter({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [20, 50, 100],
  className = "pagination-footer",
}: {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  pageSizeOptions?: number[];
  className?: string;
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className={className}>
      <Field label="每页">
        <select value={pageSize} onChange={(event) => onPageSizeChange(Number(event.target.value))}>
          {pageSizeOptions.map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
      </Field>
      <div className="pager">
        <button type="button" onClick={() => onPageChange(Math.max(1, page - 1))} disabled={page <= 1}>上一页</button>
        <span>第 {page} / {pageCount} 页</span>
        <button type="button" onClick={() => onPageChange(Math.min(pageCount, page + 1))} disabled={page >= pageCount}>下一页</button>
      </div>
    </div>
  );
}

export function DataTable({
  columns,
  rows,
  empty,
  onRowClick,
  sortKey,
  sortDir,
  onSort,
}: {
  columns: Array<{ key: string; label: string; align?: "left" | "right"; sortable?: boolean; render?: (row: ApiRecord, index: number) => ReactNode }>;
  rows: ApiRecord[];
  empty?: string;
  onRowClick?: (row: ApiRecord) => void;
  sortKey?: string;
  sortDir?: "asc" | "desc";
  onSort?: (key: string) => void;
}) {
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} className={column.align === "right" ? "align-right" : undefined}>
                {onSort && column.sortable !== false ? (
                  <button
                    type="button"
                    className={`table-sort-button ${column.align === "right" ? "table-sort-button--right" : ""}`.trim()}
                    onClick={() => onSort(column.key)}
                  >
                    <span>{column.label}</span>
                    {sortKey === column.key ? <em>{sortDir === "asc" ? "↑" : "↓"}</em> : null}
                  </button>
                ) : column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="table-empty">{empty ?? "暂无数据"}</td>
            </tr>
          ) : rows.map((row, index) => (
            <tr key={`${asText(row.id ?? row.document_id ?? row.trade_id ?? row.symbol ?? index)}-${index}`} onClick={onRowClick ? () => onRowClick(row) : undefined}>
              {columns.map((column) => (
                <td key={column.key} className={column.align === "right" ? "align-right" : undefined}>
                  {column.render ? column.render(row, index) : asText(row[column.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DeltaText({ value, currency }: { value: unknown; currency?: string }) {
  const klass = deltaClass(value);
  return <span className={`delta-text ${klass}`}>{currency ? formatCurrency(value, currency) : formatNumber(value)}</span>;
}
