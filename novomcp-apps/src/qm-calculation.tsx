/**
 * NovoMCP QM Calculation Viewer
 *
 * Renders run_qm_calculation output: total energy, HOMO/LUMO
 * frontier orbital bar + gap, dipole moment, solvent, charge/uhf
 * state. Click HOMO or LUMO → ask Claude about the orbital.
 */

import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types
// =============================================================================

interface QmCalculationToolInput {
  smiles?: string;
  calculation_type?: string;
  calculation?: string;
  charge?: number;
  uhf?: number;
  solvent?: string;
  xyz_input?: string;

  energy_hartree?: number;
  energy_kcal_mol?: number;
  energy_eV?: number;

  // HOMO / LUMO come back under different names depending on the backend
  // version. Every plausible variant is accepted here; the viewer resolves
  // them with pickFirst() below.
  homo_ev?: number;
  homo_eV?: number;
  homo_energy_ev?: number;
  homo_energy_eV?: number;
  homo?: number;

  lumo_ev?: number;
  lumo_eV?: number;
  lumo_energy_ev?: number;
  lumo_energy_eV?: number;
  lumo?: number;

  homo_lumo_gap_eV?: number;
  homo_lumo_gap_ev?: number;
  gap_ev?: number;

  dipole_debye?: number;

  optimized_xyz?: string;

  solvation_energy_kcal_mol?: number;

  method?: string;
  wall_time_seconds?: number;
  warnings?: string[];
}

// Resolve a numeric field by trying each candidate name in order.
function pickFirst(
  data: Record<string, unknown>,
  keys: string[],
): number | undefined {
  for (const k of keys) {
    const v = data[k];
    if (typeof v === "number" && Number.isFinite(v)) return v;
  }
  return undefined;
}

type QmCalculationProps = ViewProps<QmCalculationToolInput>;

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
        Running xTB semi-empirical calculation…
      </div>
    </div>
  );
}

// =============================================================================
// HOMO / LUMO ladder (reuses the frontier-orbitals pattern but smaller)
// =============================================================================

function OrbitalLadder({
  homo,
  lumo,
  gap,
  onHomoClick,
  onLumoClick,
}: {
  homo?: number;
  lumo?: number;
  gap?: number;
  onHomoClick?: () => void;
  onLumoClick?: () => void;
}) {
  if (homo == null || lumo == null) return null;

  const padEv = 0.5;
  const yMax = lumo + padEv;
  const yMin = homo - padEv;
  const ySpan = yMax - yMin || 1;

  const width = 420;
  const height = 180;
  const leftPad = 60;
  const rightPad = 18;
  const topPad = 16;
  const bottomPad = 22;
  const plotH = height - topPad - bottomPad;
  const plotW = width - leftPad - rightPad;

  const toY = (ev: number) => topPad + ((yMax - ev) / ySpan) * plotH;
  const homoY = toY(homo);
  const lumoY = toY(lumo);
  const levelWidth = plotW * 0.6;
  const levelX = leftPad + (plotW - levelWidth) / 2;

  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} style={{ display: "block" }}>
      <line
        x1={leftPad}
        x2={leftPad}
        y1={topPad}
        y2={height - bottomPad}
        stroke="var(--text-muted)"
        strokeOpacity={0.4}
      />

      {/* LUMO */}
      <g onClick={onLumoClick} style={{ cursor: onLumoClick ? "pointer" : undefined }}>
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
        <text x={levelX + levelWidth + 6} y={lumoY + 4} fontSize={11} fill="var(--text-muted)">
          LUMO
        </text>
        <title>{onLumoClick ? "Click to ask Claude about LUMO" : `LUMO: ${lumo.toFixed(2)} eV`}</title>
      </g>

      {/* HOMO */}
      <g onClick={onHomoClick} style={{ cursor: onHomoClick ? "pointer" : undefined }}>
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
        <text x={levelX + levelWidth + 6} y={homoY + 4} fontSize={11} fill="var(--text-muted)">
          HOMO
        </text>
        <title>{onHomoClick ? "Click to ask Claude about HOMO" : `HOMO: ${homo.toFixed(2)} eV`}</title>
      </g>

      {/* Gap */}
      {gap != null && (
        <>
          <line
            x1={levelX + levelWidth * 0.25}
            x2={levelX + levelWidth * 0.25}
            y1={homoY}
            y2={lumoY}
            stroke="var(--text-muted)"
            strokeWidth={1}
            markerEnd="url(#arr-up-qm)"
            markerStart="url(#arr-down-qm)"
          />
          <text
            x={levelX + levelWidth * 0.25 + 8}
            y={(homoY + lumoY) / 2 + 4}
            fontSize={11}
            fill="var(--text)"
            style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}
          >
            Δ {gap.toFixed(2)} eV
          </text>
        </>
      )}

      <defs>
        <marker id="arr-up-qm" markerWidth="6" markerHeight="6" refX="3" refY="0" orient="auto">
          <path d="M 0 6 L 3 0 L 6 6 z" fill="var(--text-muted)" />
        </marker>
        <marker id="arr-down-qm" markerWidth="6" markerHeight="6" refX="3" refY="6" orient="auto">
          <path d="M 0 0 L 3 6 L 6 0 z" fill="var(--text-muted)" />
        </marker>
      </defs>
    </svg>
  );
}

// =============================================================================
// Energy + dipole summary
// =============================================================================

function EnergyDipoleCards({ data }: { data: QmCalculationToolInput }) {
  const calc = data.calculation_type || data.calculation;
  const items: Array<{ label: string; value: string; sub?: string; color: string; title?: string }> = [];

  if (data.energy_kcal_mol != null) {
    items.push({
      label: "Electronic Energy",
      value: `${data.energy_kcal_mol.toFixed(2)}`,
      sub: `kcal/mol${data.energy_hartree != null ? ` · ${data.energy_hartree.toFixed(6)} Ha` : ""}`,
      color: "var(--accent)",
      title: "Total electronic energy from xTB (GFN2)",
    });
  }

  if (data.dipole_debye != null) {
    items.push({
      label: "Dipole Moment",
      value: `${data.dipole_debye.toFixed(3)}`,
      sub: "Debye",
      color: "var(--warning)",
      title: "Ground-state electric dipole moment; zero for symmetric molecules",
    });
  }

  if (data.solvation_energy_kcal_mol != null) {
    items.push({
      label: "Solvation Energy",
      value: `${data.solvation_energy_kcal_mol >= 0 ? "+" : ""}${data.solvation_energy_kcal_mol.toFixed(2)}`,
      sub: `kcal/mol · ${data.solvent || "ALPB"}`,
      color: "var(--success)",
      title: "ΔG of transfer from gas to solvent via ALPB implicit model",
    });
  }

  if (calc) {
    items.push({
      label: "Calculation",
      value: calc,
      color: "var(--text-muted)",
      sub: data.method || undefined,
    });
  }

  if (items.length === 0) return null;

  return (
    <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
      {items.map((it) => (
        <div
          key={it.label}
          title={it.title}
          style={{
            padding: "12px 16px",
            background: "var(--bg-warm)",
            borderRadius: 2,
            borderLeft: `3px solid ${it.color}`,
            minWidth: 150,
          }}
        >
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>
            {it.label}
          </div>
          <div
            style={{
              fontSize: 18,
              fontFamily: "var(--font-mono)",
              fontWeight: 600,
              color: it.color,
            }}
          >
            {it.value}
          </div>
          {it.sub && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)", marginTop: 2 }}>
              {it.sub}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// =============================================================================
// State chips — charge, uhf, solvent
// =============================================================================

function StateChips({ data }: { data: QmCalculationToolInput }) {
  const chips: Array<{ label: string; color: string; hint: string }> = [];

  if (data.charge && data.charge !== 0) {
    chips.push({
      label: `charge ${data.charge > 0 ? "+" + data.charge : data.charge}`,
      color: data.charge > 0 ? "var(--accent)" : "var(--warning)",
      hint: data.charge > 0 ? "cation" : "anion",
    });
  }

  if (data.uhf && data.uhf > 0) {
    chips.push({
      label: `uhf=${data.uhf}`,
      color: "var(--warning)",
      hint:
        data.uhf === 1
          ? "doublet (radical)"
          : data.uhf === 2
            ? "triplet"
            : `${data.uhf} unpaired electrons`,
    });
  }

  if (data.solvent) {
    chips.push({
      label: data.solvent,
      color: "var(--success)",
      hint: "ALPB implicit solvent",
    });
  }

  if (chips.length === 0) return null;

  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
      {chips.map((c, i) => (
        <span
          key={i}
          title={c.hint}
          style={{
            fontSize: 10,
            padding: "2px 8px",
            background: "var(--bg-warm)",
            borderLeft: `2px solid ${c.color}`,
            borderRadius: 2,
            color: c.color,
            fontWeight: 500,
            fontFamily: "var(--font-mono)",
          }}
        >
          {c.label}
        </span>
      ))}
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function QmCalculationViewer(props: QmCalculationProps) {
  const { toolInputs, toolInputsPartial, toolResult, sendMessage } = props;
  const data = useViewData<QmCalculationToolInput>(props);
  const isStreaming = !toolInputs && !toolResult;
  if (isStreaming) {
    return <LoadingShimmer />;
  }

  const smiles = data.smiles || toolInputs?.smiles || toolInputsPartial?.smiles;

  // Resolve HOMO / LUMO / gap across the various field-name variants the
  // backend might emit. Some builds use homo_ev, some homo_energy_ev, some
  // just homo; gap is sometimes homo_lumo_gap_eV (capital V), sometimes
  // homo_lumo_gap_ev, sometimes gap_ev. Accept them all.
  const dataAsAny = data as unknown as Record<string, unknown>;
  const homoEv = pickFirst(dataAsAny, [
    "homo_ev",
    "homo_eV",
    "homo_energy_ev",
    "homo_energy_eV",
    "homo",
  ]);
  const lumoEv = pickFirst(dataAsAny, [
    "lumo_ev",
    "lumo_eV",
    "lumo_energy_ev",
    "lumo_energy_eV",
    "lumo",
  ]);
  let gap = pickFirst(dataAsAny, [
    "homo_lumo_gap_eV",
    "homo_lumo_gap_ev",
    "gap_ev",
  ]);
  // Derive gap from HOMO/LUMO when the backend only emits one or the other.
  if (gap == null && homoEv != null && lumoEv != null) {
    gap = lumoEv - homoEv;
  }

  // Guards nest `sendMessage ?` inside the data-presence check so TS doesn't
  // flag `sendMessage && ...` as always-truthy (tsc --noEmit treats
  // sendMessage as a required function on ViewProps; CI fails on TS2774).
  // Same pattern docking-viewer / structure-viewer use.
  const askAboutHomo =
    homoEv != null
      ? sendMessage
        ? () => {
            const smilesRef = smiles ? ` for \`${smiles}\`` : "";
            sendMessage({
              role: "user",
              content: [
                {
                  type: "text",
                  text:
                    `I clicked the HOMO (${homoEv.toFixed(2)} eV)${smilesRef}. ` +
                    `What kind of orbital is this (π, σ, lone pair, metal d), ` +
                    `where is it localized, and what does its energy tell me about the molecule's ionization potential and reactivity (oxidation)?`,
                },
              ],
            });
          }
        : undefined
      : undefined;

  const askAboutLumo =
    lumoEv != null
      ? sendMessage
        ? () => {
            const smilesRef = smiles ? ` for \`${smiles}\`` : "";
            sendMessage({
              role: "user",
              content: [
                {
                  type: "text",
                  text:
                    `I clicked the LUMO (${lumoEv.toFixed(2)} eV)${smilesRef}. ` +
                    `What kind of orbital is this (π*, σ*, empty d), ` +
                    `where is it localized, and what does its energy tell me about the molecule's electron affinity and reactivity (reduction)?`,
                },
              ],
            });
          }
        : undefined
      : undefined;

  return (
    <div className="qm-calculation-viewer" style={{ width: "100%" }}>
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
            QM Calculation
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
          <StateChips data={data} />
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
        <div className="panel-title">Scalar Results</div>
        <EnergyDipoleCards data={data} />
      </div>

      {homoEv != null && lumoEv != null && (
        <div className="panel" style={{ marginTop: 16 }}>
          <div
            className="panel-title"
            style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
          >
            <span>Frontier Orbitals</span>
            {askAboutHomo && (
              <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-muted)" }}>
                click HOMO or LUMO to ask
              </span>
            )}
          </div>
          <OrbitalLadder
            homo={homoEv}
            lumo={lumoEv}
            gap={gap}
            onHomoClick={askAboutHomo}
            onLumoClick={askAboutLumo}
          />
        </div>
      )}

      {data.optimized_xyz && (
        <div className="panel" style={{ marginTop: 16 }}>
          <div className="panel-title">Optimized Geometry</div>
          <pre
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "var(--text-muted)",
              background: "var(--bg-warm)",
              padding: "10px 12px",
              borderRadius: 2,
              maxHeight: 180,
              overflow: "auto",
              whiteSpace: "pre",
              margin: 0,
            }}
          >
            {data.optimized_xyz}
          </pre>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6 }}>
            Pass this XYZ to run_qm_hessian, predict_redox_potential, or find_transition_state to
            continue from the optimized geometry without re-running SCF.
          </div>
        </div>
      )}

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
