/**
 * NovoMCP Reaction Thermodynamics Viewer
 *
 * Renders predict_reaction_thermodynamics output: reaction equation
 * (reactants → products), spontaneity verdict, ΔG/ΔH/TΔS breakdown,
 * K_eq, per-species thermochem table. Click any species → ask Claude
 * about that species' contribution.
 */

import type { ViewProps } from "./create-app.tsx";
import { BarChart, type Bar } from "./charts.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types
// =============================================================================

interface SpeciesData {
  role?: "reactant" | "product" | string;
  smiles?: string;
  energy_kcal?: number;
  zpe_kcal?: number;
  gibbs_correction_kcal?: number;
  has_imaginary?: boolean;
}

interface ReactionThermoToolInput {
  reactant_smiles?: string[];
  product_smiles?: string[];
  solvent?: string;
  temperature?: number;

  delta_e_kcal?: number;
  delta_h_kcal?: number;
  delta_g_kcal?: number;
  t_delta_s_kcal?: number;

  k_eq?: number;
  spontaneous?: boolean;
  confidence?: string;
  confidence_note?: string;
  has_imaginary_frequencies?: boolean;

  species_data?: SpeciesData[];

  method?: string;
  wall_time_seconds?: number;
  temperature_k?: number;
  warnings?: string[];
}

type ReactionThermoProps = ViewProps<ReactionThermoToolInput>;

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
        Computing reaction thermodynamics (Hessian per species)…
      </div>
    </div>
  );
}

// =============================================================================
// Reaction Equation — "R1 + R2 → P1 + P2" with truncated SMILES
// =============================================================================

function ReactionEquation({
  reactants,
  products,
}: {
  reactants?: string[];
  products?: string[];
}) {
  if ((!reactants || reactants.length === 0) && (!products || products.length === 0)) {
    return null;
  }

  const truncate = (s: string, max = 20) =>
    s.length > max ? s.slice(0, max) + "…" : s;

  const renderSide = (mols: string[] | undefined) =>
    (mols || []).map((s, idx) => (
      <span key={idx}>
        {idx > 0 && <span style={{ color: "var(--text-muted)", margin: "0 6px" }}>+</span>}
        <code
          title={s}
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            padding: "2px 6px",
            background: "var(--bg-warm)",
            borderRadius: 2,
          }}
        >
          {truncate(s)}
        </code>
      </span>
    ));

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        flexWrap: "wrap",
        padding: "8px 0",
      }}
    >
      {renderSide(reactants)}
      <span style={{ fontSize: 16, color: "var(--accent)", margin: "0 8px", fontWeight: 500 }}>→</span>
      {renderSide(products)}
    </div>
  );
}

// =============================================================================
// Spontaneity Verdict — large badge + K_eq
// =============================================================================

function SpontaneityVerdict({
  spontaneous,
  deltaG,
  kEq,
  confidence,
}: {
  spontaneous?: boolean;
  deltaG?: number;
  kEq?: number;
  confidence?: string;
}) {
  if (spontaneous === undefined && deltaG == null) return null;

  const color = spontaneous ? "var(--success)" : "var(--warning)";
  const label = spontaneous ? "Spontaneous" : "Non-spontaneous";
  const confidenceColor =
    confidence === "high" ? "var(--success)" : confidence === "low" ? "var(--warning)" : "var(--text-muted)";

  return (
    <div className="panel">
      <div className="panel-title">Verdict</div>
      <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
        <div>
          <span
            style={{
              padding: "6px 14px",
              background: "var(--bg-warm)",
              borderLeft: `3px solid ${color}`,
              borderRadius: 2,
              fontSize: 14,
              fontWeight: 600,
              color,
            }}
          >
            {label}
          </span>
          {deltaG != null && (
            <span
              style={{
                marginLeft: 12,
                fontSize: 12,
                color: "var(--text-muted)",
                fontFamily: "var(--font-mono)",
              }}
            >
              ΔG = {deltaG >= 0 ? "+" : ""}{deltaG.toFixed(2)} kcal/mol
            </span>
          )}
        </div>

        {kEq != null && (
          <div
            style={{
              padding: "6px 12px",
              background: "var(--bg-warm)",
              borderRadius: 2,
              fontSize: 11,
              color: "var(--text)",
              fontFamily: "var(--font-mono)",
            }}
            title="Thermodynamic equilibrium constant at the reported temperature"
          >
            K<sub>eq</sub> ={" "}
            <span style={{ fontWeight: 600 }}>
              {Math.abs(kEq) > 1e6 || (Math.abs(kEq) > 0 && Math.abs(kEq) < 1e-3)
                ? kEq.toExponential(2)
                : kEq.toFixed(3)}
            </span>
          </div>
        )}

        {confidence && (
          <span
            style={{
              fontSize: 10,
              padding: "3px 8px",
              borderRadius: 2,
              background: "var(--bg-warm)",
              color: confidenceColor,
              textTransform: "uppercase",
              letterSpacing: "0.04em",
              fontWeight: 500,
            }}
            title="Model confidence in the thermodynamic prediction based on system class"
          >
            {confidence} confidence
          </span>
        )}
      </div>
    </div>
  );
}

// =============================================================================
// Delta decomposition — ΔG, ΔH, TΔS, ΔE as a bar chart for visual intuition
// =============================================================================

function DeltaDecomposition({ data }: { data: ReactionThermoToolInput }) {
  const bars: Bar[] = [];
  if (data.delta_e_kcal != null) {
    bars.push({
      value: data.delta_e_kcal,
      label: "ΔE",
      color: data.delta_e_kcal < 0 ? "var(--success)" : "var(--warning)",
      title: `ΔE electronic: ${data.delta_e_kcal.toFixed(2)} kcal/mol`,
    });
  }
  if (data.delta_h_kcal != null) {
    bars.push({
      value: data.delta_h_kcal,
      label: "ΔH",
      color: data.delta_h_kcal < 0 ? "var(--success)" : "var(--warning)",
      title: `ΔH enthalpy: ${data.delta_h_kcal.toFixed(2)} kcal/mol`,
    });
  }
  if (data.t_delta_s_kcal != null) {
    bars.push({
      value: data.t_delta_s_kcal,
      label: "TΔS",
      color: data.t_delta_s_kcal > 0 ? "var(--success)" : "var(--warning)",
      title: `TΔS entropy: ${data.t_delta_s_kcal.toFixed(2)} kcal/mol`,
    });
  }
  if (data.delta_g_kcal != null) {
    bars.push({
      value: data.delta_g_kcal,
      label: "ΔG",
      color: data.delta_g_kcal < 0 ? "var(--success)" : "var(--warning)",
      title: `ΔG Gibbs: ${data.delta_g_kcal.toFixed(2)} kcal/mol`,
    });
  }

  if (bars.length === 0) return null;

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">
        Thermodynamic Decomposition
        {data.temperature_k != null && (
          <span style={{ fontSize: 11, fontWeight: 400, color: "var(--text-muted)", marginLeft: 8 }}>
            @ {data.temperature_k.toFixed(1)} K
          </span>
        )}
      </div>
      <BarChart bars={bars} height={200} unit="kcal/mol" />
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.6 }}>
        ΔG = ΔH − TΔS. Green bars favor products (exothermic / entropy-gaining /
        spontaneous); warning bars favor reactants. ΔG is the bottom-line spontaneity call.
      </div>
    </div>
  );
}

// =============================================================================
// Species Table — per-reactant / per-product energies, click to ask Claude
// =============================================================================

function SpeciesTable({
  species,
  sendMessage,
}: {
  species?: SpeciesData[];
  sendMessage?: ReactionThermoProps["sendMessage"];
}) {
  if (!species || species.length === 0) return null;

  const askAboutSpecies = sendMessage
    ? (sp: SpeciesData) => {
        const role = sp.role || "species";
        const smilesRef = sp.smiles ? `\`${sp.smiles}\`` : "this species";
        sendMessage({
          role: "user",
          content: [
            {
              type: "text",
              text:
                `I clicked the ${role} ${smilesRef} in the thermodynamics breakdown ` +
                `(E=${sp.energy_kcal?.toFixed(2) ?? "?"} kcal/mol, ZPE=${sp.zpe_kcal?.toFixed(2) ?? "?"} kcal/mol). ` +
                `Why does this species contribute the way it does to ΔG — is its entropy, vibrational mode spectrum, ` +
                `or electronic energy the dominant factor, and would a reasonable structural change shift the equilibrium?`,
            },
          ],
        });
      }
    : undefined;

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div
        className="panel-title"
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
      >
        <span>Per-Species Thermochemistry ({species.length})</span>
        <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-muted)" }}>
          {askAboutSpecies ? "click any species to ask" : ""}
        </span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Role</th>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>SMILES</th>
              <th style={{ textAlign: "right", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>E (kcal/mol)</th>
              <th style={{ textAlign: "right", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>ZPE (kcal/mol)</th>
              <th style={{ textAlign: "right", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>G correction</th>
              <th style={{ textAlign: "center", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Flag</th>
            </tr>
          </thead>
          <tbody>
            {species.map((sp, i) => {
              const roleColor = sp.role === "reactant" ? "var(--accent)" : sp.role === "product" ? "var(--warning)" : "var(--text-muted)";
              return (
                <tr
                  key={i}
                  onClick={askAboutSpecies ? () => askAboutSpecies(sp) : undefined}
                  style={{
                    borderBottom: "1px solid var(--border)",
                    cursor: askAboutSpecies ? "pointer" : undefined,
                  }}
                  title={askAboutSpecies ? "Click to ask Claude about this species" : sp.smiles}
                >
                  <td style={{ padding: "6px 8px", textTransform: "capitalize", color: roleColor, fontWeight: 500 }}>
                    {sp.role || "—"}
                  </td>
                  <td
                    style={{
                      padding: "6px 8px",
                      fontFamily: "var(--font-mono)",
                      color: "var(--text)",
                      maxWidth: 260,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={sp.smiles}
                  >
                    {sp.smiles || "—"}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right", fontFamily: "var(--font-mono)" }}>
                    {sp.energy_kcal?.toFixed(2) ?? "—"}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                    {sp.zpe_kcal?.toFixed(2) ?? "—"}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                    {sp.gibbs_correction_kcal?.toFixed(2) ?? "—"}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "center" }}>
                    {sp.has_imaginary && (
                      <span
                        style={{
                          fontSize: 9,
                          padding: "2px 6px",
                          background: "var(--warning-bg)",
                          color: "var(--warning)",
                          borderRadius: 2,
                        }}
                        title="This species has one or more imaginary frequencies — geometry may not be at a true minimum."
                      >
                        imag
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function ReactionThermoViewer(props: ReactionThermoProps) {
  const { toolInputs, toolInputsPartial, toolResult, sendMessage } = props;
  const data = useViewData<ReactionThermoToolInput>(props);
  const isStreaming = !toolInputs && !toolResult;
  if (isStreaming) {
    return <LoadingShimmer />;
  }

  const reactants = data.reactant_smiles || toolInputs?.reactant_smiles;
  const products = data.product_smiles || toolInputs?.product_smiles;

  return (
    <div className="reaction-thermo-viewer" style={{ width: "100%" }}>
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
            Reaction Thermodynamics
          </div>
          <ReactionEquation reactants={reactants} products={products} />
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
          {data.has_imaginary_frequencies && (
            <div
              style={{
                fontSize: 10,
                padding: "3px 8px",
                marginTop: 6,
                background: "var(--warning-bg)",
                color: "var(--warning)",
                borderRadius: 2,
                display: "inline-block",
              }}
              title="One or more species has imaginary frequencies — ΔG may be unreliable until geometries are re-optimized."
            >
              imag freqs present
            </div>
          )}
        </div>
      </div>

      <SpontaneityVerdict
        spontaneous={data.spontaneous}
        deltaG={data.delta_g_kcal}
        kEq={data.k_eq}
        confidence={data.confidence}
      />

      <DeltaDecomposition data={data} />

      {data.confidence_note && (
        <div
          className="panel"
          style={{ marginTop: 16, borderLeft: "3px solid var(--text-muted)" }}
        >
          <div className="panel-title">Confidence Note</div>
          <div style={{ fontSize: 12, color: "var(--text)", lineHeight: 1.5 }}>
            {data.confidence_note}
          </div>
        </div>
      )}

      <SpeciesTable species={data.species_data} sendMessage={sendMessage} />

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
