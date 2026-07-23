/**
 * NovoMCP Pipeline Audit Viewer
 *
 * Per-molecule audit trail for pipeline executions.
 * Shows disposition breakdown, per-molecule table with tool results,
 * and flags for excluded/invalid compounds.
 */
import { useState } from "react";
import type { ViewProps } from "./create-app.tsx";

// =============================================================================
// Types
// =============================================================================

interface AuditEntry {
  row_index: number;
  input_smiles: string | null;
  canonical_smiles: string | null;
  standardization: string;
  valid: boolean;
  tools_applied: Record<string, { status: string; key_results?: Record<string, unknown>; flags?: string[]; error?: string }>;
  disposition: string;
  exclusion_reason: string | null;
}

interface AuditSummary {
  total: number;
  included: number;
  excluded: number;
  invalid_smiles: number;
  compliance_blocks: number;
  processing_errors: number;
}

interface StageFunnel {
  stage: string;
  label: string;
  input_count: number;
  output_count: number;
  excluded_count: number;
  excluded_reasons?: Record<string, number>;
}

interface AuditInput {
  pipeline_id?: string;
  source_table?: string;
  rows_pulled?: number;
  rows_processed?: number;
  processing_tools?: string[];
  status?: string;
  audit_summary?: AuditSummary;
  molecule_audit_log?: AuditEntry[];
  stage_funnel?: StageFunnel[];
}

type AuditProps = ViewProps<AuditInput>;

// =============================================================================
// Summary Bar
// =============================================================================

function SummaryBar({ summary }: { summary: AuditSummary }) {
  const total = summary.total || 1;
  const includedPct = Math.round((summary.included / total) * 100);
  const excludedPct = Math.round((summary.excluded / total) * 100);

  return (
    <div style={{ marginBottom: 16 }}>
      {/* Progress bar */}
      <div style={{ display: "flex", height: 24, borderRadius: 4, overflow: "hidden", border: "1px solid var(--border)" }}>
        <div style={{ width: `${includedPct}%`, background: "var(--success)", display: "flex", alignItems: "center", justifyContent: "center" }}>
          {includedPct > 15 && <span style={{ fontSize: 10, color: "white", fontWeight: 600 }}>{summary.included}</span>}
        </div>
        <div style={{ width: `${excludedPct}%`, background: "var(--danger)", display: "flex", alignItems: "center", justifyContent: "center" }}>
          {excludedPct > 15 && <span style={{ fontSize: 10, color: "white", fontWeight: 600 }}>{summary.excluded}</span>}
        </div>
        {summary.invalid_smiles > 0 && (
          <div style={{ width: `${Math.round((summary.invalid_smiles / total) * 100)}%`, background: "var(--text-muted)", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <span style={{ fontSize: 10, color: "white", fontWeight: 600 }}>{summary.invalid_smiles}</span>
          </div>
        )}
      </div>

      {/* Labels */}
      <div style={{ display: "flex", gap: 16, marginTop: 8, fontSize: 11 }}>
        <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <span style={{ width: 8, height: 8, background: "var(--success)", borderRadius: 2 }} />
          <span style={{ color: "var(--text-soft)" }}>{summary.included} included</span>
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <span style={{ width: 8, height: 8, background: "var(--danger)", borderRadius: 2 }} />
          <span style={{ color: "var(--text-soft)" }}>{summary.excluded} excluded</span>
        </span>
        {summary.invalid_smiles > 0 && (
          <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ width: 8, height: 8, background: "var(--text-muted)", borderRadius: 2 }} />
            <span style={{ color: "var(--text-soft)" }}>{summary.invalid_smiles} invalid</span>
          </span>
        )}
        {summary.compliance_blocks > 0 && (
          <span style={{ fontSize: 11, color: "var(--danger)" }}>
            {summary.compliance_blocks} compliance blocks
          </span>
        )}
      </div>
    </div>
  );
}

// =============================================================================
// Inverted Funnel Visualization
// =============================================================================

function InvertedFunnel({ stages, totalInput }: { stages: StageFunnel[]; totalInput: number }) {
  const maxCount = totalInput || Math.max(...stages.map((s) => s.input_count), 1);

  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ fontSize: 10, fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-muted)", marginBottom: 10 }}>
        Processing Funnel
      </div>

      {/* Input bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: 10, color: "var(--text-muted)", width: 90, textAlign: "right", flexShrink: 0 }}>Input</span>
        <div style={{ flex: 1, height: 20, background: "var(--accent)", borderRadius: 2, position: "relative" }}>
          <span style={{ position: "absolute", right: 6, top: 2, fontSize: 10, color: "white", fontWeight: 600 }}>{totalInput}</span>
        </div>
      </div>

      {stages.map((stage, i) => {
        const pct = (stage.output_count / maxCount) * 100;
        const excluded = stage.excluded_count;
        const color = excluded > 0 ? "var(--accent)" : "var(--success)";
        const reasons = stage.excluded_reasons || {};
        const reasonText = Object.entries(reasons).map(([r, c]) => `${c} ${r}`).join(", ");

        return (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
            <span style={{ fontSize: 10, color: "var(--text-muted)", width: 90, textAlign: "right", flexShrink: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {stage.label}
            </span>
            <div style={{ flex: 1, position: "relative" }}>
              <div style={{
                width: `${Math.max(pct, 2)}%`,
                height: 20,
                background: color,
                borderRadius: 2,
                transition: "width 0.5s ease-out",
                position: "relative",
              }}>
                <span style={{ position: "absolute", right: 6, top: 2, fontSize: 10, color: "white", fontWeight: 600 }}>
                  {stage.output_count}
                </span>
              </div>
            </div>
            {excluded > 0 && (
              <span style={{ fontSize: 9, color: "var(--danger)", flexShrink: 0, minWidth: 60 }}>
                −{excluded}{reasonText ? ` (${reasonText})` : ""}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

// =============================================================================
// Derived Funnel (when stage_funnel is not available, derive from audit log)
// =============================================================================

function deriveFunnelFromLog(log: AuditEntry[], tools: string[]): StageFunnel[] {
  const stages: StageFunnel[] = [];
  let remaining = log.length;

  // Stage 0: SMILES validation
  const invalidCount = log.filter((e) => !e.valid).length;
  stages.push({
    stage: "smiles_validation",
    label: "Valid SMILES",
    input_count: log.length,
    output_count: log.length - invalidCount,
    excluded_count: invalidCount,
    excluded_reasons: invalidCount > 0 ? { invalid_smiles: invalidCount } : {},
  });
  remaining -= invalidCount;

  // Per-tool stages
  const toolLabels: Record<string, string> = {
    calculate_properties: "Properties",
    predict_admet: "ADMET Screen",
    check_compliance: "Compliance",
    optimize_molecule: "Optimization",
  };

  for (const tool of tools) {
    const validEntries = log.filter((e) => e.valid);
    const toolErrors = validEntries.filter((e) => {
      const ta = e.tools_applied[tool];
      return ta && ta.status === "error";
    }).length;

    const toolExcluded = validEntries.filter((e) => {
      return e.exclusion_reason?.startsWith(`compliance_block`) && tool === "check_compliance";
    }).length;

    const excluded = toolErrors + toolExcluded;
    stages.push({
      stage: tool,
      label: toolLabels[tool] || tool.replace(/_/g, " "),
      input_count: remaining,
      output_count: remaining - excluded,
      excluded_count: excluded,
      excluded_reasons: excluded > 0 ? { [tool + "_failure"]: toolErrors, compliance_block: toolExcluded } : {},
    });
    remaining -= excluded;
  }

  return stages;
}

// =============================================================================
// Library Composition
// =============================================================================

function LibraryComposition({ log }: { log: AuditEntry[] }) {
  const included = log.filter((e) => e.disposition === "included");
  if (included.length === 0) return null;

  const mws: number[] = [];
  const logps: number[] = [];
  const qeds: number[] = [];

  for (const entry of included) {
    const props = entry.tools_applied?.calculate_properties?.key_results || {};
    if (typeof props.mw === "number") mws.push(props.mw);
    if (typeof props.logp === "number") logps.push(props.logp);
    if (typeof props.qed === "number") qeds.push(props.qed);
  }

  const range = (arr: number[]) => arr.length > 0 ? `${Math.min(...arr).toFixed(1)}–${Math.max(...arr).toFixed(1)}` : "—";
  const mean = (arr: number[]) => arr.length > 0 ? (arr.reduce((a, b) => a + b, 0) / arr.length).toFixed(2) : "—";

  return (
    <div style={{
      display: "flex",
      gap: 12,
      padding: 12,
      background: "var(--bg-warm)",
      border: "1px solid var(--border)",
      borderRadius: 4,
      marginBottom: 16,
      flexWrap: "wrap",
    }}>
      <div style={{ textAlign: "center", flex: "1 1 60px" }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: "var(--success)" }}>{included.length}</div>
        <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase" }}>Ready</div>
      </div>
      <div style={{ textAlign: "center", flex: "1 1 60px" }}>
        <div style={{ fontSize: 12, fontWeight: 500, color: "var(--text)" }}>{range(mws)}</div>
        <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase" }}>MW (Da)</div>
      </div>
      <div style={{ textAlign: "center", flex: "1 1 60px" }}>
        <div style={{ fontSize: 12, fontWeight: 500, color: "var(--text)" }}>{range(logps)}</div>
        <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase" }}>LogP</div>
      </div>
      <div style={{ textAlign: "center", flex: "1 1 60px" }}>
        <div style={{ fontSize: 12, fontWeight: 500, color: "var(--text)" }}>{mean(qeds)}</div>
        <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase" }}>Mean QED</div>
      </div>
    </div>
  );
}

// =============================================================================
// Molecule Row
// =============================================================================

function MoleculeRow({ entry }: { entry: AuditEntry }) {
  const [expanded, setExpanded] = useState(false);

  const props = entry.tools_applied?.calculate_properties?.key_results || {};
  const admet = entry.tools_applied?.predict_admet?.key_results || {};
  const compliance = entry.tools_applied?.check_compliance?.key_results || {};
  const herg = admet.herg as number | undefined;

  const dispColor = entry.disposition === "included" ? "var(--success)" :
    entry.disposition === "excluded" ? "var(--danger)" : "var(--text-muted)";

  return (
    <div style={{ borderBottom: "1px solid var(--border)" }}>
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          display: "grid",
          gridTemplateColumns: "30px 1fr 50px 50px 50px 70px 70px",
          alignItems: "center",
          padding: "6px 8px",
          cursor: "pointer",
          fontSize: 11,
        }}
        onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-warm)")}
        onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
      >
        <span style={{ color: "var(--text-muted)" }}>{entry.row_index}</span>
        <span style={{
          fontFamily: "var(--font-mono)",
          color: entry.valid ? "var(--text)" : "var(--text-muted)",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          textDecoration: entry.valid ? "none" : "line-through",
        }}>
          {entry.input_smiles || "—"}
        </span>
        <span style={{ color: "var(--text-soft)", textAlign: "right" }}>{props.mw != null ? String(props.mw) : "—"}</span>
        <span style={{ color: "var(--text-soft)", textAlign: "right" }}>{props.qed != null ? String(props.qed) : "—"}</span>
        <span style={{
          textAlign: "right",
          color: herg !== undefined ? (herg > 0.5 ? "var(--danger)" : "var(--success)") : "var(--text-muted)",
        }}>
          {herg !== undefined ? String(herg) : "—"}
        </span>
        <span style={{ color: "var(--text-soft)", textAlign: "center" }}>
          {String(compliance.overall_status || "—")}
        </span>
        <span style={{
          textAlign: "center",
          fontSize: 10,
          fontWeight: 500,
          padding: "1px 6px",
          borderRadius: 2,
          color: dispColor,
          background: `color-mix(in srgb, ${dispColor} 10%, transparent)`,
        }}>
          {entry.disposition}
        </span>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div style={{ padding: "4px 8px 8px 38px", fontSize: 11, color: "var(--text-soft)" }}>
          {entry.exclusion_reason && (
            <div style={{ marginBottom: 4 }}>
              <span style={{ color: "var(--danger)" }}>Reason: {entry.exclusion_reason}</span>
            </div>
          )}
          {entry.standardization !== "none" && (
            <div style={{ marginBottom: 4 }}>Standardization: {entry.standardization}</div>
          )}
          {entry.canonical_smiles && entry.canonical_smiles !== entry.input_smiles && (
            <div style={{ marginBottom: 4, fontFamily: "var(--font-mono)", fontSize: 10 }}>
              Canonical: {entry.canonical_smiles}
            </div>
          )}
          {/* Tool results */}
          {Object.entries(entry.tools_applied).map(([tool, result]) => (
            <div key={tool} style={{ marginBottom: 2 }}>
              <span style={{ color: result.status === "success" ? "var(--success)" : "var(--danger)" }}>
                {result.status === "success" ? "✓" : "✗"}
              </span>
              {" "}{tool}
              {result.flags && result.flags.length > 0 && (
                <span style={{ color: "var(--warning)", marginLeft: 4 }}>
                  [{result.flags.join(", ")}]
                </span>
              )}
              {result.error && (
                <span style={{ color: "var(--danger)", marginLeft: 4 }}>{result.error}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function PipelineAuditViewer({
  toolInputs,
  toolInputsPartial,
  toolResult,
  openLink,
}: AuditProps) {
  const isStreaming = !toolInputs && !!toolInputsPartial;
  const [filter, setFilter] = useState<"all" | "included" | "excluded">("all");

  let data: AuditInput | null = null;

  if (toolResult) {
    const result = toolResult as any;
    if (result?.structuredContent) {
      data = result.structuredContent;
    } else if (result?.content?.[0]?.text) {
      try { data = JSON.parse(result.content[0].text); } catch { /* fall through */ }
    }
  }

  if (!data && toolInputs) data = toolInputs;

  if (isStreaming || !data) {
    return (
      <div className="loading">
        <div className="loading-spinner" />
        <span>Loading audit data...</span>
      </div>
    );
  }

  const log = data.molecule_audit_log || [];
  const summary = data.audit_summary || { total: log.length, included: 0, excluded: 0, invalid_smiles: 0, compliance_blocks: 0, processing_errors: 0 };

  if (log.length === 0) {
    return (
      <div style={{ padding: 32, textAlign: "center", color: "var(--text-muted)", fontSize: 13 }}>
        No audit data available for this pipeline.
      </div>
    );
  }

  const filtered = filter === "all" ? log : log.filter((e) => e.disposition === filter);

  return (
    <div style={{ width: "100%", maxWidth: 600, margin: "0 auto", padding: 20, fontFamily: "var(--font-sans)" }}>
      {/* Header */}
      <div style={{ marginBottom: 16, paddingBottom: 12, borderBottom: "1px solid var(--border)" }}>
        <div style={{ fontSize: 10, fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-muted)", marginBottom: 4 }}>
          Pipeline Audit Log
        </div>
        <div style={{ fontSize: 16, fontWeight: 600, color: "var(--text)" }}>
          {data.pipeline_id || "Pipeline"}
        </div>
        {data.source_table && (
          <div style={{ fontSize: 12, color: "var(--text-soft)", marginTop: 2 }}>
            Source: {data.source_table} — {data.rows_pulled} rows pulled
          </div>
        )}
      </div>

      {/* Summary */}
      <SummaryBar summary={summary} />

      {/* Inverted Funnel */}
      {data.stage_funnel && data.stage_funnel.length > 0 ? (
        <InvertedFunnel stages={data.stage_funnel} totalInput={data.rows_pulled || log.length} />
      ) : log.length > 0 && (data.processing_tools?.length || 0) > 0 ? (
        <InvertedFunnel
          stages={deriveFunnelFromLog(log, data.processing_tools || [])}
          totalInput={data.rows_pulled || log.length}
        />
      ) : null}

      {/* Library Composition */}
      <LibraryComposition log={log} />

      {/* Filter + export */}
      <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 8 }}>
        {(["all", "included", "excluded"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`btn ${filter === f ? "active" : ""}`}
            style={{ padding: "3px 8px", fontSize: 10 }}
          >
            {f} ({f === "all" ? log.length : log.filter((e) => e.disposition === f).length})
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <button
          className="btn"
          style={{ padding: "3px 8px", fontSize: 10 }}
          onClick={() => openLink({ url: `https://app.novomcp.com/jobs/` })}
        >
          View on Dashboard
        </button>
      </div>

      {/* Table header */}
      <div style={{ border: "1px solid var(--border)", borderRadius: 4, overflow: "hidden" }}>
        <div style={{
          display: "grid",
          gridTemplateColumns: "30px 1fr 50px 50px 50px 70px 70px",
          padding: "6px 8px",
          background: "var(--bg-warm)",
          borderBottom: "1px solid var(--border)",
          fontSize: 10,
          fontWeight: 500,
          color: "var(--text-muted)",
          textTransform: "uppercase",
          letterSpacing: "0.03em",
        }}>
          <span>#</span>
          <span>SMILES</span>
          <span style={{ textAlign: "right" }}>MW</span>
          <span style={{ textAlign: "right" }}>QED</span>
          <span style={{ textAlign: "right" }}>hERG</span>
          <span style={{ textAlign: "center" }}>Compl.</span>
          <span style={{ textAlign: "center" }}>Status</span>
        </div>

        {/* Rows */}
        {filtered.map((entry) => (
          <MoleculeRow key={entry.row_index} entry={entry} />
        ))}
      </div>

      {/* Footer */}
      <div style={{ marginTop: 12, fontSize: 10, color: "var(--text-muted)", textAlign: "center" }}>
        {filtered.length} of {log.length} molecules shown
      </div>
    </div>
  );
}
