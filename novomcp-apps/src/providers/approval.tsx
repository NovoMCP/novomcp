/**
 * ApprovalPrompt — reusable consent UI for credit-burning or destructive actions.
 *
 * Usage:
 *   <ApprovalPrompt
 *     title="Docking Cost Estimate"
 *     summary="Dock 12 molecules against 4EY7 (AChE)."
 *     cost={{ total: 48, breakdown: [{ label: "Base", amount: 12 }, { label: "Per molecule × 12", amount: 36 }] }}
 *     onConfirm={async () => { await callServerTool({ name: "dock_molecules", arguments: {...} }); }}
 *   />
 *
 * Renders three terminal states: pending (buttons), working (spinner),
 * resolved (confirmed/cancelled badge).
 */
import { useState, ReactNode } from "react";

// =============================================================================
// Types
// =============================================================================

export interface ApprovalCostBreakdown {
  label: string;
  amount: number | string;
}

export interface ApprovalCost {
  total: number | string;
  unit?: string; // default: "credits"
  breakdown?: ApprovalCostBreakdown[];
}

export interface ApprovalPromptProps {
  /** Panel title — typically the action name. */
  title: string;
  /** One-line description of what will happen. */
  summary: string;
  /** Optional cost block. Omit for non-metered actions (just a confirm prompt). */
  cost?: ApprovalCost;
  /** Optional detail rows (parameters, targets, etc.) shown below summary. */
  details?: ReactNode;
  /** Called when the user clicks confirm. May be async; UI shows working state. */
  onConfirm: () => void | Promise<void>;
  /** Called when the user clicks cancel. May be async. */
  onCancel?: () => void | Promise<void>;
  /** Label for the confirm button. Default: "Confirm". */
  confirmLabel?: string;
  /** Label for the cancel button. Default: "Cancel". */
  cancelLabel?: string;
  /** If true, the action is destructive — confirm button styled as danger. */
  destructive?: boolean;
  /**
   * External resolution override. If provided, bypasses internal state —
   * viewer owns the post-decision UI (e.g. after backend re-invocation).
   */
  resolved?: "confirmed" | "cancelled" | null;
}

type InternalState = "pending" | "working" | "confirmed" | "cancelled" | "failed";

// =============================================================================
// Component
// =============================================================================

export function ApprovalPrompt({
  title,
  summary,
  cost,
  details,
  onConfirm,
  onCancel,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive = false,
  resolved = null,
}: ApprovalPromptProps) {
  const [state, setState] = useState<InternalState>("pending");
  const [error, setError] = useState<string | null>(null);

  const effectiveState: InternalState = resolved ?? state;

  const handleConfirm = async () => {
    setState("working");
    setError(null);
    try {
      await onConfirm();
      setState("confirmed");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      setState("failed");
    }
  };

  const handleCancel = async () => {
    setState("working");
    try {
      await onCancel?.();
      setState("cancelled");
    } catch {
      setState("pending");
    }
  };

  return (
    <div className="panel">
      <div className="panel-title">{title}</div>

      {/* Summary row — cost card + text side by side */}
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        {cost && (
          <div
            style={{
              padding: "12px 16px",
              background: "var(--bg-warm)",
              borderRadius: 2,
              textAlign: "center",
              minWidth: 110,
              flexShrink: 0,
            }}
          >
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>
              {cost.unit ?? "Credits"}
            </div>
            <div
              style={{
                fontSize: 20,
                fontFamily: "var(--font-mono)",
                fontWeight: 600,
                color: "var(--accent)",
              }}
            >
              {cost.total}
            </div>
            {cost.breakdown && cost.breakdown.length > 0 && (
              <div
                style={{
                  fontSize: 10,
                  fontFamily: "var(--font-mono)",
                  color: "var(--text-muted)",
                  marginTop: 4,
                  lineHeight: 1.4,
                }}
              >
                {cost.breakdown.map((b, i) => (
                  <div key={i}>
                    {b.label}: {b.amount}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <div style={{ flex: 1, fontSize: 13, color: "var(--text)", lineHeight: 1.5 }}>
          {summary}
          {details && <div style={{ marginTop: 10 }}>{details}</div>}
        </div>
      </div>

      {/* Action row */}
      <div
        style={{
          marginTop: 16,
          paddingTop: 12,
          borderTop: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          gap: 8,
          justifyContent: "flex-end",
        }}
      >
        {effectiveState === "pending" && (
          <>
            {onCancel && (
              <button className="btn" onClick={handleCancel} type="button">
                {cancelLabel}
              </button>
            )}
            <button
              className="btn active"
              onClick={handleConfirm}
              type="button"
              style={
                destructive
                  ? { background: "var(--danger)", borderColor: "var(--danger)" }
                  : undefined
              }
            >
              {confirmLabel}
            </button>
          </>
        )}

        {effectiveState === "working" && (
          <>
            <div className="loading-spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Working…</span>
          </>
        )}

        {effectiveState === "confirmed" && (
          <span className="badge success">Confirmed</span>
        )}

        {effectiveState === "cancelled" && (
          <span className="badge warning">Cancelled</span>
        )}

        {effectiveState === "failed" && (
          <>
            <span className="badge danger">Failed</span>
            <button className="btn" onClick={handleConfirm} type="button">
              Retry
            </button>
          </>
        )}
      </div>

      {error && effectiveState === "failed" && (
        <div
          style={{
            marginTop: 10,
            padding: "8px 10px",
            background: "var(--danger-bg)",
            color: "var(--danger)",
            fontSize: 11,
            fontFamily: "var(--font-mono)",
            borderRadius: 2,
          }}
        >
          {error}
        </div>
      )}
    </div>
  );
}
