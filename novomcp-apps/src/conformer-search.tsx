/**
 * NovoMCP Conformer Search Viewer
 *
 * Renders run_conformer_search output in two phases:
 *   - Submitted: job_id + "Queued" card (CREST takes 5-15 min; poll
 *     via get_job_status, don't auto-poll).
 *   - Completed: ranked-conformers table with relative energies and
 *     Boltzmann populations, horizontal population bars. Click any
 *     conformer → Claude gets population + energy context and a
 *     question about bioactive-conformer selection.
 *
 * ConformerResultsView is exported and mounted by md-results.tsx's
 * qm_* dispatcher so polling via get_job_status renders the same UX.
 */

import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";
import { useJobPoll } from "./use-job-poll.ts";

// =============================================================================
// Types
// =============================================================================

export interface Conformer {
  rank?: number;
  energy_kcal_mol?: number;
  boltzmann_population?: number;
  population?: number; // legacy / backend variant
  relative_energy_kcal?: number;
}

export interface ConformerSearchData {
  smiles?: string;
  job_id?: string;
  status?: string;
  method?: string;
  max_conformers?: number;

  n_conformers?: number;
  energy_range_kcal?: number;
  conformers?: Conformer[];

  wall_time_seconds?: number;
  warnings?: string[];
  message?: string;
}

type ConformerSearchProps = ViewProps<ConformerSearchData>;

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
        Submitting conformer search…
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
  data: ConformerSearchData;
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
      <div className="panel-title">Conformer Search Job</div>
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
            {data.method && ` — ${data.method}`}
            {data.max_conformers != null && ` · max ${data.max_conformers} conformers`}
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
              CREST typically takes 5–15 minutes. The viewer polls{" "}
              <code style={{ fontFamily: "var(--font-mono)" }}>get_job_status</code> directly
              every 30s — ranked conformers + Boltzmann populations render here once the
              job finishes. Progress stays at 10% during computation; that's CREST's
              normal reporting cadence, not a stall.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// Completed-phase results view — exported for md-results qm_ dispatcher
// =============================================================================

export function ConformerResultsView({
  data,
  sendMessage,
}: {
  data: ConformerSearchData;
  sendMessage?: ConformerSearchProps["sendMessage"];
}) {
  const conformers = data.conformers || [];
  if (conformers.length === 0) {
    return (
      <div className="panel">
        <div className="panel-title">Conformer Ensemble</div>
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Job completed but returned no conformers. This can happen when the energy window
          filtered out all candidates or the SMILES was too rigid for CREST to sample
          meaningful diversity.
        </div>
      </div>
    );
  }

  // Normalize fields across backend variants.
  const normalized = conformers.map((c) => {
    const pop = c.boltzmann_population ?? c.population ?? 0;
    const energy = c.energy_kcal_mol;
    const relE = c.relative_energy_kcal;
    return { rank: c.rank ?? 0, energy, relE, pop, raw: c };
  });

  // Compute relative energies if the backend only gave absolutes.
  const minEnergy = Math.min(
    ...normalized
      .map((c) => c.energy)
      .filter((e): e is number => e != null),
  );
  const rows = normalized.map((c) => ({
    ...c,
    relE: c.relE ?? (c.energy != null && Number.isFinite(minEnergy) ? c.energy - minEnergy : undefined),
  }));

  const askAboutConformer = sendMessage
    ? (c: typeof rows[number]) => {
        const popPct = (c.pop * 100).toFixed(1);
        const smilesRef = data.smiles ? ` for \`${data.smiles}\`` : "";
        sendMessage({
          role: "user",
          content: [
            {
              type: "text",
              text:
                `I clicked conformer rank #${c.rank}${smilesRef} — ` +
                `relative energy ${c.relE != null ? c.relE.toFixed(2) + " kcal/mol" : "?"}, ` +
                `Boltzmann population ${popPct}%. ` +
                `Is this a plausible bioactive conformer, what's the shape/flexibility profile compared to ` +
                `the lowest-energy conformer, and should I dock this one alongside the global minimum for a ` +
                `bioactive-conformer ensemble docking strategy?`,
            },
          ],
        });
      }
    : undefined;

  return (
    <div className="conformer-results" style={{ width: "100%" }}>
      {/* Summary cards */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
        {data.n_conformers != null && (
          <div
            style={{
              padding: "10px 14px",
              background: "var(--bg-warm)",
              borderRadius: 2,
              borderLeft: "3px solid var(--accent)",
              minWidth: 120,
            }}
          >
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Conformers</div>
            <div style={{ fontSize: 20, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--accent)" }}>
              {data.n_conformers}
            </div>
          </div>
        )}
        {data.energy_range_kcal != null && (
          <div
            style={{
              padding: "10px 14px",
              background: "var(--bg-warm)",
              borderRadius: 2,
              borderLeft: "3px solid var(--accent)",
              minWidth: 140,
            }}
          >
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Energy Range</div>
            <div style={{ fontSize: 18, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text)" }}>
              {data.energy_range_kcal.toFixed(2)}
              <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 4, fontWeight: 400 }}>
                kcal/mol
              </span>
            </div>
          </div>
        )}
        {data.method && (
          <div
            style={{
              padding: "10px 14px",
              background: "var(--bg-warm)",
              borderRadius: 2,
              borderLeft: "3px solid var(--text-muted)",
              minWidth: 100,
            }}
          >
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Method</div>
            <div style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text)" }}>
              {data.method}
            </div>
          </div>
        )}
      </div>

      {/* Ranked conformers table */}
      <div className="panel">
        <div
          className="panel-title"
          style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
        >
          <span>Ranked Conformers ({rows.length})</span>
          <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-muted)" }}>
            {askAboutConformer ? "click any row to ask" : ""}
          </span>
        </div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)" }}>
                <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>#</th>
                <th style={{ textAlign: "right", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>ΔE (kcal/mol)</th>
                <th style={{ textAlign: "right", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Population</th>
                <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500, minWidth: 160 }}>Weight</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c, i) => {
                const popPct = c.pop * 100;
                const color =
                  c.rank === 1 ? "var(--success)"
                  : popPct >= 10 ? "var(--accent)"
                  : "var(--text-muted)";
                return (
                  <tr
                    key={i}
                    onClick={askAboutConformer ? () => askAboutConformer(c) : undefined}
                    style={{
                      borderBottom: "1px solid var(--border)",
                      cursor: askAboutConformer ? "pointer" : undefined,
                      background: c.rank === 1 ? "var(--success-bg)" : undefined,
                    }}
                    title={askAboutConformer ? `Click to ask Claude about conformer #${c.rank}` : undefined}
                  >
                    <td style={{ padding: "6px 8px", fontFamily: "var(--font-mono)", color, fontWeight: c.rank === 1 ? 600 : 500 }}>
                      {c.rank}
                      {c.rank === 1 && (
                        <span style={{ fontSize: 9, marginLeft: 4, color: "var(--success)" }}>global min</span>
                      )}
                    </td>
                    <td style={{ padding: "6px 8px", textAlign: "right", fontFamily: "var(--font-mono)", color }}>
                      {c.relE != null ? (c.relE === 0 ? "0.00" : `+${c.relE.toFixed(2)}`) : "—"}
                    </td>
                    <td style={{ padding: "6px 8px", textAlign: "right", fontFamily: "var(--font-mono)", color, fontWeight: popPct >= 10 ? 600 : 400 }}>
                      {popPct.toFixed(1)}%
                    </td>
                    <td style={{ padding: "6px 8px" }}>
                      <div style={{ height: 6, background: "var(--bg-warm)", borderRadius: 2, overflow: "hidden" }}>
                        <div
                          style={{
                            width: `${Math.min(100, popPct)}%`,
                            height: "100%",
                            background: color,
                          }}
                        />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.6 }}>
          Boltzmann population is the thermal occupancy at 298 K. Conformers with &gt; 10% population
          are candidates for bioactive-conformer docking — the lowest-energy conformer isn't always
          the binding one. Click a row to get Claude's take on whether to dock this specific conformer.
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// Main component — routes between submitted and completed phases
// =============================================================================

function isCompleted(data: ConformerSearchData): boolean {
  return (data.conformers?.length ?? 0) > 0 || data.n_conformers != null;
}

export default function ConformerSearchViewer(props: ConformerSearchProps) {
  const { toolInputs, toolInputsPartial, toolResult, sendMessage, callServerTool } = props;
  const data = useViewData<ConformerSearchData>(props);

  // Private-backend-tool polling pattern — same as generate-dynamics.
  // CREST jobs (qm_*) stay on the host's forwarding path fine because their
  // payloads are small (ranked conformers + Boltzmann populations, no raw
  // geometry dump), but we use the same hook for consistency across all
  // async-job viewers.
  const alreadyComplete = isCompleted(data);
  const jobId = alreadyComplete ? null : (data.job_id ?? null);
  const poll = useJobPoll<ConformerSearchData>({
    jobId,
    callServerTool,
    intervalMs: 30_000,
  });

  const displayData: ConformerSearchData = poll.data ?? data;
  // Swap to the results view whenever the poll reaches a terminal state
  // (completed / failed), even if isCompleted() is false because the
  // result shape is partial. Prevents the spinner from persisting on
  // jobs that finish without producing a conformer ensemble.
  const jobTerminal = poll.phase === "completed" || poll.phase === "failed";
  const showCompleted = alreadyComplete || isCompleted(displayData) || jobTerminal;

  const isStreaming = !toolInputs && !toolResult;
  if (isStreaming) {
    return <LoadingShimmer />;
  }

  const smiles = displayData.smiles || toolInputs?.smiles || toolInputsPartial?.smiles;

  const submissionPhase: "queued" | "running" | "polling" | "failed" =
    poll.phase === "failed" ? "failed" :
    poll.phase === "running" ? "running" :
    poll.pollCount > 0 ? "polling" :
    "queued";

  return (
    <div className="conformer-search-viewer" style={{ width: "100%" }}>
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
            Conformer Search
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
          {displayData.method && (
            <div style={{ fontSize: 10, color: "var(--text-muted)" }}>{displayData.method}</div>
          )}
          {displayData.wall_time_seconds != null && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {displayData.wall_time_seconds.toFixed(1)} s
            </div>
          )}
        </div>
      </div>

      {showCompleted ? (
        <ConformerResultsView data={displayData} sendMessage={sendMessage} />
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
            {displayData.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
