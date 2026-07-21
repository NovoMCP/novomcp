/**
 * NovoMCP Electrolyte Redox Potential Viewer
 *
 * Renders the output of predict_redox_potential: oxidation and
 * reduction potentials on a voltage scale, electrochemical window,
 * IP/EA energies, and per-application stability windows (Li-ion,
 * aqueous, organic, etc.) — each clickable to ask Claude.
 */

import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types
// =============================================================================

interface StabilityWindow {
  window?: string;
  stable?: boolean;
  oxidation_v_in_window?: number;
  reduction_v_in_window?: number;
  note?: string;
}

interface RedoxPotentialToolInput {
  smiles?: string;
  solvent?: string;
  reference_electrode?: string;

  ip_adiabatic_ev?: number;
  ip_vertical_ev?: number;
  ea_adiabatic_ev?: number;
  ea_vertical_ev?: number;

  oxidation_potential_v?: number;
  reduction_potential_v?: number;
  electrochemical_window_v?: number;

  solvent_class?: string;
  stability_windows?: StabilityWindow[];

  method?: string;
  wall_time_seconds?: number;
  warnings?: string[];
}

type RedoxPotentialProps = ViewProps<RedoxPotentialToolInput>;

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
        Running redox thermodynamic cycle (xTB × 5)…
      </div>
    </div>
  );
}

// =============================================================================
// Voltage Scale — horizontal number line showing oxidation + reduction
// potentials with the electrochemical window shaded.
// =============================================================================

function VoltageScale({
  oxidationV,
  reductionV,
  referenceElectrode,
}: {
  oxidationV?: number;
  reductionV?: number;
  referenceElectrode?: string;
}) {
  if (oxidationV == null && reductionV == null) return null;

  // Fit the number line with some padding around min and max.
  const values = [oxidationV, reductionV].filter((v): v is number => v != null);
  const vMin = Math.min(0, ...values) - 1.0;
  const vMax = Math.max(0, ...values) + 1.0;
  const vSpan = vMax - vMin;

  const width = 600;
  const height = 140;
  const leftPad = 40;
  const rightPad = 40;
  const baseY = 70;
  const plotW = width - leftPad - rightPad;
  const toX = (v: number) => leftPad + ((v - vMin) / vSpan) * plotW;

  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} style={{ display: "block" }}>
      {/* Baseline */}
      <line
        x1={leftPad}
        x2={width - rightPad}
        y1={baseY}
        y2={baseY}
        stroke="var(--text-muted)"
        strokeOpacity={0.4}
      />

      {/* ECW shaded region between reduction and oxidation */}
      {reductionV != null && oxidationV != null && (
        <rect
          x={toX(reductionV)}
          y={baseY - 14}
          width={Math.max(0, toX(oxidationV) - toX(reductionV))}
          height={28}
          fill="var(--success)"
          fillOpacity={0.12}
          stroke="var(--success)"
          strokeOpacity={0.3}
          strokeDasharray="2 2"
        />
      )}

      {/* 0 V tick */}
      {vMin < 0 && vMax > 0 && (
        <g>
          <line
            x1={toX(0)}
            x2={toX(0)}
            y1={baseY - 8}
            y2={baseY + 8}
            stroke="var(--text-muted)"
          />
          <text
            x={toX(0)}
            y={baseY + 22}
            fontSize={9}
            textAnchor="middle"
            fill="var(--text-muted)"
            style={{ fontFamily: "var(--font-mono)" }}
          >
            0
          </text>
        </g>
      )}

      {/* Ticks at integer volts */}
      {Array.from(
        { length: Math.floor(vMax) - Math.ceil(vMin) + 1 },
        (_, i) => Math.ceil(vMin) + i,
      )
        .filter((v) => v !== 0)
        .map((v) => (
          <g key={v}>
            <line
              x1={toX(v)}
              x2={toX(v)}
              y1={baseY - 4}
              y2={baseY + 4}
              stroke="var(--text-muted)"
              strokeOpacity={0.3}
            />
            <text
              x={toX(v)}
              y={baseY + 22}
              fontSize={9}
              textAnchor="middle"
              fill="var(--text-muted)"
              style={{ fontFamily: "var(--font-mono)" }}
            >
              {v}
            </text>
          </g>
        ))}

      {/* Reduction marker */}
      {reductionV != null && (
        <g>
          <line
            x1={toX(reductionV)}
            x2={toX(reductionV)}
            y1={baseY - 22}
            y2={baseY + 22}
            stroke="var(--warning)"
            strokeWidth={3}
          />
          <text
            x={toX(reductionV)}
            y={baseY - 30}
            fontSize={11}
            textAnchor="middle"
            fill="var(--warning)"
            style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}
          >
            {reductionV.toFixed(2)} V
          </text>
          <text
            x={toX(reductionV)}
            y={baseY - 44}
            fontSize={9}
            textAnchor="middle"
            fill="var(--text-muted)"
          >
            Reduction
          </text>
        </g>
      )}

      {/* Oxidation marker */}
      {oxidationV != null && (
        <g>
          <line
            x1={toX(oxidationV)}
            x2={toX(oxidationV)}
            y1={baseY - 22}
            y2={baseY + 22}
            stroke="var(--accent)"
            strokeWidth={3}
          />
          <text
            x={toX(oxidationV)}
            y={baseY - 30}
            fontSize={11}
            textAnchor="middle"
            fill="var(--accent)"
            style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}
          >
            {oxidationV.toFixed(2)} V
          </text>
          <text
            x={toX(oxidationV)}
            y={baseY - 44}
            fontSize={9}
            textAnchor="middle"
            fill="var(--text-muted)"
          >
            Oxidation
          </text>
        </g>
      )}

      {/* Reference label */}
      {referenceElectrode && (
        <text
          x={width - rightPad}
          y={height - 8}
          fontSize={10}
          textAnchor="end"
          fill="var(--text-muted)"
        >
          vs {referenceElectrode}
        </text>
      )}
    </svg>
  );
}

// =============================================================================
// ECW + per-row energies summary
// =============================================================================

function RedoxSummary({ data }: { data: RedoxPotentialToolInput }) {
  return (
    <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
      {data.electrochemical_window_v != null && (
        <div
          style={{
            padding: "12px 16px",
            background: "var(--bg-warm)",
            borderRadius: 2,
            borderLeft: "3px solid var(--success)",
            minWidth: 140,
          }}
          title="Electrochemical window = Oxidation − Reduction potential. Wider windows mean the electrolyte survives over more voltage range."
        >
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>
            Electrochemical Window
          </div>
          <div
            style={{
              fontSize: 20,
              fontFamily: "var(--font-mono)",
              fontWeight: 600,
              color: "var(--success)",
            }}
          >
            {data.electrochemical_window_v.toFixed(2)}
            <span style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 400, marginLeft: 4 }}>
              V
            </span>
          </div>
        </div>
      )}

      {(data.ip_adiabatic_ev != null || data.ip_vertical_ev != null) && (
        <div
          style={{
            padding: "12px 16px",
            background: "var(--bg-warm)",
            borderRadius: 2,
            borderLeft: "3px solid var(--accent)",
            minWidth: 140,
          }}
          title="Ionization potential — energy to remove an electron (oxidation)"
        >
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>IP (oxidation)</div>
          {data.ip_adiabatic_ev != null && (
            <div style={{ fontSize: 13, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--accent)" }}>
              {data.ip_adiabatic_ev.toFixed(2)}
              <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 4 }}>eV adiabatic</span>
            </div>
          )}
          {data.ip_vertical_ev != null && (
            <div style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)", marginTop: 2 }}>
              {data.ip_vertical_ev.toFixed(2)} eV vertical
            </div>
          )}
        </div>
      )}

      {(data.ea_adiabatic_ev != null || data.ea_vertical_ev != null) && (
        <div
          style={{
            padding: "12px 16px",
            background: "var(--bg-warm)",
            borderRadius: 2,
            borderLeft: "3px solid var(--warning)",
            minWidth: 140,
          }}
          title="Electron affinity — energy released on electron capture (reduction)"
        >
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>EA (reduction)</div>
          {data.ea_adiabatic_ev != null && (
            <div style={{ fontSize: 13, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--warning)" }}>
              {data.ea_adiabatic_ev.toFixed(2)}
              <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 4 }}>eV adiabatic</span>
            </div>
          )}
          {data.ea_vertical_ev != null && (
            <div style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)", marginTop: 2 }}>
              {data.ea_vertical_ev.toFixed(2)} eV vertical
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Stability Windows — click any to ask about application fit
// =============================================================================

function StabilityWindowsPanel({
  windows,
  sendMessage,
  smiles,
  solvent,
  referenceElectrode,
}: {
  windows?: StabilityWindow[];
  sendMessage?: RedoxPotentialProps["sendMessage"];
  smiles?: string;
  solvent?: string;
  referenceElectrode?: string;
}) {
  if (!windows || windows.length === 0) return null;

  const askAboutWindow = sendMessage
    ? (w: StabilityWindow) => {
        const windowName = w.window || "this window";
        const smilesRef = smiles ? ` for \`${smiles}\`` : "";
        const solventRef = solvent ? ` in ${solvent}` : "";
        const refNote = referenceElectrode ? ` (vs ${referenceElectrode})` : "";
        const stability = w.stable ? "stable" : "NOT stable";
        sendMessage({
          role: "user",
          content: [
            {
              type: "text",
              text:
                `I clicked the ${windowName} stability window${smilesRef}${solventRef}. ` +
                `The model flagged this as ${stability}${refNote}. ` +
                `Is this molecule suitable as an electrolyte / additive for the ${windowName} application, ` +
                `what voltage margin does it have against the limiting redox event, ` +
                `and if not stable, what structural modifications would improve the window?`,
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
        <span>Application Stability Windows</span>
        <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-muted)" }}>
          {sendMessage ? "click any window to ask" : ""}
        </span>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {windows.map((w, idx) => {
          const color = w.stable ? "var(--success)" : "var(--danger)";
          const label = w.window || `Window ${idx + 1}`;
          return (
            <div
              key={idx}
              onClick={askAboutWindow ? () => askAboutWindow(w) : undefined}
              style={{
                padding: "10px 14px",
                background: "var(--bg-warm)",
                borderRadius: 2,
                borderLeft: `3px solid ${color}`,
                cursor: askAboutWindow ? "pointer" : undefined,
                minWidth: 180,
              }}
              title={askAboutWindow ? `Click to ask Claude about ${label}` : w.note}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: 12, fontWeight: 500, color: "var(--text)" }}>{label}</span>
                <span style={{ fontSize: 10, color, fontWeight: 500 }}>
                  {w.stable ? "stable" : "unstable"}
                </span>
              </div>
              {(w.oxidation_v_in_window != null || w.reduction_v_in_window != null) && (
                <div style={{ fontSize: 9, color: "var(--text-muted)", marginTop: 4, fontFamily: "var(--font-mono)" }}>
                  {w.oxidation_v_in_window != null && `ox ${w.oxidation_v_in_window.toFixed(2)}V `}
                  {w.reduction_v_in_window != null && `red ${w.reduction_v_in_window.toFixed(2)}V`}
                </div>
              )}
              {w.note && !askAboutWindow && (
                <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4, maxWidth: 240 }}>
                  {w.note}
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
// Main Component
// =============================================================================

export default function RedoxPotentialViewer(props: RedoxPotentialProps) {
  const { toolInputs, toolInputsPartial, toolResult, sendMessage } = props;
  const data = useViewData<RedoxPotentialToolInput>(props);
  const isStreaming = !toolInputs && !toolResult;
  if (isStreaming) {
    return <LoadingShimmer />;
  }

  const smiles = data.smiles || toolInputs?.smiles || toolInputsPartial?.smiles;
  const solvent = data.solvent || toolInputs?.solvent;
  const reference = data.reference_electrode || toolInputs?.reference_electrode || "SHE";

  return (
    <div className="redox-potential-viewer" style={{ width: "100%" }}>
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
            Electrolyte Redox Potential
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
              {solvent && <span style={{ marginLeft: 12 }}>· {solvent}</span>}
              {data.solvent_class && <span style={{ marginLeft: 6 }}>({data.solvent_class})</span>}
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

      <div className="panel">
        <div className="panel-title">Voltage Scale (vs {reference})</div>
        <VoltageScale
          oxidationV={data.oxidation_potential_v}
          reductionV={data.reduction_potential_v}
          referenceElectrode={reference}
        />
      </div>

      <div className="panel" style={{ marginTop: 16 }}>
        <div className="panel-title">Redox Summary</div>
        <RedoxSummary data={data} />
      </div>

      <StabilityWindowsPanel
        windows={data.stability_windows}
        sendMessage={sendMessage}
        smiles={smiles}
        solvent={solvent}
        referenceElectrode={reference}
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
