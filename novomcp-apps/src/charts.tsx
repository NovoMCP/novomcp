/**
 * Minimal SVG chart primitives for inline MCP App viewers.
 *
 * Zero dependencies. Used by frontier-orbitals (level bars),
 * qm-hessian (vibrational-frequency bars), transition-state
 * (MEP energy profile line plot), and any future viewer that
 * needs a small chart without pulling a chart library.
 *
 * Convention: charts respect the host's theme via CSS variables
 * (var(--accent), var(--text-muted), etc.) set by global.css and
 * useHostStyles.
 */

import type { CSSProperties } from "react";

// =============================================================================
// BarChart — horizontal or vertical bars with optional per-bar labels and
// color highlighting. Handles positive AND negative values (negative bars grow
// downward from a baseline).
// =============================================================================

export interface Bar {
  /** Numeric value (can be negative for imaginary frequencies etc.) */
  value: number;
  /** Short label under / next to the bar */
  label?: string;
  /** Override color (falls back to var(--accent)) */
  color?: string;
  /** Optional click handler — if present, bar gets cursor:pointer */
  onClick?: () => void;
  /** Hover title */
  title?: string;
}

export function BarChart({
  bars,
  width = 600,
  height = 180,
  padding = 28,
  unit = "",
  orientation = "vertical",
  zeroLine = true,
}: {
  bars: Bar[];
  width?: number;
  height?: number;
  padding?: number;
  unit?: string;
  orientation?: "vertical" | "horizontal";
  zeroLine?: boolean;
}) {
  if (bars.length === 0) return null;

  const values = bars.map((b) => b.value);
  const maxVal = Math.max(0, ...values);
  const minVal = Math.min(0, ...values);
  const span = maxVal - minVal || 1;

  if (orientation === "vertical") {
    const plotH = height - padding * 2;
    const barW = (width - padding * 2) / bars.length;
    const zeroY = padding + (maxVal / span) * plotH;

    return (
      <svg width="100%" viewBox={`0 0 ${width} ${height}`} style={{ display: "block" }}>
        {zeroLine && (
          <line
            x1={padding}
            x2={width - padding}
            y1={zeroY}
            y2={zeroY}
            stroke="var(--text-muted)"
            strokeOpacity={0.4}
            strokeDasharray="2 3"
          />
        )}
        {bars.map((bar, i) => {
          const x = padding + i * barW + barW * 0.15;
          const bw = barW * 0.7;
          const barH = Math.abs(bar.value) / span * plotH;
          const y = bar.value >= 0 ? zeroY - barH : zeroY;
          const color = bar.color ?? "var(--accent)";
          return (
            <g key={i}>
              <rect
                x={x}
                y={y}
                width={bw}
                height={barH}
                fill={color}
                onClick={bar.onClick}
                style={{ cursor: bar.onClick ? "pointer" : undefined }}
              >
                {bar.title && <title>{bar.title}</title>}
              </rect>
              {bar.label && (
                <text
                  x={x + bw / 2}
                  y={height - padding + 14}
                  fontSize={9}
                  textAnchor="middle"
                  fill="var(--text-muted)"
                  style={{ fontFamily: "var(--font-mono)" }}
                >
                  {bar.label}
                </text>
              )}
            </g>
          );
        })}
        {unit && (
          <text
            x={padding - 4}
            y={padding - 6}
            fontSize={9}
            textAnchor="end"
            fill="var(--text-muted)"
          >
            {unit}
          </text>
        )}
      </svg>
    );
  }

  // Horizontal
  const plotW = width - padding * 2;
  const barH = (height - padding * 2) / bars.length;
  const zeroX = padding + (-minVal / span) * plotW;

  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} style={{ display: "block" }}>
      {zeroLine && (
        <line
          x1={zeroX}
          x2={zeroX}
          y1={padding}
          y2={height - padding}
          stroke="var(--text-muted)"
          strokeOpacity={0.4}
          strokeDasharray="2 3"
        />
      )}
      {bars.map((bar, i) => {
        const y = padding + i * barH + barH * 0.15;
        const bh = barH * 0.7;
        const bw = Math.abs(bar.value) / span * plotW;
        const x = bar.value >= 0 ? zeroX : zeroX - bw;
        const color = bar.color ?? "var(--accent)";
        return (
          <g key={i}>
            <rect
              x={x}
              y={y}
              width={bw}
              height={bh}
              fill={color}
              onClick={bar.onClick}
              style={{ cursor: bar.onClick ? "pointer" : undefined }}
            >
              {bar.title && <title>{bar.title}</title>}
            </rect>
            {bar.label && (
              <text
                x={padding - 4}
                y={y + bh / 2 + 3}
                fontSize={9}
                textAnchor="end"
                fill="var(--text-muted)"
                style={{ fontFamily: "var(--font-mono)" }}
              >
                {bar.label}
              </text>
            )}
          </g>
        );
      })}
      {unit && (
        <text
          x={width - padding}
          y={height - padding + 14}
          fontSize={9}
          textAnchor="end"
          fill="var(--text-muted)"
        >
          {unit}
        </text>
      )}
    </svg>
  );
}

// =============================================================================
// LinePlot — points connected by lines, optional per-point click handlers.
// Used for MEP energy profiles, emission spectra, reaction coordinate plots.
// =============================================================================

export interface Point {
  x: number;
  y: number;
  /** Label shown on hover / as a tick */
  label?: string;
  /** Override color (falls back to var(--accent)) */
  color?: string;
  /** Optional click handler */
  onClick?: () => void;
  /** Hover title */
  title?: string;
}

export function LinePlot({
  points,
  width = 600,
  height = 220,
  padding = 36,
  xAxisLabel,
  yAxisLabel,
  lineColor = "var(--accent)",
  pointRadius = 4,
  highlightIndex,
}: {
  points: Point[];
  width?: number;
  height?: number;
  padding?: number;
  xAxisLabel?: string;
  yAxisLabel?: string;
  lineColor?: string;
  pointRadius?: number;
  /** Optional index to highlight with a larger circle and accent border */
  highlightIndex?: number;
}) {
  if (points.length < 2) return null;

  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const yMin = Math.min(...ys);
  const yMax = Math.max(...ys);
  const xSpan = xMax - xMin || 1;
  const ySpan = yMax - yMin || 1;

  const plotW = width - padding * 2;
  const plotH = height - padding * 2;

  const toX = (x: number) => padding + ((x - xMin) / xSpan) * plotW;
  const toY = (y: number) => height - padding - ((y - yMin) / ySpan) * plotH;

  const pathD = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${toX(p.x)} ${toY(p.y)}`)
    .join(" ");

  const styleInline: CSSProperties = { display: "block" };

  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} style={styleInline}>
      {/* Y-axis baseline */}
      <line
        x1={padding}
        x2={padding}
        y1={padding}
        y2={height - padding}
        stroke="var(--text-muted)"
        strokeOpacity={0.3}
      />
      {/* X-axis baseline */}
      <line
        x1={padding}
        x2={width - padding}
        y1={height - padding}
        y2={height - padding}
        stroke="var(--text-muted)"
        strokeOpacity={0.3}
      />

      {/* y=0 guideline if zero is inside the range */}
      {yMin < 0 && yMax > 0 && (
        <line
          x1={padding}
          x2={width - padding}
          y1={toY(0)}
          y2={toY(0)}
          stroke="var(--text-muted)"
          strokeOpacity={0.2}
          strokeDasharray="2 3"
        />
      )}

      {/* Path */}
      <path d={pathD} fill="none" stroke={lineColor} strokeWidth={2} />

      {/* Points */}
      {points.map((p, i) => {
        const isHighlight = i === highlightIndex;
        const r = isHighlight ? pointRadius + 2 : pointRadius;
        return (
          <g key={i}>
            <circle
              cx={toX(p.x)}
              cy={toY(p.y)}
              r={r}
              fill={p.color ?? lineColor}
              stroke={isHighlight ? "var(--text)" : "var(--bg)"}
              strokeWidth={isHighlight ? 2 : 1}
              onClick={p.onClick}
              style={{ cursor: p.onClick ? "pointer" : undefined }}
            >
              {p.title && <title>{p.title}</title>}
            </circle>
            {p.label && (
              <text
                x={toX(p.x)}
                y={height - padding + 14}
                fontSize={9}
                textAnchor="middle"
                fill="var(--text-muted)"
                style={{ fontFamily: "var(--font-mono)" }}
              >
                {p.label}
              </text>
            )}
          </g>
        );
      })}

      {/* Axis labels */}
      {xAxisLabel && (
        <text
          x={width / 2}
          y={height - 4}
          fontSize={10}
          textAnchor="middle"
          fill="var(--text-muted)"
        >
          {xAxisLabel}
        </text>
      )}
      {yAxisLabel && (
        <text
          x={12}
          y={height / 2}
          fontSize={10}
          textAnchor="middle"
          fill="var(--text-muted)"
          transform={`rotate(-90 12 ${height / 2})`}
        >
          {yAxisLabel}
        </text>
      )}
    </svg>
  );
}
