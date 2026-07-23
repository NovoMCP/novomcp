/**
 * NovoMCP QM Hessian Viewer
 *
 * Renders the output of run_qm_hessian: vibrational frequencies as a
 * horizontal bar chart (negatives flagged as imaginary),
 * is-true-minimum status, thermochemistry table, and click-to-ask
 * on any mode.
 */

import type { ViewProps } from "./create-app.tsx";
import { BarChart, type Bar } from "./charts.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types
// =============================================================================

interface QmHessianToolInput {
  smiles?: string;
  charge?: number;
  uhf?: number;
  solvent?: string;
  temperature?: number;
  optimize_first?: boolean;

  energy_kcal_mol?: number;
  zpe_kcal_mol?: number;
  enthalpy_correction_kcal_mol?: number;
  gibbs_correction_kcal_mol?: number;
  entropy_cal_mol_k?: number;
  temperature_k?: number;

  frequencies_cm1?: number[];
  n_imaginary?: number;
  is_true_minimum?: boolean;

  method?: string;
  wall_time_seconds?: number;
  optimized_xyz?: string;
  warnings?: string[];
}

type QmHessianProps = ViewProps<QmHessianToolInput>;

// =============================================================================
// Frequency classification — broad vibrational regions help a reader skim
// =============================================================================

function frequencyRegion(freq: number): string {
  if (freq < 0) return "imaginary (saddle point / transition state)";
  if (freq < 400) return "skeletal / torsion";
  if (freq < 800) return "bending";
  if (freq < 1500) return "bending / stretching";
  if (freq < 2000) return "C=C / C=O stretch";
  if (freq < 2500) return "triple-bond / nitrile stretch";
  if (freq < 3000) return "C–H stretch (sp³)";
  if (freq < 3200) return "C–H stretch (aromatic / sp²)";
  if (freq < 3800) return "O–H / N–H stretch";
  return "very high stretch";
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
        Computing vibrational frequencies & thermochemistry…
      </div>
    </div>
  );
}

// =============================================================================
// Minimum Status Pill
// =============================================================================

function MinimumStatus({
  isTrueMinimum,
  nImaginary,
}: {
  isTrueMinimum?: boolean;
  nImaginary?: number;
}) {
  if (isTrueMinimum === undefined && nImaginary === undefined) return null;

  const hasImaginary = (nImaginary ?? 0) > 0;
  const color = isTrueMinimum && !hasImaginary ? "var(--success)" : "var(--warning)";
  const label = isTrueMinimum && !hasImaginary ? "True Minimum" : "Saddle Point / Transition State";
  const hint = isTrueMinimum && !hasImaginary
    ? "All frequencies real — this is a local minimum on the PES."
    : `${nImaginary ?? "One or more"} imaginary frequenc${(nImaginary ?? 1) === 1 ? "y" : "ies"} — the structure is not at a minimum.`;

  return (
    <div className="panel">
      <div className="panel-title">Geometry Check</div>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span
          style={{
            padding: "4px 10px",
            background: "var(--bg-warm)",
            borderLeft: `3px solid ${color}`,
            borderRadius: 2,
            fontSize: 12,
            fontWeight: 500,
            color,
          }}
        >
          {label}
        </span>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{hint}</span>
      </div>
    </div>
  );
}

// =============================================================================
// Thermochemistry Table
// =============================================================================

function ThermoRow({
  label,
  value,
  unit,
  title,
}: {
  label: string;
  value?: number;
  unit: string;
  title?: string;
}) {
  if (value == null) return null;
  return (
    <tr>
      <td style={{ padding: "6px 12px 6px 0", fontSize: 11, color: "var(--text-muted)" }} title={title}>
        {label}
      </td>
      <td
        style={{
          padding: "6px 0",
          textAlign: "right",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          fontWeight: 500,
          color: "var(--text)",
        }}
      >
        {value.toFixed(3)}
      </td>
      <td style={{ padding: "6px 0 6px 6px", fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
        {unit}
      </td>
    </tr>
  );
}

function ThermochemistryTable({ data }: { data: QmHessianToolInput }) {
  const hasAny =
    data.energy_kcal_mol != null ||
    data.zpe_kcal_mol != null ||
    data.enthalpy_correction_kcal_mol != null ||
    data.gibbs_correction_kcal_mol != null ||
    data.entropy_cal_mol_k != null;

  if (!hasAny) return null;

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">
        Thermochemistry
        {data.temperature_k != null && (
          <span style={{ fontSize: 11, fontWeight: 400, color: "var(--text-muted)", marginLeft: 8 }}>
            @ {data.temperature_k.toFixed(1)} K
          </span>
        )}
      </div>
      <table style={{ width: "100%", maxWidth: 360 }}>
        <tbody>
          <ThermoRow
            label="Electronic energy"
            value={data.energy_kcal_mol}
            unit="kcal/mol"
            title="Total electronic energy from xTB"
          />
          <ThermoRow
            label="Zero-point energy (ZPE)"
            value={data.zpe_kcal_mol}
            unit="kcal/mol"
            title="Quantum-mechanical vibrational zero-point"
          />
          <ThermoRow
            label="Enthalpy correction"
            value={data.enthalpy_correction_kcal_mol}
            unit="kcal/mol"
            title="Thermal correction to give H(T) from electronic energy"
          />
          <ThermoRow
            label="Gibbs correction"
            value={data.gibbs_correction_kcal_mol}
            unit="kcal/mol"
            title="ΔG = ΔH − TΔS correction"
          />
          <ThermoRow
            label="Entropy"
            value={data.entropy_cal_mol_k}
            unit="cal/mol·K"
            title="Total entropy S(T)"
          />
        </tbody>
      </table>
    </div>
  );
}

// =============================================================================
// Frequency Spectrum — bar chart, imaginary flagged, click-to-ask
// =============================================================================

function FrequencySpectrum({
  frequencies,
  sendMessage,
  smiles,
}: {
  frequencies?: number[];
  sendMessage?: QmHessianProps["sendMessage"];
  smiles?: string;
}) {
  if (!frequencies || frequencies.length === 0) return null;

  // Sort ascending so imaginary modes (negatives) appear leftmost.
  const sorted = [...frequencies].sort((a, b) => a - b);

  const handleModeClick = (freq: number, sortedIdx: number) => {
    if (!sendMessage) return;
    const region = frequencyRegion(freq);
    const smilesRef = smiles ? ` for \`${smiles}\`` : "";
    const freqStr =
      freq < 0 ? `an imaginary mode at ${Math.abs(freq).toFixed(0)}i cm⁻¹` : `the mode at ${freq.toFixed(0)} cm⁻¹`;
    sendMessage({
      role: "user",
      content: [
        {
          type: "text",
          text:
            `I clicked ${freqStr} (sorted rank #${sortedIdx + 1}) in the vibrational spectrum${smilesRef}. ` +
            `Region: ${region}. Describe the physical motion of this mode, ` +
            (freq < 0
              ? `what direction the structure wants to relax in, and what reaction coordinate this imaginary frequency corresponds to.`
              : `which bonds or groups are vibrating, and whether it contributes meaningfully to thermochemistry (ZPE / entropy).`),
        },
      ],
    });
  };

  const bars: Bar[] = sorted.map((freq, i) => ({
    value: freq,
    color: freq < 0 ? "var(--danger)" : "var(--accent)",
    onClick: () => handleModeClick(freq, i),
    title:
      freq < 0
        ? `Imaginary mode: ${Math.abs(freq).toFixed(1)}i cm⁻¹ — ${frequencyRegion(freq)}`
        : `${freq.toFixed(1)} cm⁻¹ — ${frequencyRegion(freq)}${sendMessage ? " (click to ask Claude)" : ""}`,
  }));

  const height = Math.min(380, Math.max(160, sorted.length * 10 + 40));

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div
        className="panel-title"
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
      >
        <span>Vibrational Spectrum ({sorted.length} modes)</span>
        <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-muted)" }}>
          cm⁻¹ · {sendMessage ? "click any mode to ask" : "view only"}
        </span>
      </div>
      <div style={{ width: "100%" }}>
        <BarChart bars={bars} height={height} orientation="vertical" unit="cm⁻¹" />
      </div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontSize: 10,
          color: "var(--text-muted)",
          marginTop: 6,
        }}
      >
        <span>
          min: {sorted[0].toFixed(0)} cm⁻¹
          {sorted[0] < 0 ? <span style={{ color: "var(--danger)", marginLeft: 4 }}>(imaginary)</span> : null}
        </span>
        <span>max: {sorted[sorted.length - 1].toFixed(0)} cm⁻¹</span>
      </div>
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function QmHessianViewer(props: QmHessianProps) {
  const { toolInputs, toolInputsPartial, toolResult, sendMessage } = props;
  const data = useViewData<QmHessianToolInput>(props);
  const isStreaming = !toolInputs && !toolResult;
  if (isStreaming) {
    return <LoadingShimmer />;
  }

  const smiles = data.smiles || toolInputs?.smiles || toolInputsPartial?.smiles;

  return (
    <div className="qm-hessian-viewer" style={{ width: "100%" }}>
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
            Vibrational Frequencies & Thermochemistry
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

      {/* Minimum status */}
      <MinimumStatus
        isTrueMinimum={data.is_true_minimum}
        nImaginary={data.n_imaginary}
      />

      {/* Frequency spectrum */}
      <FrequencySpectrum
        frequencies={data.frequencies_cm1}
        sendMessage={sendMessage}
        smiles={smiles}
      />

      {/* Thermochemistry */}
      <ThermochemistryTable data={data} />

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
