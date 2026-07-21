/**
 * NovoMCP Docking Viewer Component
 *
 * Protein Method Card, binding affinity rankings, contact details,
 * and strain validation for molecular docking results.
 */
import { useState } from "react";
import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types
// =============================================================================

interface Interaction {
  type?: string;
  residue?: string;
  chain?: string;
  residue_number?: number;
  distance_A?: number;
  distance?: number;  // legacy alias
  angle_deg?: number;
  donor_is_protein?: boolean;
  protein_positive?: boolean;
  stacking_type?: string;
  metal?: string;
}

interface InteractionSummary {
  n_hbonds?: number;
  n_hydrophobic?: number;
  n_salt_bridges?: number;
  n_pi_stacking?: number;
  n_pi_cation?: number;
  n_halogen_bonds?: number;
  n_water_bridges?: number;
  n_metal_coord?: number;
  total_interactions?: number;
  key_residues?: string[];
}

interface DockingResult {
  smiles: string;
  binding_affinity_kcal: number;
  poses?: number | Array<Record<string, unknown>>;
  contacts?: Interaction[];
  interaction_summary?: InteractionSummary;
  weak_binder?: boolean;
  delta_vs_best_kcal?: number;
  delta_vs_reference_kcal?: number;
}

interface StrainResult {
  smiles?: string;
  strain_kcal_mol?: number;
  interpretation?: string;
  relaxed_energy?: number;
  bound_energy?: number;
}

interface DockingToolInput {
  // Phase indicator
  phase?: "estimate" | "completed" | "submitted";

  // Protein metadata (from get_protein_structure enrichment)
  protein_pdb_id?: string;
  protein_name?: string;
  resolution?: number;
  method?: string;
  organism?: string;
  chains?: string[];
  ligands?: string[];
  binding_site_source?: "known" | "predicted" | "auto_detect";

  // Docking parameters
  exhaustiveness?: number;
  num_modes?: number;
  protonation_ph?: number;
  n_molecules?: number;

  // Results
  molecules_docked?: number;
  molecules_failed?: number;
  results?: DockingResult[];
  failures?: Array<{ smiles: string; error: string }>;
  best_affinity_kcal?: number;
  mean_affinity_kcal?: number;

  // Reference ligand co-docking (Theo P0)
  reference_affinity_kcal?: number;
  reference_ligand_smiles?: string;
  reference_source?: "user_provided" | "co_crystallized" | null;
  reference_error?: string;
  native_ligand?: {
    residue_name?: string;
    chain_id?: string;
    residue_number?: number;
    n_atoms?: number;
    smiles?: string;
  };
  // PLIP binding pose analysis (Theo P1)
  reference_interactions?: Interaction[];

  // Strain (from dock_with_strain)
  strain?: StrainResult;

  // Cost
  credits_consumed?: number;
  confirmation_token?: string;
  estimated_credits?: number;
  credit_breakdown?: {
    base_cost?: number;
    per_molecule_cost?: number;
    molecule_count?: number;
    total_credits?: number;
  };

  // Async batch
  job_id?: string;
  status?: string;
  estimated_minutes?: number;
  message?: string;

  height?: number;
}

type DockingViewerProps = ViewProps<DockingToolInput>;

// =============================================================================
// Loading Shimmer
// =============================================================================

function LoadingShimmer({ height }: { height: number }) {
  return (
    <div
      style={{
        width: "100%",
        height,
        borderRadius: 4,
        padding: 20,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 12,
        background: "linear-gradient(135deg, var(--bg-warm) 0%, var(--bg) 100%)",
      }}
    >
      <div className="loading-spinner" />
      <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
        Running molecular docking...
      </div>
    </div>
  );
}

// =============================================================================
// Protein Method Card
// =============================================================================

function ProteinMethodCard({
  pdbId,
  name,
  resolution,
  method,
  organism,
  chains,
  ligands,
  bindingSiteSource,
  exhaustiveness,
  numModes,
  protonationPh,
}: {
  pdbId?: string;
  name?: string;
  resolution?: number;
  method?: string;
  organism?: string;
  chains?: string[];
  ligands?: string[];
  bindingSiteSource?: string;
  exhaustiveness?: number;
  numModes?: number;
  protonationPh?: number;
}) {
  const resolveQuality = (res?: number) => {
    if (res === undefined || res === null) return { label: "Unknown", color: "var(--text-muted)" };
    if (res <= 2.0) return { label: "High", color: "var(--success)" };
    if (res <= 3.0) return { label: "Medium", color: "var(--warning)" };
    return { label: "Low", color: "var(--danger)" };
  };

  const quality = resolveQuality(resolution);

  const methodLabel = (m?: string) => {
    if (!m) return "Unknown";
    const upper = m.toUpperCase();
    if (upper.includes("X-RAY")) return "X-Ray Crystallography";
    if (upper.includes("CRYO") || upper.includes("EM")) return "Cryo-EM";
    if (upper.includes("NMR")) return "NMR";
    return m;
  };

  return (
    <div className="panel">
      <div className="panel-title">Protein Target</div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
          gap: 8,
        }}
      >
        {/* PDB ID */}
        <div style={{ padding: "10px 12px", background: "var(--bg-warm)", borderRadius: 2 }}>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>PDB ID</div>
          <div style={{ fontSize: 16, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--accent)" }}>
            {pdbId || "—"}
          </div>
        </div>

        {/* Resolution */}
        <div style={{ padding: "10px 12px", background: "var(--bg-warm)", borderRadius: 2 }}>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Resolution</div>
          <div style={{ fontSize: 16, fontFamily: "var(--font-mono)", fontWeight: 500, color: quality.color }}>
            {resolution != null ? `${resolution.toFixed(2)} A` : "—"}
          </div>
          <div style={{ fontSize: 9, color: quality.color, marginTop: 2 }}>{quality.label} Quality</div>
        </div>

        {/* Method */}
        <div style={{ padding: "10px 12px", background: "var(--bg-warm)", borderRadius: 2 }}>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Method</div>
          <div style={{ fontSize: 13, fontWeight: 500, color: "var(--text)" }}>
            {methodLabel(method)}
          </div>
        </div>

        {/* Organism */}
        {organism && (
          <div style={{ padding: "10px 12px", background: "var(--bg-warm)", borderRadius: 2 }}>
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Organism</div>
            <div style={{ fontSize: 12, fontStyle: "italic", color: "var(--text)" }}>{organism}</div>
          </div>
        )}

        {/* Binding Site */}
        <div style={{ padding: "10px 12px", background: "var(--bg-warm)", borderRadius: 2 }}>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Binding Site</div>
          <div style={{ fontSize: 12, fontWeight: 500, color: "var(--text)" }}>
            {bindingSiteSource === "known"
              ? "Known (co-crystal)"
              : bindingSiteSource === "predicted"
                ? "Predicted"
                : "Auto-detected"}
          </div>
        </div>

        {/* Exhaustiveness */}
        {exhaustiveness != null && (
          <div style={{ padding: "10px 12px", background: "var(--bg-warm)", borderRadius: 2 }}>
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Exhaustiveness</div>
            <div style={{ fontSize: 16, fontFamily: "var(--font-mono)", fontWeight: 500, color: "var(--text)" }}>
              {exhaustiveness}
            </div>
          </div>
        )}

        {/* Protonation pH */}
        {protonationPh != null && (
          <div style={{ padding: "10px 12px", background: "var(--bg-warm)", borderRadius: 2 }}>
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Protonation pH</div>
            <div style={{ fontSize: 16, fontFamily: "var(--font-mono)", fontWeight: 500, color: "var(--text)" }}>
              {protonationPh}
            </div>
          </div>
        )}
      </div>

      {/* Chains & Ligands row */}
      {(chains?.length || ligands?.length) && (
        <div style={{ display: "flex", gap: 16, marginTop: 10, fontSize: 11, color: "var(--text-muted)" }}>
          {chains && chains.length > 0 && (
            <span>
              Chains: <code style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>{chains.join(", ")}</code>
            </span>
          )}
          {ligands && ligands.length > 0 && (
            <span>
              Native ligands: <code style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>{ligands.join(", ")}</code>
            </span>
          )}
          {numModes != null && (
            <span>
              Poses: <code style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>{numModes}</code>
            </span>
          )}
        </div>
      )}

      {/* Name */}
      {name && (
        <div style={{ marginTop: 8, fontSize: 11, color: "var(--text-muted)", lineHeight: 1.4 }}>{name}</div>
      )}
    </div>
  );
}

// =============================================================================
// Affinity Table
// =============================================================================

function AffinityTable({
  results,
  bestAffinity,
  referenceAffinity,
  referenceSmiles,
  referenceSource,
  nativeLigandName,
  sendMessage,
  pdbId,
}: {
  results: DockingResult[];
  bestAffinity?: number;
  referenceAffinity?: number;
  referenceSmiles?: string;
  referenceSource?: "user_provided" | "co_crystallized" | null;
  nativeLigandName?: string;
  sendMessage?: DockingViewerProps["sendMessage"];
  pdbId?: string;
}) {
  const [sortBy, setSortBy] = useState<"affinity" | "delta">("affinity");

  const sorted = [...results].sort((a, b) =>
    sortBy === "affinity"
      ? a.binding_affinity_kcal - b.binding_affinity_kcal
      : (a.delta_vs_best_kcal ?? 0) - (b.delta_vs_best_kcal ?? 0)
  );

  const hasReference = typeof referenceAffinity === "number";

  const affinityColor = (kcal: number) => {
    if (kcal <= -9) return "var(--success)";
    if (kcal <= -7) return "var(--accent)";
    if (kcal <= -6) return "var(--warning)";
    return "var(--danger)";
  };

  const truncateSmiles = (s: string, max = 40) =>
    s.length > max ? s.slice(0, max) + "..." : s;

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 10,
        }}
      >
        <div className="panel-title" style={{ margin: 0 }}>
          Binding Affinity ({results.length} molecule{results.length !== 1 ? "s" : ""})
        </div>
        {results.length > 1 && (
          <div style={{ display: "flex", gap: 4 }}>
            {(["affinity", "delta"] as const).map((key) => (
              <button
                key={key}
                className={`btn ${sortBy === key ? "active" : ""}`}
                onClick={() => setSortBy(key)}
              >
                {key === "affinity" ? "Absolute" : "Delta"}
              </button>
            ))}
          </div>
        )}
      </div>

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              <th style={{ textAlign: "left", padding: "6px 8px", fontSize: 10, color: "var(--text-muted)", fontWeight: 500 }}>#</th>
              <th style={{ textAlign: "left", padding: "6px 8px", fontSize: 10, color: "var(--text-muted)", fontWeight: 500 }}>SMILES</th>
              <th style={{ textAlign: "right", padding: "6px 8px", fontSize: 10, color: "var(--text-muted)", fontWeight: 500 }}>Affinity (kcal/mol)</th>
              <th style={{ textAlign: "right", padding: "6px 8px", fontSize: 10, color: "var(--text-muted)", fontWeight: 500 }}>Delta vs Best</th>
              {hasReference && (
                <th
                  style={{ textAlign: "right", padding: "6px 8px", fontSize: 10, color: "var(--text-muted)", fontWeight: 500 }}
                  title={`Delta vs reference ligand (${referenceSource === "co_crystallized" ? "co-crystallized" : "user-provided"}, ${referenceAffinity?.toFixed(1)} kcal/mol)`}
                >
                  Δ vs Ref
                </th>
              )}
              <th style={{ textAlign: "center", padding: "6px 8px", fontSize: 10, color: "var(--text-muted)", fontWeight: 500 }}>Poses</th>
              <th style={{ textAlign: "center", padding: "6px 8px", fontSize: 10, color: "var(--text-muted)", fontWeight: 500 }}>Contacts</th>
              <th style={{ textAlign: "center", padding: "6px 8px", fontSize: 10, color: "var(--text-muted)", fontWeight: 500 }}>Flag</th>
            </tr>
          </thead>
          <tbody>
            {/* Reference row (pinned at top) */}
            {hasReference && (
              <tr
                style={{
                  borderBottom: "2px solid var(--accent)",
                  background: "var(--bg-warm)",
                  fontStyle: "italic",
                }}
                title={referenceSource === "co_crystallized"
                  ? `Co-crystallized ligand auto-extracted from PDB${nativeLigandName ? ` (residue ${nativeLigandName})` : ""}`
                  : "User-provided reference ligand"}
              >
                <td style={{ padding: "8px", fontSize: 11, color: "var(--accent)", fontWeight: 600 }}>
                  REF
                </td>
                <td style={{ padding: "8px", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text)", maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={referenceSmiles || ""}>
                  {referenceSmiles ? (referenceSmiles.length > 40 ? referenceSmiles.slice(0, 40) + "..." : referenceSmiles) : "—"}
                  <span style={{ marginLeft: 6, fontSize: 9, color: "var(--text-muted)", fontStyle: "normal" }}>
                    ({referenceSource === "co_crystallized" ? `co-crystal${nativeLigandName ? " " + nativeLigandName : ""}` : "user"})
                  </span>
                </td>
                <td style={{ padding: "8px", textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 600, color: affinityColor(referenceAffinity!) }}>
                  {referenceAffinity!.toFixed(1)}
                </td>
                <td style={{ padding: "8px", textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>—</td>
                <td style={{ padding: "8px", textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>0.0</td>
                <td style={{ padding: "8px", textAlign: "center", color: "var(--text-muted)" }}>—</td>
                <td style={{ padding: "8px", textAlign: "center", color: "var(--text-muted)" }}>—</td>
                <td style={{ padding: "8px", textAlign: "center", color: "var(--text-muted)" }}>—</td>
              </tr>
            )}
            {sorted.map((r, idx) => (
              <tr
                key={idx}
                onClick={sendMessage ? () => {
                  const target = pdbId ? ` against ${pdbId}` : "";
                  const deltaBest = r.delta_vs_best_kcal != null
                    ? `, Δ vs best = ${r.delta_vs_best_kcal >= 0 ? "+" : ""}${r.delta_vs_best_kcal.toFixed(1)} kcal/mol`
                    : "";
                  const deltaRef = r.delta_vs_reference_kcal != null
                    ? `, Δ vs reference = ${r.delta_vs_reference_kcal >= 0 ? "+" : ""}${r.delta_vs_reference_kcal.toFixed(1)} kcal/mol`
                    : "";
                  sendMessage({
                    role: "user",
                    content: [
                      {
                        type: "text",
                        text:
                          `I clicked the pose for \`${r.smiles}\` at ${r.binding_affinity_kcal.toFixed(1)} kcal/mol` +
                          `${target} (rank #${idx + 1}${deltaBest}${deltaRef}). ` +
                          `Is this a strong/moderate/weak binder, what does the affinity suggest about selectivity, ` +
                          `and if this is promising, what lead optimization direction would you recommend?`,
                      },
                    ],
                  });
                } : undefined}
                style={{
                  borderBottom: "1px solid var(--border)",
                  background: idx === 0 ? "var(--success-bg)" : undefined,
                  cursor: sendMessage ? "pointer" : undefined,
                }}
                title={sendMessage ? "Click to ask Claude about this pose" : undefined}
              >
                <td style={{ padding: "8px", fontFamily: "var(--font-mono)", color: "var(--text-muted)", fontSize: 11 }}>
                  {idx + 1}
                </td>
                <td
                  style={{
                    padding: "8px",
                    fontFamily: "var(--font-mono)",
                    fontSize: 11,
                    color: "var(--text)",
                    maxWidth: 300,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={r.smiles}
                >
                  {truncateSmiles(r.smiles)}
                </td>
                <td
                  style={{
                    padding: "8px",
                    textAlign: "right",
                    fontFamily: "var(--font-mono)",
                    fontWeight: 600,
                    color: affinityColor(r.binding_affinity_kcal),
                  }}
                >
                  {r.binding_affinity_kcal.toFixed(1)}
                </td>
                <td
                  style={{
                    padding: "8px",
                    textAlign: "right",
                    fontFamily: "var(--font-mono)",
                    color: "var(--text-muted)",
                  }}
                >
                  {r.delta_vs_best_kcal != null
                    ? `${r.delta_vs_best_kcal >= 0 ? "+" : ""}${r.delta_vs_best_kcal.toFixed(1)}`
                    : "—"}
                </td>
                {hasReference && (
                  <td
                    style={{
                      padding: "8px",
                      textAlign: "right",
                      fontFamily: "var(--font-mono)",
                      fontWeight: 500,
                      // Green if candidate beats reference (more negative = better binder)
                      color: r.delta_vs_reference_kcal != null
                        ? (r.delta_vs_reference_kcal < 0 ? "var(--success)" : r.delta_vs_reference_kcal > 0 ? "var(--danger)" : "var(--text-muted)")
                        : "var(--text-muted)",
                    }}
                    title={r.delta_vs_reference_kcal != null
                      ? (r.delta_vs_reference_kcal < 0 ? "Better than reference" : r.delta_vs_reference_kcal > 0 ? "Worse than reference" : "Matches reference")
                      : undefined}
                  >
                    {r.delta_vs_reference_kcal != null
                      ? `${r.delta_vs_reference_kcal >= 0 ? "+" : ""}${r.delta_vs_reference_kcal.toFixed(1)}`
                      : "—"}
                  </td>
                )}
                <td style={{ padding: "8px", textAlign: "center", fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                  {typeof r.poses === "number"
                    ? r.poses
                    : Array.isArray(r.poses)
                      ? r.poses.length
                      : "—"}
                </td>
                <td style={{ padding: "8px", textAlign: "center", fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                  {r.contacts?.length ?? 0}
                </td>
                <td style={{ padding: "8px", textAlign: "center", fontSize: 11 }}>
                  {r.weak_binder && (
                    <span
                      style={{
                        background: "var(--warning-bg)",
                        color: "var(--warning)",
                        padding: "2px 6px",
                        borderRadius: 2,
                        fontSize: 10,
                        fontWeight: 500,
                      }}
                    >
                      Weak
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// =============================================================================
// Contact Detail Panel
// =============================================================================

// Color + label for each PLIP interaction type
const INTERACTION_TYPE_INFO: Record<string, { color: string; label: string }> = {
  hbond: { color: "var(--accent)", label: "H-bond" },
  hydrophobic: { color: "var(--success)", label: "Hydrophobic" },
  salt_bridge: { color: "var(--warning)", label: "Salt bridge" },
  pi_stacking: { color: "#8B7AA1", label: "π-stacking" },
  pi_cation: { color: "#A17A8B", label: "π-cation" },
  halogen: { color: "#7AA1A1", label: "Halogen" },
  water_bridge: { color: "#7A8BA1", label: "Water bridge" },
  metal: { color: "#A18E7A", label: "Metal" },
};

function interactionInfo(type?: string) {
  return INTERACTION_TYPE_INFO[type || ""] || { color: "var(--text-muted)", label: type || "Contact" };
}

function InteractionChip({ ixn, onClick }: { ixn: Interaction; onClick?: () => void }) {
  const info = interactionInfo(ixn.type);
  const dist = ixn.distance_A ?? ixn.distance;
  const hoverTitle = onClick
    ? "Click to ask Claude about this interaction"
    : `${info.label}${dist ? ` at ${dist.toFixed(2)} Å` : ""}${ixn.angle_deg ? `, ${ixn.angle_deg.toFixed(0)}°` : ""}${ixn.chain ? ` (chain ${ixn.chain})` : ""}`;
  return (
    <div
      onClick={onClick}
      style={{
        padding: "6px 10px",
        background: "var(--bg-warm)",
        borderRadius: 2,
        borderLeft: `3px solid ${info.color}`,
        cursor: onClick ? "pointer" : undefined,
      }}
      title={hoverTitle}
    >
      <div style={{ fontSize: 11, fontWeight: 500, color: "var(--text)" }}>
        {ixn.residue || "Unknown"}
      </div>
      <div style={{ fontSize: 9, color: "var(--text-muted)" }}>
        {info.label}{dist ? ` · ${dist.toFixed(1)} Å` : ""}
      </div>
    </div>
  );
}

function InteractionCountPill({ count, label, color }: { count: number; label: string; color: string }) {
  if (count === 0) return null;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: 10,
        padding: "2px 8px",
        background: "var(--bg-warm)",
        borderLeft: `2px solid ${color}`,
        borderRadius: 2,
      }}
    >
      <strong style={{ color }}>{count}</strong>
      <span style={{ color: "var(--text-muted)" }}>{label}</span>
    </span>
  );
}

function ContactPanel({
  results,
  referenceInteractions,
  nativeLigandName,
  referenceSource,
  sendMessage,
  pdbId,
}: {
  results: DockingResult[];
  referenceInteractions?: Interaction[];
  nativeLigandName?: string;
  referenceSource?: "user_provided" | "co_crystallized" | null;
  sendMessage?: DockingViewerProps["sendMessage"];
  pdbId?: string;
}) {
  const askAboutContact = sendMessage ? (c: Interaction, group: "candidate" | "reference") => {
    const info = interactionInfo(c.type);
    const dist = c.distance_A ?? c.distance;
    const chainSuffix = c.chain ? ` chain ${c.chain}` : "";
    const distStr = dist ? ` at ${dist.toFixed(2)} Å` : "";
    const angleStr = c.angle_deg ? `, ${c.angle_deg.toFixed(0)}°` : "";
    const target = pdbId ? ` in ${pdbId}` : "";
    const refNote = group === "reference" ? " (this is the reference ligand's contact)" : "";
    sendMessage({
      role: "user",
      content: [
        {
          type: "text",
          text:
            `I clicked the ${info.label} interaction at ${c.residue || "an unknown residue"}${chainSuffix}${distStr}${angleStr}${target}${refNote}. ` +
            `Is this interaction geometry favorable for ${info.label}, and is this residue known to be important for binding at this target?`,
        },
      ],
    });
  } : undefined;
  const topResult = results.reduce(
    (best, r) => (r.binding_affinity_kcal < (best?.binding_affinity_kcal ?? 0) ? r : best),
    results[0]
  );

  const candidateContacts = topResult?.contacts || [];
  const hasCandidateContacts = candidateContacts.length > 0;
  const hasReferenceContacts = (referenceInteractions || []).length > 0;

  if (!hasCandidateContacts && !hasReferenceContacts) return null;

  const summary = topResult?.interaction_summary;
  const refLabel = referenceSource === "co_crystallized"
    ? `Reference (${nativeLigandName || "co-crystal"})`
    : "Reference (user)";

  // Key residues shared between candidate and reference (conserved interactions)
  const refResidues = new Set(
    (referenceInteractions || []).map((i) => i.residue).filter(Boolean) as string[]
  );
  const candidateResidues = new Set(
    candidateContacts.map((i) => i.residue).filter(Boolean) as string[]
  );
  const shared = [...candidateResidues].filter((r) => refResidues.has(r));

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span>
          Binding Pose Interactions
          {topResult && ` — ${topResult.binding_affinity_kcal.toFixed(1)} kcal/mol`}
        </span>
        {hasReferenceContacts && shared.length > 0 && (
          <span
            style={{
              fontSize: 10,
              padding: "2px 8px",
              background: "var(--success-bg)",
              color: "var(--success)",
              borderRadius: 12,
              border: "1px solid var(--success)",
            }}
            title={`Residues interacting with both candidate and reference: ${shared.join(", ")}`}
          >
            {shared.length} conserved with reference
          </span>
        )}
      </div>

      {/* Interaction count summary */}
      {summary && summary.total_interactions && summary.total_interactions > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
          <InteractionCountPill count={summary.n_hbonds || 0} label="H-bonds" color={INTERACTION_TYPE_INFO.hbond.color} />
          <InteractionCountPill count={summary.n_hydrophobic || 0} label="Hydrophobic" color={INTERACTION_TYPE_INFO.hydrophobic.color} />
          <InteractionCountPill count={summary.n_salt_bridges || 0} label="Salt bridges" color={INTERACTION_TYPE_INFO.salt_bridge.color} />
          <InteractionCountPill count={summary.n_pi_stacking || 0} label="π-stacking" color={INTERACTION_TYPE_INFO.pi_stacking.color} />
          <InteractionCountPill count={summary.n_pi_cation || 0} label="π-cation" color={INTERACTION_TYPE_INFO.pi_cation.color} />
          <InteractionCountPill count={summary.n_halogen_bonds || 0} label="Halogen" color={INTERACTION_TYPE_INFO.halogen.color} />
          <InteractionCountPill count={summary.n_water_bridges || 0} label="Water bridges" color={INTERACTION_TYPE_INFO.water_bridge.color} />
          <InteractionCountPill count={summary.n_metal_coord || 0} label="Metal" color={INTERACTION_TYPE_INFO.metal.color} />
        </div>
      )}

      {/* Candidate contacts */}
      {hasCandidateContacts && (
        <>
          <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 6, letterSpacing: "0.04em" }}>
            Candidate
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: hasReferenceContacts ? 14 : 0 }}>
            {candidateContacts.map((c, idx) => (
              <InteractionChip
                key={`cand-${idx}`}
                ixn={c}
                onClick={askAboutContact ? () => askAboutContact(c, "candidate") : undefined}
              />
            ))}
          </div>
        </>
      )}

      {/* Reference contacts for side-by-side comparison */}
      {hasReferenceContacts && (
        <>
          <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 6, letterSpacing: "0.04em", borderTop: "1px solid var(--border)", paddingTop: 12 }}>
            {refLabel}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {(referenceInteractions || []).map((c, idx) => (
              <InteractionChip
                key={`ref-${idx}`}
                ixn={c}
                onClick={askAboutContact ? () => askAboutContact(c, "reference") : undefined}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// =============================================================================
// Strain Validation Panel
// =============================================================================

function StrainPanel({ strain }: { strain: StrainResult }) {
  const strainColor = (kcal?: number) => {
    if (kcal === undefined) return "var(--text-muted)";
    if (kcal < 2) return "var(--success)";
    if (kcal < 5) return "var(--accent)";
    if (kcal < 10) return "var(--warning)";
    return "var(--danger)";
  };

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">Strain Validation</div>
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <div
          style={{
            padding: "12px 16px",
            background: "var(--bg-warm)",
            borderRadius: 2,
            textAlign: "center",
            minWidth: 100,
          }}
        >
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Strain Energy</div>
          <div
            style={{
              fontSize: 20,
              fontFamily: "var(--font-mono)",
              fontWeight: 600,
              color: strainColor(strain.strain_kcal_mol),
            }}
          >
            {strain.strain_kcal_mol != null ? `${strain.strain_kcal_mol.toFixed(1)}` : "—"}
          </div>
          <div style={{ fontSize: 10, color: "var(--text-muted)" }}>kcal/mol</div>
        </div>
        <div style={{ fontSize: 12, color: "var(--text)", lineHeight: 1.5 }}>
          {strain.interpretation || (
            strain.strain_kcal_mol != null
              ? strain.strain_kcal_mol < 2
                ? "Minimal strain — docking pose is natural"
                : strain.strain_kcal_mol < 5
                  ? "Moderate strain — pose is plausible"
                  : strain.strain_kcal_mol < 10
                    ? "Significant strain — docking score may be inflated"
                    : "Severe strain — ligand forced into unnatural conformation, docking score is unreliable"
              : ""
          )}
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// Cost / Estimate Phase
// =============================================================================

function EstimateCard({ data }: { data: DockingToolInput }) {
  // Passive cost notice. The approval-gate pattern was tried and dropped —
  // Claude cannot be reliably paused by UI buttons, and the server-side
  // credit pre-flight check (AGENTMODE-ARCHITECTURE §4) already hard-blocks
  // overspend. The estimate phase is now a transient informational card;
  // Claude re-invokes with the confirmation_token on its own turn.
  const breakdown = data.credit_breakdown;
  const totalCredits = breakdown?.total_credits ?? data.estimated_credits ?? "—";
  const base = breakdown?.base_cost;
  const perMol = breakdown?.per_molecule_cost;
  const molCount = breakdown?.molecule_count ?? data.n_molecules;
  const hasBreakdown =
    base !== undefined && perMol !== undefined && molCount !== undefined;

  return (
    <div className="panel">
      <div className="panel-title">Docking Cost Estimate</div>
      <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
        <div
          style={{
            padding: "12px 16px",
            background: "var(--bg-warm)",
            borderRadius: 2,
            textAlign: "center",
            minWidth: 110,
          }}
        >
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Credits</div>
          <div style={{ fontSize: 20, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--accent)" }}>
            {totalCredits}
          </div>
          {hasBreakdown && (
            <div style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)", marginTop: 4 }}>
              {base} base + {perMol} × {molCount}
            </div>
          )}
        </div>
        <div style={{ fontSize: 13, color: "var(--text-soft)", lineHeight: 1.5 }}>
          Dock {molCount ?? "the"} molecule{molCount === 1 ? "" : "s"}
          {data.protein_pdb_id ? ` against ${data.protein_pdb_id}` : ""}.
          {" "}
          <span className="loading-spinner" style={{ display: "inline-block", verticalAlign: "middle", width: 12, height: 12, borderWidth: 2, marginLeft: 4 }} />
          <span style={{ fontSize: 11, color: "var(--text-muted)", marginLeft: 6 }}>Queueing…</span>
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// Batch Status
// =============================================================================

function BatchStatus({ data }: { data: DockingToolInput }) {
  // Surface a neutral status string. Prefer explicit backend status; otherwise
  // fall back to "Queued" while we don't yet have a molecules_docked count
  // (keeps the user in normal "waiting in line" mental model rather than
  // exposing GPU warm-up mechanics).
  const statusLabel =
    (typeof data.molecules_docked === "number" && data.molecules_docked > 0
      ? `Docking · ${data.molecules_docked} processed`
      : null) ||
    data.status ||
    "Queued";

  return (
    <div className="panel">
      <div className="panel-title">Batch Docking</div>
      <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
        <div className="loading-spinner" />
        <div>
          <div style={{ fontSize: 13, color: "var(--text)", fontWeight: 500 }}>
            {statusLabel}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
            Job: <code style={{ fontFamily: "var(--font-mono)" }}>{data.job_id}</code>
            {data.estimated_minutes && ` — ~${data.estimated_minutes} min remaining`}
          </div>
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// Failures Panel
// =============================================================================

function FailuresPanel({ failures }: { failures: Array<{ smiles: string; error: string }> }) {
  if (failures.length === 0) return null;

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title" style={{ color: "var(--danger)" }}>
        Failed ({failures.length})
      </div>
      {failures.map((f, idx) => (
        <div
          key={idx}
          style={{
            padding: "6px 10px",
            background: "var(--danger-bg, var(--bg-warm))",
            borderRadius: 2,
            marginBottom: 4,
            fontSize: 11,
          }}
        >
          <code style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>{f.smiles.slice(0, 50)}</code>
          <span style={{ color: "var(--danger)", marginLeft: 8 }}>{f.error}</span>
        </div>
      ))}
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function DockingViewer({
  toolInputs,
  toolInputsPartial,
  toolResult,
  sendMessage,
}: DockingViewerProps) {
  const height = toolInputs?.height ?? toolInputsPartial?.height ?? 500;
  const isStreaming = !toolInputs && !toolResult;

  if (isStreaming) {
    return <LoadingShimmer height={height} />;
  }

  const d = useViewData<DockingToolInput>({ toolInputs, toolResult });

  // Phase: estimate — transient cost notice while Claude re-invokes with
  // the confirmation_token. No UI gate; see EstimateCard comment.
  if (d.phase === "estimate" || (d.confirmation_token && !d.results)) {
    return (
      <div className="docking-viewer" style={{ width: "100%" }}>
        <Header pdbId={d.protein_pdb_id} />
        <EstimateCard data={d} />
      </div>
    );
  }

  // Phase: batch submitted (async)
  if (d.phase === "submitted" || (d.job_id && !d.results)) {
    return (
      <div className="docking-viewer" style={{ width: "100%" }}>
        <Header pdbId={d.protein_pdb_id} />
        <ProteinMethodCard
          pdbId={d.protein_pdb_id}
          name={d.protein_name}
          resolution={d.resolution}
          method={d.method}
          organism={d.organism}
          chains={d.chains}
          ligands={d.ligands}
          bindingSiteSource={d.binding_site_source}
          exhaustiveness={d.exhaustiveness}
          numModes={d.num_modes}
          protonationPh={d.protonation_ph}
        />
        <div style={{ marginTop: 16 }}>
          <BatchStatus data={d} />
        </div>
      </div>
    );
  }

  // Phase: completed
  return (
    <div className="docking-viewer" style={{ width: "100%" }}>
      <Header pdbId={d.protein_pdb_id} credits={d.credits_consumed} />

      {/* Method Card */}
      <ProteinMethodCard
        pdbId={d.protein_pdb_id}
        name={d.protein_name}
        resolution={d.resolution}
        method={d.method}
        organism={d.organism}
        chains={d.chains}
        ligands={d.ligands}
        bindingSiteSource={d.binding_site_source}
        exhaustiveness={d.exhaustiveness}
        numModes={d.num_modes}
      />

      {/* Affinity Table */}
      {d.results && d.results.length > 0 && (
        <AffinityTable
          results={d.results}
          bestAffinity={d.best_affinity_kcal}
          referenceAffinity={d.reference_affinity_kcal}
          referenceSmiles={d.reference_ligand_smiles}
          referenceSource={d.reference_source}
          nativeLigandName={d.native_ligand?.residue_name}
          sendMessage={sendMessage}
          pdbId={d.protein_pdb_id}
        />
      )}

      {/* Contact Details */}
      {d.results && d.results.length > 0 && (
        <ContactPanel
          results={d.results}
          referenceInteractions={d.reference_interactions}
          nativeLigandName={d.native_ligand?.residue_name}
          referenceSource={d.reference_source}
          sendMessage={sendMessage}
          pdbId={d.protein_pdb_id}
        />
      )}

      {/* Strain Validation */}
      {d.strain && <StrainPanel strain={d.strain} />}

      {/* Failures */}
      {d.failures && d.failures.length > 0 && <FailuresPanel failures={d.failures} />}
    </div>
  );
}

// =============================================================================
// Header
// =============================================================================

function Header({ pdbId, credits }: { pdbId?: string; credits?: number }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
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
          Docking Results {pdbId && <span style={{ fontFamily: "var(--font-mono)", fontSize: 14, color: "var(--accent)" }}>({pdbId})</span>}
        </div>
      </div>
      {credits != null && (
        <div
          style={{
            fontSize: 11,
            fontFamily: "var(--font-mono)",
            color: "var(--text-muted)",
            background: "var(--bg-warm)",
            padding: "4px 10px",
            borderRadius: 2,
          }}
        >
          {credits} credits
        </div>
      )}
    </div>
  );
}
