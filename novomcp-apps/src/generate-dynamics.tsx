/**
 * NovoMCP Conformational Dynamics (AlphaFlow) Viewer
 *
 * Two phases:
 *   - Submitted: job_id + "Queued" card. AlphaFlow/ESMFlow takes
 *     1-5 min (plus a ~50s cold-start if ESMFlow unloaded). The
 *     viewer polls get_job_status itself via callServerTool from
 *     @modelcontextprotocol/ext-apps — the "private backend tool"
 *     pattern from FastMCP 3.2 adapted for our stack. Progress is
 *     rendered inline (elapsed time, poll count) without generating
 *     tool-use turns in Claude's conversation.
 *   - Completed: NGL multi-model PDB ensemble (trajectory play),
 *     per-residue RMSF bar chart with click-to-ask, PCA explained
 *     variance, and summary cards (frames, residues, runtime).
 *
 * Why the viewer polls directly rather than relying on the model to
 * call get_job_status: Claude.ai's host drops toolResult when a
 * completed AlphaFlow response exceeds ~250 KB (dominated by the
 * multi-model PDB). callServerTool fetches are not routed through
 * the same host-forwarding path and arrive intact, unlocking the
 * trajectory + RMSF + PCA view that had to be cut when we pulled
 * _meta.ui.resourceUri off get_job_status on 2026-04-21.
 */

import { useEffect, useRef, useState } from "react";
import type { ViewProps } from "./create-app.tsx";
import { BarChart, type Bar } from "./charts.tsx";
import MoleculeRenderer, { type MoleculeRef } from "./molecule-renderer.tsx";
import { useViewData } from "./use-view-data.ts";
import { useJobPoll } from "./use-job-poll.ts";

// =============================================================================
// Types
// =============================================================================

export interface PcaSummary {
  explained_variance_ratio?: number[];
  projections?: number[][];
}

export interface DynamicsData {
  // Input context
  pdb_id?: string;
  sequence?: string;
  name?: string;

  // Submission-phase fields
  job_id?: string;
  status?: string;
  estimated_minutes?: number;
  message?: string;
  n_frames?: number;

  // Completed-phase fields
  sequence_length?: number;
  total_runtime_seconds?: number;
  pdb_ensemble?: string;
  rmsf_per_residue?: number[];
  pca_summary?: PcaSummary;
  generated_at?: string;

  warnings?: string[];
}

type DynamicsProps = ViewProps<DynamicsData>;

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
        Submitting conformational dynamics…
      </div>
    </div>
  );
}

// =============================================================================
// Submitted-phase card
// =============================================================================

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

function SubmittedPanel({
  data,
  elapsedSeconds,
  pollCount,
  phase,
  errorMessage,
}: {
  data: DynamicsData;
  elapsedSeconds: number;
  pollCount: number;
  phase: "queued" | "running" | "polling" | "failed";
  errorMessage?: string | null;
}) {
  const label =
    phase === "failed" ? "Failed" :
    phase === "running" ? "Running" :
    elapsedSeconds > 0 ? "Polling…" :
    "Queued";
  const isFailed = phase === "failed";
  return (
    <div
      className="panel"
      style={isFailed ? { borderLeft: "3px solid var(--danger)" } : undefined}
    >
      <div className="panel-title">Conformational Dynamics Job</div>
      <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
        {!isFailed && <div className="loading-spinner" />}
        <div>
          <div
            style={{
              fontSize: 13,
              color: isFailed ? "var(--danger)" : "var(--text)",
              fontWeight: 500,
            }}
          >
            {label}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
            Job: <code style={{ fontFamily: "var(--font-mono)" }}>{data.job_id || "pending"}</code>
            {data.n_frames != null && ` — ${data.n_frames} frames`}
            {data.pdb_id && ` · ${data.pdb_id}`}
          </div>
          {elapsedSeconds > 0 && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4, fontFamily: "var(--font-mono)" }}>
              elapsed {formatElapsed(elapsedSeconds)} · {pollCount} poll{pollCount === 1 ? "" : "s"}
            </div>
          )}
          {isFailed && errorMessage && (
            <div style={{ fontSize: 11, color: "var(--danger)", marginTop: 6, lineHeight: 1.5 }}>
              {errorMessage}
            </div>
          )}
          {!isFailed && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.5 }}>
              AlphaFlow/ESMFlow typically takes 1–5 minutes. The viewer polls{" "}
              <code style={{ fontFamily: "var(--font-mono)" }}>get_job_status</code> directly
              every 30s — ensemble + RMSF + PCA render here once the job finishes.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// NGL ensemble viewer — multi-model PDB, trajectory play.
// =============================================================================

function EnsembleViewer({ pdb, height }: { pdb: string; height: number }) {
  const trajRef = useRef<any>(null);
  const [nFrames, setNFrames] = useState(0);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(true);

  const handleReady = (ref: MoleculeRef) => {
    trajRef.current = ref.trajectory;
    setNFrames(ref.trajectory?.frameCount ?? 0);
  };

  // Trajectory play loop — cycles through frames ~4 fps.
  useEffect(() => {
    if (!playing || nFrames <= 1) return;
    const id = window.setInterval(() => {
      setFrame((f) => {
        const next = (f + 1) % nFrames;
        try {
          trajRef.current?.setFrame(next);
        } catch { /* ignore transient trajectory errors */ }
        return next;
      });
    }, 250);
    return () => window.clearInterval(id);
  }, [playing, nFrames]);

  const onScrub = (value: number) => {
    setPlaying(false);
    setFrame(value);
    try {
      trajRef.current?.setFrame(value);
    } catch { /* ignore */ }
  };

  return (
    <div style={{ width: "100%" }}>
      <MoleculeRenderer
        pdb={pdb}
        height={height}
        asTrajectory
        representation="cartoon"
        representationParams={{ colorScheme: "residueindex", quality: "medium" }}
        onReady={handleReady}
      />
      {nFrames > 1 && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            marginTop: 8,
            fontSize: 11,
            color: "var(--text-muted)",
          }}
        >
          <button
            onClick={() => setPlaying((p) => !p)}
            style={{
              padding: "4px 10px",
              fontSize: 11,
              fontFamily: "var(--font-mono)",
              background: "var(--bg-warm)",
              border: "1px solid var(--border)",
              borderRadius: 2,
              color: "var(--text)",
              cursor: "pointer",
            }}
          >
            {playing ? "❚❚ pause" : "▶ play"}
          </button>
          <input
            type="range"
            min={0}
            max={nFrames - 1}
            value={frame}
            onChange={(e) => onScrub(parseInt(e.target.value, 10))}
            style={{ flex: 1 }}
          />
          <div style={{ fontFamily: "var(--font-mono)", minWidth: 48, textAlign: "right" }}>
            {frame + 1}/{nFrames}
          </div>
        </div>
      )}
    </div>
  );
}

function EnsemblePanel({ pdb }: { pdb?: string }) {
  const trimmed = pdb?.trim();
  const isValidPdb = !!trimmed && trimmed.includes("MODEL") && trimmed.includes("ATOM");
  if (!isValidPdb) {
    return (
      <div className="panel">
        <div className="panel-title">Conformational Ensemble</div>
        <div
          style={{
            padding: 16,
            background: "var(--bg-warm)",
            borderRadius: 2,
            fontSize: 12,
            color: "var(--text-muted)",
            lineHeight: 1.5,
          }}
        >
          Backend did not return a multi-model PDB ensemble. The RMSF and PCA below
          still describe the generated trajectory.
        </div>
      </div>
    );
  }
  return (
    <div className="panel">
      <div className="panel-title">Conformational Ensemble</div>
      <EnsembleViewer pdb={trimmed!} height={340} />
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6 }}>
        Multi-model PDB from AlphaFlow/ESMFlow diffusion — each frame is one sampled
        conformation. Colored by residue index.
      </div>
    </div>
  );
}

// =============================================================================
// RMSF panel — per-residue flexibility, clickable residues.
// =============================================================================

function RmsfPanel({
  rmsf,
  pdbId,
  sendMessage,
}: {
  rmsf: number[];
  pdbId?: string;
  sendMessage?: DynamicsProps["sendMessage"];
}) {
  if (rmsf.length === 0) return null;

  const mean = rmsf.reduce((a, b) => a + b, 0) / rmsf.length;
  const max = Math.max(...rmsf);
  const flexibleThreshold = mean * 1.5;
  const flexibleResidues = rmsf
    .map((v, i) => ({ idx: i + 1, v }))
    .filter((r) => r.v > flexibleThreshold);

  const askAboutResidue = sendMessage
    ? (idx: number, v: number) => {
        const ctx = pdbId ? ` of ${pdbId}` : "";
        sendMessage({
          role: "user",
          content: [
            {
              type: "text",
              text:
                `I clicked residue ${idx}${ctx} — RMSF ${v.toFixed(3)} nm ` +
                `(ensemble mean ${mean.toFixed(3)} nm, max ${max.toFixed(3)} nm). ` +
                `Is this residue in a flexible loop or a rigid core region, and should ` +
                `its flexibility shape how I design ligands targeting this site (e.g., ` +
                `cryptic pocket near this residue, or avoid as a key anchor point)?`,
            },
          ],
        });
      }
    : undefined;

  const bars: Bar[] = rmsf.map((v, i) => {
    const highlight = v > flexibleThreshold;
    return {
      value: v,
      label: (i + 1) % Math.max(1, Math.floor(rmsf.length / 10)) === 0 ? String(i + 1) : "",
      color: highlight ? "var(--accent)" : "var(--text-muted)",
      onClick: askAboutResidue ? () => askAboutResidue(i + 1, v) : undefined,
      title: `Residue ${i + 1} — RMSF ${v.toFixed(3)} nm${highlight ? " (flexible)" : ""}`,
    };
  });

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div
        className="panel-title"
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
      >
        <span>Per-Residue Flexibility (RMSF)</span>
        <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-muted)" }}>
          {askAboutResidue ? "click any bar to ask" : ""}
        </span>
      </div>
      <BarChart bars={bars} height={160} unit=" nm" />
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.6 }}>
        C-alpha RMSF per residue across the ensemble. Bars above 1.5× mean are highlighted
        — these flexible regions are candidates for cryptic-pocket identification or
        allosteric-site targeting. {flexibleResidues.length} of {rmsf.length} residues
        flagged as flexible.
      </div>
    </div>
  );
}

// =============================================================================
// PCA panel — explained variance of the principal motions.
// =============================================================================

function PcaPanel({ pca }: { pca?: PcaSummary }) {
  const variance = pca?.explained_variance_ratio ?? [];
  if (variance.length === 0) return null;

  const bars: Bar[] = variance.map((v, i) => ({
    value: v * 100,
    label: `PC${i + 1}`,
    color: i === 0 ? "var(--accent)" : "var(--text-muted)",
    title: `PC${i + 1} — ${(v * 100).toFixed(1)}% of motion`,
  }));

  const cumulative = variance.reduce((s, v) => s + v, 0);

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">Principal Components of Motion</div>
      <BarChart bars={bars} height={140} unit="%" />
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.6 }}>
        PCA of the ensemble's Cartesian coordinates. The top {variance.length} components
        capture {(cumulative * 100).toFixed(1)}% of the total motion. A dominant PC1
        (&gt; 40%) indicates a single hinge-like motion; a more even split means
        multi-axis flexibility.
      </div>
    </div>
  );
}

// =============================================================================
// Completed-phase results view — exported for md-results AlphaFlow dispatcher.
// =============================================================================

export function DynamicsResultsView({
  data,
  sendMessage,
}: {
  data: DynamicsData;
  sendMessage?: DynamicsProps["sendMessage"];
}) {
  const rmsf = data.rmsf_per_residue ?? [];
  const seqLen = data.sequence_length ?? rmsf.length;
  const nFrames = data.n_frames;
  const runtime = data.total_runtime_seconds;

  const rmsfMean = rmsf.length > 0 ? rmsf.reduce((a, b) => a + b, 0) / rmsf.length : 0;
  const rmsfMax = rmsf.length > 0 ? Math.max(...rmsf) : 0;
  const flexibleCount = rmsf.filter((v) => v > rmsfMean * 1.5).length;

  return (
    <div className="dynamics-results" style={{ width: "100%" }}>
      {/* Summary cards */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
        {nFrames != null && (
          <div
            style={{
              padding: "10px 14px",
              background: "var(--bg-warm)",
              borderRadius: 2,
              borderLeft: "3px solid var(--accent)",
              minWidth: 100,
            }}
          >
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Frames</div>
            <div style={{ fontSize: 20, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--accent)" }}>
              {nFrames}
            </div>
          </div>
        )}
        {seqLen > 0 && (
          <div
            style={{
              padding: "10px 14px",
              background: "var(--bg-warm)",
              borderRadius: 2,
              borderLeft: "3px solid var(--text-muted)",
              minWidth: 100,
            }}
          >
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Residues</div>
            <div style={{ fontSize: 20, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text)" }}>
              {seqLen}
            </div>
          </div>
        )}
        {rmsf.length > 0 && (
          <>
            <div
              style={{
                padding: "10px 14px",
                background: "var(--bg-warm)",
                borderRadius: 2,
                borderLeft: "3px solid var(--text-muted)",
                minWidth: 110,
              }}
            >
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Mean RMSF</div>
              <div style={{ fontSize: 18, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text)" }}>
                {rmsfMean.toFixed(3)}
                <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 4, fontWeight: 400 }}>nm</span>
              </div>
            </div>
            <div
              style={{
                padding: "10px 14px",
                background: "var(--bg-warm)",
                borderRadius: 2,
                borderLeft: `3px solid ${flexibleCount > 0 ? "var(--accent)" : "var(--text-muted)"}`,
                minWidth: 110,
              }}
            >
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Flexible</div>
              <div style={{ fontSize: 18, fontFamily: "var(--font-mono)", fontWeight: 600, color: flexibleCount > 0 ? "var(--accent)" : "var(--text)" }}>
                {flexibleCount}
                <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 4, fontWeight: 400 }}>res</span>
              </div>
            </div>
          </>
        )}
        {runtime != null && (
          <div
            style={{
              padding: "10px 14px",
              background: "var(--bg-warm)",
              borderRadius: 2,
              borderLeft: "3px solid var(--text-muted)",
              minWidth: 100,
            }}
          >
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Runtime</div>
            <div style={{ fontSize: 18, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text)" }}>
              {runtime < 60 ? `${runtime.toFixed(0)}s` : `${(runtime / 60).toFixed(1)}m`}
            </div>
          </div>
        )}
      </div>

      <EnsemblePanel pdb={data.pdb_ensemble} />
      <RmsfPanel rmsf={rmsf} pdbId={data.pdb_id} sendMessage={sendMessage} />
      <PcaPanel pca={data.pca_summary} />
    </div>
  );
}

// =============================================================================
// Main component — routes between submitted and completed phases
// =============================================================================

function isCompleted(data: DynamicsData): boolean {
  return (
    (data.rmsf_per_residue?.length ?? 0) > 0 ||
    !!data.pdb_ensemble ||
    !!data.pca_summary
  );
}

export default function GenerateDynamicsViewer(props: DynamicsProps) {
  const { toolInputs, toolInputsPartial, toolResult, sendMessage, callServerTool } = props;
  const data = useViewData<DynamicsData>(props);

  // If submission response already carries completion data (sync arrival),
  // skip polling. Otherwise drive our own poll via callServerTool using the
  // job_id from the submission ack. Hook is inert when job_id or
  // callServerTool is missing.
  const alreadyComplete = isCompleted(data);
  const jobId = alreadyComplete ? null : (data.job_id ?? null);
  const poll = useJobPoll<DynamicsData>({
    jobId,
    callServerTool,
    intervalMs: 30_000,
    coldStartGraceSeconds: 90,
  });

  // Merge: prefer polled data once we have it; otherwise render what came
  // with the submission response. Same shape either way.
  const displayData: DynamicsData = poll.data ?? data;
  // Swap to the results view whenever the poll reaches a terminal state
  // (completed / failed), even if isCompleted() is false because the
  // result shape is partial. Prevents the submission spinner from
  // persisting on jobs that finish without a full ensemble payload.
  const jobTerminal = poll.phase === "completed" || poll.phase === "failed";
  const showCompleted = alreadyComplete || isCompleted(displayData) || jobTerminal;

  const isStreaming = !toolInputs && !toolResult;
  if (isStreaming) {
    return <LoadingShimmer />;
  }

  const pdbId = displayData.pdb_id || toolInputs?.pdb_id || toolInputsPartial?.pdb_id;
  const name = displayData.name || pdbId || "Dynamics";

  const submissionPhase: "queued" | "running" | "polling" | "failed" =
    poll.phase === "failed" ? "failed" :
    poll.phase === "running" ? "running" :
    poll.pollCount > 0 ? "polling" :
    "queued";

  return (
    <div className="generate-dynamics-viewer" style={{ width: "100%" }}>
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
            Conformational Dynamics
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
            {name}
            {displayData.n_frames != null && ` · ${displayData.n_frames} frames`}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 10, color: "var(--text-muted)" }}>AlphaFlow / ESMFlow</div>
          {displayData.total_runtime_seconds != null && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {displayData.total_runtime_seconds.toFixed(1)} s
            </div>
          )}
        </div>
      </div>

      {showCompleted ? (
        <DynamicsResultsView data={displayData} sendMessage={sendMessage} />
      ) : (
        <SubmittedPanel
          data={displayData}
          elapsedSeconds={poll.elapsedSeconds}
          pollCount={poll.pollCount}
          phase={submissionPhase}
          errorMessage={poll.error}
        />
      )}

      {displayData.warnings && displayData.warnings.length > 0 && (
        <div
          className="panel"
          style={{ marginTop: 16, borderLeft: "3px solid var(--warning)" }}
        >
          <div className="panel-title">Warnings</div>
          <ul style={{ margin: 0, paddingLeft: 20, fontSize: 12, color: "var(--text)" }}>
            {displayData.warnings?.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
