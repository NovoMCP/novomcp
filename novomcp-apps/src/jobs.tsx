/**
 * NovoMCP Pipeline Jobs Viewer
 *
 * Shows last 15 async pipeline jobs with status, service type, and timestamps.
 * "More" button links to the full dashboard jobs page.
 */
import { useState, useEffect } from "react";
import type { ViewProps } from "./create-app.tsx";

// =============================================================================
// Types
// =============================================================================

interface Job {
  job_id: string;
  status: string;
  service: string;
  submitted_at?: string;
  completed_at?: string;
  started_at?: string;
  progress_pct?: number;
  estimated_eta?: string;
  duration_ns?: number;
  smiles?: string;
  pdb_id?: string;
}

interface JobsInput {
  jobs?: Job[];
  total?: number;
  filters?: Record<string, unknown>;
}

type JobsProps = ViewProps<JobsInput>;

// =============================================================================
// Status Config
// =============================================================================

const STATUS_CONFIG: Record<string, { color: string; bg: string; icon: string; label: string }> = {
  submitted: { color: "var(--text-muted)", bg: "var(--bg-warm)", icon: "⏳", label: "Queued" },
  queued: { color: "var(--text-muted)", bg: "var(--bg-warm)", icon: "⏳", label: "Queued" },
  running: { color: "var(--accent)", bg: "rgba(184, 112, 75, 0.1)", icon: "⚙️", label: "Running" },
  completed: { color: "var(--success)", bg: "var(--success-bg)", icon: "✓", label: "Completed" },
  failed: { color: "var(--danger)", bg: "var(--danger-bg)", icon: "✗", label: "Failed" },
};

const SERVICE_LABELS: Record<string, string> = {
  "gromacs-md": "Molecular Dynamics",
  "autodock-gpu": "Docking",
  "openfold3": "Structure Prediction",
  "lead-optimization": "Lead Optimization",
  "novo-quantum": "Quantum Chemistry",
  "novomcp-neb": "Transition State",
};

// =============================================================================
// Helpers
// =============================================================================

function formatTimeAgo(timestamp?: string): string {
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

function getJobPrefix(jobId: string): string {
  if (jobId.startsWith("gro_")) return "MD";
  if (jobId.startsWith("dock_batch_")) return "Dock";
  if (jobId.startsWith("dock_")) return "Dock";
  if (jobId.startsWith("of3_")) return "Fold";
  if (jobId.startsWith("lo_")) return "Opt";
  if (jobId.startsWith("qc_")) return "QC";
  if (jobId.startsWith("neb_")) return "NEB";
  return "Job";
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
        Loading pipeline jobs...
      </div>
    </div>
  );
}

// =============================================================================
// Job Row Component
// =============================================================================

function JobRow({
  job,
  expanded,
  onToggle,
  onResume,
}: {
  job: Job;
  expanded: boolean;
  onToggle: () => void;
  onResume: (jobId: string) => void;
}) {
  const config = STATUS_CONFIG[job.status] || STATUS_CONFIG.submitted;
  const prefix = getJobPrefix(job.job_id);
  const serviceName = SERVICE_LABELS[job.service] || job.service;
  const isTerminal = job.status === "completed" || job.status === "failed";

  return (
    <div
      style={{
        borderBottom: "1px solid var(--border)",
      }}
    >
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
        {/* Type badge */}
        <div
          style={{
            fontSize: 10,
            fontWeight: 600,
            fontFamily: "var(--font-mono)",
            padding: "2px 6px",
            background: "var(--bg-warm)",
            border: "1px solid var(--border)",
            borderRadius: 4,
            color: "var(--text-soft)",
            minWidth: 32,
            textAlign: "center",
          }}
        >
          {prefix}
        </div>

        {/* Job ID */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 12,
              fontFamily: "var(--font-mono)",
              color: "var(--text)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {job.job_id}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
            {serviceName}
          </div>
        </div>

        {/* Status badge */}
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
          }}
        >
          <span style={{ fontSize: 10 }}>{config.icon}</span>
          {config.label}
        </div>

        {/* Timestamp */}
        <div style={{ fontSize: 11, color: "var(--text-muted)", minWidth: 60, textAlign: "right" }}>
          {formatTimeAgo(job.completed_at || job.submitted_at)}
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
            {job.submitted_at && (
              <div>
                <span style={{ color: "var(--text-muted)" }}>Submitted: </span>
                <span style={{ color: "var(--text-soft)" }}>
                  {new Date(job.submitted_at).toLocaleString()}
                </span>
              </div>
            )}
            {job.completed_at && (
              <div>
                <span style={{ color: "var(--text-muted)" }}>Completed: </span>
                <span style={{ color: "var(--text-soft)" }}>
                  {new Date(job.completed_at).toLocaleString()}
                </span>
              </div>
            )}
            {job.progress_pct !== undefined && job.progress_pct < 100 && (
              <div>
                <span style={{ color: "var(--text-muted)" }}>Progress: </span>
                <span style={{ color: "var(--accent)" }}>{job.progress_pct}%</span>
              </div>
            )}
            {job.pdb_id && (
              <div>
                <span style={{ color: "var(--text-muted)" }}>Target: </span>
                <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-soft)" }}>
                  {job.pdb_id}
                </span>
              </div>
            )}
          </div>

          {isTerminal && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onResume(job.job_id);
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
              Continue Discovery →
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function JobsViewer({
  toolInputs,
  toolInputsPartial,
  toolResult,
  sendMessage,
  openLink,
}: JobsProps) {
  const isStreaming = !toolInputs && !!toolInputsPartial;
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Get data from toolResult or toolInputs
  let data: JobsInput | null = null;

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

  const allJobs = data.jobs || [];
  const jobs = allJobs.slice(0, 15);
  const total = data.total || allJobs.length;
  const hasMore = total > 15;

  const runningCount = jobs.filter((j) => j.status === "running").length;
  const completedCount = jobs.filter((j) => j.status === "completed").length;

  const handleResume = (jobId: string) => {
    sendMessage({ role: "user", content: [{ type: "text", text: `What are the results for ${jobId}?` }] });
  };

  const handleViewAll = () => {
    openLink({ url: "https://app.novomcp.com/jobs/" });
  };

  if (jobs.length === 0) {
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
        <div style={{ fontSize: 32, marginBottom: 12 }}>🧪</div>
        <div style={{ fontSize: 14, fontWeight: 500, color: "var(--text)" }}>
          No pipeline jobs yet
        </div>
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 6 }}>
          Run molecular dynamics, docking, or structure predictions to see jobs here.
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
            Pipeline Jobs
          </div>
          <div style={{ fontSize: 16, fontWeight: 600, color: "var(--text)" }}>
            {total} {total === 1 ? "Job" : "Jobs"}
          </div>
        </div>

        {/* Summary badges */}
        <div style={{ display: "flex", gap: 8 }}>
          {runningCount > 0 && (
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
              {runningCount} running
            </div>
          )}
          {completedCount > 0 && (
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
              {completedCount} done
            </div>
          )}
        </div>
      </div>

      {/* Job list */}
      <div
        style={{
          border: "1px solid var(--border)",
          borderRadius: 8,
          overflow: "hidden",
          background: "var(--bg)",
        }}
      >
        {jobs.map((job) => (
          <JobRow
            key={job.job_id}
            job={job}
            expanded={expandedId === job.job_id}
            onToggle={() =>
              setExpandedId(expandedId === job.job_id ? null : job.job_id)
            }
            onResume={handleResume}
          />
        ))}
      </div>

      {/* View all jobs button — always visible */}
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
          View all jobs →
        </button>
    </div>
  );
}
