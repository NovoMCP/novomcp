/**
 * NovoMCP NNP Results Viewer
 *
 * Shared viewer for compute_energy and optimize_geometry_nnp. Both
 * return the same energy / forces / method / timing shape; the
 * optimizer additionally returns optimized_xyz, converged, n_steps.
 *
 * Compute-energy path shows: energy cards, force diagnostics, method
 * badge, wall time.
 * Optimize path adds: convergence status pill, step count, and an
 * NGL render of the relaxed geometry (dynamic-imported so the bundle
 * stays small when only compute_energy is invoked).
 *
 * Sync tools — no submission phase.
 */

import type { ViewProps } from "./create-app.tsx";
import MoleculeRenderer from "./molecule-renderer.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types
// =============================================================================

export interface NnpData {
  smiles?: string;
  energy_ev?: number | null;
  energy_kcal_mol?: number | null;
  forces_max_ev_ang?: number | null;
  forces_rms_ev_ang?: number | null;
  method?: string;
  n_atoms?: number;
  wall_time_ms?: number | null;

  // Optimize-only fields
  optimized_xyz?: string | null;
  converged?: boolean;
  n_steps?: number;
}

type NnpProps = ViewProps<NnpData>;

// =============================================================================
// Force-classification: is this geometry a converged minimum?
// =============================================================================

function forceStatus(fmax?: number | null): { color: string; label: string } {
  if (fmax == null || !Number.isFinite(fmax)) return { color: "var(--text-muted)", label: "—" };
  if (fmax <= 0.05) return { color: "#7FA35E", label: "converged (minimum)" };
  if (fmax <= 0.1) return { color: "#BFB04E", label: "near minimum" };
  if (fmax <= 0.5) return { color: "#D4884E", label: "unrelaxed" };
  return { color: "#C25D4E", label: "high-strain geometry" };
}

// =============================================================================
// Main viewer
// =============================================================================

export default function NnpResultsViewer(props: NnpProps) {
  const { toolInputs, toolInputsPartial, toolResult, sendMessage } = props;
  const data = useViewData<NnpData>(props);
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
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>Running neural potential…</div>
      </div>
    );
  }

  const energyEv = data.energy_ev;
  const energyKcal = data.energy_kcal_mol;
  const fmax = data.forces_max_ev_ang;
  const frms = data.forces_rms_ev_ang;
  const xyz = data.optimized_xyz;
  const converged = data.converged;
  const nSteps = data.n_steps;
  const nAtoms = data.n_atoms;
  const method = data.method;
  const wallMs = data.wall_time_ms;
  const smiles = data.smiles || toolInputs?.smiles || toolInputsPartial?.smiles;

  const isOptimize = xyz != null || converged != null || nSteps != null;
  const title = isOptimize ? "Geometry Optimization" : "Single-Point Energy";
  const fStatus = forceStatus(fmax);

  const askAboutResult = sendMessage
    ? () => {
        const smilesRef = smiles ? ` for \`${smiles}\`` : "";
        if (isOptimize) {
          sendMessage({
            role: "user",
            content: [
              {
                type: "text",
                text:
                  `NNP geometry optimization${smilesRef}: ${converged ? "converged" : "NOT converged"} ` +
                  `in ${nSteps ?? "?"} steps (${method}, max force ${fmax != null ? fmax.toFixed(3) + " eV/Å" : "?"}, ` +
                  `final energy ${energyEv != null ? energyEv.toFixed(4) + " eV" : "?"}). ` +
                  `Should I use this NNP-relaxed geometry directly for docking / downstream ` +
                  `xTB refinement, or does the strain level warrant another round of optimization?`,
              },
            ],
          });
        } else {
          sendMessage({
            role: "user",
            content: [
              {
                type: "text",
                text:
                  `Single-point NNP energy${smilesRef}: ${energyEv != null ? energyEv.toFixed(4) + " eV" : "?"} ` +
                  `(${energyKcal != null ? energyKcal.toFixed(2) + " kcal/mol" : "?"}) via ${method || "NNP"}. ` +
                  `Max force ${fmax != null ? fmax.toFixed(3) + " eV/Å" : "?"} ` +
                  `(${fStatus.label}). How does this compare against what I'd expect for this ` +
                  `molecular formula, and is the geometry relaxed enough to trust?`,
              },
            ],
          });
        }
      }
    : undefined;

  return (
    <div className="nnp-results-viewer" style={{ width: "100%" }}>
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
            {title}
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
          {method && (
            <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
              {method.toUpperCase()} (NNP)
            </div>
          )}
          {wallMs != null && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {wallMs < 1000 ? `${wallMs.toFixed(0)} ms` : `${(wallMs / 1000).toFixed(2)} s`}
            </div>
          )}
        </div>
      </div>

      {/* Summary cards */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
        {energyEv != null && (
          <div
            onClick={askAboutResult}
            title={askAboutResult ? "Click to ask Claude" : undefined}
            style={{
              padding: "12px 16px",
              background: "var(--bg-warm)",
              borderRadius: 2,
              borderLeft: "3px solid var(--accent)",
              minWidth: 160,
              cursor: askAboutResult ? "pointer" : undefined,
            }}
          >
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Energy</div>
            <div style={{ fontSize: 20, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--accent)" }}>
              {energyEv.toFixed(4)}
              <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 4, fontWeight: 400 }}>eV</span>
            </div>
            {energyKcal != null && (
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2, fontFamily: "var(--font-mono)" }}>
                {energyKcal.toFixed(2)} kcal/mol
              </div>
            )}
          </div>
        )}
        {fmax != null && (
          <div
            style={{
              padding: "12px 16px",
              background: "var(--bg-warm)",
              borderRadius: 2,
              borderLeft: `3px solid ${fStatus.color}`,
              minWidth: 160,
            }}
          >
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Max Force</div>
            <div style={{ fontSize: 18, fontFamily: "var(--font-mono)", fontWeight: 600, color: fStatus.color }}>
              {fmax.toFixed(3)}
              <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 4, fontWeight: 400 }}>eV/Å</span>
            </div>
            <div style={{ fontSize: 10, color: fStatus.color, marginTop: 2 }}>
              {fStatus.label}
            </div>
          </div>
        )}
        {frms != null && (
          <div
            style={{
              padding: "12px 16px",
              background: "var(--bg-warm)",
              borderRadius: 2,
              borderLeft: "3px solid var(--text-muted)",
              minWidth: 140,
            }}
          >
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>RMS Force</div>
            <div style={{ fontSize: 16, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text)" }}>
              {frms.toFixed(3)}
              <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 4, fontWeight: 400 }}>eV/Å</span>
            </div>
          </div>
        )}
        {nAtoms != null && (
          <div
            style={{
              padding: "12px 16px",
              background: "var(--bg-warm)",
              borderRadius: 2,
              borderLeft: "3px solid var(--text-muted)",
              minWidth: 100,
            }}
          >
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Atoms</div>
            <div style={{ fontSize: 20, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text)" }}>
              {nAtoms}
            </div>
          </div>
        )}
      </div>

      {/* Optimization-specific: convergence + steps */}
      {isOptimize && (
        <div
          className="panel"
          style={{
            marginBottom: 16,
            borderLeft: `3px solid ${converged ? "#7FA35E" : "#C25D4E"}`,
          }}
        >
          <div className="panel-title">Optimization Status</div>
          <div style={{ display: "flex", gap: 20, alignItems: "center", flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: 10, color: "var(--text-muted)" }}>Convergence</div>
              <div
                style={{
                  fontSize: 14,
                  fontWeight: 600,
                  color: converged ? "#7FA35E" : "#C25D4E",
                  marginTop: 2,
                }}
              >
                {converged ? "✓ converged" : "✗ not converged"}
              </div>
            </div>
            {nSteps != null && (
              <div>
                <div style={{ fontSize: 10, color: "var(--text-muted)" }}>Steps</div>
                <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text)", fontFamily: "var(--font-mono)", marginTop: 2 }}>
                  {nSteps}
                </div>
              </div>
            )}
          </div>
          {!converged && (
            <div
              style={{
                marginTop: 8,
                fontSize: 11,
                color: "var(--text-muted)",
                lineHeight: 1.5,
              }}
            >
              BFGS hit the step limit without reaching the force tolerance. The final
              geometry is likely near a minimum but residual strain remains — consider
              looser fmax, more steps, or a different NNP model before using downstream.
            </div>
          )}
        </div>
      )}

      {/* Geometry render — optimize only */}
      {xyz && xyz.trim().length > 0 && (
        <div className="panel">
          <div className="panel-title">Relaxed Geometry</div>
          <MoleculeRenderer xyz={xyz} height={320} />
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6 }}>
            NNP-optimized atomic coordinates. Use as input for xTB single-point
            (pass <code style={{ fontFamily: "var(--font-mono)" }}>xyz_input</code> to
            run_qm_calculation) when you need more accurate energy evaluation than NNP
            provides.
          </div>
        </div>
      )}

      {/* Callout for compute_energy users */}
      {!isOptimize && fmax != null && fmax > 0.1 && (
        <div
          className="panel"
          style={{ borderLeft: "3px solid var(--warning)" }}
        >
          <div className="panel-title">Geometry Not Relaxed</div>
          <div style={{ fontSize: 12, color: "var(--text)", lineHeight: 1.6 }}>
            Max force is {fmax.toFixed(3)} eV/Å — above the 0.05 eV/Å threshold for a
            true minimum. The reported energy is at the input geometry, not a relaxed
            one. Run <code style={{ fontFamily: "var(--font-mono)" }}>optimize_geometry_nnp</code>{" "}
            first for a properly relaxed single-point.
          </div>
        </div>
      )}
    </div>
  );
}
