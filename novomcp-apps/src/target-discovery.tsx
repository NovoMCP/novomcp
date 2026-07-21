/**
 * NovoMCP Target Discovery Viewer
 *
 * Renders the ranked target list from target_discovery: composite-score
 * sparklines across the 3 subscores, tractability + competition badges,
 * suggested-PDB chips, and a highlight card for the target the backend
 * recommends proceeding with. Click any target row → Claude gets a
 * question grounded in that gene.
 */

import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";
import { Choice, type ChoiceOption } from "./providers/index.ts";

// =============================================================================
// Types
// =============================================================================

interface Target {
  gene_symbol?: string;
  ensembl_id?: string;
  uniprot_id?: string;
  overall_score?: number;
  composite_score?: number;
  genetic_score?: number;
  expression_score?: number;
  tractability_small_molecule?: boolean;
  known_drugs_count?: number;
  high_competition?: boolean;
  pdb_ids?: string[];
  suggested_pdb_id?: string | null;
  pdb_selection_criteria?: string;
  best_pdb_resolution_A?: number;
  top_pathways?: string[];
  has_structure?: boolean;
  structure_unavailable?: boolean;
}

interface TargetDiscoveryToolInput {
  disease?: string;
  disease_efo_id?: string;
  total_targets?: number;
  targets_dockable?: number;
  targets?: Target[];
  suggested_target?: string;
  suggested_pdb_id?: string;
  wall_time_seconds?: number;
  warnings?: string[];
}

type TargetDiscoveryProps = ViewProps<TargetDiscoveryToolInput>;

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
        Searching omics database for drug targets…
      </div>
    </div>
  );
}

// =============================================================================
// Score sparkline — 3-bar composite-score breakdown (genetic / expression /
// druggability). All scored 0-1; bars scale to 100% height within their cell.
// =============================================================================

function ScoreSparkline({
  genetic,
  expression,
  composite,
  overall,
}: {
  genetic?: number;
  expression?: number;
  composite?: number;
  overall?: number;
}) {
  if (genetic == null && expression == null && composite == null && overall == null) {
    return <span style={{ fontSize: 10, color: "var(--text-muted)" }}>—</span>;
  }

  // The third bar prefers overall_score (the differentiated OpenTargets signal)
  // over composite_score. The backend floors composite_score at 0.3 for most
  // omics_targets docs, so rendering it directly produces uniform bars. Fall
  // back to composite only when overall is unavailable.
  const thirdBar = overall ?? composite ?? 0;
  const thirdTitle = overall != null ? "Overall (OpenTargets)" : "Composite";
  const bars = [
    { value: genetic ?? 0, label: "G", title: "Genetic evidence" },
    { value: expression ?? 0, label: "E", title: "Expression" },
    { value: thirdBar, label: "O", title: thirdTitle },
  ];

  const width = 60;
  const height = 22;
  const barW = (width - 6) / bars.length;

  const colorFor = (v: number) =>
    v >= 0.7 ? "var(--success)" : v >= 0.4 ? "var(--accent)" : "var(--text-muted)";

  return (
    <svg width={width} height={height} style={{ display: "inline-block", verticalAlign: "middle" }}>
      {bars.map((b, i) => {
        const h = Math.max(1, (b.value / 1.0) * (height - 4));
        const y = height - h - 1;
        const x = 2 + i * barW;
        return (
          <g key={i}>
            <rect
              x={x}
              y={y}
              width={barW - 2}
              height={h}
              fill={colorFor(b.value)}
            >
              <title>{`${b.title}: ${b.value.toFixed(2)}`}</title>
            </rect>
          </g>
        );
      })}
    </svg>
  );
}

// =============================================================================
// Next-step Choice — "pick one of the top N to send to validate_target"
// =============================================================================

function NextStepChoice({
  targets,
  disease,
  suggestedGene,
  sendMessage,
}: {
  targets?: Target[];
  disease?: string;
  suggestedGene?: string;
  sendMessage?: TargetDiscoveryProps["sendMessage"];
}) {
  if (!sendMessage || !targets || targets.length === 0) return null;

  const top = targets.slice(0, 5);

  const metricTone = (score: number): "success" | "accent" | "warning" =>
    score >= 0.7 ? "success" : score >= 0.4 ? "accent" : "warning";

  const options: ChoiceOption[] = top.map((t, i): ChoiceOption => {
    const score = t.overall_score ?? t.composite_score ?? 0;
    const isSuggested = t.gene_symbol === suggestedGene;
    const drugs = t.known_drugs_count ?? 0;

    const tags: ChoiceOption["tags"] = [];
    if (isSuggested) tags.push({ label: "Suggested", variant: "success" });
    if (drugs > 5) tags.push({ label: `${drugs} approved drugs`, variant: "success" });
    else if (drugs > 0) tags.push({ label: `${drugs} approved drug${drugs === 1 ? "" : "s"}`, variant: "success" });
    else tags.push({ label: "No approved drugs", variant: "neutral" });
    if (t.tractability_small_molecule) tags.push({ label: "tractable", variant: "success" });
    if (t.high_competition) tags.push({ label: "crowded space", variant: "warning" });
    if (!t.suggested_pdb_id) tags.push({ label: "no dockable structure", variant: "warning" });

    return {
      id: t.gene_symbol || `target-${i}`,
      title: t.gene_symbol || `Target ${i + 1}`,
      subtitle: t.top_pathways && t.top_pathways.length > 0 ? t.top_pathways.slice(0, 2).join(" · ") : undefined,
      rank: i + 1,
      metric: {
        label: "Score",
        value: score.toFixed(2),
        tone: metricTone(score),
      },
      tags,
    };
  });

  return (
    <div style={{ marginBottom: 16 }}>
      <Choice
        title="Next step: validate a target"
        description={`Pick one of the top ${top.length} candidates for ${disease || "this disease"} to send to the adversarial validate_target checkpoint.`}
        options={options}
        onSelect={(opt) => {
          sendMessage({
            role: "user",
            content: [
              {
                type: "text",
                text: `Validate ${opt.id}${disease ? ` for ${disease}` : ""}. Call validate_target next.`,
              },
            ],
          });
        }}
      />
    </div>
  );
}

// =============================================================================
// Target table
// =============================================================================

function TargetTable({
  targets,
  disease,
  sendMessage,
}: {
  targets?: Target[];
  disease?: string;
  sendMessage?: TargetDiscoveryProps["sendMessage"];
}) {
  if (!targets || targets.length === 0) {
    return (
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="panel-title">No Targets</div>
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          No targets matched the disease + evidence-score filter. Try lowering
          min_evidence or check the disease name.
        </div>
      </div>
    );
  }

  const askAboutTarget = sendMessage
    ? (t: Target, rank: number) => {
        const competitive = t.high_competition
          ? ` — competitive space (${t.known_drugs_count} known drugs)`
          : t.known_drugs_count
            ? ` — ${t.known_drugs_count} known drug${t.known_drugs_count === 1 ? "" : "s"}`
            : " — no known drugs (novel target)";
        const pdb = t.suggested_pdb_id ? ` Suggested PDB: ${t.suggested_pdb_id}.` : " No dockable structure available.";
        const pathways =
          t.top_pathways && t.top_pathways.length > 0
            ? ` Pathways: ${t.top_pathways.slice(0, 3).join(", ")}.`
            : "";
        sendMessage({
          role: "user",
          content: [
            {
              type: "text",
              text:
                `I clicked rank #${rank} **${t.gene_symbol}** (score ${(t.overall_score ?? t.composite_score)?.toFixed(2) ?? "?"}) ` +
                `for ${disease || "this disease"}${competitive}.${pdb}${pathways} ` +
                `What does this protein do mechanistically, is it a credible drug target (genetics + tractability), ` +
                `and what's the clinical trial landscape? If promising, what chemotype or ligand class has worked historically?`,
            },
          ],
        });
      }
    : undefined;

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div
        className="panel-title"
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
      >
        <span>Ranked Targets ({targets.length})</span>
        <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-muted)" }}>
          {askAboutTarget ? "click any row to ask" : ""}
        </span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>#</th>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Gene</th>
              <th style={{ textAlign: "right", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Score</th>
              <th style={{ textAlign: "center", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>
                Sub-scores
                <span style={{ fontSize: 9, display: "block", color: "var(--text-muted)", fontWeight: 400, marginTop: 2 }}>
                  G · E · O
                </span>
              </th>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Suggested PDB</th>
              <th style={{ textAlign: "center", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Tractable</th>
              <th style={{ textAlign: "right", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Known drugs</th>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Top pathways</th>
            </tr>
          </thead>
          <tbody>
            {targets.map((t, i) => {
              const rank = i + 1;
              const dockable = !t.structure_unavailable;
              return (
                <tr
                  key={t.ensembl_id || t.gene_symbol || i}
                  onClick={askAboutTarget ? () => askAboutTarget(t, rank) : undefined}
                  style={{
                    borderBottom: "1px solid var(--border)",
                    cursor: askAboutTarget ? "pointer" : undefined,
                    opacity: dockable ? 1 : 0.75,
                  }}
                  title={
                    askAboutTarget
                      ? `Click to ask Claude about ${t.gene_symbol}`
                      : dockable
                        ? undefined
                        : "No suggested PDB — target is not dockable"
                  }
                >
                  <td style={{ padding: "6px 8px", fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                    {rank}
                  </td>
                  <td style={{ padding: "6px 8px", fontFamily: "var(--font-mono)", fontWeight: 500, color: "var(--accent)" }}>
                    {t.gene_symbol || "—"}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 600 }}>
                    {t.overall_score != null
                      ? t.overall_score.toFixed(2)
                      : t.composite_score != null
                        ? t.composite_score.toFixed(2)
                        : "—"}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "center" }}>
                    <ScoreSparkline
                      genetic={t.genetic_score}
                      expression={t.expression_score}
                      composite={t.composite_score}
                      overall={t.overall_score}
                    />
                  </td>
                  <td style={{ padding: "6px 8px", fontFamily: "var(--font-mono)", fontSize: 10 }}>
                    {t.suggested_pdb_id ? (
                      <span>
                        <code style={{ padding: "1px 6px", background: "var(--bg-warm)", borderRadius: 2, color: "var(--accent)" }}>
                          {t.suggested_pdb_id}
                        </code>
                        {t.best_pdb_resolution_A != null && (
                          <span style={{ marginLeft: 4, color: "var(--text-muted)" }}>
                            {t.best_pdb_resolution_A.toFixed(1)}Å
                          </span>
                        )}
                      </span>
                    ) : (
                      <span style={{ color: "var(--warning)", fontSize: 10 }}>no structure</span>
                    )}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "center", fontSize: 10 }}>
                    {t.tractability_small_molecule ? (
                      <span style={{ color: "var(--success)", fontWeight: 500 }}>✓</span>
                    ) : (
                      <span style={{ color: "var(--text-muted)" }}>—</span>
                    )}
                  </td>
                  <td
                    style={{
                      padding: "6px 8px",
                      textAlign: "right",
                      fontFamily: "var(--font-mono)",
                      color: t.high_competition ? "var(--warning)" : "var(--text-muted)",
                    }}
                    title={t.high_competition ? "Crowded competitive space (>5 drugs)" : undefined}
                  >
                    {t.known_drugs_count ?? 0}
                    {t.high_competition && (
                      <span style={{ marginLeft: 4, fontSize: 9 }}>⚠</span>
                    )}
                  </td>
                  <td
                    style={{
                      padding: "6px 8px",
                      fontSize: 10,
                      color: "var(--text-muted)",
                      maxWidth: 220,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={(t.top_pathways || []).join(" · ")}
                  >
                    {(t.top_pathways || []).slice(0, 2).join(", ") || "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.6 }}>
        Score column is OpenTargets overall_score (falls back to composite_score when
        overall is unavailable). Sub-score bars (G · E · O) = genetic evidence /
        expression / overall. Targets without a suggested PDB are faded — they can't be
        docked directly. Known-drug ⚠ = crowded competitive space.
      </div>
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function TargetDiscoveryViewer(props: TargetDiscoveryProps) {
  const { toolInputs, toolResult, sendMessage } = props;
  const data = useViewData<TargetDiscoveryToolInput>(props);
  const isStreaming = !toolInputs && !toolResult;
  if (isStreaming) {
    return <LoadingShimmer />;
  }

  const disease = data.disease || toolInputs?.disease;

  return (
    <div className="target-discovery-viewer" style={{ width: "100%" }}>
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
              textTransform: "capitalize",
            }}
          >
            Target Discovery — {disease || "?"}
          </div>
          {data.disease_efo_id && (
            <div style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)", marginTop: 4 }}>
              {data.disease_efo_id}
            </div>
          )}
        </div>
        <div style={{ textAlign: "right" }}>
          {data.total_targets != null && (
            <div style={{ fontSize: 14, fontFamily: "var(--font-mono)", color: "var(--accent)", fontWeight: 600 }}>
              {data.total_targets}
            </div>
          )}
          <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
            targets
            {data.targets_dockable != null && (
              <span style={{ marginLeft: 6 }}>· {data.targets_dockable} dockable</span>
            )}
          </div>
          {data.wall_time_seconds != null && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)", marginTop: 2 }}>
              {data.wall_time_seconds.toFixed(1)} s
            </div>
          )}
        </div>
      </div>

      <NextStepChoice
        targets={data.targets}
        disease={disease}
        suggestedGene={data.suggested_target}
        sendMessage={sendMessage}
      />

      <TargetTable targets={data.targets} disease={disease} sendMessage={sendMessage} />

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
