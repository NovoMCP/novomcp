/**
 * NovoMCP ADMET Dashboard Component
 *
 * Interactive ADMET prediction visualization with radar charts,
 * traffic-light indicators, and detailed property breakdowns.
 */
import { useState, useEffect, useRef } from "react";
import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types
// =============================================================================

interface AdmetPrediction {
  name: string;
  value: number;
  unit?: string;
  category: "absorption" | "distribution" | "metabolism" | "excretion" | "toxicity";
  status: "good" | "moderate" | "poor";
  description?: string;
}

interface AdmetToolInput {
  smiles?: string;
  name?: string;
  predictions?: AdmetPrediction[];
  summary?: {
    absorption: number;
    distribution: number;
    metabolism: number;
    excretion: number;
    toxicity: number;
  };
  druglikeness?: {
    lipinski: boolean;
    veber: boolean;
    ghose: boolean;
    egan: boolean;
    muegge: boolean;
  };
  alerts?: string[];
  height?: number;
  // Raw predictions from addie-models backend
  absorption?: Record<string, number>;
  distribution?: Record<string, number>;
  metabolism?: Record<string, number>;
  excretion?: Record<string, number>;
  toxicity?: Record<string, number>;
  nuclear_receptors?: Record<string, number>;
  stress_response?: Record<string, number>;
  properties?: Record<string, number>;
  raw_predictions?: Record<string, number>;
}

type AdmetDashboardProps = ViewProps<AdmetToolInput>;

// =============================================================================
// Summary Computation from Raw Predictions
// =============================================================================

/**
 * Compute ADMET summary scores (0-100) from raw predictions.
 * Higher scores = better (safer for toxicity, more favorable for ADME).
 */
function computeSummaryFromRaw(data: AdmetToolInput): AdmetToolInput["summary"] | null {
  const { absorption, distribution, metabolism, excretion, toxicity, raw_predictions } = data;

  // Helper to average values and convert to 0-100 score
  const avgScore = (values: number[]): number => {
    if (values.length === 0) return 50;
    const avg = values.reduce((a, b) => a + b, 0) / values.length;
    return Math.round(avg * 100);
  };

  // Helper to get "safety score" (inverse of risk)
  const safetyScore = (values: number[]): number => {
    if (values.length === 0) return 50;
    const avg = values.reduce((a, b) => a + b, 0) / values.length;
    return Math.round((1 - avg) * 100);
  };

  // If no raw data available, return null
  if (!absorption && !distribution && !metabolism && !excretion && !toxicity && !raw_predictions) {
    return null;
  }

  // Fields that are NOT 0-1 probabilities — exclude from naive averaging
  const NON_PROBABILITY_FIELDS = new Set([
    'caco2_permeability',           // log cm/s
    'lipophilicity_log_ratio',      // log-ratio
    'aqueous_solubility_log_mol_L', // log mol/L
    'ppbr_percent',                 // 0-100%
    'vdss_L_kg',                    // L/kg
    'half_life_hr',                 // hours
    'clearance_hepatocyte',         // uL/min/1e6 cells
    'clearance_microsome',          // mL/min/g
    'ld50_log_mol_kg',             // log(1/(mol/kg))
    'binding_affinity_score',       // arbitrary scale
  ]);

  // Helper: filter values to only include 0-1 probability fields
  const probabilityValues = (obj: Record<string, unknown>): number[] =>
    Object.entries(obj)
      .filter(([k, v]) => typeof v === 'number' && !NON_PROBABILITY_FIELDS.has(k))
      .map(([_, v]) => v as number);

  // Absorption: use pre-computed score if available, else average probability fields only
  let absorptionScore = 50;
  if (raw_predictions && typeof raw_predictions['absorption_score'] === 'number') {
    absorptionScore = Math.round(raw_predictions['absorption_score'] * 100);
  } else if (absorption && Object.keys(absorption).length > 0) {
    absorptionScore = avgScore(probabilityValues(absorption));
  } else if (raw_predictions) {
    const absKeys = Object.keys(raw_predictions).filter(k =>
      !NON_PROBABILITY_FIELDS.has(k) &&
      (k.includes('hia') || k.includes('pgp') || k.includes('bioavailability'))
    );
    if (absKeys.length > 0) {
      absorptionScore = avgScore(absKeys.map(k => raw_predictions[k]).filter(v => typeof v === 'number'));
    }
  }

  // Distribution: use pre-computed score if available, else average probability fields only
  let distributionScore = 50;
  if (raw_predictions && typeof raw_predictions['distribution_score'] === 'number') {
    distributionScore = Math.round(raw_predictions['distribution_score'] * 100);
  } else if (distribution && Object.keys(distribution).length > 0) {
    distributionScore = avgScore(probabilityValues(distribution));
  } else if (raw_predictions) {
    const distKeys = Object.keys(raw_predictions).filter(k =>
      !NON_PROBABILITY_FIELDS.has(k) &&
      (k.includes('bbb') || k.includes('distribution'))
    );
    if (distKeys.length > 0) {
      distributionScore = avgScore(distKeys.map(k => raw_predictions[k]).filter(v => typeof v === 'number'));
    }
  }

  // Metabolism: use pre-computed score if available, else safety score from CYP probabilities
  let metabolismScore = 50;
  if (raw_predictions && typeof raw_predictions['metabolism_score'] === 'number') {
    metabolismScore = Math.round((1 - raw_predictions['metabolism_score']) * 100);
  } else if (metabolism && Object.keys(metabolism).length > 0) {
    metabolismScore = safetyScore(Object.values(metabolism).filter(v => typeof v === 'number'));
  } else if (raw_predictions) {
    const metabKeys = Object.keys(raw_predictions).filter(k =>
      !NON_PROBABILITY_FIELDS.has(k) &&
      (k.includes('cyp') || k.includes('metabolism'))
    );
    if (metabKeys.length > 0) {
      metabolismScore = safetyScore(metabKeys.map(k => raw_predictions[k]).filter(v => typeof v === 'number'));
    }
  }

  // Excretion: no probability fields available — default to 50 (neutral)
  // Raw excretion data (half_life_hr, clearance_hepatocyte, clearance_microsome) are
  // continuous values in native units, not 0-1 probabilities. Shown in detail view.
  let excretionScore = 50;

  // Toxicity: Lower toxicity = better (safer)
  let toxicityScore = 50;
  if (toxicity && Object.keys(toxicity).length > 0) {
    // For toxicity, we want to show "safety" - invert the scores
    // Exclude overall_toxicity_score from the calculation (it's already aggregated)
    const toxValues = Object.entries(toxicity)
      .filter(([k, v]) => typeof v === 'number' && k !== 'overall_toxicity_score')
      .map(([_, v]) => v as number);
    if (toxValues.length > 0) {
      toxicityScore = safetyScore(toxValues);
    } else if (typeof toxicity.overall_toxicity_score === 'number') {
      toxicityScore = Math.round((1 - toxicity.overall_toxicity_score) * 100);
    }
  } else if (raw_predictions) {
    const toxKeys = Object.keys(raw_predictions).filter(k =>
      k.includes('toxicity') || k.includes('hepato') || k.includes('cardio') ||
      k.includes('ames') || k.includes('carcinogen') || k.includes('mutagenicity')
    );
    if (toxKeys.length > 0) {
      toxicityScore = safetyScore(toxKeys.map(k => raw_predictions[k]).filter(v => typeof v === 'number'));
    }
  }

  return {
    absorption: absorptionScore,
    distribution: distributionScore,
    metabolism: metabolismScore,
    excretion: excretionScore,
    toxicity: toxicityScore,
  };
}

/**
 * Convert raw predictions to AdmetPrediction[] format for display
 */
function extractPredictions(data: AdmetToolInput): AdmetPrediction[] {
  const predictions: AdmetPrediction[] = [];

  const categories: Array<{ key: keyof AdmetToolInput; category: AdmetPrediction["category"] }> = [
    { key: "absorption", category: "absorption" },
    { key: "distribution", category: "distribution" },
    { key: "metabolism", category: "metabolism" },
    { key: "excretion", category: "excretion" },
    { key: "toxicity", category: "toxicity" },
  ];

  for (const { key, category } of categories) {
    const categoryData = data[key] as Record<string, number> | undefined;
    if (categoryData && typeof categoryData === 'object') {
      for (const [name, value] of Object.entries(categoryData)) {
        if (typeof value !== 'number' || name === 'overall_toxicity_score') continue;

        // Determine status based on value and category
        let status: "good" | "moderate" | "poor";
        if (category === "toxicity" || category === "metabolism") {
          // Lower is better for toxicity and CYP inhibition
          status = value < 0.3 ? "good" : value < 0.7 ? "moderate" : "poor";
        } else {
          // Higher is better for absorption/distribution/excretion
          status = value > 0.7 ? "good" : value > 0.3 ? "moderate" : "poor";
        }

        predictions.push({
          name: name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
          value,
          category,
          status,
        });
      }
    }
  }

  return predictions;
}

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
        Computing ADMET predictions...
      </div>
    </div>
  );
}

// =============================================================================
// Radar Chart
// =============================================================================

function AdmetRadar({ summary }: { summary: AdmetToolInput["summary"] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !summary) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const width = canvas.width;
    const height = canvas.height;
    const centerX = width / 2;
    const centerY = height / 2;
    const radius = Math.min(width, height) / 2 - 50;

    ctx.clearRect(0, 0, width, height);

    const values = [
      summary.absorption ?? 50,
      summary.distribution ?? 50,
      summary.metabolism ?? 50,
      summary.excretion ?? 50,
      summary.toxicity ?? 50,
    ];
    const labels = ["Absorption", "Distribution", "Metabolism", "Excretion", "Toxicity"];
    const numPoints = 5;
    const angleStep = (Math.PI * 2) / numPoints;
    const startAngle = -Math.PI / 2;

    const styles = getComputedStyle(document.documentElement);
    const borderColor = styles.getPropertyValue("--border").trim() || "#E8E4DE";
    const accentColor = styles.getPropertyValue("--accent").trim() || "#B8704B";
    const textColor = styles.getPropertyValue("--text-soft").trim() || "#6B6560";
    const successColor = styles.getPropertyValue("--success").trim() || "#6B8E6B";

    // Draw grid circles with labels
    ctx.strokeStyle = borderColor;
    ctx.lineWidth = 1;
    for (let i = 1; i <= 5; i++) {
      ctx.beginPath();
      ctx.arc(centerX, centerY, (radius * i) / 5, 0, Math.PI * 2);
      ctx.stroke();

      // Draw percentage labels
      if (i === 5) {
        ctx.fillStyle = textColor;
        ctx.font = "400 9px Inter, system-ui, sans-serif";
        ctx.fillText(`${i * 20}%`, centerX + 4, centerY - (radius * i) / 5 + 3);
      }
    }

    // Draw axes
    for (let i = 0; i < numPoints; i++) {
      const angle = startAngle + i * angleStep;
      const x = centerX + Math.cos(angle) * radius;
      const y = centerY + Math.sin(angle) * radius;
      ctx.beginPath();
      ctx.moveTo(centerX, centerY);
      ctx.lineTo(x, y);
      ctx.stroke();
    }

    // Draw data polygon
    ctx.beginPath();
    ctx.fillStyle = `${successColor}22`;
    ctx.strokeStyle = successColor;
    ctx.lineWidth = 2;

    for (let i = 0; i < numPoints; i++) {
      const angle = startAngle + i * angleStep;
      const value = values[i] / 100;
      const x = centerX + Math.cos(angle) * radius * value;
      const y = centerY + Math.sin(angle) * radius * value;

      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    // Draw data points
    ctx.fillStyle = successColor;
    for (let i = 0; i < numPoints; i++) {
      const angle = startAngle + i * angleStep;
      const value = values[i] / 100;
      const x = centerX + Math.cos(angle) * radius * value;
      const y = centerY + Math.sin(angle) * radius * value;

      ctx.beginPath();
      ctx.arc(x, y, 5, 0, Math.PI * 2);
      ctx.fill();
    }

    // Draw labels
    ctx.fillStyle = textColor;
    ctx.font = "500 11px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    for (let i = 0; i < numPoints; i++) {
      const angle = startAngle + i * angleStep;
      const labelRadius = radius + 30;
      const x = centerX + Math.cos(angle) * labelRadius;
      const y = centerY + Math.sin(angle) * labelRadius;

      ctx.fillText(labels[i], x, y);
      ctx.font = "600 12px Inter, system-ui, sans-serif";
      ctx.fillStyle = accentColor;
      ctx.fillText(`${values[i]}%`, x, y + 16);
      ctx.fillStyle = textColor;
      ctx.font = "500 11px Inter, system-ui, sans-serif";
    }
  }, [summary]);

  return (
    <canvas
      ref={canvasRef}
      width={320}
      height={320}
      style={{ display: "block", margin: "0 auto" }}
    />
  );
}

// =============================================================================
// Traffic Light Indicator
// =============================================================================

function TrafficLight({ status }: { status: "good" | "moderate" | "poor" }) {
  const colors = {
    good: "var(--success)",
    moderate: "var(--warning)",
    poor: "var(--danger)",
  };

  return (
    <div
      style={{
        width: 10,
        height: 10,
        borderRadius: "50%",
        background: colors[status],
        boxShadow: `0 0 6px ${colors[status]}`,
      }}
    />
  );
}

// =============================================================================
// Prediction Card
// =============================================================================

function PredictionCard({ prediction }: { prediction: AdmetPrediction }) {
  const categoryColors: Record<string, string> = {
    absorption: "#B8704B",
    distribution: "#6B8E6B",
    metabolism: "#7B8FB8",
    excretion: "#C4956A",
    toxicity: "#A65D5D",
  };

  return (
    <div
      style={{
        padding: "12px 14px",
        background: "var(--bg-warm)",
        borderRadius: 2,
        borderLeft: `3px solid ${categoryColors[prediction.category]}`,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
        <span style={{ fontSize: 12, fontWeight: 500, color: "var(--text)" }}>
          {prediction.name}
        </span>
        <TrafficLight status={prediction.status} />
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 4 }}>
        <span style={{ fontSize: 18, fontFamily: "var(--font-mono)", color: "var(--text)" }}>
          {prediction.value.toFixed(2)}
        </span>
        {prediction.unit && (
          <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
            {prediction.unit}
          </span>
        )}
      </div>
      {prediction.description && (
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
          {prediction.description}
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Druglikeness Panel
// =============================================================================

function DruglikenessPanel({ druglikeness }: { druglikeness: AdmetToolInput["druglikeness"] }) {
  if (!druglikeness) return null;

  const rules = [
    { name: "Lipinski", passed: druglikeness.lipinski, description: "Rule of 5" },
    { name: "Veber", passed: druglikeness.veber, description: "Oral bioavailability" },
    { name: "Ghose", passed: druglikeness.ghose, description: "Drug-like filter" },
    { name: "Egan", passed: druglikeness.egan, description: "BBB permeability" },
    { name: "Muegge", passed: druglikeness.muegge, description: "Pharmacophore" },
  ];

  const passedCount = rules.filter(r => r.passed).length;

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">
        Druglikeness ({passedCount}/{rules.length} passed)
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {rules.map((rule) => (
          <div
            key={rule.name}
            style={{
              padding: "8px 12px",
              background: rule.passed ? "var(--success-bg)" : "var(--danger-bg)",
              borderRadius: 2,
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <span style={{ color: rule.passed ? "var(--success)" : "var(--danger)", fontSize: 12 }}>
              {rule.passed ? "✓" : "✗"}
            </span>
            <div>
              <div style={{ fontSize: 11, fontWeight: 500, color: "var(--text)" }}>
                {rule.name}
              </div>
              <div style={{ fontSize: 9, color: "var(--text-muted)" }}>
                {rule.description}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// =============================================================================
// Alerts Panel
// =============================================================================

function AlertsPanel({ alerts }: { alerts?: string[] }) {
  if (!alerts || alerts.length === 0) return null;

  return (
    <div className="panel" style={{ marginTop: 16, borderColor: "var(--warning)" }}>
      <div className="panel-title" style={{ color: "var(--warning)" }}>
        Structural Alerts ({alerts.length})
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {alerts.map((alert, idx) => (
          <div
            key={idx}
            style={{
              padding: "8px 12px",
              background: "var(--warning-bg)",
              borderRadius: 2,
              fontSize: 12,
              color: "var(--text)",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <span style={{ color: "var(--warning)" }}>⚠</span>
            {alert}
          </div>
        ))}
      </div>
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function AdmetDashboard({
  toolInputs,
  toolInputsPartial,
  toolResult,
}: AdmetDashboardProps) {
  const height = toolInputs?.height ?? toolInputsPartial?.height ?? 500;
  const isStreaming = !toolInputs && !toolResult;

  if (isStreaming) {
    return <LoadingShimmer height={height} />;
  }

  const rawData = useViewData<AdmetToolInput>({ toolInputs, toolResult });
  const { smiles, name, druglikeness, alerts } = rawData;

  // Use provided predictions or extract from raw data
  const predictions = rawData.predictions || extractPredictions(rawData);

  // Use provided summary or compute from raw predictions
  const summary = rawData.summary || computeSummaryFromRaw(rawData);

  // Group predictions by category
  const groupedPredictions = predictions?.reduce((acc, pred) => {
    if (!acc[pred.category]) acc[pred.category] = [];
    acc[pred.category].push(pred);
    return acc;
  }, {} as Record<string, AdmetPrediction[]>) ?? {};

  const categories = ["absorption", "distribution", "metabolism", "excretion", "toxicity"];

  return (
    <div className="admet-dashboard" style={{ width: "100%", maxHeight: "600px", overflowY: "auto" }}>
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
            {name || "ADMET Dashboard"}
          </div>
        </div>
        {smiles && (
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "var(--text-muted)",
              maxWidth: 250,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {smiles}
          </div>
        )}
      </div>

      {/* Main Content */}
      <div style={{ display: "grid", gridTemplateColumns: "340px 1fr", gap: 20 }}>
        {/* Radar Chart */}
        <div className="panel">
          <div className="panel-title">ADMET Profile</div>
          {summary ? (
            <>
              <AdmetRadar summary={summary} />
              {/* Show note if some categories have no actual data */}
              {(!rawData.absorption && !rawData.distribution && !rawData.excretion) && (
                <div style={{
                  fontSize: 10,
                  color: "var(--text-muted)",
                  textAlign: "center",
                  marginTop: 8,
                  fontStyle: "italic"
                }}>
                  Note: A/D/E scores estimated from available data.
                  {rawData.properties?.molecular_weight && rawData.properties.molecular_weight > 1000 && (
                    <> Large molecules (&gt;1000 Da) may have limited ADMET model coverage.</>
                  )}
                </div>
              )}
            </>
          ) : (
            <div style={{ height: 320, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)" }}>
              No summary data
            </div>
          )}
        </div>

        {/* Predictions by Category */}
        <div>
          {categories.map((category) => {
            const categoryPredictions = groupedPredictions[category];
            if (!categoryPredictions || categoryPredictions.length === 0) return null;

            return (
              <div key={category} style={{ marginBottom: 16 }}>
                <div
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    textTransform: "uppercase",
                    letterSpacing: "0.08em",
                    color: "var(--text-muted)",
                    marginBottom: 8,
                  }}
                >
                  {category}
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 8 }}>
                  {categoryPredictions.map((pred, idx) => (
                    <PredictionCard key={idx} prediction={pred} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Druglikeness */}
      <DruglikenessPanel druglikeness={druglikeness} />

      {/* Alerts */}
      <AlertsPanel alerts={alerts} />
    </div>
  );
}
