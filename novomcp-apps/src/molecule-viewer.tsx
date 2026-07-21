/**
 * NovoMCP Molecule Viewer Component
 *
 * Interactive 3D molecule visualization with ADMET radar chart.
 * Uses NGL Viewer for 3D rendering and Chart.js for ADMET visualization.
 */
import { useState, useEffect, useRef } from "react";
import type { ViewProps } from "./create-app.tsx";

// =============================================================================
// Types
// =============================================================================

interface MoleculeToolInput {
  smiles?: string;
  pdb_data?: string;
  name?: string;
  source?: string;
  in_database?: boolean;
  admet?: {
    // Backend format
    overall_toxicity_score?: number;
    is_aggregator_risk?: boolean;
    // Legacy format for radar chart
    absorption?: number;
    distribution?: number;
    metabolism?: number;
    excretion?: number;
    toxicity?: number;
  };
  properties?: {
    cid?: number;
    molecular_weight?: number;
    molecular_formula?: string;
    logp?: number;
    tpsa?: number;
    complexity?: number;
    hbd_count?: number;
    hba_count?: number;
    aromatic_ring_count?: number;
    heavy_atom_count?: number;
    fsp3?: number;
    qed?: number;
    drug_likeness?: number;
    synthetic_accessibility?: number;
    // Legacy names
    hbd?: number;
    hba?: number;
    rotatable_bonds?: number;
  };
  compliance?: {
    status?: string;
    is_dea_controlled?: boolean;
    is_fda_banned?: boolean;
    is_cwc_scheduled?: boolean;
    is_epa_pbt?: boolean;
    is_eu_reach_banned?: boolean;
    faves_flag_count?: number;
    // Legacy
    lipinski?: boolean;
    veber?: boolean;
    leadlike?: boolean;
  };
  structural_alerts?: {
    has_pains?: boolean;
    pains_count?: number;
    has_reactive_groups?: boolean;
    has_structural_alerts?: boolean;
  };
  height?: number;
}

type MoleculeViewerProps = ViewProps<MoleculeToolInput>;

// =============================================================================
// Loading Shimmer (during streaming)
// =============================================================================

function LoadingShimmer({ height, name }: { height: number; name?: string }) {
  return (
    <div
      style={{
        width: "100%",
        height,
        borderRadius: 4,
        padding: 20,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 12,
        background: "linear-gradient(135deg, var(--bg-warm) 0%, var(--bg) 100%)",
      }}
    >
      <div className="loading-spinner" />
      <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
        {name ? `Loading ${name}...` : "Preparing molecule viewer..."}
      </div>
    </div>
  );
}

// =============================================================================
// ADMET Radar Chart (using Canvas API)
// =============================================================================

function AdmetRadar({ admet }: { admet: MoleculeToolInput["admet"] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !admet) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const width = canvas.width;
    const height = canvas.height;
    const centerX = width / 2;
    const centerY = height / 2;
    const radius = Math.min(width, height) / 2 - 40;

    // Clear canvas
    ctx.clearRect(0, 0, width, height);

    // ADMET values (default to 50 if not provided)
    const values = [
      admet.absorption ?? 50,
      admet.distribution ?? 50,
      admet.metabolism ?? 50,
      admet.excretion ?? 50,
      admet.toxicity ?? 50,
    ];
    const labels = ["A", "D", "M", "E", "T"];
    const fullLabels = ["Absorption", "Distribution", "Metabolism", "Excretion", "Toxicity"];
    const numPoints = 5;
    const angleStep = (Math.PI * 2) / numPoints;
    const startAngle = -Math.PI / 2;

    // Get computed styles for theming
    const styles = getComputedStyle(document.documentElement);
    const borderColor = styles.getPropertyValue("--border").trim() || "#E8E4DE";
    const accentColor = styles.getPropertyValue("--accent").trim() || "#B8704B";
    const textColor = styles.getPropertyValue("--text-soft").trim() || "#6B6560";

    // Draw grid circles
    ctx.strokeStyle = borderColor;
    ctx.lineWidth = 1;
    for (let i = 1; i <= 5; i++) {
      ctx.beginPath();
      ctx.arc(centerX, centerY, (radius * i) / 5, 0, Math.PI * 2);
      ctx.stroke();
    }

    // Draw axes
    for (let i = 0; i < numPoints; i++) {
      const angle = startAngle + i * angleStep;
      const x = centerX + Math.cos(angle) * radius;
      const y = centerY + Math.sin(angle) * radius;
      ctx.beginPath();
      ctx.moveTo(centerX, centerY);
      ctx.lineTo(x, y);
      ctx.stroke();
    }

    // Draw data polygon
    ctx.beginPath();
    ctx.fillStyle = `${accentColor}22`;
    ctx.strokeStyle = accentColor;
    ctx.lineWidth = 2;

    for (let i = 0; i < numPoints; i++) {
      const angle = startAngle + i * angleStep;
      const value = values[i] / 100;
      const x = centerX + Math.cos(angle) * radius * value;
      const y = centerY + Math.sin(angle) * radius * value;

      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    // Draw data points
    ctx.fillStyle = accentColor;
    for (let i = 0; i < numPoints; i++) {
      const angle = startAngle + i * angleStep;
      const value = values[i] / 100;
      const x = centerX + Math.cos(angle) * radius * value;
      const y = centerY + Math.sin(angle) * radius * value;

      ctx.beginPath();
      ctx.arc(x, y, 5, 0, Math.PI * 2);
      ctx.fill();
    }

    // Draw labels
    ctx.fillStyle = textColor;
    ctx.font = "500 11px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    for (let i = 0; i < numPoints; i++) {
      const angle = startAngle + i * angleStep;
      const labelRadius = radius + 20;
      const x = centerX + Math.cos(angle) * labelRadius;
      const y = centerY + Math.sin(angle) * labelRadius;

      // Draw letter
      ctx.fillText(labels[i], x, y);

      // Draw value below
      ctx.font = "400 10px Inter, system-ui, sans-serif";
      ctx.fillText(`${values[i]}`, x, y + 14);
      ctx.font = "500 11px Inter, system-ui, sans-serif";
    }
  }, [admet]);

  return (
    <canvas
      ref={canvasRef}
      width={220}
      height={220}
      style={{ display: "block" }}
    />
  );
}

// =============================================================================
// Properties Panel
// =============================================================================

function PropertiesPanel({ properties, compliance, structuralAlerts }: {
  properties?: MoleculeToolInput["properties"];
  compliance?: MoleculeToolInput["compliance"];
  structuralAlerts?: MoleculeToolInput["structural_alerts"];
}) {
  if (!properties && !compliance) return null;

  const propItems = [
    { label: "MW", value: properties?.molecular_weight?.toFixed(2), unit: "g/mol" },
    { label: "Formula", value: properties?.molecular_formula },
    { label: "LogP", value: properties?.logp?.toFixed(2) },
    { label: "TPSA", value: properties?.tpsa?.toFixed(1), unit: "Å²" },
    { label: "HBD", value: properties?.hbd_count ?? properties?.hbd },
    { label: "HBA", value: properties?.hba_count ?? properties?.hba },
    { label: "QED", value: properties?.qed?.toFixed(3) },
    { label: "Fsp3", value: properties?.fsp3?.toFixed(2) },
    { label: "SA Score", value: properties?.synthetic_accessibility?.toFixed(2) },
  ].filter(item => item.value !== undefined);

  return (
    <div className="panel" style={{ marginTop: 12 }}>
      <div className="panel-title">Properties</div>

      {propItems.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, marginBottom: 12 }}>
          {propItems.map(item => (
            <div key={item.label} style={{ textAlign: "center", padding: "8px 4px", background: "var(--bg-warm)", borderRadius: 2 }}>
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 2 }}>{item.label}</div>
              <div style={{ fontSize: 13, fontFamily: "var(--font-mono)" }}>
                {item.value}{item.unit && <span style={{ fontSize: 10, color: "var(--text-muted)" }}> {item.unit}</span>}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Compliance Status */}
      {compliance && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Compliance
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {compliance.status && (
              <span className={`badge ${compliance.status === "clean" ? "success" : "warning"}`}>
                {compliance.status === "clean" ? "✓ Clean" : compliance.status}
              </span>
            )}
            {compliance.is_dea_controlled && (
              <span className="badge danger">DEA Controlled</span>
            )}
            {compliance.is_fda_banned && (
              <span className="badge danger">FDA Banned</span>
            )}
            {compliance.is_cwc_scheduled && (
              <span className="badge danger">CWC Scheduled</span>
            )}
            {compliance.is_epa_pbt && (
              <span className="badge warning">EPA PBT</span>
            )}
            {compliance.is_eu_reach_banned && (
              <span className="badge danger">EU REACH Banned</span>
            )}
            {/* Legacy fields */}
            {compliance.lipinski !== undefined && (
              <span className={`badge ${compliance.lipinski ? "success" : "danger"}`}>
                Lipinski {compliance.lipinski ? "✓" : "✗"}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Structural Alerts */}
      {structuralAlerts && (
        <div>
          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Structural Alerts
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <span className={`badge ${!structuralAlerts.has_pains ? "success" : "danger"}`}>
              PAINS {!structuralAlerts.has_pains ? "✓" : `✗ (${structuralAlerts.pains_count})`}
            </span>
            <span className={`badge ${!structuralAlerts.has_reactive_groups ? "success" : "warning"}`}>
              Reactive {!structuralAlerts.has_reactive_groups ? "✓" : "✗"}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

// =============================================================================
// 3D Viewer (NGL)
// =============================================================================

function Viewer3D({
  smiles,
  pdbData,
  height,
}: {
  smiles?: string;
  pdbData?: string;
  height: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [representation, setRepresentation] = useState<string>("ball+stick");
  const [error, setError] = useState<string | null>(null);
  const stageRef = useRef<any>(null);
  const componentRef = useRef<any>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    // Dynamically load NGL from bundled package
    const loadNGL = async () => {
      try {
        const NGL = await import("ngl");

        // Get theme
        const styles = getComputedStyle(document.documentElement);
        const bgColor = styles.getPropertyValue("--bg-card").trim() || "#FFFFFF";

        // Create stage
        stageRef.current = new NGL.Stage(containerRef.current!, {
          backgroundColor: bgColor,
          quality: "high",
        });

        // Load structure
        if (pdbData) {
          const blob = new Blob([pdbData], { type: "text/plain" });
          componentRef.current = await stageRef.current.loadFile(blob, { ext: "pdb" });
        } else if (smiles) {
          // For SMILES, we'd need a service to convert to 3D coords
          // For now, show a placeholder message
          setError("SMILES visualization requires 3D coordinate generation. Use PDB data for direct visualization.");
          return;
        }

        if (componentRef.current) {
          componentRef.current.addRepresentation(representation);
          stageRef.current.autoView();
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load 3D viewer");
      }
    };

    loadNGL();

    return () => {
      if (stageRef.current) {
        stageRef.current.dispose();
      }
    };
  }, [pdbData, smiles]);

  // Update representation
  useEffect(() => {
    if (componentRef.current && stageRef.current) {
      componentRef.current.removeAllRepresentations();
      componentRef.current.addRepresentation(representation);
    }
  }, [representation]);

  if (error) {
    return (
      <div
        style={{
          width: "100%",
          height,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "var(--bg-warm)",
          borderRadius: 4,
          color: "var(--text-muted)",
          fontSize: 13,
          padding: 20,
          textAlign: "center",
        }}
      >
        {error}
      </div>
    );
  }

  return (
    <div style={{ position: "relative" }}>
      <div
        ref={containerRef}
        style={{
          width: "100%",
          height,
          borderRadius: 4,
          overflow: "hidden",
        }}
      />
      <div
        style={{
          position: "absolute",
          bottom: 12,
          left: 12,
          display: "flex",
          gap: 6,
        }}
      >
        {/* Only show cartoon for proteins (ATOM records), not small molecules (HETATM only) */}
        {["ball+stick", "cartoon", "surface", "licorice"]
          .filter((rep) => rep !== "cartoon" || (pdbData && pdbData.includes("\nATOM ")))
          .map((rep) => (
          <button
            key={rep}
            className={`btn ${representation === rep ? "active" : ""}`}
            onClick={() => setRepresentation(rep)}
          >
            {rep.charAt(0).toUpperCase() + rep.slice(1).replace("+", " & ")}
          </button>
        ))}
      </div>
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function MoleculeViewer({
  toolInputs,
  toolInputsPartial,
  toolResult,
  hostContext,
  callServerTool,
  sendMessage,
  openLink: _openLink,
  sendLog,
}: MoleculeViewerProps) {
  const height = toolInputs?.height ?? toolInputsPartial?.height ?? 400;
  const name = toolInputs?.name ?? toolInputsPartial?.name;
  const isStreaming = !toolInputs && !toolResult;

  // State for on-demand 3D loading
  const [loading3D, setLoading3D] = useState(false);
  const [loaded3DData, setLoaded3DData] = useState<string | null>(null);
  const [error3D, setError3D] = useState<string | null>(null);
  // When the viewer is launched from a Core-server tool (get_molecule_info,
  // calculate_properties), get_3d_properties isn't registered — the button
  // click surfaces "-32602 Tool not found". We distinguish that case from
  // a real failure so the UI can render a gentle upgrade hint instead of
  // a red error banner.
  const [tierUnavailable, setTierUnavailable] = useState(false);

  // Safe area insets from host
  const safeAreaInsets = hostContext?.safeAreaInsets;
  const containerStyle = {
    paddingTop: safeAreaInsets?.top,
    paddingRight: safeAreaInsets?.right,
    paddingBottom: safeAreaInsets?.bottom,
    paddingLeft: safeAreaInsets?.left,
  };

  // Detect the specific MCP "method not found" / "tool not found" shape.
  // get_3d_properties lives on the Compute server only; when the viewer is
  // launched from a Core-server tool the click will hit this path and we
  // want the UI to treat it as "upgrade to see 3D" rather than error.
  //
  // Check multiple signals because the error can arrive in different shapes
  // depending on whether the MCP SDK wrapped it (McpError with .code),
  // returned it via CallToolResult.isError, or propagated the JSON-RPC
  // error text directly. Belt-and-suspenders against SDK-version drift.
  const isToolNotFoundError = (msg: string, err?: unknown): boolean => {
    const lower = (msg || "").toLowerCase();
    const code = (err as { code?: number } | undefined)?.code;
    return (
      code === -32601 || // "Method not found" per JSON-RPC spec
      code === -32602 || // "Invalid params" — observed for unregistered tool in some SDK versions
      lower.includes("not found") ||
      lower.includes("-32601") ||
      lower.includes("-32602") ||
      lower.includes("method not found") ||
      lower.includes("unknown tool") ||
      lower.includes("tool not registered")
    );
  };

  // Load 3D coordinates on demand
  const load3DStructure = async (smilesStr: string) => {
    if (!callServerTool) return;

    setLoading3D(true);
    setError3D(null);
    setTierUnavailable(false);

    try {
      sendLog?.({ level: "info", data: `Loading 3D structure for ${smilesStr}` });

      const result = await callServerTool({
        name: "get_3d_properties",
        arguments: {
          smiles: smilesStr,
          include_coordinates: true,
          optimize_3d: true
        }
      });

      // Parse CallToolResult - content is an array, may have text or structuredContent
      const callResult = result as any;

      // Check for error
      if (callResult?.isError) {
        const errorMsg = callResult?.content?.[0]?.text || "Tool returned an error";
        if (isToolNotFoundError(errorMsg, callResult)) {
          setTierUnavailable(true);
        } else {
          setError3D(errorMsg);
        }
        return;
      }

      // Extract data from result - try structuredContent first, then parse text content
      let data: any = null;
      if (callResult?.structuredContent) {
        data = callResult.structuredContent;
      } else if (callResult?.content?.[0]?.text) {
        try {
          data = JSON.parse(callResult.content[0].text);
        } catch {
          data = null;
        }
      }

      // Check for PDB data in various locations (API returns "pdb" not "pdb_data")
      if (data?.pdb) {
        setLoaded3DData(data.pdb);
      } else if (data?.pdb_data) {
        setLoaded3DData(data.pdb_data);
      } else if (data?.mol_block) {
        setLoaded3DData(data.mol_block);
      } else if (data?.coordinates) {
        // Try to construct PDB from coordinates
        setLoaded3DData(JSON.stringify(data.coordinates));
      } else {
        setError3D("3D coordinates not available.");
      }
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : "Failed to load 3D structure";
      if (isToolNotFoundError(errMsg, e)) {
        setTierUnavailable(true);
      } else {
        setError3D(errMsg);
      }
      sendLog?.({ level: "error", data: errMsg });
    } finally {
      setLoading3D(false);
    }
  };

  // Show loading while streaming
  if (isStreaming) {
    return (
      <div style={containerStyle}>
        <LoadingShimmer height={height} name={name} />
      </div>
    );
  }

  // Get data from toolResult.structuredContent (actual results) or fall back to toolInputs
  const resultData = (toolResult as any)?.structuredContent || toolInputs || {};
  const { smiles, pdb_data, admet, properties, compliance, structural_alerts, source, in_database } = resultData as MoleculeToolInput;
  const hasAdmet = admet && (admet.overall_toxicity_score !== undefined || Object.values(admet).some((v) => v !== undefined));

  // Use loaded 3D data if available, otherwise use pdb_data from original result
  const effectivePdbData = loaded3DData || pdb_data;
  const has3D = !!effectivePdbData; // Show 3D viewer if we have PDB data (original or loaded)

  return (
    <div className="molecule-viewer" style={containerStyle}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
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
            {name || "Molecule Viewer"}
          </div>
        </div>
        {smiles && (
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "var(--text-muted)",
              maxWidth: 300,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {smiles}
          </div>
        )}
      </div>

      {/* Main content */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: hasAdmet ? "1fr 240px" : "1fr",
          gap: 16,
        }}
      >
        {/* 3D Viewer or molecule summary */}
        <div>
          {has3D ? (
            <Viewer3D smiles={smiles} pdbData={effectivePdbData} height={height} />
          ) : (
            <div
              style={{
                width: "100%",
                minHeight: height,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                background: "var(--bg-warm)",
                borderRadius: 4,
                padding: 24,
              }}
            >
              {smiles ? (
                <>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                    SMILES Structure
                  </div>
                  <div style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 16,
                    color: "var(--text)",
                    padding: "12px 20px",
                    background: "var(--bg)",
                    borderRadius: 4,
                    border: "1px solid var(--border)",
                    maxWidth: "100%",
                    wordBreak: "break-all",
                    textAlign: "center",
                  }}>
                    {smiles}
                  </div>
                  {properties?.molecular_formula && (
                    <div style={{ marginTop: 12, fontSize: 14, color: "var(--text-soft)" }}>
                      {properties.molecular_formula}
                    </div>
                  )}

                  {/* Load 3D Structure Button - only show when no 3D data exists
                      and we haven't already discovered the tool isn't available
                      on this server tier. */}
                  {callServerTool !== undefined && !has3D && !loading3D && !error3D && !tierUnavailable && (
                    <button
                      onClick={() => load3DStructure(smiles)}
                      style={{
                        marginTop: 20,
                        padding: "10px 20px",
                        background: "var(--accent)",
                        color: "white",
                        border: "none",
                        borderRadius: 4,
                        fontSize: 13,
                        fontWeight: 500,
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                      }}
                    >
                      <span>Load 3D Structure</span>
                      <span style={{ fontSize: 10, opacity: 0.8 }}></span>
                    </button>
                  )}

                  {/* Loading state */}
                  {loading3D && (
                    <div style={{ marginTop: 20, display: "flex", alignItems: "center", gap: 8 }}>
                      <div className="loading-spinner" style={{ width: 16, height: 16 }} />
                      <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Generating 3D coordinates...</span>
                    </div>
                  )}

                  {/* Real error (not a tier/availability issue) */}
                  {error3D && (
                    <div style={{ marginTop: 16, padding: "8px 12px", background: "rgba(220, 38, 38, 0.1)", borderRadius: 4, fontSize: 12, color: "#dc2626" }}>
                      {error3D}
                    </div>
                  )}

                  {/* Gentle info hint when 3D generation isn't reachable from
                      THIS viewer instance. Replaces the red error banner for
                      the common case where the viewer was launched by a
                      Core-server tool (get_molecule_info / calculate_properties /
                      get_molecule_profile on Core) and get_3d_properties lives
                      on Compute only. MCP Apps viewers can only call tools on
                      the server that launched them, so even users who ALSO
                      have Compute connected can't reach get_3d_properties
                      from here — Claude needs to route the next call via
                      Compute (call get_3d_properties on compute.novomcp.com,
                      which mounts a viewer instance that CAN reach it). Note
                      get_molecule_profile is Core-only and would hit this same
                      gap, so the workaround names get_3d_properties, not the
                      profile tool. */}
                  {tierUnavailable && !loading3D && (
                    <div
                      style={{
                        marginTop: 16,
                        padding: "10px 14px",
                        background: "var(--bg-warm)",
                        borderLeft: "3px solid var(--accent)",
                        borderRadius: 2,
                        fontSize: 11,
                        color: "var(--text-soft)",
                        lineHeight: 1.5,
                        maxWidth: 420,
                      }}
                    >
                      <div style={{ fontWeight: 500, color: "var(--text)", marginBottom: 4 }}>
                        3D view unavailable from this result
                      </div>
                      <div style={{ marginBottom: 10 }}>
                        This viewer was mounted by a Core-server tool call, and{" "}
                        <code style={{ fontFamily: "var(--font-mono)" }}>get_3d_properties</code>{" "}
                        is Compute-only. MCP Apps widgets can only reach tools on the server
                        that launched them, so this specific card can't load 3D coordinates —
                        even if you have Novo Compute connected.
                      </div>
                      {/* If the host supports sendMessage, fire a one-click
                          prompt to Claude asking it to route get_3d_properties
                          via Compute. Claude.ai's UI exposes sendMessage to MCP
                          Apps; other surfaces may not — fall back to the
                          textual hint when it's undefined. */}
                      {sendMessage ? (
                        <>
                          <button
                            type="button"
                            onClick={() =>
                              sendMessage({
                                role: "user",
                                content: [
                                  {
                                    type: "text",
                                    text: `Get the 3D properties for \`${smiles}\` via Novo Compute (run get_3d_properties with include_coordinates=true and optimize_3d=true, then render the structure).`,
                                  },
                                ],
                              })
                            }
                            style={{
                              padding: "8px 14px",
                              background: "var(--accent)",
                              color: "white",
                              border: "none",
                              borderRadius: 3,
                              fontSize: 12,
                              fontWeight: 500,
                              cursor: "pointer",
                              marginBottom: 8,
                            }}
                          >
                            Ask Claude to compute 3D via Novo Compute
                          </button>
                          <div style={{ color: "var(--text-muted)", fontSize: 10 }}>
                            Requires Novo Compute connected. The{" "}
                            {hasAdmet ? "2D properties, ADMET, and compliance" : "2D properties"}{" "}
                            rendered here are complete on their own.
                          </div>
                        </>
                      ) : (
                        <div style={{ color: "var(--text-muted)" }}>
                          To see 3D: ask Claude to compute 3D properties via Novo Compute (e.g.{" "}
                          <em>"get the 3D properties for &lt;SMILES&gt;"</em>) — that runs{" "}
                          <code style={{ fontFamily: "var(--font-mono)" }}>get_3d_properties</code>{" "}
                          on Compute and renders the structure. The{" "}
                          {hasAdmet ? "2D properties, ADMET, and compliance" : "2D properties"}{" "}
                          rendered here are complete on their own.
                        </div>
                      )}
                    </div>
                  )}

                  {/* Fallback message when no button and no 3D */}
                  {callServerTool === undefined && !has3D && (
                    <div style={{ marginTop: 16, fontSize: 11, color: "var(--text-muted)" }}>
                      3D visualization requires coordinate data
                    </div>
                  )}
                </>
              ) : (
                <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
                  No structure data provided
                </div>
              )}
            </div>
          )}
        </div>

        {/* Sidebar with ADMET */}
        {hasAdmet && (
          <div>
            <div className="panel">
              <div className="panel-title">ADMET Profile</div>
              {/* Show radar if we have full ADMET data, otherwise show summary */}
              {admet?.absorption !== undefined ? (
                <AdmetRadar admet={admet} />
              ) : (
                <div style={{ padding: 12 }}>
                  {admet?.overall_toxicity_score !== undefined && (
                    <div style={{ marginBottom: 12 }}>
                      <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>Toxicity Score</div>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <div style={{
                          flex: 1,
                          height: 8,
                          background: "var(--bg-warm)",
                          borderRadius: 4,
                          overflow: "hidden"
                        }}>
                          <div style={{
                            width: `${(admet.overall_toxicity_score * 100)}%`,
                            height: "100%",
                            background: admet.overall_toxicity_score < 0.3 ? "var(--success)" : admet.overall_toxicity_score < 0.6 ? "var(--warning)" : "var(--danger)",
                            borderRadius: 4
                          }} />
                        </div>
                        <span style={{ fontSize: 12, fontFamily: "var(--font-mono)" }}>
                          {(admet.overall_toxicity_score * 100).toFixed(0)}%
                        </span>
                      </div>
                      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
                        {admet.overall_toxicity_score < 0.3 ? "Low risk" : admet.overall_toxicity_score < 0.6 ? "Moderate risk" : "High risk"}
                      </div>
                    </div>
                  )}
                  {admet?.is_aggregator_risk !== undefined && (
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <span className={`badge ${!admet.is_aggregator_risk ? "success" : "warning"}`}>
                        Aggregator {!admet.is_aggregator_risk ? "✓" : "Risk"}
                      </span>
                    </div>
                  )}
                </div>
              )}
            </div>
            <PropertiesPanel properties={properties} compliance={compliance} structuralAlerts={structural_alerts} />
          </div>
        )}
      </div>

      {/* Properties panel (if no ADMET sidebar) */}
      {!hasAdmet && (properties || compliance || structural_alerts) && (
        <PropertiesPanel properties={properties} compliance={compliance} structuralAlerts={structural_alerts} />
      )}

      {/* Source indicator */}
      {source && (
        <div style={{ marginTop: 12, fontSize: 10, color: "var(--text-muted)", display: "flex", gap: 8 }}>
          <span>Source: {source}</span>
          {in_database !== undefined && (
            <span>{in_database ? "✓ In database" : "Novel compound"}</span>
          )}
        </div>
      )}
    </div>
  );
}
