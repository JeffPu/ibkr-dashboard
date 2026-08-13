import type { ReactNode } from "react";

const common = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const paths: Record<string, ReactNode> = {
  overview: (
    <>
      <path {...common} d="M4 13.5 9.2 8l4.4 4 6.4-7" />
      <path {...common} d="M4 19h16" />
      <path {...common} d="M5 16v3" />
      <path {...common} d="M11 13v6" />
      <path {...common} d="M17 10v9" />
    </>
  ),
  positions: (
    <>
      <rect {...common} x="4" y="5" width="16" height="14" rx="2" />
      <path {...common} d="M8 9h8" />
      <path {...common} d="M8 13h5" />
      <path {...common} d="M16 13h.01" />
    </>
  ),
  performance: (
    <>
      <path {...common} d="M5 19V5" />
      <path {...common} d="M5 19h14" />
      <path {...common} d="M8 15l3-4 3 2 4-6" />
      <circle {...common} cx="11" cy="11" r="1" />
      <circle {...common} cx="18" cy="7" r="1" />
    </>
  ),
  trades: (
    <>
      <path {...common} d="M7 7h11" />
      <path {...common} d="m15 4 3 3-3 3" />
      <path {...common} d="M17 17H6" />
      <path {...common} d="m9 14-3 3 3 3" />
    </>
  ),
  analysis: (
    <>
      <circle {...common} cx="12" cy="12" r="7" />
      <path {...common} d="M12 5v7l5 3" />
      <path {...common} d="M8 18.2 6.5 21" />
      <path {...common} d="M16 18.2l1.5 2.8" />
    </>
  ),
  settings: (
    <>
      <circle {...common} cx="12" cy="12" r="3" />
      <path {...common} d="M19 12a7.6 7.6 0 0 0-.1-1.2l2-1.5-2-3.5-2.4 1a7 7 0 0 0-2-1.1L14 3h-4l-.5 2.7a7 7 0 0 0-2 1.1l-2.4-1-2 3.5 2 1.5A7.6 7.6 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.5 2.4-1a7 7 0 0 0 2 1.1L10 21h4l.5-2.7a7 7 0 0 0 2-1.1l2.4 1 2-3.5-2-1.5c.1-.4.1-.8.1-1.2Z" />
    </>
  ),
  alert: (
    <>
      <path {...common} d="M12 3 2.8 19h18.4L12 3Z" />
      <path {...common} d="M12 8v5" />
      <path {...common} d="M12 16h.01" />
    </>
  ),
  radar: (
    <>
      <circle {...common} cx="12" cy="12" r="8" />
      <path {...common} d="M12 4v8l5 3" />
      <path {...common} d="M4 12h16" />
    </>
  ),
  target: (
    <>
      <circle {...common} cx="12" cy="12" r="8" />
      <circle {...common} cx="12" cy="12" r="3" />
      <path {...common} d="M12 2v3M12 19v3M2 12h3M19 12h3" />
    </>
  ),
  spark: (
    <>
      <path {...common} d="M12 2l1.8 6.2L20 10l-6.2 1.8L12 18l-1.8-6.2L4 10l6.2-1.8L12 2Z" />
      <path {...common} d="M18 16l.8 2.4L21 19l-2.2.6L18 22l-.8-2.4L15 19l2.2-.6L18 16Z" />
    </>
  ),
  database: (
    <>
      <ellipse {...common} cx="12" cy="5" rx="7" ry="3" />
      <path {...common} d="M5 5v6c0 1.7 3.1 3 7 3s7-1.3 7-3V5" />
      <path {...common} d="M5 11v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" />
    </>
  ),
  compass: (
    <>
      <circle {...common} cx="12" cy="12" r="9" />
      <path {...common} d="m15.5 8.5-2.2 4.8-4.8 2.2 2.2-4.8 4.8-2.2Z" />
    </>
  ),
};

export function Icon({
  name,
  className,
  wrapClassName,
}: {
  name: string;
  className?: string;
  wrapClassName?: string;
}) {
  const svg = (
    <svg className={className} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {paths[name] ?? paths.check}
    </svg>
  );
  return wrapClassName ? <span className={wrapClassName} aria-hidden="true">{svg}</span> : svg;
}
