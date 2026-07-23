/**
 * NovoMCP pKa Prediction Viewer
 *
 * Renders predict_pka output: a pH scale with pKa markers, an
 * ionizable-groups table with per-group pKa values, and a charge-
 * state verdict at physiological pH (7.4). Click any group to ask
 * Claude about the charge-state implications for permeability,
 * binding, or formulation.
 *
 * Sync tool — no submission phase.
 */

import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types
// =============================================================================

export interface PkaData {
  smiles?: string;
  pka_values?: number[];
  ionizable_groups?: string[];
  method?: string;
  confidence?: number | null;
  interpretation?: string;
}

type PkaProps = ViewProps<PkaData>;

// =============================================================================
// pH scale — shows pKa markers against physiological pH range.
// =============================================================================

function PhScale({
  pkaValues,
  groups,
  onClickPka,
}: {
  pkaValues: number[];
  groups: string[];
  onClickPka?: (idx: number) => void;
}) {
  const width = 520;
  const height = 70;
  const padL = 32;
  const padR = 16;
  const padT = 20;
  const track = height - padT - 24;
  const usableW = width - padL - padR;

  // pH range 0..14 — 7.4 is physiological
  const xFor = (pH: number) => padL + (pH / 14) * usableW;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: "auto", display: "block" }}>
      {/* gradient track */}
      <defs>
        <linearGradient id="phGradient" x1="0" x2="1">
          <stop offset="0" stopColor="#C25D4E" />
          <stop offset="0.5" stopColor="var(--text-muted)" />
          <stop offset="1" stopColor="#4E9BC2" />
        </linearGradient>
      </defs>
      <rect x={padL} y={padT} width={usableW} height={8} fill="url(#phGradient)" rx={4} />

      {/* physiological pH band */}
      <rect
        x={xFor(7.0)}
        y={padT - 2}
        width={xFor(7.4) - xFor(7.0)}
        height={12}
        fill="var(--accent)"
        opacity={0.25}
      />
      <line
        x1={xFor(7.4)}
        x2={xFor(7.4)}
        y1={padT - 6}
        y2={padT + 14}
        stroke="var(--accent)"
        strokeWidth={1.5}
        strokeDasharray="2 2"
      />
      <text
        x={xFor(7.4)}
        y={padT + track + 18}
        textAnchor="middle"
        fontSize={9}
        fill="var(--accent)"
        fontWeight={600}
      >
        pH 7.4
      </text>

      {/* axis labels */}
      {[0, 7, 14].map((p) => (
        <text
          key={p}
          x={xFor(p)}
          y={padT + track + 4}
          textAnchor="middle"
          fontSize={9}
          fill="var(--text-muted)"
        >
          {p}
        </text>
      ))}

      {/* pKa markers */}
      {pkaValues.map((pka, i) => {
        if (!Number.isFinite(pka)) return null;
        const x = xFor(Math.max(0, Math.min(14, pka)));
        return (
          <g
            key={i}
            style={{ cursor: onClickPka ? "pointer" : undefined }}
            onClick={onClickPka ? () => onClickPka(i) : undefined}
          >
            <line x1={x} x2={x} y1={padT - 4} y2={padT + 14} stroke="var(--text)" strokeWidth={1.5} />
            <circle cx={x} cy={padT + 4} r={5} fill="var(--bg-card)" stroke="var(--text)" strokeWidth={1.5} />
            <text
              x={x}
              y={padT - 8}
              textAnchor="middle"
              fontSize={10}
              fontWeight={600}
              fontFamily="var(--font-mono)"
              fill="var(--text)"
            >
              {pka.toFixed(2)}
            </text>
            <title>{groups[i] || "ionizable group"} — pKa {pka.toFixed(2)}</title>
          </g>
        );
      })}
    </svg>
  );
}

// =============================================================================
// Ionizable groups table
// =============================================================================

function groupColor(group: string): string {
  if (group.includes("acid") || group === "phenol" || group === "thiol" || group === "tetrazole" || group === "sulfonamide") {
    return "#C25D4E"; // acidic
  }
  if (group.includes("amine") || group === "guanidine" || group === "imidazole" || group === "pyridine") {
    return "#4E9BC2"; // basic
  }
  return "var(--text-muted)";
}

function chargeAtPH(pka: number, group: string, ph: number = 7.4): string {
  const isAcid =
    group.includes("acid") || group === "phenol" || group === "thiol" || group === "tetrazole" || group === "sulfonamide";
  const isBase =
    group.includes("amine") || group === "guanidine" || group === "imidazole" || group === "pyridine";
  if (isAcid) {
    if (ph > pka + 2) return "deprotonated (−)";
    if (ph < pka - 2) return "neutral";
    return "partial (−)";
  }
  if (isBase) {
    if (ph < pka - 2) return "protonated (+)";
    if (ph > pka + 2) return "neutral";
    return "partial (+)";
  }
  return "—";
}

function IonizableTable({
  pkaValues,
  groups,
  sendMessage,
  smiles,
}: {
  pkaValues: number[];
  groups: string[];
  sendMessage?: PkaProps["sendMessage"];
  smiles?: string;
}) {
  const noneDetected = groups.length === 1 && groups[0] === "none_detected";
  if (noneDetected || groups.length === 0) {
    return (
      <div className="panel">
        <div className="panel-title">Ionizable Groups</div>
        <div
          style={{
            padding: 16,
            background: "var(--bg-warm)",
            borderRadius: 2,
            fontSize: 12,
            color: "var(--text-muted)",
            lineHeight: 1.5,
          }}
        >
          No ionizable groups detected in the physiological pH window. The molecule is
          expected to remain neutral at pH 7.4 — good for passive membrane permeability,
          but charge-based salt-bridge interactions with targets are unavailable.
        </div>
      </div>
    );
  }

  const askAboutGroup = sendMessage
    ? (i: number) => {
        const group = groups[i] || "this group";
        const pka = pkaValues[i];
        const state = chargeAtPH(pka, group, 7.4);
        const smilesRef = smiles ? ` for \`${smiles}\`` : "";
        sendMessage({
          role: "user",
          content: [
            {
              type: "text",
              text:
                `I clicked the ${group} pKa ${pka.toFixed(2)}${smilesRef}. At physiological pH 7.4 this group is ` +
                `${state}. How does this charge state affect passive membrane permeability, target binding ` +
                `(H-bond donors/acceptors, salt bridges), and do I need to consider alternative salt forms or ` +
                `prodrug strategies?`,
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
        <span>Ionizable Groups ({groups.length})</span>
        <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-muted)" }}>
          {askAboutGroup ? "click any row to ask" : ""}
        </span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Group</th>
              <th style={{ textAlign: "right", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>pKa</th>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>At pH 7.4</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g, i) => {
              const pka = pkaValues[i];
              const state = chargeAtPH(pka, g, 7.4);
              const color = groupColor(g);
              return (
                <tr
                  key={i}
                  onClick={askAboutGroup ? () => askAboutGroup(i) : undefined}
                  style={{
                    borderBottom: "1px solid var(--border)",
                    cursor: askAboutGroup ? "pointer" : undefined,
                  }}
                  title={askAboutGroup ? `Click to ask Claude about ${g}` : undefined}
                >
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
                      {g.replace(/_/g, " ")}
                    </span>
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text)", fontWeight: 600 }}>
                    {Number.isFinite(pka) ? pka.toFixed(2) : "—"}
                  </td>
                  <td style={{ padding: "6px 8px", color, fontSize: 11 }}>
                    {state}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.6 }}>
        Charge state uses the standard Henderson-Hasselbalch convention: &plusmn;2 pH units
        around pKa is the titration window. Groups &gt; 2 units away from 7.4 are essentially
        fully protonated or deprotonated — those drive the dominant charge at physiological pH.
      </div>
    </div>
  );
}

// =============================================================================
// Main viewer
// =============================================================================

export default function PredictPkaViewer(props: PkaProps) {
  const { toolInputs, toolInputsPartial, toolResult, sendMessage } = props;
  const data = useViewData<PkaData>(props);
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
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>Predicting pKa…</div>
      </div>
    );
  }

  const pkaValues = data.pka_values ?? [];
  const groups = data.ionizable_groups ?? [];
  const smiles = data.smiles || toolInputs?.smiles || toolInputsPartial?.smiles;

  // Determine overall charge at pH 7.4
  let overallCharge = 0;
  groups.forEach((g, i) => {
    const pka = pkaValues[i];
    if (!Number.isFinite(pka)) return;
    const state = chargeAtPH(pka, g, 7.4);
    if (state.includes("(+)")) overallCharge += state.startsWith("partial") ? 0.5 : 1;
    else if (state.includes("(−)")) overallCharge -= state.startsWith("partial") ? 0.5 : 1;
  });
  const chargeLabel =
    overallCharge > 0 ? `+${overallCharge}` :
    overallCharge < 0 ? `${overallCharge}` :
    "0 (neutral)";
  const chargeColor =
    overallCharge > 0 ? "#4E9BC2" :
    overallCharge < 0 ? "#C25D4E" :
    "var(--text-muted)";

  return (
    <div className="predict-pka-viewer" style={{ width: "100%" }}>
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
            pKa Prediction
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
          {data.confidence != null && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              confidence {data.confidence.toFixed(2)}
            </div>
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
            borderLeft: `3px solid ${chargeColor}`,
            minWidth: 140,
          }}
        >
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Net charge at pH 7.4</div>
          <div style={{ fontSize: 20, fontFamily: "var(--font-mono)", fontWeight: 600, color: chargeColor }}>
            {chargeLabel}
          </div>
        </div>
        <div
          style={{
            padding: "10px 14px",
            background: "var(--bg-warm)",
            borderRadius: 2,
            borderLeft: "3px solid var(--text-muted)",
            minWidth: 110,
          }}
        >
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Ionizable sites</div>
          <div style={{ fontSize: 20, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text)" }}>
            {groups.filter((g) => g !== "none_detected").length}
          </div>
        </div>
      </div>

      {/* pH scale */}
      {pkaValues.length > 0 && pkaValues.some(Number.isFinite) && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <div className="panel-title">pKa on pH Scale</div>
          <PhScale pkaValues={pkaValues} groups={groups} />
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.6 }}>
            Red end of the gradient is acidic, blue end is basic. The dashed line marks
            physiological pH (7.4). pKa values within &plusmn;2 units of 7.4 are the ones
            that matter for charge state in the bloodstream.
          </div>
        </div>
      )}

      {/* Ionizable groups table */}
      <IonizableTable pkaValues={pkaValues} groups={groups} sendMessage={sendMessage} smiles={smiles} />

      {/* Interpretation */}
      {data.interpretation && (
        <div
          className="panel"
          style={{ marginTop: 16, borderLeft: "3px solid var(--accent)" }}
        >
          <div className="panel-title">Interpretation</div>
          <div style={{ fontSize: 12, color: "var(--text)", lineHeight: 1.6 }}>
            {data.interpretation}
          </div>
        </div>
      )}
    </div>
  );
}
