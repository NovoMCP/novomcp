/**
 * NovoMCP Credit Usage Dashboard
 *
 * Interactive dashboard showing account tier, credit balance, and usage stats.
 */
import { useState, useEffect } from "react";
import type { ViewProps } from "./create-app.tsx";

// =============================================================================
// Types
// =============================================================================

interface CreditUsageInput {
  org_name?: string;
  tier?: string;
  credits_available?: number;
  credits_used_total?: number;
  max_credits?: number;
  usage_percent?: number;
  credits_remaining_percent?: number;
  status?: string;
  alert?: string | null;
  summary?: string;
  // Extended fields for hybrid billing model
  credits_included?: number;       // Monthly included credits
  overage_rate?: number;           // $/credit for overage (e.g., 0.50)
  overage_credits?: number;        // Credits used beyond included
  overage_cost?: number;           // Dollar cost of overage
  period_start?: string;           // Billing period start date
  period_end?: string;             // Billing period end date
  tools_available?: number;
  member_count?: number;
}

type CreditUsageProps = ViewProps<CreditUsageInput>;

// =============================================================================
// Tier Configuration
// =============================================================================

const TIER_CONFIG: Record<string, { color: string; icon: string; label: string }> = {
  free: { color: "#6b7280", icon: "🆓", label: "Free Trial" },
  pro: { color: "#6b7280", icon: "🆓", label: "Free Trial" },   // Legacy → Free
  team: { color: "#6b7280", icon: "🆓", label: "Free Trial" },  // Legacy → Free
  enterprise: { color: "#f59e0b", icon: "🏢", label: "Enterprise" },
};

// =============================================================================
// Loading Shimmer
// =============================================================================

function LoadingShimmer() {
  return (
    <div
      style={{
        width: "100%",
        minHeight: 300,
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
        Loading account information...
      </div>
    </div>
  );
}

// =============================================================================
// Credit Bar Component
// =============================================================================

function CreditBar({ used, total, color }: { used: number; total: number; color: string }) {
  const percentage = total > 0 ? Math.min((used / total) * 100, 100) : 0;
  const remaining = total - used;

  return (
    <div style={{ width: "100%" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          marginBottom: 8,
          fontSize: 12,
          color: "var(--text-muted)",
        }}
      >
        <span>{used.toLocaleString()} used</span>
        <span>{remaining.toLocaleString()} remaining</span>
      </div>
      <div
        style={{
          width: "100%",
          height: 24,
          background: "var(--bg-recessed)",
          borderRadius: 12,
          overflow: "hidden",
          position: "relative",
        }}
      >
        <div
          style={{
            width: `${percentage}%`,
            height: "100%",
            background: `linear-gradient(90deg, ${color}88, ${color})`,
            borderRadius: 12,
            transition: "width 0.5s ease-out",
          }}
        />
        <div
          style={{
            position: "absolute",
            top: "50%",
            left: "50%",
            transform: "translate(-50%, -50%)",
            fontSize: 11,
            fontWeight: 600,
            color: percentage > 50 ? "white" : "var(--text)",
            textShadow: percentage > 50 ? "0 1px 2px rgba(0,0,0,0.3)" : "none",
          }}
        >
          {percentage.toFixed(1)}% used
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// Stat Card Component
// =============================================================================

function StatCard({
  label,
  value,
  icon,
  color = "var(--accent)"
}: {
  label: string;
  value: string | number;
  icon: string;
  color?: string;
}) {
  return (
    <div
      style={{
        flex: "1 1 120px",
        padding: 16,
        background: "var(--bg-elevated)",
        borderRadius: 8,
        border: "1px solid var(--border)",
        textAlign: "center",
      }}
    >
      <div style={{ fontSize: 24, marginBottom: 4 }}>{icon}</div>
      <div style={{ fontSize: 20, fontWeight: 600, color }}>{value}</div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
        {label}
      </div>
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function CreditUsageViewer({
  toolInputs,
  toolInputsPartial,
  toolResult,
  hostContext,
}: CreditUsageProps) {
  const isStreaming = !toolInputs && !!toolInputsPartial;

  // Get data from toolResult.structuredContent or parse from content
  let data: CreditUsageInput | null = null;

  if (toolResult) {
    const result = toolResult as any;
    if (result?.structuredContent) {
      data = result.structuredContent;
    } else if (result?.content?.[0]?.text) {
      try {
        data = JSON.parse(result.content[0].text);
      } catch {
        // Fall through to toolInputs
      }
    }
  }

  // Fall back to toolInputs if no result yet
  if (!data && toolInputs) {
    data = toolInputs;
  }

  if (isStreaming || !data) {
    return <LoadingShimmer />;
  }

  const {
    org_name = "Organization",
    tier = "free",
    credits_available = 0,
    credits_used_total = 0,
    max_credits = 1000,
    status = "ok",
    alert,
    summary,
    credits_included,
    overage_rate = 0,
    overage_credits = 0,
    overage_cost = 0,
  } = data;

  // Calculate derived values
  const hasOverage = overage_credits > 0;
  const effectiveIncluded = credits_included ?? max_credits;

  const tierConfig = TIER_CONFIG[tier.toLowerCase()] || TIER_CONFIG.free;
  const isLow = credits_available < max_credits * 0.2;
  const isCritical = credits_available < max_credits * 0.05;

  return (
    <div
      style={{
        width: "100%",
        maxWidth: 500,
        margin: "0 auto",
        padding: 20,
        fontFamily: "var(--font-family)",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 24,
          paddingBottom: 16,
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
            NovoMCP Account
          </div>
          <div style={{ fontSize: 18, fontWeight: 600, color: "var(--text)" }}>
            {org_name}
          </div>
        </div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "6px 12px",
            background: `${tierConfig.color}20`,
            borderRadius: 20,
            border: `1px solid ${tierConfig.color}40`,
          }}
        >
          <span>{tierConfig.icon}</span>
          <span style={{ fontSize: 13, fontWeight: 600, color: tierConfig.color }}>
            {tierConfig.label}
          </span>
        </div>
      </div>

      {/* Status Alert */}
      {(alert || isCritical) && (
        <div
          style={{
            padding: "12px 16px",
            background: isCritical ? "rgba(220, 38, 38, 0.1)" : "rgba(245, 158, 11, 0.1)",
            border: `1px solid ${isCritical ? "rgba(220, 38, 38, 0.3)" : "rgba(245, 158, 11, 0.3)"}`,
            borderRadius: 8,
            marginBottom: 20,
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
        >
          <span style={{ fontSize: 18 }}>{isCritical ? "⚠️" : "💡"}</span>
          <span style={{ fontSize: 13, color: isCritical ? "#dc2626" : "#d97706" }}>
            {alert || "Credits running low. Consider upgrading your plan."}
          </span>
        </div>
      )}

      {/* Credit Balance */}
      <div
        style={{
          background: "var(--bg-elevated)",
          borderRadius: 12,
          padding: 20,
          marginBottom: 20,
          border: "1px solid var(--border)",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
            marginBottom: 16,
          }}
        >
          <span style={{ fontSize: 13, color: "var(--text-muted)" }}>Credit Balance</span>
          <div>
            <span
              style={{
                fontSize: 28,
                fontWeight: 700,
                color: isCritical ? "#dc2626" : isLow ? "#d97706" : tierConfig.color,
              }}
            >
              {credits_available.toLocaleString()}
            </span>
            <span style={{ fontSize: 14, color: "var(--text-muted)", marginLeft: 4 }}>
              / {max_credits.toLocaleString()}
            </span>
          </div>
        </div>
        <CreditBar
          used={max_credits - credits_available}
          total={max_credits}
          color={isCritical ? "#dc2626" : isLow ? "#d97706" : tierConfig.color}
        />
      </div>

      {/* Stats Grid */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 12,
          marginBottom: 20,
        }}
      >
        <StatCard
          label="Credits Used"
          value={credits_used_total.toLocaleString()}
          icon="📊"
          color="var(--text)"
        />
        <StatCard
          label="Included"
          value={effectiveIncluded.toLocaleString()}
          icon="🎁"
          color={tierConfig.color}
        />
        <StatCard
          label="Status"
          value={status === "ok" ? "Active" : status}
          icon={status === "ok" ? "✅" : "⚠️"}
          color={status === "ok" ? "#22c55e" : "#f59e0b"}
        />
      </div>

      {/* Overage Alert */}
      {hasOverage && (
        <div
          style={{
            padding: "16px 20px",
            background: "rgba(245, 158, 11, 0.1)",
            border: "1px solid rgba(245, 158, 11, 0.3)",
            borderRadius: 8,
            marginBottom: 20,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: "#d97706" }}>
                Overage Usage
              </div>
              <div style={{ fontSize: 12, color: "var(--text-soft)" }}>
                {overage_credits.toLocaleString()} credits beyond included @ ${overage_rate}/credit
              </div>
            </div>
            <div style={{ fontSize: 20, fontWeight: 700, color: "#d97706" }}>
              +${overage_cost.toFixed(2)}
            </div>
          </div>
        </div>
      )}

      {/* Tier Benefits */}
      <div
        style={{
          background: "var(--bg-recessed)",
          borderRadius: 8,
          padding: 16,
          marginBottom: 16,
        }}
      >
        <div
          style={{
            fontSize: 11,
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
            color: "var(--text-muted)",
            marginBottom: 12,
          }}
        >
          {tierConfig.label} Tier — 1 credit = $1
        </div>
        <div style={{ fontSize: 12, color: "var(--text-soft)", lineHeight: 1.6 }}>
          {["free", "pro", "team"].includes(tier.toLowerCase()) && (
            <>
              <div>• 250 credits (30-day trial)</div>
              <div>• All 27 tools included</div>
              <div>• ADMET, optimization, structure prediction</div>
              <div>• No overage — trial ends at 0</div>
            </>
          )}
          {tier.toLowerCase() === "enterprise" && (
            <>
              <div>• Custom credits (default 50,000)</div>
              <div>• All tools + data connectors</div>
              <div>• Dedicated support & custom SLAs</div>
              <div>• $0.10/credit overage</div>
            </>
          )}
        </div>
      </div>

      {/* Summary */}
      {summary && (
        <div
          style={{
            fontSize: 12,
            color: "var(--text-muted)",
            textAlign: "center",
            padding: "12px 16px",
            background: "var(--bg-elevated)",
            borderRadius: 8,
            border: "1px solid var(--border)",
          }}
        >
          {summary}
        </div>
      )}
    </div>
  );
}
