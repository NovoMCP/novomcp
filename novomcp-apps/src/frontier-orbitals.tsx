/**
 * NovoMCP Frontier Orbital Analysis Viewer
 *
 * Renders the output of predict_frontier_orbitals: HOMO/LUMO energy
 * levels as a ladder diagram, emission color preview, OLED
 * classification + rationale, and SMARTS-detected motifs with
 * click-to-ask-Claude bidirectional interaction.
 */

import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types
// =============================================================================

interface MotifHit {
  motif?: string;
  name?: string;
  description?: string;
  count?: number;
}

interface FrontierOrbitalsToolInput {
  smiles?: string;
  solvent?: string;

  homo_ev?: number;
  lumo_ev?: number;
  gap_ev?: number;

  emission_wavelength_nm?: number;
  emission_color?: string;
  triplet_energy_ev?: number;
  singlet_triplet_gap_ev?: number;

  oled_classification?: string;
  oled_rationale?: string;
  oled_motifs?: MotifHit[];

  dipole_debye?: number;
  method?: string;
  wall_time_seconds?: number;
  warnings?: string[];
}

type FrontierOrbitalsProps = ViewProps<FrontierOrbitalsToolInput>;

// =============================================================================
// Emission color mapping — physical light colors for the preview swatch.
// Matches the backend's emission_color categories (UV / blue / green / yellow /
// orange / red / infrared / not_emissive / visible).
// =============================================================================

const EMISSION_COLOR_MAP: Record<string, string> = {
  uv: "#8b7cc0",
  blue: "#3b82f6",
  cyan: "#22d3ee",
  green: "#22c55e",
  yellow: "#eab308",
  orange: "#f97316",
  red: "#ef4444",
  infrared: "#7f1d1d",
  visible: "#a3a3a3",
  not_emissive: "var(--text-muted)",
};

function emissionSwatchColor(color?: string): string {
  if (!color) return "var(--text-muted)";
  return EMISSION_COLOR_MAP[color.toLowerCase()] ?? "var(--text-muted)";
}

// =============================================================================
// Classification → label + accent color
// =============================================================================

function classificationInfo(cls?: string): { label: string; color: string; hint: string } {
  const key = (cls || "").toLowerCase();
  switch (key) {
    case "fluorescent_emitter":
      return { label: "Fluorescent Emitter", color: "var(--success)", hint: "Singlet-to-ground transition (S1 → S0)" };
    case "phosphorescent_emitter":
      return { label: "Phosphorescent Emitter", color: "var(--accent)", hint: "Triplet-harvesting (T1 → S0)" };
    case "tadf_candidate":
      return { label: "TADF Candidate", color: "var(--accent)", hint: "Small S–T gap, thermal upconversion" };
    case "charge_transport":
      return { label: "Charge Transport / Host", color: "var(--warning)", hint: "Wide gap; hole-transport or host material" };
    case "host":
      return { label: "Host Material", color: "var(--warning)", hint: "Wide gap, not primarily emissive" };
    case "not_emissive":
      return { label: "Not Emissive", color: "var(--text-muted)", hint: "Gap too wide for visible emission" };
    default:
      return { label: cls || "Unclassified", color: "var(--text-muted)", hint: "" };
  }
}

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
        Computing frontier orbitals…
      </div>
    </div>
  );
}

// =============================================================================
// Energy Ladder — HOMO / LUMO / (optional) triplet levels on a vertical axis
// =============================================================================

function EnergyLadder({
  homo,
  lumo,
  triplet,
  gap,
  stGap,
}: {
  homo?: number;
  lumo?: number;
  triplet?: number;
  gap?: number;
  stGap?: number;
}) {
  if (homo == null || lumo == null) return null;

  // Pad the scale so the bars aren't clipped at the edges.
  const padEv = 0.6;
  const yMax = lumo + padEv;
  const yMin = homo - padEv;
  const ySpan = yMax - yMin || 1;

  const width = 460;
  const height = 240;
  const leftPad = 64;
  const rightPad = 20;
  const topPad = 20;
  const bottomPad = 30;
  const plotH = height - topPad - bottomPad;
  const plotW = width - leftPad - rightPad;

  const toY = (ev: number) => topPad + ((yMax - ev) / ySpan) * plotH;

  const homoY = toY(homo);
  const lumoY = toY(lumo);
  const tripletY = triplet != null ? toY(triplet) : null;

  const levelWidth = plotW * 0.55;
  const levelX = leftPad + (plotW - levelWidth) / 2;

  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} style={{ display: "block" }}>
      {/* Y-axis */}
      <line
        x1={leftPad}
        x2={leftPad}
        y1={topPad}
        y2={height - bottomPad}
        stroke="var(--text-muted)"
        strokeOpacity={0.4}
      />
      {/* y=0 guide if 0 is inside range */}
      {yMin < 0 && yMax > 0 && (
        <line
          x1={leftPad}
          x2={width - rightPad}
          y1={toY(0)}
          y2={toY(0)}
          stroke="var(--text-muted)"
          strokeOpacity={0.2}
          strokeDasharray="2 3"
        />
      )}

      {/* HOMO level */}
      <line
        x1={levelX}
        x2={levelX + levelWidth}
        y1={homoY}
        y2={homoY}
        stroke="var(--accent)"
        strokeWidth={3}
      />
      <text
        x={leftPad - 6}
        y={homoY + 4}
        fontSize={11}
        textAnchor="end"
        fill="var(--text)"
        style={{ fontFamily: "var(--font-mono)" }}
      >
        {homo.toFixed(2)} eV
      </text>
      <text
        x={levelX + levelWidth + 6}
        y={homoY + 4}
        fontSize={11}
        fill="var(--text-muted)"
      >
        HOMO
      </text>

      {/* LUMO level */}
      <line
        x1={levelX}
        x2={levelX + levelWidth}
        y1={lumoY}
        y2={lumoY}
        stroke="var(--accent)"
        strokeWidth={3}
      />
      <text
        x={leftPad - 6}
        y={lumoY + 4}
        fontSize={11}
        textAnchor="end"
        fill="var(--text)"
        style={{ fontFamily: "var(--font-mono)" }}
      >
        {lumo.toFixed(2)} eV
      </text>
      <text
        x={levelX + levelWidth + 6}
        y={lumoY + 4}
        fontSize={11}
        fill="var(--text-muted)"
      >
        LUMO
      </text>

      {/* Triplet level (dashed, if present) */}
      {tripletY != null && triplet != null && (
        <>
          <line
            x1={levelX + 30}
            x2={levelX + levelWidth - 30}
            y1={tripletY}
            y2={tripletY}
            stroke="var(--warning)"
            strokeWidth={2}
            strokeDasharray="4 3"
          />
          <text
            x={levelX + levelWidth - 24}
            y={tripletY - 4}
            fontSize={10}
            textAnchor="end"
            fill="var(--warning)"
          >
            T₁ {triplet.toFixed(2)} eV
          </text>
        </>
      )}

      {/* Gap arrow */}
      {gap != null && (
        <>
          <line
            x1={levelX + levelWidth * 0.3}
            x2={levelX + levelWidth * 0.3}
            y1={homoY}
            y2={lumoY}
            stroke="var(--text-muted)"
            strokeWidth={1}
            markerEnd="url(#arrowhead-up)"
            markerStart="url(#arrowhead-down)"
          />
          <text
            x={levelX + levelWidth * 0.3 + 8}
            y={(homoY + lumoY) / 2 + 4}
            fontSize={11}
            fill="var(--text)"
            style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}
          >
            Δ {gap.toFixed(2)} eV
          </text>
        </>
      )}

      {/* S–T gap annotation */}
      {stGap != null && tripletY != null && (
        <text
          x={levelX + levelWidth * 0.3 + 8}
          y={tripletY + 14}
          fontSize={9}
          fill="var(--warning)"
        >
          S–T gap {stGap.toFixed(2)} eV
        </text>
      )}

      {/* Arrowhead defs */}
      <defs>
        <marker
          id="arrowhead-up"
          markerWidth="6"
          markerHeight="6"
          refX="3"
          refY="0"
          orient="auto"
        >
          <path d="M 0 6 L 3 0 L 6 6 z" fill="var(--text-muted)" />
        </marker>
        <marker
          id="arrowhead-down"
          markerWidth="6"
          markerHeight="6"
          refX="3"
          refY="6"
          orient="auto"
        >
          <path d="M 0 0 L 3 6 L 6 0 z" fill="var(--text-muted)" />
        </marker>
      </defs>
    </svg>
  );
}

// =============================================================================
// Emission Preview — color swatch + wavelength
// =============================================================================

function EmissionPreview({
  wavelengthNm,
  color,
}: {
  wavelengthNm?: number;
  color?: string;
}) {
  if (!color && wavelengthNm == null) return null;
  const swatch = emissionSwatchColor(color);
  const displayColor = color ? color.replace("_", " ") : "—";

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">Predicted Emission</div>
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <div
          style={{
            width: 72,
            height: 72,
            borderRadius: 4,
            background: swatch,
            boxShadow: "inset 0 0 0 1px var(--border)",
          }}
          title={`Emission color preview — ${displayColor}`}
        />
        <div>
          {wavelengthNm != null && (
            <div style={{ fontSize: 22, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text)" }}>
              {wavelengthNm.toFixed(0)} nm
            </div>
          )}
          <div style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 2, textTransform: "capitalize" }}>
            {displayColor}
          </div>
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// Motifs — click each chip to ask Claude about the motif
// =============================================================================

function MotifChips({
  motifs,
  sendMessage,
  smiles,
}: {
  motifs?: MotifHit[];
  sendMessage?: FrontierOrbitalsProps["sendMessage"];
  smiles?: string;
}) {
  if (!motifs || motifs.length === 0) {
    return (
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="panel-title">OLED Functional Motifs</div>
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          No OLED-relevant motifs detected (carbazole, triphenylamine, Ir complexes, etc.).
        </div>
      </div>
    );
  }

  const handleMotifClick = (motif: MotifHit) => {
    if (!sendMessage) return;
    const name = motif.motif || motif.name || "this motif";
    const smilesRef = smiles ? ` in \`${smiles}\`` : "";
    sendMessage({
      role: "user",
      content: [
        {
          type: "text",
          text:
            `I clicked the ${name} motif${smilesRef}. ` +
            `Why does this motif matter for OLED performance, ` +
            `does it shift emission wavelength or enable phosphorescence, ` +
            `and are there common structural variations I should consider?`,
        },
      ],
    });
  };

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">
        OLED Functional Motifs ({motifs.length})
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {motifs.map((motif, idx) => {
          const name = motif.motif || motif.name || `Motif ${idx + 1}`;
          const desc = motif.description;
          const count = motif.count;
          return (
            <div
              key={idx}
              onClick={sendMessage ? () => handleMotifClick(motif) : undefined}
              style={{
                padding: "8px 12px",
                background: "var(--bg-warm)",
                borderRadius: 2,
                borderLeft: "3px solid var(--accent)",
                cursor: sendMessage ? "pointer" : undefined,
              }}
              title={sendMessage ? `Click to ask Claude about ${name}` : desc}
            >
              <div style={{ fontSize: 12, fontWeight: 500, color: "var(--text)" }}>
                {name}
                {count && count > 1 ? <span style={{ color: "var(--text-muted)", marginLeft: 6 }}>×{count}</span> : null}
              </div>
              {desc && (
                <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2, maxWidth: 280 }}>
                  {desc}
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
// Classification Panel
// =============================================================================

function ClassificationPanel({
  classification,
  rationale,
}: {
  classification?: string;
  rationale?: string;
}) {
  const info = classificationInfo(classification);
  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">OLED Classification</div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: rationale ? 10 : 0 }}>
        <span
          style={{
            padding: "4px 10px",
            background: "var(--bg-warm)",
            borderLeft: `3px solid ${info.color}`,
            borderRadius: 2,
            fontSize: 12,
            fontWeight: 500,
            color: info.color,
          }}
        >
          {info.label}
        </span>
        {info.hint && (
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{info.hint}</span>
        )}
      </div>
      {rationale && (
        <div style={{ fontSize: 12, color: "var(--text)", lineHeight: 1.5 }}>
          {rationale}
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function FrontierOrbitalsViewer(props: FrontierOrbitalsProps) {
  const { toolInputs, toolInputsPartial, toolResult, sendMessage } = props;
  const data = useViewData<FrontierOrbitalsToolInput>(props);
  const isStreaming = !toolInputs && !toolResult;
  if (isStreaming) {
    return <LoadingShimmer />;
  }

  const smiles = data.smiles || toolInputs?.smiles || toolInputsPartial?.smiles;

  return (
    <div className="frontier-orbitals-viewer" style={{ width: "100%" }}>
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
            Frontier Orbital Analysis
          </div>
          {smiles && (
            <div
              style={{
                fontSize: 11,
                fontFamily: "var(--font-mono)",
                color: "var(--text-muted)",
                marginTop: 4,
                maxWidth: 420,
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
          {data.wall_time_seconds != null && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {data.wall_time_seconds.toFixed(1)} s
            </div>
          )}
        </div>
      </div>

      {/* Energy ladder */}
      {(data.homo_ev != null && data.lumo_ev != null) && (
        <div className="panel">
          <div className="panel-title">Frontier Orbital Energies</div>
          <EnergyLadder
            homo={data.homo_ev}
            lumo={data.lumo_ev}
            triplet={data.triplet_energy_ev}
            gap={data.gap_ev}
            stGap={data.singlet_triplet_gap_ev}
          />
        </div>
      )}

      {/* Emission preview */}
      <EmissionPreview
        wavelengthNm={data.emission_wavelength_nm}
        color={data.emission_color}
      />

      {/* Classification + rationale */}
      {(data.oled_classification || data.oled_rationale) && (
        <ClassificationPanel
          classification={data.oled_classification}
          rationale={data.oled_rationale}
        />
      )}

      {/* OLED motifs */}
      <MotifChips
        motifs={data.oled_motifs}
        sendMessage={sendMessage}
        smiles={smiles}
      />

      {/* Warnings */}
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
