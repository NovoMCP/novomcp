/**
 * NovoMCP Clinical Outcomes Viewer
 *
 * Renders predict_clinical_outcomes output: Phase I clearance
 * probability gauge, calibration note, competence-check panel, and
 * SHAP feature-contribution waterfall. Click the probability or any
 * SHAP feature to ask Claude for grounded interpretation.
 */

import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types
// =============================================================================

interface ShapFeature {
  name?: string;
  feature?: string;
  value?: number;
  feature_value?: number;
  shap?: number;           // novoexpert's actual field name
  shap_value?: number;     // legacy / alternate backends
  contribution?: number;   // legacy / alternate backends
  direction?: "positive" | "negative" | string;
}

interface CompetenceCheck {
  in_domain?: boolean;
  domain?: string;
  therapeutic_area?: string;
  auroc?: number;
  message?: string;
  reason?: string;
}

interface FeatureSources {
  succeeded?: string[];
  failed?: string[];
}

interface ClinicalOutcomesToolInput {
  smiles?: string;

  phase1_clearance_probability?: number;
  phase1_clearance_probability_raw?: number;
  calibration?: string;

  model_name?: string;
  model_version?: string;

  competence_check?: CompetenceCheck;

  top_shap_features?: ShapFeature[];
  feature_count?: number;
  missing_features?: string[];

  feature_sources?: FeatureSources;

  wall_time_seconds?: number;
  warnings?: string[];
}

type ClinicalOutcomesProps = ViewProps<ClinicalOutcomesToolInput>;

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
        Predicting Phase I clearance (NovoExpert v3)…
      </div>
    </div>
  );
}

// =============================================================================
// Probability Gauge — horizontal bar with color bands and labeled marker
// =============================================================================

function ProbabilityGauge({
  probability,
  rawProbability,
  onClick,
}: {
  probability?: number;
  rawProbability?: number;
  onClick?: () => void;
}) {
  if (probability == null) return null;

  const pct = Math.max(0, Math.min(1, probability));
  const rawPct = rawProbability != null ? Math.max(0, Math.min(1, rawProbability)) : null;

  const width = 600;
  const height = 74;
  const leftPad = 30;
  const rightPad = 30;
  const barY = 28;
  const barH = 18;
  const plotW = width - leftPad - rightPad;

  // Color bands keyed to the discovery funnel's 0.4 / 0.6 cutoffs used in
  // the autonomous runbook (Stage 9 — clinical outcomes gate).
  const bandBoundaries = [0, 0.4, 0.6, 1];
  const bandColors = ["var(--danger)", "var(--warning)", "var(--success)"];

  const toX = (p: number) => leftPad + p * plotW;

  return (
    <svg
      width="100%"
      viewBox={`0 0 ${width} ${height}`}
      onClick={onClick}
      style={{ display: "block", cursor: onClick ? "pointer" : undefined }}
    >
      {/* Color bands */}
      {bandBoundaries.slice(0, -1).map((start, i) => {
        const end = bandBoundaries[i + 1];
        return (
          <rect
            key={i}
            x={toX(start)}
            y={barY}
            width={toX(end) - toX(start)}
            height={barH}
            fill={bandColors[i]}
            fillOpacity={0.15}
          />
        );
      })}
      {/* Cutoff tick marks at 0.4, 0.6 */}
      {[0.4, 0.6].map((c) => (
        <g key={c}>
          <line
            x1={toX(c)}
            x2={toX(c)}
            y1={barY - 2}
            y2={barY + barH + 2}
            stroke="var(--text-muted)"
            strokeOpacity={0.6}
            strokeDasharray="2 3"
          />
          <text
            x={toX(c)}
            y={barY + barH + 14}
            fontSize={9}
            textAnchor="middle"
            fill="var(--text-muted)"
            style={{ fontFamily: "var(--font-mono)" }}
          >
            {c.toFixed(1)}
          </text>
        </g>
      ))}

      {/* Raw (uncalibrated) probability — lighter marker */}
      {rawPct != null && (
        <line
          x1={toX(rawPct)}
          x2={toX(rawPct)}
          y1={barY - 4}
          y2={barY + barH + 4}
          stroke="var(--text-muted)"
          strokeWidth={2}
          strokeOpacity={0.5}
        >
          <title>Raw probability (pre-calibration): {rawPct.toFixed(3)}</title>
        </line>
      )}

      {/* Calibrated probability — primary marker */}
      <line
        x1={toX(pct)}
        x2={toX(pct)}
        y1={barY - 8}
        y2={barY + barH + 8}
        stroke="var(--text)"
        strokeWidth={3}
      />
      <text
        x={toX(pct)}
        y={barY - 12}
        fontSize={14}
        textAnchor="middle"
        fill="var(--text)"
        style={{ fontFamily: "var(--font-mono)", fontWeight: 700 }}
      >
        {(pct * 100).toFixed(1)}%
      </text>

      {/* 0 / 1 scale labels */}
      <text
        x={leftPad}
        y={barY + barH + 14}
        fontSize={9}
        textAnchor="start"
        fill="var(--text-muted)"
        style={{ fontFamily: "var(--font-mono)" }}
      >
        0
      </text>
      <text
        x={width - rightPad}
        y={barY + barH + 14}
        fontSize={9}
        textAnchor="end"
        fill="var(--text-muted)"
        style={{ fontFamily: "var(--font-mono)" }}
      >
        1
      </text>
    </svg>
  );
}

// =============================================================================
// Competence-check panel
// =============================================================================

function CompetencePanel({
  check,
}: {
  check?: CompetenceCheck;
}) {
  if (!check) return null;

  const inDomain = check.in_domain;
  const color = inDomain ? "var(--success)" : "var(--warning)";
  const label = inDomain ? "In-domain prediction" : "Out-of-domain — interpret cautiously";
  const domain = check.domain || check.therapeutic_area;

  return (
    <div
      className="panel"
      style={{ marginTop: 16, borderLeft: `3px solid ${color}` }}
    >
      <div className="panel-title">Competence Check</div>
      <div style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
        <span
          style={{
            padding: "3px 10px",
            background: "var(--bg-warm)",
            borderRadius: 2,
            fontSize: 12,
            fontWeight: 500,
            color,
          }}
        >
          {label}
        </span>
        {domain && (
          <span style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
            {domain}
          </span>
        )}
        {check.auroc != null && (
          <span
            style={{ fontSize: 11, color: "var(--text)", fontFamily: "var(--font-mono)" }}
            title="Domain-specific AUROC from the NovoExpert v3 validation set"
          >
            AUROC {check.auroc.toFixed(2)}
          </span>
        )}
      </div>
      {(check.message || check.reason) && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 8, lineHeight: 1.5 }}>
          {check.message || check.reason}
        </div>
      )}
    </div>
  );
}

// =============================================================================
// SHAP feature waterfall — horizontal bars showing which features drive
// the prediction up (positive contribution) or down (negative).
// =============================================================================

function ShapWaterfall({
  features,
  onFeatureClick,
}: {
  features?: ShapFeature[];
  onFeatureClick?: (f: ShapFeature) => void;
}) {
  if (!features || features.length === 0) return null;

  // Normalize a shap feature across the possible backend variants.
  const items = features
    .map((f) => {
      const name = f.name || f.feature || "feature";
      const shap = f.shap ?? f.shap_value ?? f.contribution ?? 0;
      const rawValue = f.feature_value ?? f.value;
      return { name, shap, rawValue, raw: f };
    })
    .sort((a, b) => Math.abs(b.shap) - Math.abs(a.shap))
    .slice(0, 15);

  if (items.length === 0) return null;

  const maxAbs = Math.max(...items.map((x) => Math.abs(x.shap)), 1e-9);

  const barHeight = 20;
  const rowGap = 6;
  const leftPad = 190;
  const rightPad = 70;
  const topPad = 10;
  const bottomPad = 10;
  const plotWidth = 600 - leftPad - rightPad;
  const svgH = topPad + bottomPad + items.length * (barHeight + rowGap);
  const zeroX = leftPad + plotWidth / 2;

  return (
    <svg width="100%" viewBox={`0 0 600 ${svgH}`} style={{ display: "block" }}>
      {/* Zero axis */}
      <line
        x1={zeroX}
        x2={zeroX}
        y1={topPad}
        y2={svgH - bottomPad}
        stroke="var(--text-muted)"
        strokeOpacity={0.4}
        strokeDasharray="2 3"
      />
      {items.map((it, i) => {
        const y = topPad + i * (barHeight + rowGap);
        const barLen = (Math.abs(it.shap) / maxAbs) * (plotWidth / 2);
        const isPos = it.shap >= 0;
        const x = isPos ? zeroX : zeroX - barLen;
        const color = isPos ? "var(--success)" : "var(--danger)";
        return (
          <g
            key={i}
            onClick={onFeatureClick ? () => onFeatureClick(it.raw) : undefined}
            style={{ cursor: onFeatureClick ? "pointer" : undefined }}
          >
            {/* Feature label (left) */}
            <text
              x={leftPad - 6}
              y={y + barHeight / 2 + 4}
              fontSize={10}
              textAnchor="end"
              fill="var(--text)"
              style={{ fontFamily: "var(--font-mono)" }}
            >
              {it.name.length > 30 ? it.name.slice(0, 28) + "…" : it.name}
            </text>

            {/* Shap bar */}
            <rect
              x={x}
              y={y}
              width={barLen}
              height={barHeight}
              fill={color}
              rx={1}
            >
              <title>
                {`${it.name}\nSHAP: ${it.shap >= 0 ? "+" : ""}${it.shap.toFixed(4)}${it.rawValue != null ? `\nFeature value: ${typeof it.rawValue === "number" ? it.rawValue.toFixed(3) : it.rawValue}` : ""}`}
              </title>
            </rect>

            {/* Shap value label (right of bar) */}
            <text
              x={isPos ? x + barLen + 4 : x - 4}
              y={y + barHeight / 2 + 4}
              fontSize={10}
              textAnchor={isPos ? "start" : "end"}
              fill={color}
              style={{ fontFamily: "var(--font-mono)", fontWeight: 500 }}
            >
              {it.shap >= 0 ? "+" : ""}
              {it.shap.toFixed(3)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// =============================================================================
// Feature Sources badges
// =============================================================================

function FeatureSourceBadges({
  sources,
  featureCount,
  missingFeatures,
}: {
  sources?: FeatureSources;
  featureCount?: number;
  missingFeatures?: string[];
}) {
  const succeeded = sources?.succeeded || [];
  const failed = sources?.failed || [];
  const hasAny = succeeded.length > 0 || failed.length > 0 || featureCount != null;

  if (!hasAny) return null;

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">Feature Assembly</div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
        {succeeded.map((s) => (
          <span
            key={s}
            style={{
              fontSize: 10,
              padding: "2px 8px",
              background: "var(--bg-warm)",
              borderLeft: "2px solid var(--success)",
              borderRadius: 2,
              color: "var(--success)",
              fontFamily: "var(--font-mono)",
            }}
            title="Upstream service call succeeded"
          >
            ✓ {s}
          </span>
        ))}
        {failed.map((s) => (
          <span
            key={s}
            style={{
              fontSize: 10,
              padding: "2px 8px",
              background: "var(--bg-warm)",
              borderLeft: "2px solid var(--danger)",
              borderRadius: 2,
              color: "var(--danger)",
              fontFamily: "var(--font-mono)",
            }}
            title="Upstream service call failed — features may be imputed or missing"
          >
            ✗ {s}
          </span>
        ))}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
        {featureCount != null && (
          <span>
            <strong style={{ color: "var(--text)", fontFamily: "var(--font-mono)" }}>{featureCount}</strong>{" "}
            features assembled
          </span>
        )}
        {missingFeatures && missingFeatures.length > 0 && (
          <span style={{ marginLeft: 10, color: "var(--warning)" }}>
            · {missingFeatures.length} missing (imputed): {missingFeatures.slice(0, 4).join(", ")}
            {missingFeatures.length > 4 && ` +${missingFeatures.length - 4}`}
          </span>
        )}
      </div>
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function ClinicalOutcomesViewer(props: ClinicalOutcomesProps) {
  const { toolInputs, toolInputsPartial, toolResult, sendMessage } = props;
  const data = useViewData<ClinicalOutcomesToolInput>(props);
  const isStreaming = !toolInputs && !toolResult;
  if (isStreaming) {
    return <LoadingShimmer />;
  }

  const smiles = data.smiles || toolInputs?.smiles || toolInputsPartial?.smiles;
  const prob = data.phase1_clearance_probability;

  const askAboutProbability =
    prob != null
      ? sendMessage
        ? () => {
            const smilesRef = smiles ? `\`${smiles}\`` : "this compound";
            const band =
              prob < 0.4
                ? "low (below the 0.4 Stage 9 gate — the runbook suggests deprioritizing for MD)"
                : prob < 0.6
                  ? "borderline (0.4–0.6 — proceed with caution)"
                  : "high (above 0.6 — runbook greenlights MD simulation)";
            sendMessage({
              role: "user",
              content: [
                {
                  type: "text",
                  text:
                    `Phase I clearance probability for ${smilesRef} is ${(prob * 100).toFixed(1)}%. ` +
                    `That's ${band}. Given the discovery-funnel runbook's 0.4/0.6 gates, should I proceed with ` +
                    `MD simulation (50 credits), switch to the second-ranked binder, or revisit the chemotype? ` +
                    `Which SHAP features most explain this prediction and are they fixable by medicinal-chemistry modifications?`,
                },
              ],
            });
          }
        : undefined
      : undefined;

  const askAboutFeature = sendMessage
    ? (f: ShapFeature) => {
        const name = f.name || f.feature || "a feature";
        const shap = f.shap ?? f.shap_value ?? f.contribution ?? 0;
        const rawVal = f.feature_value ?? f.value;
        const smilesRef = smiles ? ` for \`${smiles}\`` : "";
        const direction = shap >= 0 ? "increasing" : "decreasing";
        sendMessage({
          role: "user",
          content: [
            {
              type: "text",
              text:
                `I clicked the SHAP feature **${name}**${smilesRef}. ` +
                `Its contribution is ${shap >= 0 ? "+" : ""}${shap.toFixed(4)} (${direction} clearance probability), ` +
                `with feature value ${rawVal != null ? rawVal : "?"}. ` +
                `What does this feature represent mechanistically, why is this molecule's value pushing the prediction ` +
                `${direction === "increasing" ? "up" : "down"}, and can this feature be moved in the right direction ` +
                `by a reasonable medicinal-chemistry modification?`,
            },
          ],
        });
      }
    : undefined;

  const modelLabel = [data.model_name, data.model_version].filter(Boolean).join(" ");

  return (
    <div className="clinical-outcomes-viewer" style={{ width: "100%" }}>
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
            }}
          >
            Clinical Outcomes — Phase I Clearance
          </div>
          {smiles && (
            <div
              style={{
                fontSize: 11,
                fontFamily: "var(--font-mono)",
                color: "var(--text-muted)",
                marginTop: 4,
                maxWidth: 460,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
              title={smiles}
            >
              {smiles}
            </div>
          )}
        </div>
        <div style={{ textAlign: "right" }}>
          {modelLabel && (
            <div style={{ fontSize: 10, color: "var(--text-muted)" }}>{modelLabel}</div>
          )}
          {data.calibration && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {data.calibration} calibration
            </div>
          )}
          {data.wall_time_seconds != null && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)", marginTop: 2 }}>
              {data.wall_time_seconds.toFixed(1)} s
            </div>
          )}
        </div>
      </div>

      {prob != null && (
        <div className="panel">
          <div
            className="panel-title"
            style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
          >
            <span>Calibrated Phase I Clearance Probability</span>
            {askAboutProbability && (
              <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-muted)" }}>
                click gauge to ask
              </span>
            )}
          </div>
          <ProbabilityGauge
            probability={prob}
            rawProbability={data.phase1_clearance_probability_raw}
            onClick={askAboutProbability}
          />
          {data.phase1_clearance_probability_raw != null &&
            Math.abs(data.phase1_clearance_probability_raw - prob) > 0.01 && (
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
                Raw (pre-calibration): {(data.phase1_clearance_probability_raw * 100).toFixed(1)}% ·
                Isotonic calibration maps to {(prob * 100).toFixed(1)}%
              </div>
            )}
        </div>
      )}

      <CompetencePanel check={data.competence_check} />

      {data.top_shap_features && data.top_shap_features.length > 0 && (
        <div className="panel" style={{ marginTop: 16 }}>
          <div
            className="panel-title"
            style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
          >
            <span>SHAP Feature Contributions</span>
            {askAboutFeature && (
              <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-muted)" }}>
                click any bar to ask
              </span>
            )}
          </div>
          <ShapWaterfall
            features={data.top_shap_features}
            onFeatureClick={askAboutFeature}
          />
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.6 }}>
            Green bars increase Phase I clearance probability; red bars decrease it.
            Bar length is proportional to absolute SHAP magnitude. Showing top 15 by
            magnitude.
          </div>
        </div>
      )}

      <FeatureSourceBadges
        sources={data.feature_sources}
        featureCount={data.feature_count}
        missingFeatures={data.missing_features}
      />

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
