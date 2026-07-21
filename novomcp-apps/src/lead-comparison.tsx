/**
 * NovoMCP Lead Comparison Component
 *
 * Side-by-side property comparison table for lead optimization variants.
 * Replaces Swiss ADME workflow — transposed table with color-coded
 * drug-likeness ranges, delta-vs-seed toggle, and column sorting.
 */
import { useState } from "react";
import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types
// =============================================================================

interface Variant {
  smiles: string;
  source?: "scaffold_hop" | "molmim";
  modification?: string;
  mw?: number;
  logp?: number;
  tpsa?: number;
  qed?: number;
  sa_score?: number;
  hbd?: number;
  hba?: number;
  rotatable_bonds?: number;
  lipinski_violations?: number;
  veber_violations?: number;
  tanimoto_to_seed?: number;
  patent_risk?: string;
  patent_note?: string;
  compliance_status?: string;
  prior_art?: {
    // disclosed: true → disclosed in PubChem/local 122M
    // disclosed: false → novel (lookup ran, no match)
    // disclosed: null → lookup did not run (FAVES service unreachable, Redis miss + PubChem unavailable, etc.)
    disclosed?: boolean | null;
    pubchem_cid?: string | null;
    disclosure_source?: string | null;
    inchikey?: string | null;
  };
  // Scaffold diversity (Theo P0)
  murcko_scaffold?: string;
  murcko_cluster_id?: number;
  scaffold_cluster_id?: number;
  cluster_size?: number;
  cluster_note?: string | null;
  diversity_rank?: number;
}

interface LeadComparisonInput {
  seed_smiles?: string;
  seed?: Variant;
  variants?: Variant[];
  optimization_type?: string;
  input_smiles?: string;
  height?: number;
  // Scaffold diversity summary (Theo P0)
  unique_scaffolds?: number;
  diversity_score?: number;
  n_clusters?: number;
  clusters?: Array<{ cluster_id: number; size: number; members: string[] }>;
  // Configurable Tanimoto filtering (Theo P1)
  similarity_range?: { min: number; max: number };
  patent_risk_thresholds?: { low: number; high: number };
  filtered_by_similarity?: number;
  similarity_filter_note?: string;
}

type LeadComparisonProps = ViewProps<LeadComparisonInput>;

// =============================================================================
// Property Definitions
// =============================================================================

interface PropertyDef {
  key: string;
  label: string;
  group: string;
  format: (v: unknown) => string;
  color: (v: number) => string;
  deltaDirection?: "lower" | "higher"; // which direction is "better" for deltas
  isNumeric: boolean;
}

const VAR_SUCCESS = "var(--success)";
const VAR_WARNING = "var(--warning)";
const VAR_DANGER = "var(--danger)";
const VAR_MUTED = "var(--text-muted)";

const PROPERTIES: PropertyDef[] = [
  // Physicochemical
  {
    key: "mw", label: "MW", group: "Physicochemical",
    format: (v) => typeof v === "number" ? v.toFixed(1) : "—",
    color: (v) => v < 500 ? VAR_SUCCESS : v <= 600 ? VAR_WARNING : VAR_DANGER,
    deltaDirection: "lower", isNumeric: true,
  },
  {
    key: "logp", label: "LogP", group: "Physicochemical",
    format: (v) => typeof v === "number" ? v.toFixed(2) : "—",
    color: (v) => v < 5 ? VAR_SUCCESS : v <= 6 ? VAR_WARNING : VAR_DANGER,
    deltaDirection: "lower", isNumeric: true,
  },
  {
    key: "tpsa", label: "TPSA", group: "Physicochemical",
    format: (v) => typeof v === "number" ? v.toFixed(1) : "—",
    color: (v) => (v >= 20 && v <= 130) ? VAR_SUCCESS : (v > 130 && v <= 150) || v < 20 ? VAR_WARNING : VAR_DANGER,
    isNumeric: true,
  },
  {
    key: "qed", label: "QED", group: "Physicochemical",
    format: (v) => typeof v === "number" ? v.toFixed(3) : "—",
    color: (v) => v > 0.6 ? VAR_SUCCESS : v >= 0.4 ? VAR_WARNING : VAR_DANGER,
    deltaDirection: "higher", isNumeric: true,
  },
  {
    key: "sa_score", label: "SA Score", group: "Physicochemical",
    format: (v) => typeof v === "number" ? v.toFixed(2) : "—",
    color: (v) => v < 4 ? VAR_SUCCESS : v <= 6 ? VAR_WARNING : VAR_DANGER,
    deltaDirection: "lower", isNumeric: true,
  },
  {
    key: "hbd", label: "HBD", group: "Physicochemical",
    format: (v) => typeof v === "number" ? String(v) : "—",
    color: (v) => v <= 5 ? VAR_SUCCESS : v <= 7 ? VAR_WARNING : VAR_DANGER,
    deltaDirection: "lower", isNumeric: true,
  },
  {
    key: "hba", label: "HBA", group: "Physicochemical",
    format: (v) => typeof v === "number" ? String(v) : "—",
    color: (v) => v <= 10 ? VAR_SUCCESS : v <= 12 ? VAR_WARNING : VAR_DANGER,
    deltaDirection: "lower", isNumeric: true,
  },
  {
    key: "rotatable_bonds", label: "Rot. Bonds", group: "Physicochemical",
    format: (v) => typeof v === "number" ? String(v) : "—",
    color: (v) => v <= 10 ? VAR_SUCCESS : v <= 12 ? VAR_WARNING : VAR_DANGER,
    deltaDirection: "lower", isNumeric: true,
  },
  // Drug-likeness
  {
    key: "lipinski_violations", label: "Lipinski Violations", group: "Drug-likeness",
    format: (v) => typeof v === "number" ? String(v) : "—",
    color: (v) => v === 0 ? VAR_SUCCESS : v === 1 ? VAR_WARNING : VAR_DANGER,
    deltaDirection: "lower", isNumeric: true,
  },
  {
    key: "veber_violations", label: "Veber Violations", group: "Drug-likeness",
    format: (v) => typeof v === "number" ? String(v) : "—",
    color: (v) => v === 0 ? VAR_SUCCESS : VAR_DANGER,
    deltaDirection: "lower", isNumeric: true,
  },
  // Patent
  {
    key: "tanimoto_to_seed", label: "Tc to Seed", group: "Patent",
    format: (v) => typeof v === "number" ? v.toFixed(3) : "—",
    color: (v) => v <= 0.4 ? VAR_SUCCESS : v <= 0.7 ? VAR_SUCCESS : VAR_DANGER,
    isNumeric: true,
  },
  {
    key: "patent_risk", label: "Patent Risk", group: "Patent",
    format: (v) => typeof v === "string" ? v : "—",
    color: (_v) => VAR_MUTED,
    isNumeric: false,
  },
  {
    // Rendering owned by <PriorArtBadge>; format() is unused (see cell-render
    // dispatch at lines ~554/569 which short-circuits to the badge for this key).
    key: "prior_art", label: "Prior Art", group: "Patent",
    format: () => "",
    color: (_v) => VAR_MUTED,
    isNumeric: false,
  },
  // Compliance
  {
    key: "compliance_status", label: "Compliance", group: "Compliance",
    format: (v) => typeof v === "string" ? v : "—",
    color: (_v) => VAR_MUTED,
    isNumeric: false,
  },
];

// Unique groups in order
const GROUPS = [...new Set(PROPERTIES.map((p) => p.group))];

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
        Comparing lead variants...
      </div>
    </div>
  );
}

// =============================================================================
// Source Badge
// =============================================================================

function SourceBadge({ source }: { source?: string }) {
  if (!source) return null;
  const isHop = source === "scaffold_hop";
  return (
    <span
      className="badge"
      style={{
        background: isHop ? "var(--bg-warm)" : "var(--accent)",
        color: isHop ? "var(--text)" : "#fff",
        border: isHop ? "1px solid var(--border)" : "none",
      }}
    >
      {isHop ? "Scaffold Hop" : "MolMIM"}
    </span>
  );
}

// =============================================================================
// Patent Risk Badge
// =============================================================================

function PatentBadge({ risk }: { risk?: string }) {
  if (!risk) return <span style={{ color: "var(--text-muted)" }}>—</span>;
  const cls = risk === "high" ? "danger" : risk === "novel" ? "success" : "success";
  return <span className={`badge ${cls}`}>{risk}</span>;
}

// =============================================================================
// Compliance Badge
// =============================================================================

function ComplianceBadge({ status }: { status?: string }) {
  if (!status) return <span style={{ color: "var(--text-muted)" }}>—</span>;
  const cls = status === "clean" ? "success" : status === "flagged" ? "danger" : "warning";
  return <span className={`badge ${cls}`}>{status}</span>;
}

// =============================================================================
// Prior Art Badge (InChIKey-based disclosure check)
// =============================================================================

function PriorArtBadge({ priorArt }: { priorArt?: Variant["prior_art"] }) {
  // Field missing entirely — upstream tool never attached prior_art (older payload
  // before the disclosed-sentinel fix). Treat the same as "lookup unavailable".
  if (!priorArt) {
    return (
      <span style={{ color: "var(--text-muted)" }} title="Prior art not provided by upstream tool">—</span>
    );
  }
  if (priorArt.disclosed === false) {
    return <span className="badge success" title={priorArt.inchikey || ""}>novel</span>;
  }
  if (priorArt.disclosed === true) {
    const source = priorArt.disclosure_source === "local_122m" ? "122M DB" : "PubChem";
    const label = priorArt.pubchem_cid ? `CID ${priorArt.pubchem_cid}` : "disclosed";
    const title = `Disclosed in ${source}. Composition-of-matter patent likely forfeited.`;
    return <span className="badge danger" title={title}>{label}</span>;
  }
  // disclosed === null (or any other non-boolean) — lookup ran but did not return
  // a verdict (FAVES service unreachable, Redis miss, PubChem rate-limited, etc.).
  // Distinct from a true "—" so we don't silently mislead the chemist into
  // treating it as novel.
  return (
    <span
      style={{ color: "var(--text-muted)", fontStyle: "italic" }}
      title="Prior art lookup unavailable for this variant — disclosure status unknown"
    >
      unchecked
    </span>
  );
}

// =============================================================================
// Delta Cell
// =============================================================================

// =============================================================================
// Scaffold Cluster Color Palette (rotating, for column badges)
// =============================================================================

const CLUSTER_COLORS = [
  "#8B7AA1", // muted purple
  "#7AA1A1", // muted teal
  "#A18E7A", // muted tan
  "#A17A8B", // muted rose
  "#7A8BA1", // muted blue
  "#A1A17A", // muted olive
  "#8BA17A", // muted sage
];

function clusterColor(clusterId?: number): string {
  if (typeof clusterId !== "number" || clusterId < 0) return "var(--text-muted)";
  return CLUSTER_COLORS[clusterId % CLUSTER_COLORS.length];
}

// =============================================================================
// Diversity Summary Panel
// =============================================================================

function DiversitySummary({
  uniqueScaffolds,
  diversityScore,
  nClusters,
  nVariants,
}: {
  uniqueScaffolds?: number;
  diversityScore?: number;
  nClusters?: number;
  nVariants: number;
}) {
  if (typeof uniqueScaffolds !== "number" && typeof nClusters !== "number") {
    return null;
  }

  // Color-code diversity_score: <0.4 = red (QSAR homogeneity), 0.4-0.7 = warning,
  // >0.7 = healthy. These thresholds mirror Theo's patent_risk breakpoints.
  const score = diversityScore ?? 0;
  const scoreColor =
    score >= 0.7 ? "var(--success)" : score >= 0.4 ? "var(--warning)" : "var(--danger)";
  const scoreLabel =
    score >= 0.7 ? "Diverse" : score >= 0.4 ? "Moderate" : "Homogeneous";

  return (
    <div
      style={{
        display: "flex",
        gap: 16,
        alignItems: "center",
        padding: "10px 14px",
        background: "var(--bg-warm)",
        borderLeft: `3px solid ${scoreColor}`,
        borderRadius: 2,
        marginBottom: 12,
        fontSize: 11,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <span
          style={{
            fontSize: 9,
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            color: "var(--text-muted)",
          }}
        >
          Scaffold Diversity
        </span>
        <span style={{ fontSize: 14, fontWeight: 600, color: scoreColor }}>
          {scoreLabel} ({((score) * 100).toFixed(0)}%)
        </span>
      </div>
      <div style={{ height: 32, width: 1, background: "var(--border)" }} />
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <span style={{ fontSize: 9, textTransform: "uppercase", color: "var(--text-muted)" }}>
          Unique Scaffolds
        </span>
        <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text)" }}>
          {uniqueScaffolds ?? "—"} / {nVariants}
        </span>
      </div>
      <div style={{ height: 32, width: 1, background: "var(--border)" }} />
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <span style={{ fontSize: 9, textTransform: "uppercase", color: "var(--text-muted)" }}>
          Butina Clusters
        </span>
        <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text)" }}>
          {nClusters ?? "—"}
        </span>
      </div>
      {score < 0.4 && (
        <div
          style={{
            marginLeft: "auto",
            fontSize: 10,
            color: "var(--danger)",
            fontStyle: "italic",
            maxWidth: 280,
          }}
        >
          QSAR homogeneity warning: variants share too few scaffolds. Consider broader chemotype exploration.
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Cluster Badge (for column headers)
// =============================================================================

function ClusterBadge({ clusterId, clusterSize }: { clusterId?: number; clusterSize?: number }) {
  if (typeof clusterId !== "number" || clusterId < 0) return null;
  const color = clusterColor(clusterId);
  const title = clusterSize && clusterSize > 1
    ? `Cluster #${clusterId + 1} · ${clusterSize} variants share this scaffold`
    : `Cluster #${clusterId + 1} · unique scaffold`;
  return (
    <span
      title={title}
      style={{
        display: "inline-block",
        width: 6,
        height: 6,
        borderRadius: "50%",
        background: color,
        marginLeft: 4,
        verticalAlign: "middle",
      }}
    />
  );
}

function DeltaCell({
  value,
  seedValue,
  prop,
}: {
  value: unknown;
  seedValue: unknown;
  prop: PropertyDef;
}) {
  if (typeof value !== "number" || typeof seedValue !== "number") {
    return <span style={{ color: "var(--text-muted)" }}>—</span>;
  }
  const delta = value - seedValue;
  if (Math.abs(delta) < 0.001) {
    return <span style={{ color: "var(--text-muted)" }}>0</span>;
  }

  // Determine if delta is "good" or "bad"
  let isGood = false;
  if (prop.deltaDirection === "lower") isGood = delta < 0;
  else if (prop.deltaDirection === "higher") isGood = delta > 0;

  const sign = delta > 0 ? "+" : "";
  const color = isGood ? "var(--success)" : "var(--danger)";

  return (
    <span style={{ color, fontFamily: "var(--font-mono)", fontSize: 11 }}>
      {sign}{delta.toFixed(prop.key === "qed" ? 3 : prop.key === "logp" ? 2 : 1)}
    </span>
  );
}

// =============================================================================
// Comparison Table
// =============================================================================

function ComparisonTable({
  seed,
  variants,
  showDeltas,
  sortBy,
  sortDir,
  onSort,
}: {
  seed: Variant;
  variants: Variant[];
  showDeltas: boolean;
  sortBy: string | null;
  sortDir: "asc" | "desc";
  onSort: (key: string) => void;
}) {
  // Sort variants
  const sorted = [...variants];
  if (sortBy) {
    const prop = PROPERTIES.find((p) => p.key === sortBy);
    if (prop?.isNumeric) {
      sorted.sort((a, b) => {
        const av = (a as any)[sortBy] ?? Infinity;
        const bv = (b as any)[sortBy] ?? Infinity;
        return sortDir === "asc" ? av - bv : bv - av;
      });
    }
  }

  const truncSmiles = (s: string, max = 20) =>
    s.length > max ? s.slice(0, max) + "..." : s;

  const colCount = sorted.length + 2; // label col + seed col + N variant cols

  return (
    <div style={{ overflowX: "auto", marginTop: 16 }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: `140px 120px repeat(${sorted.length}, 110px)`,
          gap: 0,
          minWidth: "fit-content",
        }}
      >
        {/* Header row: empty + Seed + variant SMILES */}
        <div style={headerCell}>Property</div>
        <div style={{ ...headerCell, background: "var(--bg-warm)", fontWeight: 600 }}>
          Seed
        </div>
        {sorted.map((v, i) => (
          <div
            key={i}
            style={{
              ...headerCell,
              borderTop: typeof v.scaffold_cluster_id === "number" && v.scaffold_cluster_id >= 0
                ? `3px solid ${clusterColor(v.scaffold_cluster_id)}`
                : undefined,
            }}
            title={v.cluster_note ? `${v.smiles}\n${v.cluster_note}` : v.smiles}
          >
            <div style={{ fontSize: 10, fontFamily: "var(--font-mono)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 100 }}>
              {truncSmiles(v.smiles)}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <SourceBadge source={v.source} />
              <ClusterBadge clusterId={v.scaffold_cluster_id} clusterSize={v.cluster_size} />
            </div>
          </div>
        ))}

        {/* Modification row */}
        <div style={labelCell}>Modification</div>
        <div style={{ ...valueCell, background: "var(--bg-warm)" }}>—</div>
        {sorted.map((v, i) => (
          <div key={i} style={valueCell} title={v.modification}>
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
              {v.modification ? (v.modification.length > 15 ? v.modification.slice(0, 15) + "..." : v.modification) : "—"}
            </span>
          </div>
        ))}

        {/* Property rows grouped */}
        {GROUPS.map((group) => {
          const groupProps = PROPERTIES.filter((p) => p.group === group);
          return [
            // Group header
            <div key={`gh-${group}`} style={{ ...groupHeaderCell, gridColumn: `1 / ${colCount + 1}` }}>
              {group}
            </div>,
            // Property rows
            ...groupProps.flatMap((prop) => {
              const seedVal = (seed as any)[prop.key];
              const rows = [
                // Main value row
                <div key={`l-${prop.key}`} style={{ ...labelCell, cursor: prop.isNumeric ? "pointer" : "default" }} onClick={() => prop.isNumeric && onSort(prop.key)}>
                  {prop.label}
                  {sortBy === prop.key && (
                    <span style={{ marginLeft: 4, fontSize: 9 }}>{sortDir === "asc" ? "▲" : "▼"}</span>
                  )}
                </div>,
                <div key={`s-${prop.key}`} style={{ ...valueCell, background: "var(--bg-warm)" }}>
                  {prop.key === "patent_risk" ? <PatentBadge risk={seedVal as string} /> :
                   prop.key === "compliance_status" ? <ComplianceBadge status={seedVal as string} /> :
                   prop.key === "prior_art" ? <PriorArtBadge priorArt={seedVal as Variant["prior_art"]} /> :
                   <span style={{
                     fontFamily: prop.isNumeric ? "var(--font-mono)" : undefined,
                     fontWeight: 500,
                     color: typeof seedVal === "number" ? prop.color(seedVal) : "var(--text-muted)",
                   }}>
                     {prop.format(seedVal)}
                   </span>}
                </div>,
                ...sorted.map((v, i) => {
                  const val = (v as any)[prop.key];
                  return (
                    <div key={`v-${prop.key}-${i}`} style={valueCell}>
                      {prop.key === "patent_risk" ? <PatentBadge risk={val as string} /> :
                       prop.key === "compliance_status" ? <ComplianceBadge status={val as string} /> :
                       prop.key === "prior_art" ? <PriorArtBadge priorArt={val as Variant["prior_art"]} /> :
                       <span style={{
                         fontFamily: prop.isNumeric ? "var(--font-mono)" : undefined,
                         fontWeight: 500,
                         color: typeof val === "number" ? prop.color(val) : "var(--text-muted)",
                       }}>
                         {prop.format(val)}
                       </span>}
                    </div>
                  );
                }),
              ];

              // Delta row (only for numeric properties when showDeltas is on)
              if (showDeltas && prop.isNumeric && prop.deltaDirection) {
                rows.push(
                  <div key={`dl-${prop.key}`} style={{ ...labelCell, fontSize: 10, color: "var(--text-muted)", fontStyle: "italic" }}>
                    Δ {prop.label}
                  </div>,
                  <div key={`ds-${prop.key}`} style={{ ...valueCell, background: "var(--bg-warm)" }}>
                    <span style={{ color: "var(--text-muted)", fontSize: 10 }}>ref</span>
                  </div>,
                  ...sorted.map((v, i) => (
                    <div key={`dv-${prop.key}-${i}`} style={valueCell}>
                      <DeltaCell value={(v as any)[prop.key]} seedValue={seedVal} prop={prop} />
                    </div>
                  )),
                );
              }

              return rows;
            }),
          ];
        })}
      </div>
    </div>
  );
}

// =============================================================================
// Cell Styles
// =============================================================================

const headerCell: React.CSSProperties = {
  padding: "8px 10px",
  fontSize: 10,
  fontWeight: 500,
  color: "var(--text-muted)",
  borderBottom: "2px solid var(--border)",
  display: "flex",
  flexDirection: "column",
  gap: 4,
  alignItems: "center",
  textAlign: "center",
};

const labelCell: React.CSSProperties = {
  padding: "6px 10px",
  fontSize: 11,
  fontWeight: 500,
  color: "var(--text)",
  borderBottom: "1px solid var(--border)",
  display: "flex",
  alignItems: "center",
  position: "sticky",
  left: 0,
  background: "var(--bg-card)",
  zIndex: 1,
};

const valueCell: React.CSSProperties = {
  padding: "6px 10px",
  fontSize: 12,
  borderBottom: "1px solid var(--border)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  textAlign: "center",
};

const groupHeaderCell: React.CSSProperties = {
  padding: "8px 10px",
  fontSize: 9,
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: "0.08em",
  color: "var(--accent)",
  background: "var(--bg)",
  borderBottom: "1px solid var(--border)",
  borderTop: "1px solid var(--border)",
};

// =============================================================================
// Main Component
// =============================================================================

export default function LeadComparison({
  toolInputs,
  toolInputsPartial,
  toolResult,
}: LeadComparisonProps) {
  const height = toolInputs?.height ?? toolInputsPartial?.height ?? 600;
  const isStreaming = !toolInputs && !toolResult;

  const [showDeltas, setShowDeltas] = useState(false);
  const [sortBy, setSortBy] = useState<string | null>("qed");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  if (isStreaming) {
    return <LoadingShimmer height={height} />;
  }

  const d = useViewData<LeadComparisonInput>({ toolInputs, toolResult });

  const seed = d.seed || { smiles: d.seed_smiles || d.input_smiles || "" };
  const variants = d.variants || [];

  if (variants.length === 0) {
    return (
      <div className="panel" style={{ padding: 24, textAlign: "center" }}>
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>No variants to compare</div>
      </div>
    );
  }

  const handleSort = (key: string) => {
    if (sortBy === key) {
      setSortDir((prev) => (prev === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(key);
      // Default sort direction based on property
      const prop = PROPERTIES.find((p) => p.key === key);
      setSortDir(prop?.deltaDirection === "higher" ? "desc" : "asc");
    }
  };

  return (
    <div className="lead-comparison" style={{ width: "100%" }}>
      {/* Header */}
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
            Lead Comparison
            <span style={{ fontSize: 13, fontWeight: 400, color: "var(--text-muted)", marginLeft: 8 }}>
              {variants.length} variant{variants.length !== 1 ? "s" : ""}
            </span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <button
            className={`btn ${showDeltas ? "active" : ""}`}
            onClick={() => setShowDeltas(!showDeltas)}
          >
            {showDeltas ? "Hide Deltas" : "Show Deltas"}
          </button>
        </div>
      </div>

      {/* Seed SMILES */}
      <div style={{ marginBottom: 12, fontSize: 11, color: "var(--text-muted)" }}>
        Seed:{" "}
        <code
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            background: "var(--bg-warm)",
            padding: "2px 6px",
            borderRadius: 2,
          }}
          title={seed.smiles}
        >
          {seed.smiles.length > 60 ? seed.smiles.slice(0, 60) + "..." : seed.smiles}
        </code>
      </div>

      {/* Scaffold Diversity Summary (Theo P0) */}
      <DiversitySummary
        uniqueScaffolds={d.unique_scaffolds}
        diversityScore={d.diversity_score}
        nClusters={d.n_clusters}
        nVariants={variants.length}
      />

      {/* Similarity Filter Summary (Theo P1) — only renders if user passed custom
          ranges OR variants were filtered. Default run with zero filters stays
          visually clean. */}
      {(d.filtered_by_similarity && d.filtered_by_similarity > 0) ||
       (d.similarity_range && (d.similarity_range.min !== 0.3 || d.similarity_range.max !== 0.85)) ||
       (d.patent_risk_thresholds && (d.patent_risk_thresholds.low !== 0.4 || d.patent_risk_thresholds.high !== 0.7)) ? (
        <div
          style={{
            display: "flex",
            gap: 12,
            alignItems: "center",
            flexWrap: "wrap",
            padding: "8px 14px",
            background: "var(--bg-warm)",
            borderLeft: "3px solid var(--accent)",
            borderRadius: 2,
            marginBottom: 12,
            fontSize: 11,
          }}
        >
          <span style={{ fontSize: 9, textTransform: "uppercase", color: "var(--text-muted)", letterSpacing: "0.04em" }}>
            Active Filters
          </span>
          {d.similarity_range && (
            <span title="Tanimoto similarity window to seed (variants outside this range are filtered out)">
              <strong style={{ color: "var(--text)" }}>Tc:</strong>{" "}
              <span style={{ fontFamily: "var(--font-mono)", color: "var(--accent)" }}>
                [{d.similarity_range.min}–{d.similarity_range.max}]
              </span>
            </span>
          )}
          {d.patent_risk_thresholds && (
            <span title="Patent risk classification breakpoints (Tc ≥ high = same family risk, low–high = scaffold hop, < low = novel)">
              <strong style={{ color: "var(--text)" }}>Risk:</strong>{" "}
              <span style={{ fontFamily: "var(--font-mono)", color: "var(--accent)" }}>
                low &lt; {d.patent_risk_thresholds.low} · high ≥ {d.patent_risk_thresholds.high}
              </span>
            </span>
          )}
          {d.filtered_by_similarity && d.filtered_by_similarity > 0 && (
            <span style={{ color: "var(--warning)", marginLeft: "auto", fontStyle: "italic" }}>
              {d.filtered_by_similarity} variant{d.filtered_by_similarity !== 1 ? "s" : ""} excluded by Tc window
            </span>
          )}
        </div>
      ) : null}

      {/* Comparison Table */}
      <ComparisonTable
        seed={seed}
        variants={variants}
        showDeltas={showDeltas}
        sortBy={sortBy}
        sortDir={sortDir}
        onSort={handleSort}
      />

      {/* Legend */}
      <div
        style={{
          marginTop: 16,
          padding: "10px 14px",
          background: "var(--bg-warm)",
          borderRadius: 2,
          display: "flex",
          gap: 16,
          flexWrap: "wrap",
          fontSize: 10,
          color: "var(--text-muted)",
        }}
      >
        <span><span style={{ color: "var(--success)" }}>●</span> Drug-like range</span>
        <span><span style={{ color: "var(--warning)" }}>●</span> Borderline</span>
        <span><span style={{ color: "var(--danger)" }}>●</span> Out of range</span>
        {showDeltas && (
          <>
            <span style={{ borderLeft: "1px solid var(--border)", paddingLeft: 16 }}>
              <span style={{ color: "var(--success)" }}>Δ−</span> Improved
            </span>
            <span>
              <span style={{ color: "var(--danger)" }}>Δ+</span> Degraded
            </span>
          </>
        )}
      </div>
    </div>
  );
}
