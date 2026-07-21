/**
 * NovoMCP Bond Dissociation Energy (BDE) Viewer
 *
 * Renders predict_bde output (ALFABET): ranked bond list with
 * metabolic-risk flagging (BDE < 85 kcal/mol → CYP-labile), weakest-
 * bond callout, bond histogram, and click-through questions about
 * metabolic soft spots / deuteration / structural mitigation.
 *
 * Sync tool — no submission phase.
 */

import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";
import { BarChart, type Bar } from "./charts.tsx";

// =============================================================================
// Types
// =============================================================================

export interface Bond {
  atom_index?: number;
  bde_kcal_mol?: number;
  bond_type?: string;
}

export interface BdeData {
  smiles?: string;
  bonds?: Bond[];
  weakest_bond?: Bond | null;
  method?: string;
  interpretation?: string;
  bond_count?: number;
}

type BdeProps = ViewProps<BdeData>;

// =============================================================================
// Risk threshold (kcal/mol). ALFABET convention:
//   < 85  : metabolic soft spot (CYP-labile)
//   85-95 : moderate
//   > 95  : stable
// =============================================================================

const SOFT_SPOT_THRESHOLD = 85;
const STABLE_THRESHOLD = 95;

function riskColor(bde: number): string {
  if (bde < SOFT_SPOT_THRESHOLD) return "#C25D4E";
  if (bde < STABLE_THRESHOLD) return "#D4884E";
  return "var(--text-muted)";
}

function riskLabel(bde: number): string {
  if (bde < SOFT_SPOT_THRESHOLD) return "soft spot";
  if (bde < STABLE_THRESHOLD) return "moderate";
  return "stable";
}

// =============================================================================
// Bond table — clickable rows.
// =============================================================================

function BondTable({
  bonds,
  sendMessage,
  smiles,
  weakest,
}: {
  bonds: Bond[];
  sendMessage?: BdeProps["sendMessage"];
  smiles?: string;
  weakest?: Bond | null;
}) {
  // Sort by BDE ascending — weakest first.
  const sorted = [...bonds]
    .filter((b) => b.bde_kcal_mol != null && Number.isFinite(b.bde_kcal_mol))
    .sort((a, b) => (a.bde_kcal_mol! - b.bde_kcal_mol!));

  const weakestIdx = weakest?.atom_index;

  const askAboutBond = sendMessage
    ? (bond: Bond) => {
        const bde = bond.bde_kcal_mol;
        const type = bond.bond_type || "bond";
        const idx = bond.atom_index;
        const smilesRef = smiles ? ` for \`${smiles}\`` : "";
        const risk = bde != null ? riskLabel(bde) : "unknown";
        sendMessage({
          role: "user",
          content: [
            {
              type: "text",
              text:
                `I clicked the ${type} bond at atom index ${idx}${smilesRef} — ` +
                `BDE ${bde != null ? bde.toFixed(1) + " kcal/mol" : "unknown"} (${risk}). ` +
                `Is this likely a CYP-mediated metabolic soft spot, and what mitigation ` +
                `strategies apply here — deuteration (D/H exchange for KIE), fluorination, ` +
                `ring modification, or moving the metabolism to a non-cleared site?`,
            },
          ],
        });
      }
    : undefined;

  return (
    <div className="panel">
      <div
        className="panel-title"
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
      >
        <span>Bond Dissociation Energies ({sorted.length})</span>
        <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-muted)" }}>
          {askAboutBond ? "click any row to ask" : ""}
        </span>
      </div>
      <div style={{ overflowX: "auto", maxHeight: 380 }}>
        <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
          <thead style={{ position: "sticky", top: 0, background: "var(--bg-card)" }}>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Atom</th>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Type</th>
              <th style={{ textAlign: "right", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>BDE (kcal/mol)</th>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Risk</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((b, i) => {
              const bde = b.bde_kcal_mol!;
              const color = riskColor(bde);
              const isWeakest = weakestIdx != null && b.atom_index === weakestIdx;
              return (
                <tr
                  key={i}
                  onClick={askAboutBond ? () => askAboutBond(b) : undefined}
                  style={{
                    borderBottom: "1px solid var(--border)",
                    cursor: askAboutBond ? "pointer" : undefined,
                    background: isWeakest ? "rgba(194, 93, 78, 0.08)" : undefined,
                  }}
                  title={askAboutBond ? `Click to ask Claude about this bond` : undefined}
                >
                  <td style={{ padding: "6px 8px", fontFamily: "var(--font-mono)", color: "var(--text)", fontWeight: isWeakest ? 600 : 400 }}>
                    {b.atom_index ?? "—"}
                    {isWeakest && (
                      <span style={{ fontSize: 9, marginLeft: 4, color: "#C25D4E" }}>weakest</span>
                    )}
                  </td>
                  <td style={{ padding: "6px 8px", fontFamily: "var(--font-mono)", color: "var(--text)" }}>
                    {b.bond_type || "—"}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right", fontFamily: "var(--font-mono)", color, fontWeight: 600 }}>
                    {bde.toFixed(1)}
                  </td>
                  <td style={{ padding: "6px 8px" }}>
                    <span
                      style={{
                        padding: "2px 8px",
                        borderRadius: 10,
                        background: "var(--bg-warm)",
                        color,
                        fontSize: 10,
                        fontWeight: 500,
                        border: `1px solid ${color}`,
                      }}
                    >
                      {riskLabel(bde)}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.6 }}>
        ALFABET BDE predictions. Bonds below {SOFT_SPOT_THRESHOLD} kcal/mol are typical
        CYP-mediated metabolic soft spots — candidates for deuteration or fluorination to
        improve half-life. Bonds above {STABLE_THRESHOLD} kcal/mol are generally resistant
        to oxidative cleavage.
      </div>
    </div>
  );
}

// =============================================================================
// BDE histogram — distribution across all bonds
// =============================================================================

function BdeHistogram({ bonds }: { bonds: Bond[] }) {
  const values = bonds
    .map((b) => b.bde_kcal_mol)
    .filter((v): v is number => v != null && Number.isFinite(v));
  if (values.length < 3) return null;

  const bars: Bar[] = bonds
    .filter((b) => b.bde_kcal_mol != null && Number.isFinite(b.bde_kcal_mol))
    .sort((a, b) => (a.bde_kcal_mol! - b.bde_kcal_mol!))
    .slice(0, 40)
    .map((b) => ({
      value: b.bde_kcal_mol!,
      label: String(b.atom_index ?? ""),
      color: riskColor(b.bde_kcal_mol!),
      title: `Atom ${b.atom_index} ${b.bond_type || ""} — ${b.bde_kcal_mol!.toFixed(1)} kcal/mol`,
    }));

  return (
    <div className="panel" style={{ marginBottom: 16 }}>
      <div className="panel-title">Bond Strength Distribution</div>
      <BarChart bars={bars} height={160} unit=" kcal/mol" />
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.6 }}>
        Sorted weakest → strongest (first 40 bonds). Red bars are metabolic soft spots
        (&lt; {SOFT_SPOT_THRESHOLD} kcal/mol).
      </div>
    </div>
  );
}

// =============================================================================
// Main viewer
// =============================================================================

export default function PredictBdeViewer(props: BdeProps) {
  const { toolInputs, toolInputsPartial, toolResult, sendMessage } = props;
  const data = useViewData<BdeData>(props);
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
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>Predicting BDEs…</div>
      </div>
    );
  }

  const bonds = data.bonds ?? [];
  const weakest = data.weakest_bond;
  const smiles = data.smiles || toolInputs?.smiles || toolInputsPartial?.smiles;
  const softSpots = bonds.filter(
    (b) => b.bde_kcal_mol != null && b.bde_kcal_mol < SOFT_SPOT_THRESHOLD,
  ).length;
  const bondCount = data.bond_count ?? bonds.length;

  return (
    <div className="predict-bde-viewer" style={{ width: "100%" }}>
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
            Bond Dissociation Energies
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
        </div>
      </div>

      {/* Summary cards */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
        <div
          style={{
            padding: "10px 14px",
            background: "var(--bg-warm)",
            borderRadius: 2,
            borderLeft: "3px solid var(--text-muted)",
            minWidth: 110,
          }}
        >
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Bonds</div>
          <div style={{ fontSize: 20, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text)" }}>
            {bondCount}
          </div>
        </div>
        <div
          style={{
            padding: "10px 14px",
            background: "var(--bg-warm)",
            borderRadius: 2,
            borderLeft: `3px solid ${softSpots > 0 ? "#C25D4E" : "var(--text-muted)"}`,
            minWidth: 130,
          }}
        >
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Soft Spots</div>
          <div style={{ fontSize: 20, fontFamily: "var(--font-mono)", fontWeight: 600, color: softSpots > 0 ? "#C25D4E" : "var(--text)" }}>
            {softSpots}
          </div>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
            &lt; {SOFT_SPOT_THRESHOLD} kcal/mol
          </div>
        </div>
        {weakest && weakest.bde_kcal_mol != null && (
          <div
            style={{
              padding: "10px 14px",
              background: "var(--bg-warm)",
              borderRadius: 2,
              borderLeft: `3px solid ${riskColor(weakest.bde_kcal_mol)}`,
              minWidth: 140,
            }}
          >
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Weakest Bond</div>
            <div style={{ fontSize: 20, fontFamily: "var(--font-mono)", fontWeight: 600, color: riskColor(weakest.bde_kcal_mol) }}>
              {weakest.bde_kcal_mol.toFixed(1)}
            </div>
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
              {weakest.bond_type || "bond"} at atom {weakest.atom_index}
            </div>
          </div>
        )}
      </div>

      {/* Histogram */}
      <BdeHistogram bonds={bonds} />

      {/* Bond table */}
      <BondTable bonds={bonds} sendMessage={sendMessage} smiles={smiles} weakest={weakest} />

      {/* Interpretation */}
      {data.interpretation && (
        <div
          className="panel"
          style={{ marginTop: 16, borderLeft: `3px solid ${softSpots > 0 ? "#C25D4E" : "var(--accent)"}` }}
        >
          <div className="panel-title">Metabolic Stability Summary</div>
          <div style={{ fontSize: 12, color: "var(--text)", lineHeight: 1.6 }}>
            {data.interpretation}
          </div>
        </div>
      )}
    </div>
  );
}
