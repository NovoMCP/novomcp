/**
 * NovoMCP Transition State (NEB) Viewer
 *
 * Handles both phases of find_transition_state:
 *   - Submitted: just job_id + status. Shows a "Queued" panel.
 *   - Completed: activation barriers (forward + reverse), MEP line plot
 *                with click-to-ask on each image, optional TS geometry
 *                rendered via NGL (dynamic import, only loaded when
 *                ts_geometry_xyz is present).
 *
 * Used directly as the viewer for find_transition_state (submission
 * ack), and also mounted by md-results.tsx's neb_ dispatcher when a
 * completed NEB job is polled via get_job_status.
 */

import type { ViewProps } from "./create-app.tsx";
import { LinePlot, type Point } from "./charts.tsx";
import MoleculeRenderer from "./molecule-renderer.tsx";
import { useViewData } from "./use-view-data.ts";
import { useJobPoll } from "./use-job-poll.ts";

// =============================================================================
// Types
// =============================================================================

export interface TransitionStateData {
  // Submission-phase fields
  job_id?: string;
  status?: string;
  message?: string;

  // Completed-phase fields
  activation_energy_kcal?: number;
  activation_energy_ev?: number;
  reverse_barrier_kcal?: number;
  reverse_barrier_ev?: number;
  ts_energy_ev?: number;
  reactant_energy_ev?: number;
  product_energy_ev?: number;
  ts_geometry_xyz?: string;
  mep_energies_ev?: number[];
  mep_energies_kcal?: number[];
  n_images?: number;
  n_steps?: number;
  converged?: boolean;
  method?: string;
  wall_time_seconds?: number;
  warnings?: string[];

  // Input context (for click prompts)
  reactant_smiles?: string;
  product_smiles?: string;
}

type TransitionStateProps = ViewProps<TransitionStateData>;

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
        Submitting NEB transition state search…
      </div>
    </div>
  );
}

// =============================================================================
// Submitted Phase — NEB job queued, poll via get_job_status
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
  data: TransitionStateData;
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
      <div className="panel-title">NEB Transition State Search</div>
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
            {data.n_images != null && ` — ${data.n_images} NEB images`}
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
              NEB typically takes 1–10 minutes. The viewer polls{" "}
              <code style={{ fontFamily: "var(--font-mono)" }}>get_job_status</code> directly
              every 30s — barriers, MEP, and TS geometry render here once the job finishes.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// Activation Barrier Cards — forward & reverse
// =============================================================================

function BarrierStat({
  label,
  kcal,
  ev,
  color,
  onClick,
  clickTitle,
}: {
  label: string;
  kcal?: number;
  ev?: number;
  color: string;
  onClick?: () => void;
  clickTitle?: string;
}) {
  if (kcal == null && ev == null) return null;
  return (
    <div
      onClick={onClick}
      title={onClick ? clickTitle : undefined}
      style={{
        padding: "12px 16px",
        background: "var(--bg-warm)",
        borderRadius: 2,
        borderLeft: `3px solid ${color}`,
        minWidth: 140,
        cursor: onClick ? "pointer" : undefined,
      }}
    >
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>{label}</div>
      {kcal != null && (
        <div style={{ fontSize: 20, fontFamily: "var(--font-mono)", fontWeight: 600, color }}>
          {kcal.toFixed(1)}
          <span style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 400, marginLeft: 4 }}>
            kcal/mol
          </span>
        </div>
      )}
      {ev != null && (
        <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)", marginTop: 2 }}>
          {ev.toFixed(3)} eV
        </div>
      )}
    </div>
  );
}

function BarrierPanel({
  data,
  sendMessage,
}: {
  data: TransitionStateData;
  sendMessage?: TransitionStateProps["sendMessage"];
}) {
  const forwardKcal = data.activation_energy_kcal;
  const reverseKcal = data.reverse_barrier_kcal;
  const reactionDeltaKcal =
    forwardKcal != null && reverseKcal != null ? forwardKcal - reverseKcal : null;

  const askAboutForward = sendMessage
    ? () => {
        const rxnLabel =
          data.reactant_smiles && data.product_smiles
            ? `\`${data.reactant_smiles}\` → \`${data.product_smiles}\``
            : "this reaction";
        sendMessage({
          role: "user",
          content: [
            {
              type: "text",
              text:
                `I clicked the forward activation barrier (${forwardKcal?.toFixed(1)} kcal/mol) ` +
                `for ${rxnLabel}. ` +
                `Is this barrier consistent with the expected mechanism, is the reaction kinetically feasible at room temperature, ` +
                `and what bond-breaking/bond-forming is happening at the transition state?`,
            },
          ],
        });
      }
    : undefined;

  const askAboutReverse = sendMessage
    ? () => {
        const rxnLabel =
          data.reactant_smiles && data.product_smiles
            ? `\`${data.product_smiles}\` → \`${data.reactant_smiles}\``
            : "the reverse reaction";
        sendMessage({
          role: "user",
          content: [
            {
              type: "text",
              text:
                `I clicked the reverse activation barrier (${reverseKcal?.toFixed(1)} kcal/mol) ` +
                `for ${rxnLabel}. ` +
                `What does this barrier tell me about the stability of the product vs. reactant, ` +
                `and is the reaction thermodynamically or kinetically reversible under ambient conditions?`,
            },
          ],
        });
      }
    : undefined;

  return (
    <div className="panel">
      <div
        className="panel-title"
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
      >
        <span>Activation Barriers</span>
        {data.converged === false && (
          <span
            style={{
              fontSize: 10,
              padding: "2px 8px",
              background: "var(--warning-bg)",
              color: "var(--warning)",
              borderRadius: 2,
            }}
            title="NEB optimization did not fully converge — barriers are approximate."
          >
            Not Converged
          </span>
        )}
      </div>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <BarrierStat
          label="Forward Barrier (TS − reactant)"
          kcal={forwardKcal}
          ev={data.activation_energy_ev}
          color="var(--accent)"
          onClick={askAboutForward}
          clickTitle="Click to ask Claude about this barrier"
        />
        <BarrierStat
          label="Reverse Barrier (TS − product)"
          kcal={reverseKcal}
          ev={data.reverse_barrier_ev}
          color="var(--warning)"
          onClick={askAboutReverse}
          clickTitle="Click to ask Claude about the reverse"
        />
        {reactionDeltaKcal != null && (
          <BarrierStat
            label="ΔE reaction (product − reactant)"
            kcal={reactionDeltaKcal}
            color={reactionDeltaKcal < 0 ? "var(--success)" : "var(--text-muted)"}
          />
        )}
      </div>
      {data.method && (
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 12 }}>
          {data.method}
          {data.n_images != null && ` · ${data.n_images} NEB images`}
          {data.n_steps != null && ` · ${data.n_steps} optimizer steps`}
          {data.wall_time_seconds != null && ` · ${data.wall_time_seconds.toFixed(1)} s`}
        </div>
      )}
    </div>
  );
}

// =============================================================================
// MEP (Minimum Energy Path) Line Plot — click any image to ask Claude
// =============================================================================

function MepPanel({
  data,
  sendMessage,
}: {
  data: TransitionStateData;
  sendMessage?: TransitionStateProps["sendMessage"];
}) {
  const energies = data.mep_energies_kcal;
  if (!energies || energies.length < 2) return null;

  // Reference every image energy to the reactant (image 0) so the plot shows
  // barriers relative to the starting structure — a chemist's default frame.
  const reference = energies[0];
  const relative = energies.map((e) => e - reference);

  // Identify TS image index (highest energy along MEP).
  let tsIndex = 0;
  relative.forEach((e, i) => {
    if (e > relative[tsIndex]) tsIndex = i;
  });

  const askAboutImage = sendMessage
    ? (imageIdx: number) => {
        const role =
          imageIdx === 0
            ? "the reactant endpoint"
            : imageIdx === relative.length - 1
              ? "the product endpoint"
              : imageIdx === tsIndex
                ? "the transition-state image (highest point on the MEP)"
                : `NEB image #${imageIdx}`;
        const energyLabel = `${relative[imageIdx] >= 0 ? "+" : ""}${relative[imageIdx].toFixed(1)} kcal/mol above the reactant`;
        sendMessage({
          role: "user",
          content: [
            {
              type: "text",
              text:
                `I clicked ${role} on the MEP profile (energy: ${energyLabel}). ` +
                `What's happening at this point on the reaction coordinate — ` +
                `which bonds are breaking or forming, how far along is the geometry relative to reactant and product, ` +
                `and is this image near a plateau or a genuine minimum/maximum?`,
            },
          ],
        });
      }
    : undefined;

  const points: Point[] = relative.map((e, i) => ({
    x: i,
    y: e,
    label: i === 0 ? "R" : i === relative.length - 1 ? "P" : i === tsIndex ? "TS" : undefined,
    color: i === tsIndex ? "var(--warning)" : i === 0 || i === relative.length - 1 ? "var(--success)" : "var(--accent)",
    onClick: askAboutImage ? () => askAboutImage(i) : undefined,
    title:
      i === 0
        ? "Reactant"
        : i === relative.length - 1
          ? "Product"
          : i === tsIndex
            ? `Transition State — +${relative[i].toFixed(1)} kcal/mol`
            : `Image ${i} — ${relative[i] >= 0 ? "+" : ""}${relative[i].toFixed(1)} kcal/mol`,
  }));

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div
        className="panel-title"
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
      >
        <span>Minimum Energy Path ({energies.length} images)</span>
        <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-muted)" }}>
          kcal/mol · {sendMessage ? "click any image to ask" : "view only"}
        </span>
      </div>
      <LinePlot
        points={points}
        xAxisLabel="Reaction coordinate (NEB image index)"
        yAxisLabel="ΔE (kcal/mol, relative to reactant)"
        highlightIndex={tsIndex}
      />
    </div>
  );
}

// =============================================================================
// TS Geometry — NGL viewer, dynamic import so bundles without a TS stay small.
// =============================================================================

function TsGeometryPanel({ xyz, converged }: { xyz?: string; converged?: boolean }) {
  const trimmed = xyz?.trim();
  const isValidXyz = !!trimmed && trimmed.split("\n").length >= 3;

  // If NEB didn't converge, the TS geometry is unreliable even if an XYZ
  // string is present — skip the render and surface the reason.
  if (!isValidXyz || converged === false) {
    const reason = converged === false
      ? "NEB did not converge — the climbing image geometry is not a trustworthy TS."
      : "Backend did not return a TS geometry for this run.";
    return (
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="panel-title">Transition-State Geometry</div>
        <div
          style={{
            padding: 16,
            background: "var(--bg-warm)",
            borderRadius: 2,
            borderLeft: "3px solid var(--warning)",
            fontSize: 12,
            color: "var(--text-muted)",
            lineHeight: 1.5,
          }}
        >
          <div style={{ color: "var(--text)", fontWeight: 500, marginBottom: 4 }}>
            TS geometry unavailable
          </div>
          {reason} Try re-running with more NEB images (e.g. 8–12) and more
          optimizer steps, or tighten the endpoint optimizations first.
        </div>
      </div>
    );
  }

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">Transition-State Geometry</div>
      <MoleculeRenderer xyz={trimmed!} height={320} />
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6 }}>
        NEB climbing-image geometry — the point of highest energy along the MEP.
      </div>
    </div>
  );
}

// =============================================================================
// Completed phase — sub-component reusable by md-results.tsx neb_ dispatch
// =============================================================================

export function TransitionStateResultsView({
  data,
  sendMessage,
}: {
  data: TransitionStateData;
  sendMessage?: TransitionStateProps["sendMessage"];
}) {
  return (
    <div className="transition-state-results" style={{ width: "100%" }}>
      <BarrierPanel data={data} sendMessage={sendMessage} />
      <MepPanel data={data} sendMessage={sendMessage} />
      <TsGeometryPanel xyz={data.ts_geometry_xyz} converged={data.converged} />
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

// =============================================================================
// Main Component — routes between submitted and completed phases based on data
// =============================================================================

function isCompleted(data: TransitionStateData): boolean {
  // Treat as completed if we have either barrier data or an MEP profile. The
  // submission ack from find_transition_state has neither.
  return (
    data.activation_energy_kcal != null ||
    data.activation_energy_ev != null ||
    (data.mep_energies_kcal?.length ?? 0) > 1 ||
    (data.mep_energies_ev?.length ?? 0) > 1
  );
}

export default function TransitionStateViewer(props: TransitionStateProps) {
  const { toolInputs, toolResult, sendMessage, callServerTool } = props;
  const data = useViewData<TransitionStateData>(props);

  // Private-backend-tool polling — same pattern as generate-dynamics /
  // conformer-search. NEB completion payloads (barriers, MEP array,
  // TS geometry XYZ) are small; host forwarding works, but we use the
  // hook for consistent submission-to-completion UX across async jobs.
  const alreadyComplete = isCompleted(data);
  const jobId = alreadyComplete ? null : (data.job_id ?? null);
  const poll = useJobPoll<TransitionStateData>({
    jobId,
    callServerTool,
    intervalMs: 30_000,
  });

  const displayData: TransitionStateData = poll.data ?? data;
  // Swap to the results view whenever the poll reaches a terminal state
  // (completed / failed), even if isCompleted() is false because the
  // result shape is partial. Example: NEB runs to completion but doesn't
  // converge — activation_energy stays null, MEP is empty, but warnings
  // + "TS geometry unavailable" are meaningful to render. Staying on the
  // submission panel forever with a spinner was the wrong behavior.
  const jobTerminal = poll.phase === "completed" || poll.phase === "failed";
  const showCompleted = alreadyComplete || isCompleted(displayData) || jobTerminal;

  const submissionPhase: "queued" | "running" | "polling" | "failed" =
    poll.phase === "failed" ? "failed" :
    poll.phase === "running" ? "running" :
    poll.pollCount > 0 ? "polling" :
    "queued";

  const isStreaming = !toolInputs && !toolResult;
  if (isStreaming) {
    return <LoadingShimmer />;
  }

  return (
    <div className="transition-state-viewer" style={{ width: "100%" }}>
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
            Transition State (NEB)
          </div>
          {(displayData.reactant_smiles || displayData.product_smiles) && (
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
              title={`${displayData.reactant_smiles || "?"} → ${displayData.product_smiles || "?"}`}
            >
              {displayData.reactant_smiles || "?"} → {displayData.product_smiles || "?"}
            </div>
          )}
        </div>
      </div>

      {showCompleted ? (
        <TransitionStateResultsView data={displayData} sendMessage={sendMessage} />
      ) : (
        <SubmittedPanel
          data={displayData}
          elapsedSeconds={poll.elapsedSeconds}
          pollCount={poll.pollCount}
          phase={submissionPhase}
          errorMessage={poll.error}
        />
      )}
    </div>
  );
}
