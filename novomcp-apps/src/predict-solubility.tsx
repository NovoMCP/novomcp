/**
 * NovoMCP Solubility Prediction Viewer
 *
 * Renders predict_solubility output: logS on a categorical color-banded
 * scale (insoluble → highly soluble), mg/mL secondary, development-
 * phase verdict, and click-through questions about formulation / dose
 * implications.
 *
 * Sync tool — no submission phase.
 */

import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types
// =============================================================================

export interface SolubilityData {
  smiles?: string;
  logS?: number;
  solubility_mg_ml?: number | null;
  temperature?: string;
  category?: string;
  method?: string;
  confidence?: number | null;
}

type SolubilityProps = ViewProps<SolubilityData>;

// =============================================================================
// Category classification (logS bands — standard Lipinski/Yalkowsky scale)
// =============================================================================

interface Band {
  label: string;
  color: string;
  min: number;
  max: number;
  verdict: string;
}

const SOL_BANDS: Band[] = [
  { label: "insoluble", color: "#C25D4E", min: -12, max: -6, verdict: "Likely requires advanced formulation (nanosuspension, amorphous solid dispersion) or molecular redesign." },
  { label: "poorly soluble", color: "#D4884E", min: -6, max: -4, verdict: "Absorption-limited for oral routes; consider salt forms, lipid-based formulations, or prodrug strategies." },
  { label: "moderately soluble", color: "#BFB04E", min: -4, max: -2, verdict: "Developable with standard formulation; monitor in vivo exposure and permeability." },
  { label: "soluble", color: "#7FA35E", min: -2, max: 0, verdict: "Good baseline for oral formulation; unlikely to be solubility-limited." },
  { label: "highly soluble", color: "#4E9BC2", min: 0, max: 2, verdict: "Excellent solubility — but check LogP for membrane permeability trade-off." },
];

function classify(logS: number): Band {
  const found = SOL_BANDS.find((b) => logS >= b.min && logS < b.max);
  return found || (logS >= 2 ? SOL_BANDS[SOL_BANDS.length - 1] : SOL_BANDS[0]);
}

// =============================================================================
// logS gauge — horizontal scale showing where this molecule sits
// =============================================================================

function LogSGauge({ logS }: { logS: number }) {
  const width = 520;
  const height = 70;
  const padL = 24;
  const padR = 24;
  const track = 12;
  const trackY = 22;
  const usableW = width - padL - padR;

  const clamped = Math.max(-8, Math.min(2, logS));
  const xFor = (x: number) => padL + ((x - -8) / 10) * usableW;
  const markerX = xFor(clamped);

  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: "auto", display: "block" }}>
      {/* Bands */}
      {SOL_BANDS.map((b) => {
        const x0 = xFor(Math.max(-8, b.min));
        const x1 = xFor(Math.min(2, b.max));
        return (
          <g key={b.label}>
            <rect
              x={x0}
              y={trackY}
              width={x1 - x0}
              height={track}
              fill={b.color}
              opacity={0.5}
            />
            <text
              x={(x0 + x1) / 2}
              y={trackY + track + 10}
              textAnchor="middle"
              fontSize={8}
              fill="var(--text-muted)"
            >
              {b.label}
            </text>
          </g>
        );
      })}

      {/* Marker */}
      <g>
        <line
          x1={markerX}
          x2={markerX}
          y1={trackY - 6}
          y2={trackY + track + 6}
          stroke="var(--text)"
          strokeWidth={2}
        />
        <circle cx={markerX} cy={trackY - 9} r={5} fill="var(--text)" />
        <text
          x={markerX}
          y={trackY - 14}
          textAnchor="middle"
          fontSize={11}
          fontWeight={600}
          fontFamily="var(--font-mono)"
          fill="var(--text)"
        >
          logS {logS.toFixed(2)}
        </text>
      </g>

      {/* Axis ticks */}
      {[-8, -6, -4, -2, 0, 2].map((x) => (
        <text
          key={x}
          x={xFor(x)}
          y={trackY + track + 24}
          textAnchor="middle"
          fontSize={9}
          fill="var(--text-muted)"
          fontFamily="var(--font-mono)"
        >
          {x}
        </text>
      ))}
    </svg>
  );
}

// =============================================================================
// Main viewer
// =============================================================================

export default function PredictSolubilityViewer(props: SolubilityProps) {
  const { toolInputs, toolInputsPartial, toolResult, sendMessage } = props;
  const data = useViewData<SolubilityData>(props);
  const isStreaming = !toolInputs && !toolResult;
  if (isStreaming) {
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
          minHeight: 180,
        }}
      >
        <div className="loading-spinner" />
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>Predicting solubility…</div>
      </div>
    );
  }

  const logS = data.logS;
  const mgml = data.solubility_mg_ml;
  const smiles = data.smiles || toolInputs?.smiles || toolInputsPartial?.smiles;
  const category = data.category;
  const band = logS != null && Number.isFinite(logS) ? classify(logS) : null;
  const displayCategory = category || band?.label || "—";

  const askAboutSolubility =
    logS != null && band
      ? sendMessage
        ? () => {
            const smilesRef = smiles ? ` for \`${smiles}\`` : "";
            sendMessage({
              role: "user",
              content: [
                {
                  type: "text",
                  text:
                    `Solubility prediction${smilesRef}: logS ${logS.toFixed(2)} ` +
                    `(${mgml != null ? mgml.toFixed(3) + " mg/mL, " : ""}${band.label}). ` +
                    `What formulation strategy should I consider, what's the likely in vivo ` +
                    `absorption ceiling, and do I need to modify the structure to shift this into a more drug-like range?`,
                },
              ],
            });
          }
        : undefined
      : undefined;

  return (
    <div className="predict-solubility-viewer" style={{ width: "100%" }}>
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
            Solubility Prediction
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
          {data.method && (
            <div style={{ fontSize: 10, color: "var(--text-muted)" }}>{data.method}</div>
          )}
          {data.temperature && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {data.temperature}
            </div>
          )}
        </div>
      </div>

      {/* Value cards */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
        {logS != null && (
          <div
            onClick={askAboutSolubility}
            title={askAboutSolubility ? "Click to ask Claude about formulation strategy" : undefined}
            style={{
              padding: "12px 16px",
              background: "var(--bg-warm)",
              borderRadius: 2,
              borderLeft: `3px solid ${band?.color || "var(--accent)"}`,
              minWidth: 140,
              cursor: askAboutSolubility ? "pointer" : undefined,
            }}
          >
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>logS</div>
            <div style={{ fontSize: 24, fontFamily: "var(--font-mono)", fontWeight: 600, color: band?.color || "var(--text)" }}>
              {logS.toFixed(2)}
            </div>
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
              log(mol/L)
            </div>
          </div>
        )}
        {mgml != null && (
          <div
            style={{
              padding: "12px 16px",
              background: "var(--bg-warm)",
              borderRadius: 2,
              borderLeft: "3px solid var(--text-muted)",
              minWidth: 140,
            }}
          >
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Solubility</div>
            <div style={{ fontSize: 22, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text)" }}>
              {mgml < 0.001 ? mgml.toExponential(2) : mgml.toFixed(3)}
            </div>
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>mg/mL</div>
          </div>
        )}
        <div
          style={{
            padding: "12px 16px",
            background: "var(--bg-warm)",
            borderRadius: 2,
            borderLeft: `3px solid ${band?.color || "var(--text-muted)"}`,
            minWidth: 140,
          }}
        >
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Category</div>
          <div style={{ fontSize: 14, fontWeight: 600, color: band?.color || "var(--text)", marginTop: 4, textTransform: "capitalize" }}>
            {displayCategory}
          </div>
          {data.confidence != null && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2, fontFamily: "var(--font-mono)" }}>
              confidence {data.confidence.toFixed(2)}
            </div>
          )}
        </div>
      </div>

      {/* logS gauge */}
      {logS != null && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <div
            className="panel-title"
            style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
          >
            <span>logS on Aqueous Solubility Scale</span>
            <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-muted)" }}>
              {askAboutSolubility ? "click logS card to ask" : ""}
            </span>
          </div>
          <LogSGauge logS={logS} />
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.6 }}>
            Yalkowsky aqueous solubility scale. logS &lt; -6 is typically below the detection
            threshold for traditional formulation and warrants specialized approaches.
            Drug-like oral compounds generally sit in the -4 to -2 range.
          </div>
        </div>
      )}

      {/* Verdict */}
      {band && (
        <div
          className="panel"
          style={{ borderLeft: `3px solid ${band.color}` }}
        >
          <div className="panel-title">Development Implication</div>
          <div style={{ fontSize: 12, color: "var(--text)", lineHeight: 1.6 }}>
            {band.verdict}
          </div>
        </div>
      )}
    </div>
  );
}
