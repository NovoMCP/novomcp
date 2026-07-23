/**
 * NovoMCP Discovery Funnels Viewer
 *
 * Shows recent discovery funnels with disease, target gene, outcome,
 * stage count, credits consumed, and best affinity. Bidirectional:
 * clicking a row sends a message to Claude to show the funnel audit trail.
 * "View all" links to the full pipelines dashboard.
 *
 * Mirrors the jobs.tsx pattern exactly — same layout, same interactions.
 */
import { useState } from "react";
import type { ViewProps } from "./create-app.tsx";

// =============================================================================
// Types
// =============================================================================

interface Funnel {
  funnel_id: string;
  disease?: string | null;
  target_gene?: string | null;
  outcome?: string | null;
  chemotype?: string | null;
  best_affinity_kcal?: number | null;
  final_lead_count?: number | null;
  stage_count: number;
  last_stage_index: number;
  started_at?: string | null;
  last_activity?: string | null;
  total_credits: number;
  reviewed_stages: number;
  unreviewed_stages: number;
}

interface FunnelsInput {
  funnels?: Funnel[];
  total?: number;
}

type FunnelsProps = ViewProps<FunnelsInput>;

// =============================================================================
// Outcome Config
// =============================================================================

const OUTCOME_CONFIG: Record<string, { color: string; bg: string; icon: string; label: string }> = {
  SUCCEEDED:        { color: "var(--success)", bg: "var(--success-bg)", icon: "✓", label: "Succeeded" },
  FAILED_NO_LEADS:  { color: "var(--danger)",  bg: "var(--danger-bg)",  icon: "✗", label: "No Leads" },
  FAILED_TOXICITY:  { color: "var(--danger)",  bg: "var(--danger-bg)",  icon: "⚠", label: "Toxicity" },
  FAILED_POTENCY:   { color: "var(--danger)",  bg: "var(--danger-bg)",  icon: "↓", label: "Low Potency" },
  ABANDONED:        { color: "var(--text-muted)", bg: "var(--bg-warm)", icon: "—", label: "Abandoned" },
};

const IN_PROGRESS_CONFIG = {
  color: "var(--accent)", bg: "rgba(184, 112, 75, 0.1)", icon: "⚙️", label: "In Progress",
};

// =============================================================================
// Helpers
// =============================================================================

function formatTimeAgo(timestamp?: string | null): string {
  if (!timestamp) return "—";
  try {
    const d = new Date(timestamp);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    const diffDays = Math.floor(diffHr / 24);
    if (diffDays < 7) return `${diffDays}d ago`;
    return d.toLocaleDateString();
  } catch {
    return "—";
  }
}

function extractDisease(funnelId: string): string {
  // funnel_glioblastoma_20260330_143022 → glioblastoma
  const parts = funnelId.replace(/^funnel_/, "").split("_");
  // Remove date parts (8-digit and 6-digit suffixes)
  const filtered = parts.filter((p) => !/^\d{6,}$/.test(p));
  if (filtered.length === 0) return funnelId;
  return filtered.join(" ").replace(/^\w/, (c) => c.toUpperCase());
}

// =============================================================================
// Loading Shimmer
// =============================================================================

function LoadingShimmer() {
  return (
    <div
      style={{
        width: "100%",
        minHeight: 200,
        borderRadius: 8,
        padding: 24,
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
        Loading discovery funnels...
      </div>
    </div>
  );
}

// =============================================================================
// Funnel Row Component
// =============================================================================

function FunnelRow({
  funnel,
  expanded,
  onToggle,
  onViewAudit,
}: {
  funnel: Funnel;
  expanded: boolean;
  onToggle: () => void;
  onViewAudit: (funnelId: string) => void;
}) {
  const outcome = funnel.outcome;
  const config = outcome
    ? OUTCOME_CONFIG[outcome] || IN_PROGRESS_CONFIG
    : IN_PROGRESS_CONFIG;

  const disease = funnel.disease || extractDisease(funnel.funnel_id);
  const target = funnel.target_gene;

  return (
    <div style={{ borderBottom: "1px solid var(--border)" }}>
      <div
        onClick={onToggle}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "12px 16px",
          cursor: "pointer",
          transition: "background 0.2s var(--ease)",
        }}
        onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-warm)")}
        onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
      >
        {/* Target badge */}
        <div
          style={{
            fontSize: 10,
            fontWeight: 600,
            fontFamily: "var(--font-mono)",
            padding: "2px 6px",
            background: "var(--bg-warm)",
            border: "1px solid var(--border)",
            borderRadius: 4,
            color: target ? "var(--accent)" : "var(--text-muted)",
            minWidth: 40,
            textAlign: "center",
          }}
        >
          {target || "—"}
        </div>

        {/* Disease + funnel ID */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 12,
              color: "var(--text)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              fontWeight: 500,
            }}
          >
            {disease}
          </div>
          <div
            style={{
              fontSize: 10,
              fontFamily: "var(--font-mono)",
              color: "var(--text-muted)",
              marginTop: 2,
            }}
          >
            {funnel.funnel_id.length > 35
              ? funnel.funnel_id.slice(0, 35) + "..."
              : funnel.funnel_id}
          </div>
        </div>

        {/* Stage count */}
        <div
          style={{
            fontSize: 11,
            color: "var(--text-soft)",
            minWidth: 50,
            textAlign: "center",
          }}
        >
          {funnel.stage_count} stages
        </div>

        {/* Outcome badge */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            padding: "3px 8px",
            background: config.bg,
            borderRadius: 12,
            fontSize: 11,
            fontWeight: 500,
            color: config.color,
            minWidth: 80,
            justifyContent: "center",
          }}
        >
          <span style={{ fontSize: 10 }}>{config.icon}</span>
          {config.label}
        </div>

        {/* Timestamp */}
        <div style={{ fontSize: 11, color: "var(--text-muted)", minWidth: 60, textAlign: "right" }}>
          {formatTimeAgo(funnel.last_activity || funnel.started_at)}
        </div>

        {/* Expand arrow */}
        <div
          style={{
            fontSize: 10,
            color: "var(--text-muted)",
            transition: "transform 0.2s var(--ease)",
            transform: expanded ? "rotate(180deg)" : "rotate(0)",
          }}
        >
          ▼
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div
          style={{
            padding: "8px 16px 16px",
            background: "var(--bg-warm)",
            fontSize: 12,
          }}
        >
          <div style={{ display: "flex", flexWrap: "wrap", gap: 16, marginBottom: 12 }}>
            {funnel.started_at && (
              <div>
                <span style={{ color: "var(--text-muted)" }}>Started: </span>
                <span style={{ color: "var(--text-soft)" }}>
                  {new Date(funnel.started_at).toLocaleString()}
                </span>
              </div>
            )}
            {funnel.last_activity && (
              <div>
                <span style={{ color: "var(--text-muted)" }}>Last activity: </span>
                <span style={{ color: "var(--text-soft)" }}>
                  {new Date(funnel.last_activity).toLocaleString()}
                </span>
              </div>
            )}
            <div>
              <span style={{ color: "var(--text-muted)" }}>Credits: </span>
              <span style={{ color: "var(--text-soft)" }}>{funnel.total_credits.toFixed(0)}</span>
            </div>
            <div>
              <span style={{ color: "var(--text-muted)" }}>Reviewed: </span>
              <span style={{ color: "var(--text-soft)" }}>
                {funnel.reviewed_stages}/{funnel.stage_count}
              </span>
            </div>
            {funnel.chemotype && (
              <div>
                <span style={{ color: "var(--text-muted)" }}>Chemotype: </span>
                <span style={{ color: "var(--text-soft)" }}>{funnel.chemotype}</span>
              </div>
            )}
            {funnel.best_affinity_kcal && (
              <div>
                <span style={{ color: "var(--text-muted)" }}>Best affinity: </span>
                <span style={{ fontFamily: "var(--font-mono)", color: "var(--success)" }}>
                  {funnel.best_affinity_kcal.toFixed(2)} kcal/mol
                </span>
              </div>
            )}
            {funnel.final_lead_count !== null && funnel.final_lead_count !== undefined && (
              <div>
                <span style={{ color: "var(--text-muted)" }}>Leads: </span>
                <span style={{ color: "var(--text-soft)" }}>{funnel.final_lead_count}</span>
              </div>
            )}
          </div>

          <button
            onClick={(e) => {
              e.stopPropagation();
              onViewAudit(funnel.funnel_id);
            }}
            style={{
              padding: "6px 14px",
              fontSize: 12,
              fontWeight: 500,
              background: "var(--bg)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              color: "var(--accent)",
              cursor: "pointer",
              transition: "all 0.2s var(--ease)",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = "var(--accent)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = "var(--border)";
            }}
          >
            View Audit Trail →
          </button>
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function FunnelsViewer({
  toolInputs,
  toolInputsPartial,
  toolResult,
  sendMessage,
  openLink,
}: FunnelsProps) {
  const isStreaming = !toolInputs && !!toolInputsPartial;
  const [expandedId, setExpandedId] = useState<string | null>(null);

  let data: FunnelsInput | null = null;

  if (toolResult) {
    const result = toolResult as any;
    if (result?.structuredContent) {
      data = result.structuredContent;
    } else if (result?.content?.[0]?.text) {
      try {
        data = JSON.parse(result.content[0].text);
      } catch {
        // Fall through
      }
    }
  }

  if (!data && toolInputs) {
    data = toolInputs;
  }

  if (isStreaming || !data) {
    return <LoadingShimmer />;
  }

  const allFunnels = data.funnels || [];
  const funnels = allFunnels.slice(0, 15);
  const total = data.total || allFunnels.length;

  const succeededCount = funnels.filter((f) => f.outcome === "SUCCEEDED").length;
  const inProgressCount = funnels.filter((f) => !f.outcome).length;

  const handleViewAudit = (funnelId: string) => {
    sendMessage({
      role: "user",
      content: [{ type: "text", text: `Show me the full audit trail for funnel ${funnelId}` }],
    });
  };

  const handleViewAll = () => {
    openLink({ url: "https://app.novomcp.com/audit/pipelines/" });
  };

  if (funnels.length === 0) {
    return (
      <div
        style={{
          width: "100%",
          maxWidth: 500,
          margin: "0 auto",
          padding: 32,
          textAlign: "center",
          fontFamily: "var(--font-sans)",
        }}
      >
        <div style={{ fontSize: 32, marginBottom: 12 }}>🔬</div>
        <div style={{ fontSize: 14, fontWeight: 500, color: "var(--text)" }}>
          No discovery funnels yet
        </div>
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 6 }}>
          Run a drug discovery pipeline to see funnels here.
          Try: "Run a discovery funnel for glioblastoma"
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        width: "100%",
        maxWidth: 500,
        margin: "0 auto",
        padding: 20,
        fontFamily: "var(--font-sans)",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
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
              marginBottom: 4,
            }}
          >
            Discovery Funnels
          </div>
          <div style={{ fontSize: 16, fontWeight: 600, color: "var(--text)" }}>
            {total} {total === 1 ? "Funnel" : "Funnels"}
          </div>
        </div>

        {/* Summary badges */}
        <div style={{ display: "flex", gap: 8 }}>
          {inProgressCount > 0 && (
            <div
              style={{
                padding: "4px 10px",
                background: "rgba(184, 112, 75, 0.1)",
                borderRadius: 12,
                fontSize: 11,
                fontWeight: 500,
                color: "var(--accent)",
              }}
            >
              {inProgressCount} active
            </div>
          )}
          {succeededCount > 0 && (
            <div
              style={{
                padding: "4px 10px",
                background: "var(--success-bg)",
                borderRadius: 12,
                fontSize: 11,
                fontWeight: 500,
                color: "var(--success)",
              }}
            >
              {succeededCount} succeeded
            </div>
          )}
        </div>
      </div>

      {/* Funnel list */}
      <div
        style={{
          border: "1px solid var(--border)",
          borderRadius: 8,
          overflow: "hidden",
          background: "var(--bg)",
        }}
      >
        {funnels.map((funnel) => (
          <FunnelRow
            key={funnel.funnel_id}
            funnel={funnel}
            expanded={expandedId === funnel.funnel_id}
            onToggle={() =>
              setExpandedId(expandedId === funnel.funnel_id ? null : funnel.funnel_id)
            }
            onViewAudit={handleViewAudit}
          />
        ))}
      </div>

      {/* View all button */}
      <button
        onClick={handleViewAll}
        style={{
          display: "block",
          width: "100%",
          marginTop: 12,
          padding: "10px 16px",
          fontSize: 12,
          fontWeight: 500,
          background: "var(--bg-warm)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          color: "var(--accent)",
          cursor: "pointer",
          textAlign: "center",
          transition: "all 0.2s var(--ease)",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.borderColor = "var(--accent)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.borderColor = "var(--border)";
        }}
      >
        View all funnels →
      </button>
    </div>
  );
}
