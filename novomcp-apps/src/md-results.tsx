/**
 * NovoMCP MD Results Visualization
 *
 * Wired exclusively to `run_molecular_dynamics` (sync GROMACS analysis —
 * RMSD, RMSF, equilibration, binding stability). Async-poll dispatch to
 * AlphaFlow / conformer-search / NEB / docking / structure / lead-opt sub-
 * views was removed 2026-04-21: the host does not reliably forward
 * `toolResult` for polymorphic `get_job_status` polls, so every attempt
 * rendered an empty "analysis data not rendered inline" fallback.
 * Submission-phase viewers (run_conformer_search, generate_dynamics,
 * find_transition_state) remain wired to their own tools and still work;
 * completion analysis for async jobs is delivered via Claude's text
 * response which has full access to content[0].text.
 */
import { useState } from "react";
import type { ViewProps } from "./create-app.tsx";

// =============================================================================
// Types
// =============================================================================

interface TimeSeries {
  mean: number;
  std: number;
  final: number;
  last_quarter_mean?: number;
  stable: boolean;
  trajectory: number[][];
}

interface Equilibration {
  nvt_temperature?: TimeSeries;
  npt_pressure?: TimeSeries;
  npt_density?: TimeSeries;
  production_potential_energy?: TimeSeries;
  production_temperature?: TimeSeries;
}

interface RmsdData {
  mean_nm: number;
  max_nm: number;
  final_nm: number;
  trajectory: number[][];
}

interface LigandDynamics {
  ligand_rmsd?: {
    mean_nm: number;
    max_nm: number;
    final_nm: number;
    trajectory: number[][];
  };
  hbond_persistence?: {
    mean_count: number;
    max_count: number;
    any_contact_persistence: number;
    quality: string;  // "High Quality" | "Moderate" | "False Positive Risk"
    n_frames: number;
  };
  pose_clusters?: {
    n_clusters: number;
    dominant_cluster_pct: number;
    clusters: Array<{ cluster_id: number; frames: number; population_pct: number }>;
    stable_pose: boolean;
  };
  error?: string;
}

interface MmgbsaResult {
  mean_kcal_mol?: number;
  std_kcal_mol?: number;
  n_frames?: number;
  method?: string;
  per_residue_top10?: Array<{ residue: string; contribution_kcal_mol: number }>;
  trajectory?: number[][];
  skipped?: boolean;
  reason?: string;
  error?: string;
}

interface Analysis {
  rmsd?: RmsdData;
  rmsf?: { mean_nm: number; max_nm: number; max_residue: number; n_flexible_residues: number };
  radius_of_gyration?: { mean_nm: number; std_nm: number; stable: boolean };
  equilibration?: Equilibration;
  // Theo P1: binding-specific analyses (only present when ligand docked into protein)
  mmgbsa?: MmgbsaResult;
  ligand_dynamics?: LigandDynamics;
}

interface MdResultPayload {
  analysis?: Analysis;
  simulation_ns?: number;
  temperature?: number;
  pressure?: number;
  compound_id?: string;
  simulation_completed?: boolean;
  output_files?: string[];
}

interface MdResultsInput {
  job_id?: string;
  status?: string;
  compound_id?: string;
  simulation_ns?: number;
  temperature?: number;
  pressure?: number;
  analysis?: Analysis;
  // get_job_status wraps the MD payload under `results` (plural) in the MCP
  // response, while the raw gromacs-md /results/{id} endpoint returns it under
  // `result` (singular). Accept both so the viewer works with either shape.
  results?: MdResultPayload;
  result?: MdResultPayload;
  progress?: number;
  message?: string;
}

type MdResultsProps = ViewProps<MdResultsInput>;

// =============================================================================
// SVG Line Chart
// =============================================================================

function LineChart({
  data,
  width = 460,
  height = 140,
  xLabel,
  yLabel,
  color = "var(--accent)",
  refLine,
  refLabel,
}: {
  data: number[][];
  width?: number;
  height?: number;
  xLabel?: string;
  yLabel?: string;
  color?: string;
  refLine?: number;
  refLabel?: string;
}) {
  if (!data || data.length < 2) {
    return (
      <div style={{ width, height, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)", fontSize: 12 }}>
        No data
      </div>
    );
  }

  const pad = { top: 10, right: 12, bottom: 28, left: 52 };
  const w = width - pad.left - pad.right;
  const h = height - pad.top - pad.bottom;

  const xs = data.map((d) => d[0]);
  const ys = data.map((d) => d[1]);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const yMin = Math.min(...ys, refLine ?? Infinity);
  const yMax = Math.max(...ys, refLine ?? -Infinity);
  const yRange = yMax - yMin || 1;
  const yPad = yRange * 0.08;

  const sx = (v: number) => pad.left + ((v - xMin) / (xMax - xMin || 1)) * w;
  const sy = (v: number) => pad.top + h - ((v - (yMin - yPad)) / (yRange + 2 * yPad)) * h;

  // Downsample for rendering if too many points
  const maxPoints = 200;
  const step = Math.max(1, Math.floor(data.length / maxPoints));
  const sampled = data.filter((_, i) => i % step === 0 || i === data.length - 1);

  const pathD = sampled
    .map((d, i) => `${i === 0 ? "M" : "L"}${sx(d[0]).toFixed(1)},${sy(d[1]).toFixed(1)}`)
    .join(" ");

  // Y-axis ticks (5 ticks)
  const yTicks: number[] = [];
  for (let i = 0; i <= 4; i++) {
    yTicks.push(yMin - yPad + ((yRange + 2 * yPad) * i) / 4);
  }

  function formatTick(v: number): string {
    if (Math.abs(v) >= 10000) return (v / 1000).toFixed(0) + "k";
    if (Math.abs(v) >= 100) return v.toFixed(0);
    if (Math.abs(v) >= 1) return v.toFixed(1);
    return v.toFixed(3);
  }

  return (
    <svg width={width} height={height} style={{ display: "block", overflow: "visible" }}>
      {/* Grid lines */}
      {yTicks.map((v, i) => (
        <line
          key={i}
          x1={pad.left}
          x2={pad.left + w}
          y1={sy(v)}
          y2={sy(v)}
          stroke="var(--border)"
          strokeWidth={0.5}
        />
      ))}

      {/* Reference line */}
      {refLine !== undefined && (
        <>
          <line
            x1={pad.left}
            x2={pad.left + w}
            y1={sy(refLine)}
            y2={sy(refLine)}
            stroke="var(--text-muted)"
            strokeWidth={1}
            strokeDasharray="4,3"
          />
          {refLabel && (
            <text
              x={pad.left + w - 2}
              y={sy(refLine) - 4}
              textAnchor="end"
              fontSize={9}
              fill="var(--text-muted)"
            >
              {refLabel}
            </text>
          )}
        </>
      )}

      {/* Data line */}
      <path d={pathD} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />

      {/* Y-axis labels */}
      {yTicks.map((v, i) => (
        <text
          key={i}
          x={pad.left - 6}
          y={sy(v) + 3}
          textAnchor="end"
          fontSize={9}
          fill="var(--text-muted)"
          fontFamily="var(--font-mono)"
        >
          {formatTick(v)}
        </text>
      ))}

      {/* X-axis label */}
      {xLabel && (
        <text
          x={pad.left + w / 2}
          y={height - 4}
          textAnchor="middle"
          fontSize={9}
          fill="var(--text-muted)"
        >
          {xLabel}
        </text>
      )}

      {/* Y-axis label */}
      {yLabel && (
        <text
          x={12}
          y={pad.top + h / 2}
          textAnchor="middle"
          fontSize={9}
          fill="var(--text-muted)"
          transform={`rotate(-90, 12, ${pad.top + h / 2})`}
        >
          {yLabel}
        </text>
      )}
    </svg>
  );
}

// =============================================================================
// Stability Badge
// =============================================================================

function StabilityBadge({ stable, label }: { stable: boolean; label?: string }) {
  return (
    <span
      className={`badge ${stable ? "success" : "warning"}`}
    >
      {stable ? "✓" : "⚠"} {label || (stable ? "Converged" : "Not converged")}
    </span>
  );
}

// =============================================================================
// Chart Panel
// =============================================================================

function ChartPanel({
  title,
  series,
  xLabel,
  yLabel,
  color,
  refLine,
  refLabel,
  unit,
}: {
  title: string;
  series: TimeSeries;
  xLabel?: string;
  yLabel?: string;
  color?: string;
  refLine?: number;
  refLabel?: string;
  unit?: string;
}) {
  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div className="panel-title" style={{ marginBottom: 0 }}>{title}</div>
        <StabilityBadge stable={series.stable} />
      </div>
      <LineChart
        data={series.trajectory}
        xLabel={xLabel || "Time (ps)"}
        yLabel={yLabel}
        color={color}
        refLine={refLine}
        refLabel={refLabel}
      />
      <div
        style={{
          display: "flex",
          gap: 16,
          marginTop: 8,
          fontSize: 11,
          color: "var(--text-soft)",
        }}
      >
        <span>Mean: <strong>{series.mean.toFixed(2)}</strong>{unit ? ` ${unit}` : ""}</span>
        <span>Std: <strong>{series.std.toFixed(2)}</strong></span>
        <span>Final: <strong>{series.final.toFixed(2)}</strong>{unit ? ` ${unit}` : ""}</span>
      </div>
    </div>
  );
}

// =============================================================================
// Loading / Running State
// =============================================================================

function RunningState({ data }: { data: MdResultsInput }) {
  const progress = data.progress || 0;
  const message = data.message || "Simulation in progress...";

  return (
    <div style={{ width: "100%", maxWidth: 500, margin: "0 auto", padding: 24, fontFamily: "var(--font-sans)" }}>
      <div style={{ textAlign: "center", marginBottom: 24 }}>
        <div style={{ fontSize: 32, marginBottom: 8 }}>⚗️</div>
        <div style={{ fontSize: 14, fontWeight: 500, color: "var(--text)" }}>
          {data.job_id || "MD Simulation"}
        </div>
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>
          {message}
        </div>
      </div>

      {/* Progress bar */}
      <div style={{
        width: "100%",
        height: 20,
        background: "var(--bg-warm)",
        borderRadius: 10,
        overflow: "hidden",
        border: "1px solid var(--border)",
      }}>
        <div style={{
          width: `${progress}%`,
          height: "100%",
          background: `linear-gradient(90deg, var(--accent), var(--success))`,
          borderRadius: 10,
          transition: "width 0.5s ease-out",
        }} />
      </div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", textAlign: "center", marginTop: 6 }}>
        {progress}% complete
      </div>
    </div>
  );
}


// =============================================================================
// Main Component
// =============================================================================

/**
 * Recursively walk a value looking for an object with a recognizable MD
 * analysis shape (has `rmsd`, `rmsf`, `radius_of_gyration`, or `equilibration`
 * as keys). Returns the first match, or null.
 *
 * Depth-limited to avoid runaway traversal on pathological inputs.
 *
 * This is a safety net for when the response wrapping changes between
 * novomcp / gromacs-md / the MCP SDK and none of the explicit fallback
 * paths match. It guarantees that if analysis data exists anywhere in the
 * response tree, we find it.
 */
function findAnalysis(value: unknown, depth = 0): Analysis | null {
  if (depth > 6) return null;
  if (!value || typeof value !== "object") return null;
  const obj = value as Record<string, unknown>;

  // Direct match: this object looks like an Analysis
  if (
    "rmsd" in obj ||
    "rmsf" in obj ||
    "radius_of_gyration" in obj ||
    "equilibration" in obj
  ) {
    // Only accept if rmsd/rmsf are objects, not just keys with null values
    const rmsd = obj.rmsd;
    const rmsf = obj.rmsf;
    const rog = obj.radius_of_gyration;
    const eq = obj.equilibration;
    const hasRealData =
      (rmsd && typeof rmsd === "object") ||
      (rmsf && typeof rmsf === "object") ||
      (rog && typeof rog === "object") ||
      (eq && typeof eq === "object");
    if (hasRealData) return obj as unknown as Analysis;
  }

  // Recurse into object children
  for (const v of Object.values(obj)) {
    const found = findAnalysis(v, depth + 1);
    if (found) return found;
  }
  return null;
}

/**
 * Walk a value tree looking for the first object key matching one of `keys`.
 * Used to pull simulation_ns, temperature, compound_id, etc. out of whatever
 * nesting level the response happens to put them at.
 */
function findField<T = unknown>(value: unknown, keys: string[], depth = 0): T | undefined {
  if (depth > 6) return undefined;
  if (!value || typeof value !== "object") return undefined;
  const obj = value as Record<string, unknown>;
  for (const k of keys) {
    if (k in obj && obj[k] !== undefined && obj[k] !== null) return obj[k] as T;
  }
  for (const v of Object.values(obj)) {
    const found = findField<T>(v, keys, depth + 1);
    if (found !== undefined) return found;
  }
  return undefined;
}

export default function MdResultsViewer({
  toolInputs,
  toolInputsPartial,
  toolResult,
  sendMessage,
}: MdResultsProps) {
  const isStreaming = !toolInputs && !!toolInputsPartial;
  const [activeTab, setActiveTab] = useState<"convergence" | "equilibration">("convergence");
  const [showDebug, setShowDebug] = useState(false);

  // Extract data from toolResult (structuredContent preferred, content text as
  // fallback). We try hard here because schema validation, SDK version
  // differences, or upstream wrapping changes can put the payload in several
  // places.
  let data: MdResultsInput | null = null;
  let dataSource: string = "none";

  if (toolResult) {
    const result = toolResult as any;

    // Path 1: structuredContent (the happy path — matches outputSchema)
    if (result?.structuredContent && typeof result.structuredContent === "object") {
      data = result.structuredContent;
      dataSource = "structuredContent";
    }

    // Path 2: content[0].text (JSON-stringified response body)
    //
    // Some SDK versions strip structuredContent when Zod validation fails
    // silently. The text block is always set, so parse it as a fallback.
    if (!data && result?.content?.[0]?.text) {
      try {
        data = JSON.parse(result.content[0].text);
        dataSource = "content[0].text";
      } catch { /* fall through */ }
    }

    // Path 3: content array concatenated text
    if (!data && Array.isArray(result?.content)) {
      for (const block of result.content) {
        if (block?.type === "text" && typeof block.text === "string") {
          try {
            const parsed = JSON.parse(block.text);
            if (parsed && typeof parsed === "object") {
              data = parsed;
              dataSource = "content[].text";
              break;
            }
          } catch { /* keep trying */ }
        }
      }
    }
  }

  // Last resort: tool input args (just the job_id, usually)
  if (!data && toolInputs) {
    data = toolInputs;
    dataSource = "toolInputs";
  }

  if (isStreaming || !data) {
    return (
      <div className="loading">
        <div className="loading-spinner" />
        <span>Loading MD results...</span>
      </div>
    );
  }

  // Handle running/queued jobs
  const status = data.status;
  if (status && status !== "completed" && status !== "success") {
    return <RunningState data={data} />;
  }

  // This viewer is wired exclusively to run_molecular_dynamics — a
  // synchronous GROMACS tool that returns the MD analysis block directly
  // in its response. Async-poll dispatch to AlphaFlow / conformer / NEB /
  // docking / structure / lead-opt sub-views was removed 2026-04-21; those
  // completions are rendered by Claude's text response now.
  const results = (data as any).results || (data as any).result;

  // Analysis may live at multiple nesting levels depending on the response source:
  //   - data.analysis                        (flat — legacy or direct paste)
  //   - data.results.analysis                (get_job_status MCP response, plural)
  //   - data.result.analysis                 (raw gromacs-md /results/{id}, singular)
  //   - data.results.result.analysis         (double-wrapped when MCP forwards raw)
  //   - data.data.*.analysis                 (some SDK versions wrap in .data)
  //
  // As a final safety net, we recursively search the whole response tree for
  // any object that looks like an Analysis (has rmsd/rmsf/rog/equilibration).
  // This guarantees we find the data even if the wrapping shape changes again.
  const analysis: Analysis | null =
    data.analysis ||
    data.results?.analysis ||
    data.result?.analysis ||
    (data.results as { result?: MdResultPayload })?.result?.analysis ||
    findAnalysis(data);

  // Pull metadata from wherever it lives in the response tree
  const simNs = findField<number>(data, ["simulation_ns"]);
  const targetTemp = findField<number>(data, ["temperature"]) || 300;
  const compoundId = findField<string>(data, ["compound_id"]);

  // Graceful degradation: if we couldn't find the analysis object but the job
  // is clearly completed, show a minimal "completed" card with whatever
  // metadata we have, plus a debug expander so future failures are diagnosable
  // without a redeploy.
  if (!analysis) {
    const topKeys = Object.keys(data);
    const hasResults = !!(data.results || data.result);
    return (
      <div style={{ width: "100%", maxWidth: 520, margin: "0 auto", padding: 20, fontFamily: "var(--font-sans)" }}>
        <div style={{ marginBottom: 16, paddingBottom: 12, borderBottom: "1px solid var(--border)" }}>
          <div style={{ fontSize: 10, fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-muted)", marginBottom: 4 }}>
            Molecular Dynamics Results
          </div>
          <div style={{ fontSize: 16, fontWeight: 600, color: "var(--text)" }}>
            {data.job_id || compoundId || "Simulation"}
          </div>
          {simNs && (
            <div style={{ fontSize: 12, color: "var(--text-soft)", marginTop: 2 }}>
              {simNs} ns at {targetTemp} K
            </div>
          )}
        </div>

        <div style={{
          padding: 14,
          background: "rgba(212, 164, 90, 0.08)",
          border: "1px solid rgba(212, 164, 90, 0.25)",
          borderRadius: 4,
          marginBottom: 12,
        }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: "var(--warning)", marginBottom: 4 }}>
            Job completed — analysis data not rendered inline
          </div>
          <div style={{ fontSize: 11, color: "var(--text-soft)", lineHeight: 1.5 }}>
            {hasResults
              ? "The results payload is present but analysis metrics (RMSD, RMSF, radius of gyration, equilibration) were not found at any expected location in the response tree. The text summary above should contain the actual values."
              : "No results payload was returned for this job. The job may have completed before analysis data was written, or the results endpoint timed out. Try calling get_job_status again."}
          </div>
        </div>

        <button
          onClick={() => setShowDebug(!showDebug)}
          style={{
            fontSize: 10,
            color: "var(--text-muted)",
            background: "transparent",
            border: "1px solid var(--border)",
            borderRadius: 2,
            padding: "4px 8px",
            cursor: "pointer",
            fontFamily: "var(--font-mono)",
          }}
        >
          {showDebug ? "Hide" : "Show"} response shape
        </button>

        {showDebug && (
          <div style={{
            marginTop: 10,
            padding: 10,
            background: "var(--bg-warm)",
            borderRadius: 2,
            fontSize: 10,
            fontFamily: "var(--font-mono)",
            color: "var(--text-soft)",
            maxHeight: 300,
            overflow: "auto",
          }}>
            <div style={{ marginBottom: 6, color: "var(--text-muted)" }}>
              Data source: <span style={{ color: "var(--text)" }}>{dataSource}</span>
            </div>
            <div style={{ marginBottom: 6, color: "var(--text-muted)" }}>
              Top-level keys: <span style={{ color: "var(--text)" }}>{topKeys.join(", ") || "(none)"}</span>
            </div>
            <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-all", fontSize: 9 }}>
              {JSON.stringify(data, null, 2).slice(0, 2000)}
              {JSON.stringify(data, null, 2).length > 2000 ? "\n... (truncated)" : ""}
            </pre>
          </div>
        )}
      </div>
    );
  }

  const rmsd = analysis.rmsd;
  const rmsf = analysis.rmsf;
  const rog = analysis.radius_of_gyration;
  const eq = analysis.equilibration;
  const hasEquilibration = eq && (eq.nvt_temperature || eq.npt_pressure || eq.npt_density || eq.production_potential_energy);
  // Theo P1: binding-specific analyses
  const mmgbsa = analysis.mmgbsa;
  const ligand = analysis.ligand_dynamics;
  const hasBindingData = Boolean(mmgbsa || ligand);

  return (
    <div style={{ width: "100%", maxWidth: 520, margin: "0 auto", padding: 20, fontFamily: "var(--font-sans)" }}>
      {/* Header */}
      <div style={{ marginBottom: 20, paddingBottom: 12, borderBottom: "1px solid var(--border)" }}>
        <div style={{
          fontSize: 10, fontWeight: 500, textTransform: "uppercase",
          letterSpacing: "0.06em", color: "var(--text-muted)", marginBottom: 4,
        }}>
          Molecular Dynamics Results
        </div>
        <div style={{ fontSize: 16, fontWeight: 600, color: "var(--text)" }}>
          {data.job_id || compoundId || "Simulation"}
        </div>
        {simNs && (
          <div style={{ fontSize: 12, color: "var(--text-soft)", marginTop: 2 }}>
            {simNs} ns at {targetTemp} K
          </div>
        )}
      </div>

      {/* Summary cards */}
      <div style={{ display: "flex", gap: 10, marginBottom: 16, flexWrap: "wrap" }}>
        {rmsd && (
          <div className="panel" style={{ flex: "1 1 100px", textAlign: "center", padding: 12 }}>
            <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>RMSD</div>
            <div style={{ fontSize: 20, fontWeight: 600, color: "var(--text)", marginTop: 4 }}>
              {rmsd.mean_nm.toFixed(3)}
            </div>
            <div style={{ fontSize: 10, color: "var(--text-muted)" }}>nm (mean)</div>
          </div>
        )}
        {rmsf && (
          <div className="panel" style={{ flex: "1 1 100px", textAlign: "center", padding: 12 }}>
            <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Flexible</div>
            <div style={{ fontSize: 20, fontWeight: 600, color: "var(--text)", marginTop: 4 }}>
              {rmsf.n_flexible_residues}
            </div>
            <div style={{ fontSize: 10, color: "var(--text-muted)" }}>residues (&gt;0.2 nm)</div>
          </div>
        )}
        {rog && (
          <div className="panel" style={{ flex: "1 1 100px", textAlign: "center", padding: 12 }}>
            <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Rg</div>
            <div style={{ fontSize: 20, fontWeight: 600, color: rog.stable ? "var(--success)" : "var(--warning)", marginTop: 4 }}>
              {rog.mean_nm.toFixed(3)}
            </div>
            <div style={{ fontSize: 10, color: "var(--text-muted)" }}>nm {rog.stable ? "(stable)" : "(unstable)"}</div>
          </div>
        )}
      </div>

      {/* Tab bar */}
      {hasEquilibration && (
        <div style={{ display: "flex", gap: 0, marginBottom: 16, border: "1px solid var(--border)", borderRadius: 2, overflow: "hidden" }}>
          <button
            onClick={() => setActiveTab("convergence")}
            className={`btn ${activeTab === "convergence" ? "active" : ""}`}
            style={{ flex: 1, borderRadius: 0, border: "none", borderRight: "1px solid var(--border)" }}
          >
            Convergence
          </button>
          <button
            onClick={() => setActiveTab("equilibration")}
            className={`btn ${activeTab === "equilibration" ? "active" : ""}`}
            style={{ flex: 1, borderRadius: 0, border: "none" }}
          >
            Equilibration
          </button>
        </div>
      )}

      {/* Convergence tab — RMSD + production energy */}
      {(activeTab === "convergence" || !hasEquilibration) && (
        <div>
          {rmsd?.trajectory && (
            <div className="panel" style={{ marginBottom: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <div className="panel-title" style={{ marginBottom: 0 }}>RMSD</div>
                <StabilityBadge
                  stable={rmsd.trajectory.length > 10 &&
                    Math.abs(rmsd.trajectory[rmsd.trajectory.length - 1][1] - rmsd.mean_nm) < rmsd.mean_nm * 0.3}
                  label={rmsd.trajectory.length > 10 &&
                    Math.abs(rmsd.trajectory[rmsd.trajectory.length - 1][1] - rmsd.mean_nm) < rmsd.mean_nm * 0.3
                    ? "Converged" : "Drifting"}
                />
              </div>
              <LineChart
                data={rmsd.trajectory}
                xLabel="Time (ns)"
                yLabel="RMSD (nm)"
                color="var(--accent)"
              />
              <div style={{ display: "flex", gap: 16, marginTop: 8, fontSize: 11, color: "var(--text-soft)" }}>
                <span>Mean: <strong>{rmsd.mean_nm.toFixed(4)}</strong> nm</span>
                <span>Max: <strong>{rmsd.max_nm.toFixed(4)}</strong> nm</span>
                <span>Final: <strong>{rmsd.final_nm.toFixed(4)}</strong> nm</span>
              </div>
            </div>
          )}

          {eq?.production_potential_energy && (
            <ChartPanel
              title="Potential Energy"
              series={eq.production_potential_energy}
              xLabel="Time (ps)"
              yLabel="Energy (kJ/mol)"
              color="var(--success)"
              unit="kJ/mol"
            />
          )}

          {eq?.production_temperature && (
            <ChartPanel
              title="Production Temperature"
              series={eq.production_temperature}
              xLabel="Time (ps)"
              yLabel="Temperature (K)"
              color="#e07c4b"
              refLine={targetTemp}
              refLabel={`${targetTemp} K`}
              unit="K"
            />
          )}
        </div>
      )}

      {/* Equilibration tab — NVT temp, NPT pressure, NPT density */}
      {activeTab === "equilibration" && hasEquilibration && (
        <div>
          {eq?.nvt_temperature && (
            <ChartPanel
              title="NVT — Temperature"
              series={eq.nvt_temperature}
              xLabel="Time (ps)"
              yLabel="Temperature (K)"
              color="#e07c4b"
              refLine={targetTemp}
              refLabel={`Target ${targetTemp} K`}
              unit="K"
            />
          )}

          {eq?.npt_pressure && (
            <ChartPanel
              title="NPT — Pressure"
              series={eq.npt_pressure}
              xLabel="Time (ps)"
              yLabel="Pressure (bar)"
              color="#5b7fa5"
              refLine={1.0}
              refLabel="1 bar"
              unit="bar"
            />
          )}

          {eq?.npt_density && (
            <ChartPanel
              title="NPT — Density"
              series={eq.npt_density}
              xLabel="Time (ps)"
              yLabel="Density (kg/m³)"
              color="#8b6fb0"
              refLine={1000}
              refLabel="1000 kg/m³"
              unit="kg/m³"
            />
          )}
        </div>
      )}

      {/* Theo P1: Binding Stability Panel (MM-GBSA + ligand dynamics) */}
      {hasBindingData && (
        <div style={{ marginTop: 20, paddingTop: 16, borderTop: "1px solid var(--border)" }}>
          <div
            style={{
              fontSize: 11,
              fontWeight: 600,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              color: "var(--text-muted)",
              marginBottom: 12,
            }}
          >
            Binding Stability
          </div>

          {/* MM-GBSA — ΔG_bind + per-residue decomposition */}
          {mmgbsa && (
            <div className="panel" style={{ marginBottom: 12, padding: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)" }}>
                  MM-GBSA Binding Free Energy
                </div>
                {mmgbsa.method && (
                  <div style={{ fontSize: 9, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                    {mmgbsa.method}
                  </div>
                )}
              </div>

              {mmgbsa.skipped && (
                <div
                  style={{
                    padding: "8px 10px",
                    background: "var(--bg-warm)",
                    borderLeft: "3px solid var(--warning)",
                    fontSize: 11,
                    color: "var(--text-muted)",
                  }}
                >
                  <strong>Skipped:</strong> {mmgbsa.reason || "see job output"}
                </div>
              )}

              {mmgbsa.error && !mmgbsa.skipped && (
                <div style={{ padding: "8px 10px", background: "var(--danger-bg)", fontSize: 11, color: "var(--danger)" }}>
                  <strong>Error:</strong> {mmgbsa.error}
                </div>
              )}

              {typeof mmgbsa.mean_kcal_mol === "number" && (
                <>
                  <div style={{ display: "flex", gap: 12, marginBottom: 10 }}>
                    <div style={{ flex: "1", textAlign: "center" }}>
                      <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase" }}>ΔG_bind</div>
                      <div
                        style={{
                          fontSize: 22,
                          fontWeight: 600,
                          color: mmgbsa.mean_kcal_mol < -20
                            ? "var(--success)"
                            : mmgbsa.mean_kcal_mol < -10
                              ? "var(--accent)"
                              : "var(--warning)",
                        }}
                      >
                        {mmgbsa.mean_kcal_mol.toFixed(1)}
                      </div>
                      <div style={{ fontSize: 9, color: "var(--text-muted)" }}>kcal/mol</div>
                    </div>
                    {typeof mmgbsa.std_kcal_mol === "number" && (
                      <div style={{ flex: "1", textAlign: "center" }}>
                        <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase" }}>Std Dev</div>
                        <div style={{ fontSize: 22, fontWeight: 600, color: "var(--text)" }}>
                          ± {mmgbsa.std_kcal_mol.toFixed(1)}
                        </div>
                        <div style={{ fontSize: 9, color: "var(--text-muted)" }}>{mmgbsa.n_frames || "?"} frames</div>
                      </div>
                    )}
                  </div>

                  {/* Per-residue top contributors */}
                  {mmgbsa.per_residue_top10 && mmgbsa.per_residue_top10.length > 0 && (
                    <>
                      <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 6 }}>
                        Top Residue Contributors
                      </div>
                      {(() => {
                        const maxAbs = Math.max(
                          ...mmgbsa.per_residue_top10!.map((r) => Math.abs(r.contribution_kcal_mol)),
                          0.001
                        );
                        return (
                          <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                            {mmgbsa.per_residue_top10!.map((r, idx) => {
                              const pct = (Math.abs(r.contribution_kcal_mol) / maxAbs) * 100;
                              const isFavorable = r.contribution_kcal_mol < 0;
                              return (
                                <div
                                  key={idx}
                                  style={{ display: "grid", gridTemplateColumns: "80px 1fr 60px", gap: 6, alignItems: "center", fontSize: 11 }}
                                >
                                  <div style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>
                                    {r.residue}
                                  </div>
                                  <div style={{ background: "var(--bg-warm)", borderRadius: 2, height: 14, position: "relative" }}>
                                    <div
                                      style={{
                                        position: "absolute",
                                        left: 0,
                                        top: 0,
                                        height: "100%",
                                        width: `${pct}%`,
                                        background: isFavorable ? "var(--success)" : "var(--danger)",
                                        borderRadius: 2,
                                      }}
                                    />
                                  </div>
                                  <div
                                    style={{
                                      textAlign: "right",
                                      fontFamily: "var(--font-mono)",
                                      color: isFavorable ? "var(--success)" : "var(--danger)",
                                    }}
                                  >
                                    {r.contribution_kcal_mol > 0 ? "+" : ""}
                                    {r.contribution_kcal_mol.toFixed(2)}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        );
                      })()}
                    </>
                  )}
                </>
              )}
            </div>
          )}

          {/* Ligand RMSD + H-bond persistence + pose clusters */}
          {ligand && (
            <div className="panel" style={{ padding: 12 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", marginBottom: 8 }}>
                Ligand Dynamics
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, fontSize: 11 }}>
                {/* Ligand RMSD */}
                {ligand.ligand_rmsd && (
                  <div style={{ padding: 8, background: "var(--bg-warm)", borderRadius: 2 }}>
                    <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase" }}>
                      Ligand RMSD
                    </div>
                    <div style={{ fontSize: 16, fontWeight: 600, color: "var(--text)", marginTop: 2 }}>
                      {ligand.ligand_rmsd.mean_nm.toFixed(3)}
                    </div>
                    <div style={{ fontSize: 9, color: "var(--text-muted)" }}>
                      nm mean · max {ligand.ligand_rmsd.max_nm.toFixed(3)}
                    </div>
                  </div>
                )}
                {/* H-bond persistence with Theo's quality thresholds */}
                {ligand.hbond_persistence && (
                  <div
                    style={{
                      padding: 8,
                      background: "var(--bg-warm)",
                      borderRadius: 2,
                      borderLeft: `3px solid ${
                        ligand.hbond_persistence.quality === "High Quality"
                          ? "var(--success)"
                          : ligand.hbond_persistence.quality === "False Positive Risk"
                            ? "var(--danger)"
                            : "var(--warning)"
                      }`,
                    }}
                  >
                    <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase" }}>
                      H-bond Persistence
                    </div>
                    <div style={{ fontSize: 16, fontWeight: 600, color: "var(--text)", marginTop: 2 }}>
                      {(ligand.hbond_persistence.any_contact_persistence * 100).toFixed(0)}%
                    </div>
                    <div
                      style={{
                        fontSize: 9,
                        color:
                          ligand.hbond_persistence.quality === "High Quality"
                            ? "var(--success)"
                            : ligand.hbond_persistence.quality === "False Positive Risk"
                              ? "var(--danger)"
                              : "var(--warning)",
                      }}
                    >
                      {ligand.hbond_persistence.quality}
                    </div>
                  </div>
                )}
                {/* Pose clusters — dominant population % */}
                {ligand.pose_clusters && (
                  <div
                    style={{
                      padding: 8,
                      background: "var(--bg-warm)",
                      borderRadius: 2,
                      borderLeft: `3px solid ${ligand.pose_clusters.stable_pose ? "var(--success)" : "var(--warning)"}`,
                    }}
                    title={`${ligand.pose_clusters.n_clusters} pose cluster${
                      ligand.pose_clusters.n_clusters !== 1 ? "s" : ""
                    } (1.5 Å cutoff); dominant = ${ligand.pose_clusters.dominant_cluster_pct}% of frames`}
                  >
                    <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase" }}>
                      Dominant Pose
                    </div>
                    <div style={{ fontSize: 16, fontWeight: 600, color: "var(--text)", marginTop: 2 }}>
                      {ligand.pose_clusters.dominant_cluster_pct.toFixed(0)}%
                    </div>
                    <div
                      style={{
                        fontSize: 9,
                        color: ligand.pose_clusters.stable_pose ? "var(--success)" : "var(--warning)",
                      }}
                    >
                      {ligand.pose_clusters.stable_pose ? "Stable" : `${ligand.pose_clusters.n_clusters} clusters`}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
