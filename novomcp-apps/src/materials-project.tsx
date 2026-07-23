/**
 * NovoMCP Materials Project Search Viewer
 *
 * Renders search_materials_project output: table of matched
 * inorganic materials with band gap, stability, formation energy,
 * crystal system, and space group. Click any row → Claude gets a
 * specific prompt about that material's applications.
 */

import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types
// =============================================================================

interface MaterialResult {
  material_id?: string;
  formula?: string;
  // Band gap — try band_gap_ev, band_gap.
  band_gap_ev?: number;
  band_gap?: number;
  // Formation energy — novomcp emits `formation_energy_ev_atom` (see
  // tools.py:11795). The raw Materials Project API uses
  // `formation_energy_per_atom`. Older builds used `formation_energy_ev`.
  // Accept all three so the viewer works whether it's reading through the
  // gateway or a future direct-MP path.
  formation_energy_ev_atom?: number;
  formation_energy_per_atom?: number;
  formation_energy_ev?: number;
  // Energy above hull — same story: novomcp emits
  // `energy_above_hull_ev_atom`, raw MP uses `energy_above_hull`, older
  // builds used `energy_above_hull_ev`.
  energy_above_hull_ev_atom?: number;
  energy_above_hull?: number;
  energy_above_hull_ev?: number;
  is_stable?: boolean;
  is_metal?: boolean;
  theoretical?: boolean;
  crystal_system?: string;
  space_group?: string;
  spacegroup?: string;
  symbol?: string;
  density?: number;
  density_g_cm3?: number;
  volume?: number;
  nsites?: number;
  n_sites?: number;
}

interface MaterialsProjectToolInput {
  query?: string;
  search_type?: string;
  top_k?: number;

  count?: number;
  results?: MaterialResult[];

  method?: string;
  wall_time_seconds?: number;
  warnings?: string[];
}

type MaterialsProjectProps = ViewProps<MaterialsProjectToolInput>;

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
        Searching Materials Project…
      </div>
    </div>
  );
}

// =============================================================================
// Band-gap classification helper
// =============================================================================

function bandGapCategory(gap?: number): { label: string; color: string } {
  if (gap == null) return { label: "unknown", color: "var(--text-muted)" };
  if (gap < 0.01) return { label: "metal", color: "var(--warning)" };
  if (gap < 1.0) return { label: "semimetal", color: "var(--accent)" };
  if (gap < 3.0) return { label: "semiconductor", color: "var(--accent)" };
  if (gap < 6.0) return { label: "wide-gap semiconductor", color: "var(--success)" };
  return { label: "insulator", color: "var(--text-muted)" };
}

// =============================================================================
// Results Table
// =============================================================================

function MaterialsTable({
  results,
  sendMessage,
  queryContext,
}: {
  results?: MaterialResult[];
  sendMessage?: MaterialsProjectProps["sendMessage"];
  queryContext?: string;
}) {
  if (!results || results.length === 0) {
    return (
      <div className="panel">
        <div className="panel-title">No Matches</div>
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          No materials found for this query. Try a different formula, chemical system (e.g. Li-Co-O),
          or Materials Project material_id (mp-xxxxx).
        </div>
      </div>
    );
  }

  const askAboutMaterial = sendMessage
    ? (m: MaterialResult) => {
        const ref = m.material_id ? `\`${m.material_id}\`` : m.formula ? `\`${m.formula}\`` : "this material";
        const bandGap = m.band_gap_ev ?? m.band_gap;
        const eAboveHull =
          m.energy_above_hull_ev_atom ??
          m.energy_above_hull ??
          m.energy_above_hull_ev;
        const gap = bandGap != null ? `band gap ${bandGap.toFixed(2)} eV` : "unknown band gap";
        const stability = m.is_stable
          ? "stable"
          : eAboveHull != null
            ? `E above hull ${eAboveHull.toFixed(3)} eV/atom (metastable)`
            : "stability unknown";
        const context = queryContext ? ` (from my ${queryContext} query)` : "";
        sendMessage({
          role: "user",
          content: [
            {
              type: "text",
              text:
                `I clicked ${ref} in the Materials Project results${context}. ` +
                `Formula: ${m.formula || "?"}. ${gap}. ${stability}. ` +
                `Crystal system: ${m.crystal_system || "?"}. ` +
                `What is this material typically used for, what's its role in Li-ion / catalysis / optoelectronics research, ` +
                `and are the computed properties (band gap, formation energy) consistent with experimental data or an outlier?`,
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
        <span>Matched Materials ({results.length})</span>
        <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-muted)" }}>
          {askAboutMaterial ? "click any row to ask" : ""}
        </span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>ID</th>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Formula</th>
              <th style={{ textAlign: "right", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Band Gap (eV)</th>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Class</th>
              <th style={{ textAlign: "right", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Formation E (eV/atom)</th>
              <th style={{ textAlign: "right", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>E above hull</th>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Crystal</th>
              <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Space Group</th>
              <th style={{ textAlign: "center", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Stable?</th>
            </tr>
          </thead>
          <tbody>
            {results.map((m, i) => {
              // Resolve fields across backend variants.
              const bandGap = m.band_gap_ev ?? m.band_gap;
              const formationEnergy =
                m.formation_energy_ev_atom ??
                m.formation_energy_per_atom ??
                m.formation_energy_ev;
              const eAboveHull =
                m.energy_above_hull_ev_atom ??
                m.energy_above_hull ??
                m.energy_above_hull_ev;
              const spaceGroup = m.space_group || m.spacegroup || m.symbol;
              const cat = bandGapCategory(bandGap);

              return (
                <tr
                  key={m.material_id || i}
                  onClick={askAboutMaterial ? () => askAboutMaterial(m) : undefined}
                  style={{
                    borderBottom: "1px solid var(--border)",
                    cursor: askAboutMaterial ? "pointer" : undefined,
                  }}
                  title={askAboutMaterial ? `Click to ask Claude about ${m.material_id || m.formula}` : undefined}
                >
                  <td style={{ padding: "6px 8px", fontFamily: "var(--font-mono)", fontWeight: 500, color: "var(--accent)" }}>
                    {m.material_id || "—"}
                  </td>
                  <td style={{ padding: "6px 8px", fontFamily: "var(--font-mono)", fontWeight: 500, color: "var(--text)" }}>
                    {m.formula || "—"}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right", fontFamily: "var(--font-mono)" }}>
                    {bandGap != null ? bandGap.toFixed(3) : "—"}
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: 10, color: cat.color }}>
                    {cat.label}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right", fontFamily: "var(--font-mono)", color: formationEnergy != null && formationEnergy < 0 ? "var(--success)" : "var(--text-muted)" }}>
                    {formationEnergy != null ? (formationEnergy >= 0 ? "+" : "") + formationEnergy.toFixed(3) : "—"}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right", fontFamily: "var(--font-mono)", color: eAboveHull != null && eAboveHull > 0.05 ? "var(--warning)" : "var(--text-muted)" }}>
                    {eAboveHull != null ? eAboveHull.toFixed(3) : "—"}
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: 11, color: "var(--text-muted)", textTransform: "capitalize" }}>
                    {m.crystal_system || "—"}
                  </td>
                  <td style={{ padding: "6px 8px", fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                    {spaceGroup || "—"}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "center" }}>
                    {m.is_stable ? (
                      <span style={{ fontSize: 9, color: "var(--success)", fontWeight: 500 }}>✓</span>
                    ) : m.is_stable === false ? (
                      <span style={{ fontSize: 9, color: "var(--warning)" }}>metastable</span>
                    ) : (
                      <span style={{ fontSize: 9, color: "var(--text-muted)" }}>—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.6 }}>
        Stability: "stable" = on the convex hull (E above hull = 0). Metastable phases
        may still be synthesizable. Formation energy &lt; 0 = thermodynamically more stable
        than the elemental reference states.
      </div>
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function MaterialsProjectViewer(props: MaterialsProjectProps) {
  const { toolInputs, toolInputsPartial, toolResult, sendMessage } = props;
  const data = useViewData<MaterialsProjectToolInput>(props);
  const isStreaming = !toolInputs && !toolResult;
  if (isStreaming) {
    return <LoadingShimmer />;
  }

  const query = data.query || toolInputs?.query || toolInputsPartial?.query;
  const searchType = data.search_type || toolInputs?.search_type;
  const count = data.count ?? data.results?.length ?? 0;

  const queryContext = searchType
    ? searchType === "formula"
      ? "formula"
      : searchType === "chemsys"
        ? "chemical-system"
        : searchType === "material_id"
          ? "material-ID"
          : searchType
    : undefined;

  return (
    <div className="materials-project-viewer" style={{ width: "100%" }}>
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
            Materials Project
          </div>
          {query && (
            <div
              style={{
                fontSize: 11,
                fontFamily: "var(--font-mono)",
                color: "var(--text-muted)",
                marginTop: 4,
              }}
            >
              {query}
              {searchType && <span style={{ marginLeft: 6 }}>· {searchType}</span>}
            </div>
          )}
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 14, fontFamily: "var(--font-mono)", color: "var(--accent)", fontWeight: 600 }}>
            {count}
          </div>
          <div style={{ fontSize: 10, color: "var(--text-muted)" }}>matches</div>
          {data.wall_time_seconds != null && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)", marginTop: 2 }}>
              {data.wall_time_seconds.toFixed(2)} s
            </div>
          )}
        </div>
      </div>

      <MaterialsTable
        results={data.results}
        sendMessage={sendMessage}
        queryContext={queryContext}
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
