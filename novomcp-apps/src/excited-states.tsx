/**
 * NovoMCP Excited States (sTDA-xTB) Viewer
 *
 * Renders the output of run_excited_states: S1/T1 energies + S-T gap,
 * emission and phosphorescence wavelengths, and a scrollable list of
 * all computed excited states (singlet + triplet) with oscillator
 * strengths. Click any state to ask Claude about the transition.
 */

import { useEffect, useRef, useState } from "react";
import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types
// =============================================================================

interface ExcitedState {
  state?: number;
  energy_ev?: number;
  wavelength_nm?: number;
  is_singlet?: boolean;
  oscillator_strength?: number;
}

interface ExcitedStatesToolInput {
  smiles?: string;
  charge?: number;
  num_states?: number;

  s1_energy_ev?: number;
  t1_energy_ev?: number;
  singlet_triplet_gap_ev?: number;
  emission_wavelength_nm?: number;
  phosphorescence_wavelength_nm?: number;

  n_states?: number;
  excited_states?: ExcitedState[];

  method?: string;
  wall_time_seconds?: number;
  warnings?: string[];
}

type ExcitedStatesProps = ViewProps<ExcitedStatesToolInput>;

// =============================================================================
// Loading Shimmer
// =============================================================================

function LoadingShimmer() {
  return (
    <div
      style={{
        width: "100%",
        padding: 24,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 12,
        background: "linear-gradient(135deg, var(--bg-warm) 0%, var(--bg) 100%)",
        borderRadius: 4,
        minHeight: 220,
      }}
    >
      <div className="loading-spinner" />
      <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
        Computing sTDA-xTB excited states…
      </div>
    </div>
  );
}

// =============================================================================
// S / T Ladder — two columns showing singlet and triplet state stacks
//
// Dense excited-state manifolds (e.g. coumarin S6-S10 clustered within 0.5 eV)
// would otherwise overprint labels on a fixed linear y-axis. Two fixes:
//   (1) iterative label collision-resolution with leader lines, so the
//       default view stays legible regardless of clustering;
//   (2) interactive zoom (scroll-wheel) + pan (drag) + double-click reset,
//       so the user can spread any cluster on demand.
// =============================================================================

const MIN_LABEL_SPACING = 13; // px between label baselines in SVG units
const LABEL_GAP = 14; // px from bar end to leader-line terminus / label start

// Iteratively relax label positions so no two are closer than `minSpacing`.
// Clamps to [yTop, yBottom]. Returns the new y for each input in input order.
function distributeLabels(
  natural: number[],
  minSpacing: number,
  yTop: number,
  yBottom: number,
): number[] {
  if (natural.length === 0) return [];
  const order = natural.map((_, i) => i).sort((a, b) => natural[a] - natural[b]);
  const sorted = order.map((i) => natural[i]);
  for (let iter = 0; iter < 200; iter++) {
    let changed = false;
    for (let i = 0; i < sorted.length - 1; i++) {
      const gap = sorted[i + 1] - sorted[i];
      if (gap < minSpacing - 0.01) {
        const push = (minSpacing - gap) / 2 + 0.05;
        sorted[i] -= push;
        sorted[i + 1] += push;
        changed = true;
      }
    }
    for (let i = 0; i < sorted.length; i++) {
      sorted[i] = Math.max(yTop, Math.min(yBottom, sorted[i]));
    }
    if (!changed) break;
  }
  const out = new Array<number>(natural.length);
  order.forEach((origIdx, sortedIdx) => {
    out[origIdx] = sorted[sortedIdx];
  });
  return out;
}

function generateTicks(yMin: number, yMax: number): number[] {
  const span = yMax - yMin;
  let step = 1;
  if (span < 0.3) step = 0.05;
  else if (span < 0.8) step = 0.1;
  else if (span < 2) step = 0.2;
  else if (span < 5) step = 0.5;
  else step = 1;
  const ticks: number[] = [];
  const start = Math.ceil(yMin / step) * step;
  for (let v = start; v <= yMax + 1e-9; v += step) {
    ticks.push(Math.round(v / step) * step);
  }
  return ticks;
}

function formatTick(value: number, span: number): string {
  if (span < 1) return value.toFixed(2);
  if (span < 3) return value.toFixed(1);
  return value.toFixed(0);
}

function StateLadder({
  states,
  onStateClick,
}: {
  states: ExcitedState[];
  onStateClick?: (state: ExcitedState) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const panStartRef = useRef<{ clientY: number; range: [number, number] } | null>(null);
  const [yRange, setYRange] = useState<[number, number] | null>(null);
  const [isPanning, setIsPanning] = useState(false);

  const allEnergies = (states ?? [])
    .map((s) => s.energy_ev)
    .filter((e): e is number => e != null);
  const yMaxFull = allEnergies.length ? Math.max(...allEnergies) + 0.3 : 7;
  const yMinFull = 0;

  // Wheel zoom needs a non-passive listener; React's onWheel can be passive
  // in some setups, which would block preventDefault. Attach natively.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const ctm = svg.getScreenCTM();
      if (!ctm) return;
      const pt = svg.createSVGPoint();
      pt.x = e.clientX;
      pt.y = e.clientY;
      const svgPt = pt.matrixTransform(ctm.inverse());
      const yMax = yRange ? yRange[1] : yMaxFull;
      const yMin = yRange ? yRange[0] : yMinFull;
      const ySpan = yMax - yMin || 1;
      const cursorEnergy = yMax - ((svgPt.y - 20) / (280 - 20 - 36)) * ySpan;
      if (cursorEnergy < yMinFull - 0.5 || cursorEnergy > yMaxFull + 0.5) return;
      const zoomFactor = e.deltaY > 0 ? 1.25 : 1 / 1.25;
      const newSpan = ySpan * zoomFactor;
      const fullSpan = yMaxFull - yMinFull;
      if (newSpan >= fullSpan) {
        setYRange(null);
        return;
      }
      if (newSpan < 0.2) return; // hard floor
      const ratioTop = (yMax - cursorEnergy) / ySpan;
      let newMax = cursorEnergy + ratioTop * newSpan;
      let newMin = newMax - newSpan;
      if (newMin < yMinFull) {
        newMax += yMinFull - newMin;
        newMin = yMinFull;
      }
      if (newMax > yMaxFull) {
        newMin -= newMax - yMaxFull;
        newMax = yMaxFull;
      }
      setYRange([newMin, newMax]);
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, [yRange, yMaxFull, yMinFull]);

  if (!states || states.length === 0) return null;

  const singlets = states.filter((s) => s.is_singlet);
  const triplets = states.filter((s) => !s.is_singlet);

  const yMax = yRange ? yRange[1] : yMaxFull;
  const yMin = yRange ? yRange[0] : yMinFull;
  const ySpan = yMax - yMin || 1;

  const width = 500;
  const height = 280;
  const leftPad = 40;
  const rightPad = 20;
  const topPad = 20;
  const bottomPad = 36;
  const plotH = height - topPad - bottomPad;

  const toY = (ev: number) => topPad + ((yMax - ev) / ySpan) * plotH;
  const inRange = (ev: number) => ev >= yMin - 1e-6 && ev <= yMax + 1e-6;

  const singletX = leftPad + 80;
  const tripletX = leftPad + 280;
  const barWidth = 130;

  const visibleSinglets = singlets.filter(
    (s) => s.energy_ev != null && inRange(s.energy_ev),
  );
  const visibleTriplets = triplets.filter(
    (t) => t.energy_ev != null && inRange(t.energy_ev),
  );

  const singletLabelY = distributeLabels(
    visibleSinglets.map((s) => toY(s.energy_ev!)),
    MIN_LABEL_SPACING,
    topPad,
    topPad + plotH,
  );
  const tripletLabelY = distributeLabels(
    visibleTriplets.map((t) => toY(t.energy_ev!)),
    MIN_LABEL_SPACING,
    topPad,
    topPad + plotH,
  );

  const ticks = generateTicks(yMin, yMax);

  const handleMouseDown = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!yRange) return;
    setIsPanning(true);
    panStartRef.current = { clientY: e.clientY, range: yRange };
  };

  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!isPanning || !panStartRef.current) return;
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const dy = e.clientY - panStartRef.current.clientY;
    const pxToEnergy = (ySpan / plotH) * (height / rect.height);
    const energyShift = dy * pxToEnergy;
    const [a, b] = panStartRef.current.range;
    let newMin = a + energyShift;
    let newMax = b + energyShift;
    if (newMin < yMinFull) {
      newMax += yMinFull - newMin;
      newMin = yMinFull;
    }
    if (newMax > yMaxFull) {
      newMin -= newMax - yMaxFull;
      newMax = yMaxFull;
    }
    setYRange([newMin, newMax]);
  };

  const stopPan = () => {
    setIsPanning(false);
    panStartRef.current = null;
  };

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          fontSize: 10,
          color: "var(--text-muted)",
          marginBottom: 4,
          padding: "0 4px",
          minHeight: 22,
        }}
      >
        <span>scroll to zoom · drag to pan · double-click to reset</span>
        {yRange && (
          <button
            type="button"
            onClick={() => setYRange(null)}
            style={{
              background: "var(--bg-warm)",
              border: "1px solid var(--border)",
              color: "var(--text)",
              fontSize: 10,
              padding: "2px 8px",
              borderRadius: 2,
              cursor: "pointer",
              fontFamily: "var(--font-mono)",
            }}
          >
            {yRange[0].toFixed(2)}–{yRange[1].toFixed(2)} eV · reset
          </button>
        )}
      </div>
      <svg
        ref={svgRef}
        width="100%"
        viewBox={`0 0 ${width} ${height}`}
        style={{
          display: "block",
          cursor: isPanning ? "grabbing" : yRange ? "grab" : "default",
          touchAction: "none",
          userSelect: "none",
        }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={stopPan}
        onMouseLeave={stopPan}
        onDoubleClick={() => setYRange(null)}
      >
        <defs>
          <clipPath id="state-ladder-plot-clip">
            <rect
              x={leftPad}
              y={topPad}
              width={width - leftPad - rightPad}
              height={plotH}
            />
          </clipPath>
        </defs>

        {/* Y axis */}
        <line
          x1={leftPad}
          x2={leftPad}
          y1={topPad}
          y2={height - bottomPad}
          stroke="var(--text-muted)"
          strokeOpacity={0.4}
        />
        {ticks.map((e) => (
          <g key={e}>
            <line
              x1={leftPad - 4}
              x2={leftPad}
              y1={toY(e)}
              y2={toY(e)}
              stroke="var(--text-muted)"
              strokeOpacity={0.4}
            />
            <text
              x={leftPad - 6}
              y={toY(e) + 3}
              fontSize={9}
              textAnchor="end"
              fill="var(--text-muted)"
              style={{ fontFamily: "var(--font-mono)" }}
            >
              {formatTick(e, ySpan)}
            </text>
          </g>
        ))}
        <text
          x={12}
          y={topPad + plotH / 2}
          fontSize={10}
          textAnchor="middle"
          fill="var(--text-muted)"
          transform={`rotate(-90 12 ${topPad + plotH / 2})`}
        >
          Energy (eV)
        </text>

        {/* S0 ground-state line across both columns — only when 0 is in range */}
        {inRange(0) && (
          <>
            <line
              x1={singletX}
              x2={singletX + barWidth}
              y1={toY(0)}
              y2={toY(0)}
              stroke="var(--text)"
              strokeWidth={2}
            />
            <line
              x1={tripletX}
              x2={tripletX + barWidth}
              y1={toY(0)}
              y2={toY(0)}
              stroke="var(--text)"
              strokeWidth={2}
            />
            <text
              x={singletX - 6}
              y={toY(0) + 3}
              fontSize={10}
              textAnchor="end"
              fill="var(--text-muted)"
              style={{ fontFamily: "var(--font-mono)" }}
            >
              S₀
            </text>
            <text
              x={tripletX - 6}
              y={toY(0) + 3}
              fontSize={10}
              textAnchor="end"
              fill="var(--text-muted)"
              style={{ fontFamily: "var(--font-mono)" }}
            >
              S₀
            </text>
          </>
        )}

        {/* Column headers */}
        <text
          x={singletX + barWidth / 2}
          y={height - bottomPad + 20}
          fontSize={11}
          textAnchor="middle"
          fill="var(--accent)"
          style={{ fontWeight: 500 }}
        >
          Singlets
        </text>
        <text
          x={tripletX + barWidth / 2}
          y={height - bottomPad + 20}
          fontSize={11}
          textAnchor="middle"
          fill="var(--warning)"
          style={{ fontWeight: 500 }}
        >
          Triplets
        </text>

        {/* Singlet level bars (clipped to plot area) */}
        <g clipPath="url(#state-ladder-plot-clip)">
          {visibleSinglets.map((s, i) => {
            const y = toY(s.energy_ev!);
            const f = s.oscillator_strength ?? 0;
            const strokeWidth = f > 0.1 ? 3 : f > 0.01 ? 2 : 1;
            return (
              <line
                key={`sl-${i}`}
                x1={singletX}
                x2={singletX + barWidth}
                y1={y}
                y2={y}
                stroke="var(--accent)"
                strokeWidth={strokeWidth}
                onClick={onStateClick ? () => onStateClick(s) : undefined}
                style={{ cursor: onStateClick ? "pointer" : undefined }}
              />
            );
          })}
        </g>

        {/* Singlet labels (de-collided, with leader lines when offset) */}
        {visibleSinglets.map((s, i) => {
          const y = toY(s.energy_ev!);
          const ly = singletLabelY[i];
          const f = s.oscillator_strength ?? 0;
          const showLeader = Math.abs(ly - y) > 1.5;
          return (
            <g
              key={`sll-${i}`}
              onClick={onStateClick ? () => onStateClick(s) : undefined}
              style={{ cursor: onStateClick ? "pointer" : undefined }}
            >
              {showLeader && (
                <line
                  x1={singletX + barWidth}
                  x2={singletX + barWidth + LABEL_GAP - 2}
                  y1={y}
                  y2={ly}
                  stroke="var(--accent)"
                  strokeOpacity={0.5}
                  strokeWidth={0.6}
                />
              )}
              <text
                x={singletX + barWidth + LABEL_GAP}
                y={ly + 3}
                fontSize={9}
                fill="var(--text-muted)"
                style={{ fontFamily: "var(--font-mono)" }}
              >
                S{s.state} · f={f.toFixed(3)}
              </text>
              <title>
                {`S${s.state}: ${s.energy_ev?.toFixed(2)} eV (${s.wavelength_nm?.toFixed(0)} nm), f=${f.toFixed(4)}`}
              </title>
            </g>
          );
        })}

        {/* Triplet level bars (clipped) */}
        <g clipPath="url(#state-ladder-plot-clip)">
          {visibleTriplets.map((t, i) => {
            const y = toY(t.energy_ev!);
            return (
              <line
                key={`tl-${i}`}
                x1={tripletX}
                x2={tripletX + barWidth}
                y1={y}
                y2={y}
                stroke="var(--warning)"
                strokeWidth={2}
                strokeDasharray="4 3"
                onClick={onStateClick ? () => onStateClick(t) : undefined}
                style={{ cursor: onStateClick ? "pointer" : undefined }}
              />
            );
          })}
        </g>

        {/* Triplet labels (de-collided, with leader lines) */}
        {visibleTriplets.map((t, i) => {
          const y = toY(t.energy_ev!);
          const ly = tripletLabelY[i];
          const showLeader = Math.abs(ly - y) > 1.5;
          return (
            <g
              key={`tll-${i}`}
              onClick={onStateClick ? () => onStateClick(t) : undefined}
              style={{ cursor: onStateClick ? "pointer" : undefined }}
            >
              {showLeader && (
                <line
                  x1={tripletX + barWidth}
                  x2={tripletX + barWidth + LABEL_GAP - 2}
                  y1={y}
                  y2={ly}
                  stroke="var(--warning)"
                  strokeOpacity={0.5}
                  strokeWidth={0.6}
                />
              )}
              <text
                x={tripletX + barWidth + LABEL_GAP}
                y={ly + 3}
                fontSize={9}
                fill="var(--text-muted)"
                style={{ fontFamily: "var(--font-mono)" }}
              >
                T{t.state}
              </text>
              <title>
                {`T${t.state}: ${t.energy_ev?.toFixed(2)} eV (${t.wavelength_nm?.toFixed(0)} nm)`}
              </title>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// =============================================================================
// Key energies panel — S1, T1, S-T gap, emission, phosphorescence
// =============================================================================

function KeyEnergiesPanel({ data }: { data: ExcitedStatesToolInput }) {
  const items: Array<{ label: string; value?: string; color: string; hint?: string }> = [
    {
      label: "S₁",
      value:
        data.s1_energy_ev != null
          ? `${data.s1_energy_ev.toFixed(2)} eV${data.emission_wavelength_nm != null ? ` · ${data.emission_wavelength_nm.toFixed(0)} nm` : ""}`
          : undefined,
      color: "var(--accent)",
      hint: "First singlet excited state (fluorescence origin)",
    },
    {
      label: "T₁",
      value:
        data.t1_energy_ev != null
          ? `${data.t1_energy_ev.toFixed(2)} eV${data.phosphorescence_wavelength_nm != null ? ` · ${data.phosphorescence_wavelength_nm.toFixed(0)} nm` : ""}`
          : undefined,
      color: "var(--warning)",
      hint: "First triplet excited state (phosphorescence origin)",
    },
    {
      label: "S-T gap",
      value:
        data.singlet_triplet_gap_ev != null
          ? `${data.singlet_triplet_gap_ev.toFixed(2)} eV`
          : undefined,
      color: "var(--text-muted)",
      hint: "Small gap (<0.3 eV) favors TADF; large gap (>0.5 eV) favors clean phosphorescence vs fluorescence separation",
    },
  ];

  return (
    <div className="panel">
      <div className="panel-title">Key Excited-State Energies</div>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        {items.map((it) =>
          it.value ? (
            <div
              key={it.label}
              title={it.hint}
              style={{
                padding: "10px 14px",
                background: "var(--bg-warm)",
                borderRadius: 2,
                borderLeft: `3px solid ${it.color}`,
                minWidth: 150,
              }}
            >
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>
                {it.label}
              </div>
              <div
                style={{
                  fontSize: 13,
                  fontFamily: "var(--font-mono)",
                  fontWeight: 600,
                  color: it.color,
                }}
              >
                {it.value}
              </div>
            </div>
          ) : null,
        )}
      </div>
    </div>
  );
}

// =============================================================================
// All-states list (for skimming oscillator strengths)
// =============================================================================

function AllStatesList({
  states,
  onStateClick,
}: {
  states?: ExcitedState[];
  onStateClick?: (state: ExcitedState) => void;
}) {
  if (!states || states.length === 0) return null;

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">All States ({states.length})</div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Type</th>
              <th style={{ textAlign: "right", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Energy (eV)</th>
              <th style={{ textAlign: "right", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>λ (nm)</th>
              <th style={{ textAlign: "right", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Oscillator f</th>
            </tr>
          </thead>
          <tbody>
            {states.map((s, i) => {
              const label = `${s.is_singlet ? "S" : "T"}${s.state}`;
              const color = s.is_singlet ? "var(--accent)" : "var(--warning)";
              const f = s.oscillator_strength ?? 0;
              const isBright = f > 0.1;
              return (
                <tr
                  key={i}
                  onClick={onStateClick ? () => onStateClick(s) : undefined}
                  style={{
                    borderBottom: "1px solid var(--border)",
                    cursor: onStateClick ? "pointer" : undefined,
                  }}
                  title={onStateClick ? "Click to ask Claude about this transition" : undefined}
                >
                  <td style={{ padding: "6px 8px", fontFamily: "var(--font-mono)", fontWeight: 500, color }}>
                    {label}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right", fontFamily: "var(--font-mono)" }}>
                    {s.energy_ev?.toFixed(3) ?? "—"}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                    {s.wavelength_nm?.toFixed(0) ?? "—"}
                  </td>
                  <td
                    style={{
                      padding: "6px 8px",
                      textAlign: "right",
                      fontFamily: "var(--font-mono)",
                      color: isBright ? "var(--success)" : f > 0.01 ? "var(--text)" : "var(--text-muted)",
                      fontWeight: isBright ? 600 : 400,
                    }}
                    title={
                      !s.is_singlet && s.oscillator_strength == null
                        ? "Triplets are spin-forbidden — f = 0 by selection rules"
                        : undefined
                    }
                  >
                    {/* f=0 is legitimate for triplets (spin-forbidden) and for dark
                        singlets; render 0.0000 so it doesn't look like missing data.
                        Only show — when the field is genuinely absent. */}
                    {s.oscillator_strength != null
                      ? s.oscillator_strength.toFixed(4)
                      : !s.is_singlet
                        ? "0.0000"
                        : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6 }}>
        f = oscillator strength. Bright singlets (f &gt; 0.1) are likely emitters;
        triplets have f = 0 (spin-forbidden) and require SOC for phosphorescence.
      </div>
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function ExcitedStatesViewer(props: ExcitedStatesProps) {
  const { toolInputs, toolInputsPartial, toolResult, sendMessage } = props;
  const data = useViewData<ExcitedStatesToolInput>(props);
  const isStreaming = !toolInputs && !toolResult;
  if (isStreaming) {
    return <LoadingShimmer />;
  }

  const smiles = data.smiles || toolInputs?.smiles || toolInputsPartial?.smiles;

  const askAboutState = sendMessage
    ? (s: ExcitedState) => {
        const kind = s.is_singlet ? "S" : "T";
        const label = `${kind}${s.state}`;
        const energy = s.energy_ev?.toFixed(2);
        const wavelength = s.wavelength_nm?.toFixed(0);
        const f = s.oscillator_strength ?? 0;
        const smilesRef = smiles ? ` for \`${smiles}\`` : "";
        const isBright = f > 0.1;
        const isTriplet = !s.is_singlet;

        const prompt = isTriplet
          ? `I clicked the ${label} triplet state at ${energy} eV (${wavelength} nm)${smilesRef}. ` +
            `This is a triplet (spin-forbidden, f=0). Is there enough spin-orbit coupling in this molecule ` +
            `to make phosphorescence observable, what would the T₁ → S₀ emission look like, ` +
            `and is this molecule a viable phosphorescent emitter or a triplet quencher?`
          : `I clicked the ${label} singlet state at ${energy} eV (${wavelength} nm), f=${f.toFixed(3)}${smilesRef}. ` +
            (isBright
              ? `This is a bright state (f > 0.1 — strong absorption). Which transition dominates ` +
                `(HOMO→LUMO, n→π*, charge-transfer, etc.), what would this look like in a UV-vis spectrum, ` +
                `and is it a good emitter for OLED applications?`
              : `This is a weak state (low oscillator strength). What's making this transition forbidden or weak, ` +
                `and does it have implications for non-radiative decay pathways?`);

        sendMessage({
          role: "user",
          content: [{ type: "text", text: prompt }],
        });
      }
    : undefined;

  return (
    <div className="excited-states-viewer" style={{ width: "100%" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          marginBottom: 16,
          paddingBottom: 12,
          borderBottom: "1px solid var(--border)",
        }}
      >
        <div>
          <div
            style={{
              fontSize: 10,
              fontWeight: 500,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              color: "var(--text-muted)",
            }}
          >
            Novo<span style={{ color: "var(--accent)" }}>MCP</span>
          </div>
          <div
            style={{
              fontFamily: "var(--font-serif)",
              fontSize: 18,
              color: "var(--text)",
              marginTop: 4,
            }}
          >
            Excited States (sTDA-xTB)
          </div>
          {smiles && (
            <div
              style={{
                fontSize: 11,
                fontFamily: "var(--font-mono)",
                color: "var(--text-muted)",
                marginTop: 4,
                maxWidth: 460,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
              title={smiles}
            >
              {smiles}
            </div>
          )}
        </div>
        <div style={{ textAlign: "right" }}>
          {data.method && (
            <div style={{ fontSize: 10, color: "var(--text-muted)" }}>{data.method}</div>
          )}
          {data.wall_time_seconds != null && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {data.wall_time_seconds.toFixed(1)} s
            </div>
          )}
        </div>
      </div>

      <KeyEnergiesPanel data={data} />

      {data.excited_states && data.excited_states.length > 0 && (
        <div className="panel" style={{ marginTop: 16 }}>
          <div className="panel-title">
            Singlet / Triplet Ladder
            <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-muted)", marginLeft: 8 }}>
              {askAboutState ? "· click any state to ask" : ""}
            </span>
          </div>
          <StateLadder states={data.excited_states} onStateClick={askAboutState} />
        </div>
      )}

      <AllStatesList states={data.excited_states} onStateClick={askAboutState} />

      {data.warnings && data.warnings.length > 0 && (
        <div
          className="panel"
          style={{ marginTop: 16, borderLeft: "3px solid var(--warning)" }}
        >
          <div className="panel-title">Warnings</div>
          <ul style={{ margin: 0, paddingLeft: 20, fontSize: 12, color: "var(--text)" }}>
            {data.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
