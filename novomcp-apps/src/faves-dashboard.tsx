/**
 * NovoMCP FAVES Compliance Dashboard
 *
 * Interactive compliance visualization showing regulatory status,
 * risk assessment, and recommendations for context-dependent analysis.
 */
import { useState } from "react";
import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types
// =============================================================================

interface ComplianceToolInput {
  smiles?: string;
  context?: {
    intended_use?: string;
    jurisdiction?: string;
    therapeutic_area?: string;
  };
  base_compliance?: {
    status?: string;
    is_dea_controlled?: boolean;
    is_fda_banned?: boolean;
    is_cwc_scheduled?: boolean;
    is_epa_pbt?: boolean;
    is_eu_reach_banned?: boolean;
    is_scaffold_match?: boolean;
    scaffold_matches?: string;
    faves_flag_count?: number;
    lipinski?: boolean;
    dea_schedule?: string;
    fda_category?: string;
    is_whitelisted?: boolean;
    whitelist_name?: string;
    alert_free?: boolean;
    structural_alert_summary?: {
      pains?: { count: number; alerts: string[] };
      brenk?: { count: number; alerts: string[] };
      nih?: { count: number; alerts: string[] };
      zinc?: { count: number; alerts: string[] };
      chembl?: { count: number; catalogs?: Record<string, string[]> };
      total_alert_count?: number;
    };
    pk_classification?: {
      gi_absorption?: string;
      bbb_permeant?: string;
    };
  };
  context_compliance?: {
    overall_status?: string;
    jurisdiction_specific?: Record<string, unknown>;
    dual_use_assessment?: {
      risk_level?: string;
      concerns?: string[];
    };
    ethical_considerations?: string[];
  };
  overall_status?: string;
  recommendations?: string[];
  regulatory_pathway?: {
    pathway?: string;
    estimated_timeline?: string;
    key_requirements?: string[];
  };
  risk_assessment?: {
    overall_risk?: string;
    risk_factors?: Array<{ factor: string; level: string; description?: string }>;
    mitigations?: string[];
  };
  height?: number;
}

type FavesDashboardProps = ViewProps<ComplianceToolInput>;

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
        Running compliance assessment...
      </div>
    </div>
  );
}

// =============================================================================
// Status Indicator (Traffic Light)
// =============================================================================

function StatusIndicator({ status }: { status?: string }) {
  // Vocabulary reference:
  //   - Legacy FAVES: clean / flagged / controlled
  //   - Raw faves-compliance endpoint: PASS / BLOCKED / CONDITIONAL / REVIEW_REQUIRED / DEGRADED
  //   - novomcp check_compliance normalization (FUNNEL_SUPPLEMENT): PROCEED / STOP / CAUTION
  // This component must understand all three so the dashboard renders correctly
  // regardless of which response path populated `overall_status`.
  const getStatusInfo = (s?: string) => {
    switch (s?.toLowerCase()) {
      case "clean":
      case "compliant":
      case "approved":
      case "pass":
      case "pass_with_warnings":
      case "proceed":
        return { color: "var(--success)", bg: "var(--success-bg)", label: "Compliant", icon: "✓" };
      case "flagged":
      case "warning":
      case "caution":
      case "review_required":
      case "conditional":
      case "degraded":
        return { color: "var(--warning)", bg: "var(--warning-bg)", label: "Review Required", icon: "!" };
      case "controlled":
      case "banned":
      case "prohibited":
      case "blocked":
      case "stop":
        return { color: "var(--danger)", bg: "var(--danger-bg)", label: "Blocked", icon: "✗" };
      default:
        return { color: "var(--text-muted)", bg: "var(--bg-warm)", label: "Unknown", icon: "?" };
    }
  };

  const info = getStatusInfo(status);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: 24,
        background: info.bg,
        borderRadius: 8,
        border: `2px solid ${info.color}`,
      }}
    >
      <div
        style={{
          width: 64,
          height: 64,
          borderRadius: "50%",
          background: info.color,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 32,
          color: "white",
          fontWeight: "bold",
          marginBottom: 12,
        }}
      >
        {info.icon}
      </div>
      <div style={{ fontSize: 20, fontWeight: 600, color: info.color }}>
        {info.label}
      </div>
      <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>
        Overall Compliance Status
      </div>
    </div>
  );
}

// =============================================================================
// Whitelist Banner — explains why a compound with structural alerts is PROCEED
// =============================================================================

function WhitelistBanner({
  isWhitelisted,
  whitelistName,
  hasAlerts,
}: {
  isWhitelisted?: boolean;
  whitelistName?: string;
  hasAlerts?: boolean;
}) {
  if (!isWhitelisted) return null;
  const name = whitelistName || "FDA-approved compound";
  return (
    <div
      style={{
        padding: "10px 14px",
        background: "var(--success-bg)",
        border: "1px solid var(--success)",
        borderLeft: "4px solid var(--success)",
        borderRadius: 4,
        display: "flex",
        alignItems: "center",
        gap: 10,
        marginBottom: 16,
      }}
    >
      <span style={{ fontSize: 18 }}>✓</span>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--success)" }}>
          FDA-Approved compound: {name}
        </div>
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
          Whitelist match — overall verdict overrides structural-alert review.
          {hasAlerts && " Structural alerts below are surfaced for context but do not block."}
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// Context Panel
// =============================================================================

function ContextPanel({ context }: { context?: ComplianceToolInput["context"] }) {
  if (!context) return null;

  const contextItems = [
    { label: "Intended Use", value: context.intended_use },
    { label: "Jurisdiction", value: context.jurisdiction },
    { label: "Therapeutic Area", value: context.therapeutic_area },
  ].filter((item) => item.value);

  if (contextItems.length === 0) return null;

  return (
    <div className="panel">
      <div className="panel-title">Assessment Context</div>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        {contextItems.map((item) => (
          <div
            key={item.label}
            style={{
              padding: "8px 16px",
              background: "var(--bg-warm)",
              borderRadius: 4,
              borderLeft: "3px solid var(--accent)",
            }}
          >
            <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase" }}>
              {item.label}
            </div>
            <div style={{ fontSize: 14, fontWeight: 500, color: "var(--text)" }}>
              {item.value}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// =============================================================================
// Regulatory Breakdown
// =============================================================================

function RegulatoryBreakdown({ compliance }: { compliance?: ComplianceToolInput["base_compliance"] }) {
  if (!compliance) return null;

  const regulations = [
    {
      id: "dea",
      name: "DEA",
      fullName: "Drug Enforcement Administration",
      flagged: compliance.is_dea_controlled,
      detail: compliance.dea_schedule,
      icon: "🏛️",
    },
    {
      id: "fda",
      name: "FDA",
      fullName: "Food & Drug Administration",
      flagged: compliance.is_fda_banned,
      detail: compliance.fda_category,
      icon: "💊",
    },
    {
      id: "cwc",
      name: "CWC",
      fullName: "Chemical Weapons Convention",
      flagged: compliance.is_cwc_scheduled,
      icon: "☣️",
    },
    {
      id: "epa",
      name: "EPA",
      fullName: "Environmental Protection Agency",
      flagged: compliance.is_epa_pbt,
      detail: compliance.is_epa_pbt ? "PBT Substance" : undefined,
      icon: "🌿",
    },
    {
      id: "reach",
      name: "EU REACH",
      fullName: "Registration, Evaluation, Authorization",
      flagged: compliance.is_eu_reach_banned,
      icon: "🇪🇺",
    },
  ];

  return (
    <div className="panel">
      <div className="panel-title">Regulatory Status</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 12 }}>
        {regulations.map((reg) => (
          <div
            key={reg.id}
            style={{
              padding: 16,
              background: reg.flagged ? "var(--danger-bg)" : "var(--success-bg)",
              borderRadius: 4,
              border: `1px solid ${reg.flagged ? "var(--danger)" : "var(--success)"}`,
              opacity: reg.flagged === undefined ? 0.5 : 1,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
              <span style={{ fontSize: 20 }}>{reg.icon}</span>
              <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text)" }}>{reg.name}</span>
            </div>
            <div
              style={{
                fontSize: 12,
                fontWeight: 500,
                color: reg.flagged ? "var(--danger)" : "var(--success)",
              }}
            >
              {reg.flagged === undefined ? "Not Checked" : reg.flagged ? "FLAGGED" : "Clear"}
            </div>
            {reg.detail && (
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
                {reg.detail}
              </div>
            )}
            <div style={{ fontSize: 9, color: "var(--text-muted)", marginTop: 4 }}>
              {reg.fullName}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// =============================================================================
// Scaffold Analysis
// =============================================================================

function ScaffoldAnalysis({ compliance }: { compliance?: ComplianceToolInput["base_compliance"] }) {
  if (!compliance) return null;

  const flagCount = compliance.faves_flag_count ?? 0;
  const isScaffoldMatch = compliance.is_scaffold_match;

  return (
    <div className="panel">
      <div className="panel-title">Scaffold Analysis</div>
      <div style={{ display: "flex", gap: 16 }}>
        <div
          style={{
            flex: 1,
            padding: 16,
            background: isScaffoldMatch ? "var(--warning-bg)" : "var(--success-bg)",
            borderRadius: 4,
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase" }}>
            Controlled Scaffold
          </div>
          <div
            style={{
              fontSize: 18,
              fontWeight: 600,
              color: isScaffoldMatch ? "var(--warning)" : "var(--success)",
              marginTop: 4,
            }}
          >
            {isScaffoldMatch ? "Match Found" : "No Match"}
          </div>
        </div>
        <div
          style={{
            flex: 1,
            padding: 16,
            background: flagCount > 0 ? "var(--warning-bg)" : "var(--success-bg)",
            borderRadius: 4,
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase" }}>
            Total FAVES Flags
          </div>
          <div
            style={{
              fontSize: 24,
              fontWeight: 700,
              fontFamily: "var(--font-mono)",
              color: flagCount > 0 ? "var(--warning)" : "var(--success)",
              marginTop: 4,
            }}
          >
            {flagCount}
          </div>
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// Drug-Likeness Rules
// =============================================================================

function DrugLikenessRules({ compliance }: { compliance?: ComplianceToolInput["base_compliance"] }) {
  if (compliance?.lipinski === undefined) return null;

  const rules = [
    { name: "Lipinski Rule of 5", passed: compliance.lipinski, description: "MW ≤ 500, LogP ≤ 5, HBD ≤ 5, HBA ≤ 10" },
  ];

  return (
    <div className="panel">
      <div className="panel-title">Drug-Likeness</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {rules.map((rule) => (
          <div
            key={rule.name}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "12px 16px",
              background: rule.passed ? "var(--success-bg)" : "var(--danger-bg)",
              borderRadius: 4,
            }}
          >
            <div>
              <div style={{ fontSize: 13, fontWeight: 500, color: "var(--text)" }}>{rule.name}</div>
              <div style={{ fontSize: 10, color: "var(--text-muted)" }}>{rule.description}</div>
            </div>
            <div
              style={{
                fontSize: 14,
                fontWeight: 600,
                color: rule.passed ? "var(--success)" : "var(--danger)",
              }}
            >
              {rule.passed ? "PASS" : "FAIL"}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// =============================================================================
// Structural Alert Summary (FAVES V4)
// =============================================================================

function StructuralAlertSummary({
  compliance,
  isWhitelisted,
}: {
  compliance?: ComplianceToolInput["base_compliance"];
  isWhitelisted?: boolean;
}) {
  const summary = compliance?.structural_alert_summary;
  if (!summary) return null;

  const alertFree = compliance?.alert_free;
  const totalAlerts = summary.total_alert_count ?? 0;

  // When the compound is FDA-whitelisted, structural alerts surface as
  // informational context — the verdict has already overridden to PROCEED.
  // Render in muted tones rather than warning colors so the dashboard
  // doesn't contradict the green status pill.
  const accentColor = alertFree
    ? "var(--success)"
    : isWhitelisted
      ? "var(--text-muted)"
      : "var(--warning)";
  const accentBg = alertFree
    ? "var(--success-bg)"
    : isWhitelisted
      ? "var(--bg-warm)"
      : "var(--warning-bg)";

  const catalogs = [
    { id: "pains", name: "PAINS", fullName: "Pan-Assay Interference", data: summary.pains },
    { id: "brenk", name: "Brenk", fullName: "Brenk Undesirable Substructures", data: summary.brenk },
    { id: "nih", name: "NIH", fullName: "NIH MLSMR Exclusion", data: summary.nih },
    { id: "zinc", name: "ZINC", fullName: "ZINC Purchasable Exclusion", data: summary.zinc },
    { id: "chembl", name: "ChEMBL", fullName: "ChEMBL Structural Alerts (7 sub-catalogs)", data: summary.chembl ? { count: summary.chembl.count, alerts: [] } : undefined },
  ];

  const pillLabel = alertFree
    ? "Alert-Free"
    : isWhitelisted
      ? `${totalAlerts} Informational Alert${totalAlerts !== 1 ? "s" : ""}`
      : `${totalAlerts} Alert${totalAlerts !== 1 ? "s" : ""}`;

  return (
    <div className="panel">
      <div className="panel-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span>Structural Alerts</span>
        <span
          style={{
            fontSize: 11,
            fontWeight: 600,
            padding: "3px 10px",
            borderRadius: 12,
            background: accentBg,
            color: accentColor,
            border: `1px solid ${accentColor}`,
          }}
        >
          {pillLabel}
        </span>
      </div>
      {isWhitelisted && totalAlerts > 0 && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic", marginBottom: 10 }}>
          Compound is FDA-whitelisted; alerts shown for transparency and do not change the verdict.
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: 10 }}>
        {catalogs.map((cat) => {
          const count = cat.data?.count ?? 0;
          const alerts: string[] = (cat.data as any)?.alerts ?? [];
          const flagged = count > 0;
          // Same softening for individual catalog tiles when whitelisted.
          const tileColor = !flagged
            ? "var(--success)"
            : isWhitelisted
              ? "var(--text-muted)"
              : "var(--warning)";
          const tileBg = !flagged
            ? "var(--success-bg)"
            : isWhitelisted
              ? "var(--bg-warm)"
              : "var(--warning-bg)";
          return (
            <div
              key={cat.id}
              style={{
                padding: 12,
                background: tileBg,
                borderRadius: 4,
                border: `1px solid ${tileColor}`,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>{cat.name}</div>
                <div style={{ fontSize: 16, fontWeight: 700, color: tileColor }}>
                  {count}
                </div>
              </div>
              <div style={{ fontSize: 9, color: "var(--text-muted)", marginBottom: alerts.length > 0 ? 6 : 0 }}>
                {cat.fullName}
              </div>
              {alerts.length > 0 && (
                <div style={{ fontSize: 10, color: tileColor, lineHeight: 1.4 }}>
                  {alerts.slice(0, 3).join(", ")}
                  {alerts.length > 3 && ` +${alerts.length - 3} more`}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// =============================================================================
// PK Classification (Boiled Egg)
// =============================================================================

function PKClassification({ compliance }: { compliance?: ComplianceToolInput["base_compliance"] }) {
  const pk = compliance?.pk_classification;
  if (!pk) return null;

  const giHigh = pk.gi_absorption === "High";
  const bbbYes = pk.bbb_permeant === "Yes";

  return (
    <div className="panel">
      <div className="panel-title">Pharmacokinetic Classification</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div
          style={{
            padding: 16,
            background: giHigh ? "var(--success-bg)" : "var(--warning-bg)",
            borderRadius: 4,
            border: `1px solid ${giHigh ? "var(--success)" : "var(--warning)"}`,
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 4 }}>
            GI Absorption
          </div>
          <div style={{ fontSize: 20, fontWeight: 700, color: giHigh ? "var(--success)" : "var(--warning)" }}>
            {pk.gi_absorption ?? "—"}
          </div>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>BOILED-Egg HIA</div>
        </div>
        <div
          style={{
            padding: 16,
            background: bbbYes ? "var(--accent-bg, var(--bg-warm))" : "var(--bg-warm)",
            borderRadius: 4,
            border: `1px solid ${bbbYes ? "var(--accent, var(--warning))" : "var(--border)"}`,
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 4 }}>
            BBB Permeant
          </div>
          <div style={{ fontSize: 20, fontWeight: 700, color: bbbYes ? "var(--accent, var(--warning))" : "var(--text-muted)" }}>
            {pk.bbb_permeant ?? "—"}
          </div>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>BOILED-Egg BBB</div>
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// Risk Assessment
// =============================================================================

function RiskAssessment({ risk }: { risk?: ComplianceToolInput["risk_assessment"] }) {
  if (!risk) return null;

  const getRiskColor = (level?: string) => {
    switch (level?.toLowerCase()) {
      case "low":
        return "var(--success)";
      case "medium":
      case "moderate":
        return "var(--warning)";
      case "high":
      case "critical":
        return "var(--danger)";
      default:
        return "var(--text-muted)";
    }
  };

  return (
    <div className="panel">
      <div className="panel-title">Risk Assessment</div>

      {/* Overall Risk */}
      {risk.overall_risk && (
        <div
          style={{
            padding: 16,
            background: "var(--bg-warm)",
            borderRadius: 4,
            borderLeft: `4px solid ${getRiskColor(risk.overall_risk)}`,
            marginBottom: 16,
          }}
        >
          <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase" }}>
            Overall Risk Level
          </div>
          <div
            style={{
              fontSize: 20,
              fontWeight: 600,
              color: getRiskColor(risk.overall_risk),
              marginTop: 4,
            }}
          >
            {risk.overall_risk.toUpperCase()}
          </div>
        </div>
      )}

      {/* Risk Factors */}
      {risk.risk_factors && risk.risk_factors.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 8, textTransform: "uppercase" }}>
            Risk Factors
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {risk.risk_factors.map((factor, idx) => (
              <div
                key={idx}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "10px 14px",
                  background: "var(--bg)",
                  borderRadius: 4,
                  borderLeft: `3px solid ${getRiskColor(factor.level)}`,
                }}
              >
                <div>
                  <div style={{ fontSize: 12, fontWeight: 500, color: "var(--text)" }}>{factor.factor}</div>
                  {factor.description && (
                    <div style={{ fontSize: 10, color: "var(--text-muted)" }}>{factor.description}</div>
                  )}
                </div>
                <span
                  style={{
                    fontSize: 10,
                    fontWeight: 600,
                    color: getRiskColor(factor.level),
                    textTransform: "uppercase",
                  }}
                >
                  {factor.level}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Mitigations */}
      {risk.mitigations && risk.mitigations.length > 0 && (
        <div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 8, textTransform: "uppercase" }}>
            Suggested Mitigations
          </div>
          <ul style={{ margin: 0, paddingLeft: 20, fontSize: 12, color: "var(--text)" }}>
            {risk.mitigations.map((m, idx) => (
              <li key={idx} style={{ marginBottom: 4 }}>{m}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Regulatory Pathway
// =============================================================================

function RegulatoryPathway({ pathway }: { pathway?: ComplianceToolInput["regulatory_pathway"] }) {
  if (!pathway) return null;

  return (
    <div className="panel">
      <div className="panel-title">Regulatory Pathway</div>
      <div
        style={{
          padding: 16,
          background: "var(--bg-warm)",
          borderRadius: 4,
          borderLeft: "4px solid var(--accent)",
        }}
      >
        {pathway.pathway && (
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase" }}>
              Recommended Pathway
            </div>
            <div style={{ fontSize: 16, fontWeight: 600, color: "var(--text)", marginTop: 4 }}>
              {pathway.pathway}
            </div>
          </div>
        )}

        {pathway.estimated_timeline && (
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase" }}>
              Estimated Timeline
            </div>
            <div style={{ fontSize: 14, color: "var(--text)", marginTop: 4 }}>
              {pathway.estimated_timeline}
            </div>
          </div>
        )}

        {pathway.key_requirements && pathway.key_requirements.length > 0 && (
          <div>
            <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 8 }}>
              Key Requirements
            </div>
            <ul style={{ margin: 0, paddingLeft: 20, fontSize: 12, color: "var(--text)" }}>
              {pathway.key_requirements.map((req, idx) => (
                <li key={idx} style={{ marginBottom: 4 }}>{req}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

// =============================================================================
// Recommendations
// =============================================================================

function Recommendations({ recommendations }: { recommendations?: string[] }) {
  if (!recommendations || recommendations.length === 0) return null;

  return (
    <div className="panel">
      <div className="panel-title">Recommendations</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {recommendations.map((rec, idx) => (
          <div
            key={idx}
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 12,
              padding: "12px 16px",
              background: "var(--bg-warm)",
              borderRadius: 4,
            }}
          >
            <span
              style={{
                width: 24,
                height: 24,
                borderRadius: "50%",
                background: "var(--accent)",
                color: "white",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 12,
                fontWeight: 600,
                flexShrink: 0,
              }}
            >
              {idx + 1}
            </span>
            <span style={{ fontSize: 13, color: "var(--text)", lineHeight: 1.5 }}>{rec}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function FavesDashboard({
  toolInputs,
  toolInputsPartial,
  toolResult,
}: FavesDashboardProps) {
  const height = toolInputs?.height ?? toolInputsPartial?.height ?? 600;
  const isStreaming = !toolInputs && !toolResult;

  if (isStreaming) {
    return <LoadingShimmer height={height} />;
  }

  const resultData = useViewData<Record<string, any>>({ toolInputs, toolResult });
  const {
    smiles,
    context,
    base_compliance: rawBaseCompliance,
    context_compliance,
    overall_status,
    recommendations,
    regulatory_pathway,
    risk_assessment,
  } = resultData as ComplianceToolInput & { context_compliance?: Record<string, any> };

  // Extract FAVES V3 regulatory fields from context_compliance.base_classification.faves_v3
  // and V4 structural alerts + PK from context_compliance.base_classification
  const baseClassification = (context_compliance as any)?.base_classification;
  const faves_v3 = baseClassification?.faves_v3;
  const base_compliance: ComplianceToolInput["base_compliance"] = {
    ...rawBaseCompliance,
    // Map FAVES V3 regulatory fields if available
    ...(faves_v3 ? {
      is_dea_controlled: faves_v3.is_dea_controlled,
      is_fda_banned: faves_v3.is_fda_banned,
      is_cwc_scheduled: faves_v3.is_cwc_scheduled,
      is_epa_pbt: faves_v3.is_epa_pbt,
      is_eu_reach_banned: faves_v3.is_eu_reach_banned,
      is_scaffold_match: faves_v3.is_scaffold_match,
      scaffold_matches: faves_v3.scaffold_matches,
      faves_flag_count: faves_v3.faves_flag_count,
      dea_schedule: faves_v3.dea_schedule,
      is_whitelisted: faves_v3.is_whitelisted,
      whitelist_name: faves_v3.whitelist_name,
    } : {}),
    // Fall back to rawBaseCompliance whitelist fields when faves_v3 is absent
    // (e.g., novel molecules where base_classification path doesn't populate).
    is_whitelisted: faves_v3?.is_whitelisted ?? rawBaseCompliance?.is_whitelisted,
    whitelist_name: faves_v3?.whitelist_name ?? rawBaseCompliance?.whitelist_name,
    // Map FAVES V4 structural alerts + PK classification
    alert_free: baseClassification?.alert_free,
    structural_alert_summary: baseClassification?.structural_alert_summary,
    pk_classification: baseClassification?.pk_classification,
  };

  // Determine overall status — handle both old format (clean/flagged) and new FAVES format (PASS/BLOCKED)
  const displayStatus = overall_status || base_compliance?.status || (context_compliance as any)?.overall_status;
  const isWhitelisted = !!base_compliance.is_whitelisted;
  const hasAlerts = (base_compliance.structural_alert_summary?.total_alert_count ?? 0) > 0;

  return (
    <div className="faves-dashboard" style={{ width: "100%" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 20,
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
            FAVES Compliance Dashboard
          </div>
        </div>
        {smiles && (
          <code
            style={{
              fontSize: 11,
              fontFamily: "var(--font-mono)",
              color: "var(--text-muted)",
              background: "var(--bg-code)",
              padding: "4px 8px",
              borderRadius: 4,
              maxWidth: 200,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {smiles}
          </code>
        )}
      </div>

      {/* Whitelist banner — explains PROCEED verdicts on compounds with structural alerts */}
      <WhitelistBanner
        isWhitelisted={isWhitelisted}
        whitelistName={base_compliance.whitelist_name}
        hasAlerts={hasAlerts}
      />

      {/* Status + Context Row */}
      <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: 20, marginBottom: 20 }}>
        <StatusIndicator status={displayStatus} />
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <ContextPanel context={context} />
          <ScaffoldAnalysis compliance={base_compliance} />
        </div>
      </div>

      {/* Regulatory Breakdown */}
      <div style={{ marginBottom: 20 }}>
        <RegulatoryBreakdown compliance={base_compliance} />
      </div>

      {/* Drug-Likeness */}
      <div style={{ marginBottom: 20 }}>
        <DrugLikenessRules compliance={base_compliance} />
      </div>

      {/* Structural Alerts (FAVES V4) */}
      <div style={{ marginBottom: 20 }}>
        <StructuralAlertSummary compliance={base_compliance} isWhitelisted={isWhitelisted} />
      </div>

      {/* PK Classification */}
      <div style={{ marginBottom: 20 }}>
        <PKClassification compliance={base_compliance} />
      </div>

      {/* Risk Assessment & Pathway - Side by Side */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, marginBottom: 20 }}>
        <RiskAssessment risk={risk_assessment} />
        <RegulatoryPathway pathway={regulatory_pathway} />
      </div>

      {/* Recommendations */}
      <Recommendations recommendations={recommendations} />
    </div>
  );
}
