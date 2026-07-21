/**
 * NovoMCP Molecule Results Table Viewer
 *
 * Shared viewer for tools that return lists of molecules with property
 * annotations — `screen_library`, `search_similar`, `filter_molecules`,
 * `batch_profile`. Each tool's shape varies slightly (list field name,
 * per-row nesting depth, which annotation blocks are populated), so the
 * viewer is deliberately shape-tolerant:
 *
 *   - List field resolution: data.results ?? data.molecules ?? data.hits
 *     ?? data.profiles ?? []
 *   - Per-row flattening: row.data?.properties ?? row.properties ?? row
 *     (batch_profile nests enriched molecules under `.data`; others put
 *     properties at the top level)
 *   - Optional columns render only when at least one row has the field
 *     (no empty toxicity column when search_similar didn't return ADMET)
 *
 * Columns: SMILES (truncated) · MW · LogP · TPSA · QED · Compliance
 * badge · Alerts badge · Toxicity badge. Sortable by every numeric
 * column. Row-click → Claude gets a grounded question about that
 * specific molecule.
 */

import { useState } from "react";
import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types — maximally tolerant. Every field optional; we normalize at read time.
// =============================================================================

interface Properties {
  molecular_weight?: number;
  mw?: number;
  logp?: number;
  tpsa?: number;
  qed?: number;
  hbd_count?: number;
  hba_count?: number;
  hbd?: number;
  hba?: number;
  rotatable_bond_count?: number;
  rotatable_bonds?: number;
  aromatic_ring_count?: number;
  fsp3?: number;
  complexity?: number;
  [key: string]: unknown;
}

interface Compliance {
  status?: string;
  overall_status?: string;
  is_dea_controlled?: boolean;
  is_fda_banned?: boolean;
  is_cwc_scheduled?: boolean;
  is_epa_pbt?: boolean;
  is_eu_reach_banned?: boolean;
  is_whitelisted?: boolean;
  faves_flag_count?: number;
  [key: string]: unknown;
}

interface Admet {
  overall_toxicity_score?: number;
  is_aggregator_risk?: boolean;
  [key: string]: unknown;
}

interface StructuralAlerts {
  has_pains?: boolean;
  pains_count?: number;
  has_reactive_groups?: boolean;
  has_structural_alerts?: boolean;
  structural_alert_count?: number;
  [key: string]: unknown;
}

interface MoleculeRow {
  smiles?: string;
  source?: string;
  in_database?: boolean;
  // Batch-profile enriched molecules nest under `.data`; others flatten.
  data?: {
    properties?: Properties;
    admet?: Admet;
    compliance?: Compliance;
    structural_alerts?: StructuralAlerts;
  };
  properties?: Properties;
  admet?: Admet;
  compliance?: Compliance;
  structural_alerts?: StructuralAlerts;
  // Screen_library + filter_molecules occasionally flatten properties to the
  // top level too — e.g. direct molecular_weight / logp / qed. Capture via
  // pass-through keys.
  [key: string]: unknown;
}

interface ResultsTableInput {
  // The list field varies by tool. Resolve in order.
  results?: MoleculeRow[];
  molecules?: MoleculeRow[];
  hits?: MoleculeRow[];
  profiles?: MoleculeRow[];
  candidates?: MoleculeRow[];

  // Summary stats (screen_library provides this; others may too).
  summary?: {
    total?: number;
    known?: number;
    novel?: number;
    clean?: number;
    flagged?: number;
    controlled?: number;
    [key: string]: unknown;
  };
  total?: number;
  known_molecules?: number;
  novel_molecules?: number;

  query?: string;
  search_type?: string;
  context_applied?: boolean;
  [key: string]: unknown;
}

type ResultsTableProps = ViewProps<ResultsTableInput>;

// =============================================================================
// Row normalization — flatten whatever shape arrived into a common view model.
// =============================================================================

interface NormalizedRow {
  smiles: string;
  source: string;
  inDatabase: boolean;
  mw: number | null;
  logp: number | null;
  tpsa: number | null;
  qed: number | null;
  hbd: number | null;
  hba: number | null;
  complianceStatus: string | null;
  complianceBlocked: boolean;
  toxicityScore: number | null;
  alertCount: number;
  hasAlerts: boolean;
  raw: MoleculeRow;
}

function pickNum(...values: unknown[]): number | null {
  for (const v of values) {
    if (typeof v === "number" && Number.isFinite(v)) return v;
    if (typeof v === "string" && v !== "") {
      const n = Number(v);
      if (Number.isFinite(n)) return n;
    }
  }
  return null;
}

function normalize(row: MoleculeRow): NormalizedRow {
  const props = row.data?.properties ?? row.properties ?? {};
  const admet = row.data?.admet ?? row.admet ?? {};
  const compliance = row.data?.compliance ?? row.compliance ?? {};
  const alerts = row.data?.structural_alerts ?? row.structural_alerts ?? {};
  const topLevel = row as Record<string, unknown>;

  const complianceStatus =
    typeof compliance.status === "string" ? compliance.status :
    typeof compliance.overall_status === "string" ? compliance.overall_status :
    null;
  const complianceBlocked = [
    compliance.is_dea_controlled,
    compliance.is_fda_banned,
    compliance.is_cwc_scheduled,
    compliance.is_epa_pbt,
    compliance.is_eu_reach_banned,
  ].some((flag) => flag === true) || complianceStatus === "STOP" || complianceStatus === "BLOCKED";

  const alertCount =
    (alerts.structural_alert_count as number | undefined) ??
    ((alerts.pains_count as number | undefined) ?? 0);
  const hasAlerts =
    alerts.has_structural_alerts === true ||
    alerts.has_pains === true ||
    alerts.has_reactive_groups === true ||
    alertCount > 0;

  return {
    smiles: row.smiles ?? "",
    source: row.source ?? "",
    inDatabase: row.in_database ?? false,
    mw: pickNum(props.molecular_weight, props.mw, topLevel.molecular_weight, topLevel.mw),
    logp: pickNum(props.logp, props.xlogp, topLevel.logp, topLevel.xlogp),
    tpsa: pickNum(props.tpsa, topLevel.tpsa),
    qed: pickNum(props.qed, topLevel.qed),
    hbd: pickNum(props.hbd_count, props.hbd, topLevel.hbd_count, topLevel.hbd),
    hba: pickNum(props.hba_count, props.hba, topLevel.hba_count, topLevel.hba),
    complianceStatus,
    complianceBlocked,
    toxicityScore: pickNum(admet.overall_toxicity_score),
    alertCount,
    hasAlerts,
    raw: row,
  };
}

// =============================================================================
// Sorting
// =============================================================================

type SortKey = "mw" | "logp" | "tpsa" | "qed" | "toxicityScore" | "alertCount" | null;

function sortRows(rows: NormalizedRow[], key: SortKey, direction: "asc" | "desc"): NormalizedRow[] {
  if (!key) return rows;
  const sorted = [...rows].sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return direction === "asc" ? av - bv : bv - av;
  });
  return sorted;
}

// =============================================================================
// Truncated SMILES with tooltip
// =============================================================================

function SmilesCell({ smiles }: { smiles: string }) {
  const truncated = smiles.length > 32 ? smiles.slice(0, 32) + "…" : smiles;
  return (
    <span
      title={smiles}
      style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text)" }}
    >
      {truncated}
    </span>
  );
}

// =============================================================================
// Compliance badge
// =============================================================================

function ComplianceBadge({ status, blocked }: { status: string | null; blocked: boolean }) {
  if (!status && !blocked) {
    return <span style={{ fontSize: 10, color: "var(--text-muted)" }}>—</span>;
  }
  if (blocked || status === "STOP" || status === "BLOCKED") {
    return (
      <span className="badge danger" title="Blocked by compliance check">
        blocked
      </span>
    );
  }
  if (status === "CAUTION" || status === "CONDITIONAL" || status === "REVIEW_REQUIRED") {
    return (
      <span className="badge warning" title={status}>
        caution
      </span>
    );
  }
  if (status === "PROCEED" || status === "PASS") {
    return (
      <span className="badge success" title={status}>
        pass
      </span>
    );
  }
  return <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{status}</span>;
}

// =============================================================================
// Toxicity / alert badges
// =============================================================================

function ToxicityBadge({ score }: { score: number | null }) {
  if (score == null) return <span style={{ fontSize: 10, color: "var(--text-muted)" }}>—</span>;
  const color =
    score > 0.7 ? "var(--danger)" :
    score > 0.4 ? "var(--warning)" :
    "var(--success)";
  return (
    <span
      style={{
        fontSize: 10,
        fontFamily: "var(--font-mono)",
        color,
        fontWeight: 600,
      }}
      title="Overall toxicity score"
    >
      {score.toFixed(2)}
    </span>
  );
}

function AlertsBadge({ hasAlerts, count }: { hasAlerts: boolean; count: number }) {
  if (!hasAlerts) {
    return <span style={{ fontSize: 10, color: "var(--success)" }}>clean</span>;
  }
  return (
    <span
      style={{ fontSize: 10, color: "var(--warning)", fontWeight: 500 }}
      title={`${count} structural alerts`}
    >
      {count > 0 ? `${count} alert${count === 1 ? "" : "s"}` : "alerts"}
    </span>
  );
}

// =============================================================================
// Main viewer
// =============================================================================

export default function ResultsTableViewer(props: ResultsTableProps) {
  const { toolInputs, toolResult, sendMessage } = props;
  const data = useViewData<ResultsTableInput>(props);

  const [sortKey, setSortKey] = useState<SortKey>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const isStreaming = !toolInputs && !toolResult;
  if (isStreaming) {
    return (
      <div className="loading">
        <div className="loading-spinner" />
        <span>Loading results…</span>
      </div>
    );
  }

  const rawRows =
    data.results ??
    data.molecules ??
    data.hits ??
    data.profiles ??
    data.candidates ??
    [];
  const rows = rawRows.map(normalize);
  const displayRows = sortRows(rows, sortKey, sortDir);

  // Show a column only when at least one row has data for it. Keeps the
  // table narrow when tools return just properties (no ADMET etc.).
  const showTox = rows.some((r) => r.toxicityScore != null);
  const showAlerts = rows.some((r) => r.hasAlerts || r.alertCount > 0);
  const showCompliance = rows.some((r) => r.complianceStatus != null || r.complianceBlocked);

  // Summary counts (prefer explicit summary block, fall back to derived).
  const total = data.summary?.total ?? data.total ?? rows.length;
  const known = data.summary?.known ?? data.known_molecules ?? rows.filter((r) => r.inDatabase).length;
  const novel = data.summary?.novel ?? data.novel_molecules ?? rows.filter((r) => !r.inDatabase).length;
  const flagged = data.summary?.flagged ?? rows.filter((r) => r.complianceBlocked || r.hasAlerts).length;

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const handleRowClick = (row: NormalizedRow) => {
    const bits: string[] = [];
    if (row.mw != null) bits.push(`MW ${row.mw.toFixed(1)}`);
    if (row.logp != null) bits.push(`LogP ${row.logp.toFixed(2)}`);
    if (row.qed != null) bits.push(`QED ${row.qed.toFixed(2)}`);
    if (row.toxicityScore != null) bits.push(`toxicity ${row.toxicityScore.toFixed(2)}`);
    if (row.complianceBlocked) bits.push("compliance BLOCKED");
    else if (row.complianceStatus) bits.push(`compliance ${row.complianceStatus}`);
    if (row.hasAlerts) bits.push(`${row.alertCount || "some"} structural alerts`);
    const summary = bits.length > 0 ? ` (${bits.join(", ")})` : "";
    sendMessage({
      role: "user",
      content: [
        {
          type: "text",
          text:
            `I clicked this molecule in the results table: \`${row.smiles}\`${summary}. ` +
            `Give me a quick read on whether it's worth advancing — would you dock this, ` +
            `push it through optimization, or kick it out of the library? Flag anything in the ` +
            `properties or compliance profile that should change the decision.`,
        },
      ],
    });
  };

  return (
    <div className="results-table-viewer" style={{ width: "100%" }}>
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
          <div style={{ fontSize: 10, fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-muted)" }}>
            Novo<span style={{ color: "var(--accent)" }}>MCP</span>
          </div>
          <div style={{ fontFamily: "var(--font-serif)", fontSize: 18, color: "var(--text)", marginTop: 4 }}>
            Molecule Results
          </div>
          {data.query && (
            <div
              style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text-muted)", marginTop: 4 }}
              title={data.query}
            >
              {data.query}
            </div>
          )}
        </div>
        <div style={{ textAlign: "right", fontSize: 10, color: "var(--text-muted)", lineHeight: 1.5 }}>
          {data.search_type && <div>{data.search_type}</div>}
          {data.context_applied && <div style={{ color: "var(--accent)" }}>context-aware</div>}
        </div>
      </div>

      {/* Summary cards */}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 16 }}>
        <SummaryCard label="Total" value={total} color="var(--accent)" />
        {known > 0 && <SummaryCard label="Known" value={known} color="var(--text-muted)" />}
        {novel > 0 && <SummaryCard label="Novel" value={novel} color="var(--text-muted)" />}
        {flagged > 0 && <SummaryCard label="Flagged" value={flagged} color="var(--warning)" />}
      </div>

      {/* Results table */}
      {rows.length === 0 ? (
        <div className="panel" style={{ textAlign: "center", color: "var(--text-muted)", fontSize: 12 }}>
          No molecules in result set.
        </div>
      ) : (
        <div className="panel">
          <div
            className="panel-title"
            style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
          >
            <span>Results ({rows.length})</span>
            <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-muted)" }}>
              click any row · sort by column header
            </span>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border)" }}>
                  <Th>SMILES</Th>
                  <Th sortable onClick={() => handleSort("mw")} active={sortKey === "mw"} dir={sortDir} align="right">MW</Th>
                  <Th sortable onClick={() => handleSort("logp")} active={sortKey === "logp"} dir={sortDir} align="right">LogP</Th>
                  <Th sortable onClick={() => handleSort("tpsa")} active={sortKey === "tpsa"} dir={sortDir} align="right">TPSA</Th>
                  <Th sortable onClick={() => handleSort("qed")} active={sortKey === "qed"} dir={sortDir} align="right">QED</Th>
                  {showCompliance && <Th align="center">Compliance</Th>}
                  {showAlerts && <Th align="center">Alerts</Th>}
                  {showTox && <Th sortable onClick={() => handleSort("toxicityScore")} active={sortKey === "toxicityScore"} dir={sortDir} align="right">Tox</Th>}
                </tr>
              </thead>
              <tbody>
                {displayRows.map((row, i) => (
                  <tr
                    key={i}
                    onClick={() => handleRowClick(row)}
                    style={{
                      borderBottom: "1px solid var(--border)",
                      cursor: "pointer",
                    }}
                    title="Click to ask about this molecule"
                  >
                    <Td>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <SmilesCell smiles={row.smiles} />
                        {row.inDatabase && (
                          <span style={{ fontSize: 9, color: "var(--text-muted)", fontStyle: "italic" }}>in DB</span>
                        )}
                      </div>
                    </Td>
                    <Td align="right">{row.mw != null ? row.mw.toFixed(1) : "—"}</Td>
                    <Td align="right">{row.logp != null ? row.logp.toFixed(2) : "—"}</Td>
                    <Td align="right">{row.tpsa != null ? row.tpsa.toFixed(1) : "—"}</Td>
                    <Td align="right">{row.qed != null ? row.qed.toFixed(2) : "—"}</Td>
                    {showCompliance && (
                      <Td align="center">
                        <ComplianceBadge status={row.complianceStatus} blocked={row.complianceBlocked} />
                      </Td>
                    )}
                    {showAlerts && (
                      <Td align="center">
                        <AlertsBadge hasAlerts={row.hasAlerts} count={row.alertCount} />
                      </Td>
                    )}
                    {showTox && (
                      <Td align="right">
                        <ToxicityBadge score={row.toxicityScore} />
                      </Td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 8, lineHeight: 1.5 }}>
            Columns show only when the tool populated that field. Click any row to use the
            properties and compliance profile as context for a follow-up question.
          </div>
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Small helpers
// =============================================================================

function SummaryCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div
      style={{
        padding: "8px 12px",
        background: "var(--bg-warm)",
        borderRadius: 2,
        borderLeft: `3px solid ${color}`,
        minWidth: 80,
      }}
    >
      <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
        {label}
      </div>
      <div style={{ fontSize: 18, fontFamily: "var(--font-mono)", fontWeight: 600, color, marginTop: 2 }}>
        {value}
      </div>
    </div>
  );
}

function Th({
  children,
  align = "left",
  sortable = false,
  onClick,
  active = false,
  dir = "desc",
}: {
  children: React.ReactNode;
  align?: "left" | "right" | "center";
  sortable?: boolean;
  onClick?: () => void;
  active?: boolean;
  dir?: "asc" | "desc";
}) {
  return (
    <th
      onClick={onClick}
      style={{
        textAlign: align,
        padding: "6px 8px",
        color: active ? "var(--text)" : "var(--text-muted)",
        fontSize: 10,
        fontWeight: 500,
        cursor: sortable ? "pointer" : undefined,
        userSelect: "none",
      }}
    >
      {children}
      {sortable && active && <span style={{ marginLeft: 4 }}>{dir === "asc" ? "↑" : "↓"}</span>}
    </th>
  );
}

function Td({
  children,
  align = "left",
}: {
  children: React.ReactNode;
  align?: "left" | "right" | "center";
}) {
  return (
    <td
      style={{
        padding: "6px 8px",
        textAlign: align,
        fontFamily: align === "right" ? "var(--font-mono)" : undefined,
        color: "var(--text)",
      }}
    >
      {children}
    </td>
  );
}
