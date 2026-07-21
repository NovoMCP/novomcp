/**
 * NovoMCP Structure Viewer Component
 *
 * Protein structure visualization with confidence coloring,
 * secondary structure annotation, and interactive controls.
 */
import { useState, useEffect, useRef } from "react";
import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types
// =============================================================================

interface StructureToolInput {
  pdb_data?: string;
  pdb_url?: string;
  pdb_id?: string;
  pdb_size?: number;
  name?: string;
  sequence?: string;
  confidence_scores?: number[];
  secondary_structure?: {
    helices?: Array<{ start: number; end: number }>;
    sheets?: Array<{ start: number; end: number }>;
    loops?: Array<{ start: number; end: number }>;
  };
  metrics?: {
    plddt_mean?: number;
    ptm?: number;
    iptm?: number;
    clash_score?: number;
    ramachandran_favored?: number;
  };
  ligand_binding_sites?: Array<{
    residues: number[];
    confidence: number;
    ligand_type?: string;
  }>;
  job_id?: string;
  status?: string;
  message?: string;
  height?: number;
}

type StructureViewerProps = ViewProps<StructureToolInput>;

// =============================================================================
// Loading Shimmer
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
        {name ? `Loading ${name} structure...` : "Preparing structure viewer..."}
      </div>
    </div>
  );
}

// =============================================================================
// Confidence Legend
// =============================================================================

function ConfidenceLegend() {
  const colors = [
    { label: "Very High (>90)", color: "#0053D6" },
    { label: "High (70-90)", color: "#65CBF3" },
    { label: "Medium (50-70)", color: "#FFDB13" },
    { label: "Low (<50)", color: "#FF7D45" },
  ];

  return (
    <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
      {colors.map((c) => (
        <div key={c.label} style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <div
            style={{
              width: 12,
              height: 12,
              borderRadius: 2,
              background: c.color,
            }}
          />
          <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{c.label}</span>
        </div>
      ))}
    </div>
  );
}

// =============================================================================
// Metrics Panel
// =============================================================================

function MetricsPanel({ metrics }: { metrics?: StructureToolInput["metrics"] }) {
  if (!metrics) return null;

  const metricsList = [
    { label: "pLDDT", value: metrics.plddt_mean, format: (v: number) => v.toFixed(1), good: (v: number) => v > 70 },
    { label: "pTM", value: metrics.ptm, format: (v: number) => v.toFixed(3), good: (v: number) => v > 0.5 },
    { label: "ipTM", value: metrics.iptm, format: (v: number) => v.toFixed(3), good: (v: number) => v > 0.5 },
    { label: "Clash Score", value: metrics.clash_score, format: (v: number) => v.toFixed(1), good: (v: number) => v < 10 },
    { label: "Rama. Favored", value: metrics.ramachandran_favored, format: (v: number) => `${v.toFixed(1)}%`, good: (v: number) => v > 95 },
  ].filter((m) => m.value !== undefined);

  return (
    <div className="panel">
      <div className="panel-title">Structure Quality</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(100px, 1fr))", gap: 8 }}>
        {metricsList.map((m) => (
          <div
            key={m.label}
            style={{
              padding: "10px 12px",
              background: m.good(m.value!) ? "var(--success-bg)" : "var(--warning-bg)",
              borderRadius: 2,
              textAlign: "center",
            }}
          >
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>
              {m.label}
            </div>
            <div
              style={{
                fontSize: 16,
                fontFamily: "var(--font-mono)",
                fontWeight: 500,
                color: m.good(m.value!) ? "var(--success)" : "var(--warning)",
              }}
            >
              {m.format(m.value!)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// =============================================================================
// Sequence Viewer
// =============================================================================

function SequenceViewer({
  sequence,
  confidenceScores,
  secondaryStructure,
}: {
  sequence?: string;
  confidenceScores?: number[];
  secondaryStructure?: StructureToolInput["secondary_structure"];
}) {
  if (!sequence) return null;

  const getConfidenceColor = (score?: number) => {
    if (score === undefined) return "var(--text-muted)";
    if (score >= 90) return "#0053D6";
    if (score >= 70) return "#65CBF3";
    if (score >= 50) return "#FFDB13";
    return "#FF7D45";
  };

  const getSSType = (pos: number) => {
    if (secondaryStructure?.helices?.some((h) => pos >= h.start && pos <= h.end)) return "H";
    if (secondaryStructure?.sheets?.some((s) => pos >= s.start && pos <= s.end)) return "E";
    return "C";
  };

  const ssColors: Record<string, string> = {
    H: "var(--danger)",
    E: "var(--success)",
    C: "var(--text-muted)",
  };

  // Split into chunks of 10
  const chunkSize = 10;
  const chunks: string[] = [];
  for (let i = 0; i < sequence.length; i += chunkSize) {
    chunks.push(sequence.slice(i, i + chunkSize));
  }

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">Sequence ({sequence.length} residues)</div>
      <div
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          lineHeight: 2,
          overflowX: "auto",
          whiteSpace: "nowrap",
          padding: "8px 0",
        }}
      >
        {chunks.map((chunk, chunkIdx) => (
          <span key={chunkIdx} style={{ marginRight: 8 }}>
            {chunk.split("").map((aa, i) => {
              const pos = chunkIdx * chunkSize + i;
              const score = confidenceScores?.[pos];
              const ss = getSSType(pos);
              return (
                <span
                  key={pos}
                  style={{
                    color: getConfidenceColor(score),
                    borderBottom: `2px solid ${ssColors[ss]}`,
                    padding: "0 1px",
                  }}
                  title={`${aa}${pos + 1} - pLDDT: ${score?.toFixed(1) ?? "N/A"} - SS: ${ss}`}
                >
                  {aa}
                </span>
              );
            })}
          </span>
        ))}
      </div>
      <div style={{ display: "flex", gap: 16, marginTop: 8, fontSize: 10, color: "var(--text-muted)" }}>
        <span><span style={{ color: "var(--danger)" }}>━</span> Helix</span>
        <span><span style={{ color: "var(--success)" }}>━</span> Sheet</span>
        <span><span style={{ color: "var(--text-muted)" }}>━</span> Coil</span>
      </div>
    </div>
  );
}

// =============================================================================
// Binding Sites
// =============================================================================

function BindingSites({ sites }: { sites?: StructureToolInput["ligand_binding_sites"] }) {
  if (!sites || sites.length === 0) return null;

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">Predicted Binding Sites ({sites.length})</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {sites.map((site, idx) => (
          <div
            key={idx}
            style={{
              padding: "10px 14px",
              background: "var(--bg-warm)",
              borderRadius: 2,
              borderLeft: "3px solid var(--accent)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 12, fontWeight: 500, color: "var(--text)" }}>
                Site {idx + 1} {site.ligand_type && `(${site.ligand_type})`}
              </span>
              <span
                style={{
                  fontSize: 11,
                  fontFamily: "var(--font-mono)",
                  color: site.confidence > 0.7 ? "var(--success)" : "var(--warning)",
                }}
              >
                {(site.confidence * 100).toFixed(0)}% confidence
              </span>
            </div>
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
              Residues: {site.residues.slice(0, 10).join(", ")}
              {site.residues.length > 10 && ` +${site.residues.length - 10} more`}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// =============================================================================
// 3D Viewer
// =============================================================================

function Viewer3D({
  pdbData,
  pdbUrl,
  pdbId,
  height,
  jobId,
  status,
  message,
  sendMessage,
  structureName,
}: {
  pdbData?: string;
  pdbUrl?: string;
  pdbId?: string;
  height: number;
  jobId?: string;
  status?: string;
  message?: string;
  sendMessage?: StructureViewerProps["sendMessage"];
  structureName?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [colorScheme, setColorScheme] = useState<string>("bfactor");
  const [representation, setRepresentation] = useState<string>("cartoon");
  const [error, setError] = useState<string | null>(null);
  const [fetchedPdbData, setFetchedPdbData] = useState<string | null>(null);
  const [isFetching, setIsFetching] = useState(false);
  const [selectedResidue, setSelectedResidue] = useState<{
    resname: string;
    resno: number;
    chain: string;
    element: string;
  } | null>(null);
  const stageRef = useRef<any>(null);
  const componentRef = useRef<any>(null);
  const selectionReprRef = useRef<any>(null);
  // Debounce rapid clicks so NGL picker events don't spam the chat.
  const lastClickTimeRef = useRef<number>(0);

  // Fetch PDB from URL if provided and no inline data
  useEffect(() => {
    if (pdbUrl && !pdbData && !fetchedPdbData && !isFetching) {
      setIsFetching(true);
      fetch(pdbUrl)
        .then((res) => {
          if (!res.ok) throw new Error(`Failed to fetch PDB: ${res.status}`);
          return res.text();
        })
        .then((data) => {
          setFetchedPdbData(data);
          setIsFetching(false);
        })
        .catch((e) => {
          setError(e.message);
          setIsFetching(false);
        });
    }
  }, [pdbUrl, pdbData, fetchedPdbData, isFetching]);

  // Use either inline data or fetched data
  const effectivePdbData = pdbData || fetchedPdbData;

  useEffect(() => {
    if (!containerRef.current || !effectivePdbData) return;

    const loadNGL = async () => {
      try {
        const NGL = await import("ngl");

        const styles = getComputedStyle(document.documentElement);
        const bgColor = styles.getPropertyValue("--bg-card").trim() || "#FFFFFF";

        stageRef.current = new NGL.Stage(containerRef.current!, {
          backgroundColor: bgColor,
          quality: "high",
        });

        const blob = new Blob([effectivePdbData], { type: "text/plain" });
        componentRef.current = await stageRef.current.loadFile(blob, { ext: "pdb" });

        componentRef.current.addRepresentation(representation, {
          colorScheme: colorScheme,
        });
        stageRef.current.autoView();

        // Bidirectional state: NGL picker → Claude conversation
        // On residue click, pull the picked atom's context and post a new
        // user turn to Claude via sendMessage. Debounced so double-clicks
        // don't fire twice. See novomcp-apps/src/jobs.tsx for the shipping
        // pattern this follows.
        stageRef.current.signals.clicked.add((pickingProxy: any) => {
          if (!pickingProxy?.atom || !sendMessage) return;
          const now = Date.now();
          if (now - lastClickTimeRef.current < 400) return;
          lastClickTimeRef.current = now;

          const atom = pickingProxy.atom;
          const resname = atom.resname || "residue";
          const resno = atom.resno;
          const chain = atom.chainname || "";
          const element = atom.element || "";

          setSelectedResidue({ resname, resno, chain, element });

          const chainSuffix = chain ? ` chain ${chain}` : "";
          const structureRef = structureName || pdbId || "this structure";
          sendMessage({
            role: "user",
            content: [
              {
                type: "text",
                text:
                  `I clicked ${resname}${resno}${chainSuffix} in ${structureRef}. ` +
                  `What role does this residue play — is it catalytic, near a binding site, ` +
                  `structurally important, or disease-associated? ` +
                  `If relevant, call audit_system or get_protein_structure for more detail.`,
              },
            ],
          });
        });
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load structure viewer");
      }
    };

    loadNGL();

    return () => {
      if (stageRef.current) {
        stageRef.current.dispose();
      }
    };
  }, [effectivePdbData]);

  // Update representation and color scheme
  useEffect(() => {
    if (componentRef.current && stageRef.current) {
      componentRef.current.removeAllRepresentations();
      componentRef.current.addRepresentation(representation, {
        colorScheme: colorScheme,
      });
      // Rebuilding representations clears the selection overlay; drop the ref
      // so the selection-highlight effect below reattaches it.
      selectionReprRef.current = null;
    }
  }, [representation, colorScheme]);

  // Visual feedback for the clicked residue — ball+stick overlay in accent color.
  // Rebuilt whenever selection changes; cleared when selection is null.
  useEffect(() => {
    if (!componentRef.current || !stageRef.current) return;

    if (selectionReprRef.current) {
      componentRef.current.removeRepresentation(selectionReprRef.current);
      selectionReprRef.current = null;
    }

    if (selectedResidue) {
      const { resno, chain } = selectedResidue;
      const sel = chain ? `${resno} and :${chain}` : `${resno}`;
      selectionReprRef.current = componentRef.current.addRepresentation("ball+stick", {
        sele: sel,
        color: "#ff7f50",
        aspectRatio: 2.0,
      });
    }
  }, [selectedResidue]);

  // Show loading state while fetching from URL
  if (isFetching) {
    return (
      <div
        style={{
          width: "100%",
          height,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          background: "linear-gradient(135deg, var(--bg-warm) 0%, var(--bg) 100%)",
          borderRadius: 4,
          gap: 12,
          padding: 24,
        }}
      >
        <div className="loading-spinner" />
        <div style={{ color: "var(--text)", fontSize: 14, fontWeight: 500 }}>
          Fetching Structure from RCSB PDB
        </div>
        {pdbId && (
          <div style={{ color: "var(--text-muted)", fontSize: 12 }}>
            PDB ID: <code style={{ fontFamily: "var(--font-mono)", background: "var(--bg-code)", padding: "2px 6px", borderRadius: 2 }}>{pdbId}</code>
          </div>
        )}
      </div>
    );
  }

  if (!effectivePdbData) {
    // Case 1: Async job is still running
    if (jobId && (!status || status === "running" || status === "pending" || status === "submitted")) {
      return (
        <div
          style={{
            width: "100%",
            height,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            background: "linear-gradient(135deg, var(--bg-warm) 0%, var(--bg) 100%)",
            borderRadius: 4,
            gap: 16,
            padding: 24,
            textAlign: "center",
          }}
        >
          <div className="loading-spinner" />
          <div style={{ color: "var(--text)", fontSize: 14, fontWeight: 500 }}>
            Structure Prediction In Progress
          </div>
          <div style={{ color: "var(--text-muted)", fontSize: 12 }}>
            Job ID: <code style={{ fontFamily: "var(--font-mono)", background: "var(--bg-code)", padding: "2px 6px", borderRadius: 2 }}>{jobId}</code>
          </div>
          <div style={{ color: "var(--text-muted)", fontSize: 11, maxWidth: 300 }}>
            {message || "Ask Claude to check the job status using get_job_status or get_structure_result"}
          </div>
        </div>
      );
    }

    // Case 2: Job completed but no structure data (might be truncated)
    if (jobId && status === "completed") {
      return (
        <div
          style={{
            width: "100%",
            height,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            background: "var(--bg-warm)",
            borderRadius: 4,
            gap: 12,
            padding: 24,
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 24 }}>⚠️</div>
          <div style={{ color: "var(--warning)", fontSize: 14, fontWeight: 500 }}>
            Structure Data Not Available
          </div>
          <div style={{ color: "var(--text-muted)", fontSize: 12, maxWidth: 300 }}>
            The result may have been too large. Ask Claude to retrieve the structure using:
          </div>
          <code style={{ fontFamily: "var(--font-mono)", fontSize: 11, background: "var(--bg-code)", padding: "8px 12px", borderRadius: 2 }}>
            get_structure_result job_id: {jobId}
          </code>
        </div>
      );
    }

    // Case 3: Fetch error (show actual error message)
    if (error) {
      return (
        <div
          style={{
            width: "100%",
            height,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            background: "var(--bg-warm)",
            borderRadius: 4,
            gap: 12,
            padding: 24,
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 24 }}>⚠️</div>
          <div style={{ color: "var(--danger)", fontSize: 14, fontWeight: 500 }}>
            Failed to Load Structure
          </div>
          <div style={{ color: "var(--text-muted)", fontSize: 12, maxWidth: 300 }}>
            {error}
          </div>
          {pdbUrl && (
            <div style={{ color: "var(--text-muted)", fontSize: 11 }}>
              URL: <code style={{ fontFamily: "var(--font-mono)", background: "var(--bg-code)", padding: "2px 6px", borderRadius: 2 }}>{pdbUrl}</code>
            </div>
          )}
        </div>
      );
    }

    // Case 4: Generic no data
    return (
      <div
        style={{
          width: "100%",
          height,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          background: "var(--bg-warm)",
          borderRadius: 4,
          color: "var(--text-muted)",
          fontSize: 13,
          gap: 8,
          padding: 24,
          textAlign: "center",
        }}
      >
        <div style={{ fontSize: 20 }}>🔬</div>
        <div>No structure data available</div>
        {message && <div style={{ fontSize: 11 }}>{message}</div>}
        {pdbUrl && (
          <div style={{ marginTop: 8, fontSize: 11 }}>
            Attempting to fetch from: <code style={{ fontFamily: "var(--font-mono)" }}>{pdbUrl}</code>
          </div>
        )}
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
      {/* Controls */}
      <div
        style={{
          position: "absolute",
          bottom: 12,
          left: 12,
          display: "flex",
          gap: 6,
        }}
      >
        {["cartoon", "surface", "ribbon", "ball+stick"].map((rep) => (
          <button
            key={rep}
            className={`btn ${representation === rep ? "active" : ""}`}
            onClick={() => setRepresentation(rep)}
          >
            {rep.charAt(0).toUpperCase() + rep.slice(1).replace("+", " & ")}
          </button>
        ))}
      </div>
      <div
        style={{
          position: "absolute",
          bottom: 12,
          right: 12,
          display: "flex",
          gap: 6,
        }}
      >
        {[
          { key: "bfactor", label: "Confidence" },
          { key: "chainid", label: "Chain" },
          { key: "residueindex", label: "Position" },
          { key: "sstruc", label: "Secondary" },
        ].map((cs) => (
          <button
            key={cs.key}
            className={`btn ${colorScheme === cs.key ? "active" : ""}`}
            onClick={() => setColorScheme(cs.key)}
          >
            {cs.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function StructureViewer({
  toolInputs,
  toolInputsPartial,
  toolResult,
  sendMessage,
}: StructureViewerProps) {
  const height = toolInputs?.height ?? toolInputsPartial?.height ?? 500;
  const name = toolInputs?.name ?? toolInputsPartial?.name;
  const isStreaming = !toolInputs && !toolResult;

  if (isStreaming) {
    return <LoadingShimmer height={height} name={name} />;
  }

  const resultData = useViewData<Record<string, any>>({ toolInputs, toolResult });
  const {
    pdb_data,
    pdb_url,
    pdb_id,
    sequence,
    confidence_scores,
    secondary_structure,
    metrics,
    ligand_binding_sites,
    job_id,
    status,
    message,
  } = resultData as StructureToolInput;

  return (
    <div className="structure-viewer" style={{ width: "100%" }}>
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
            {name || "Structure Viewer"}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {job_id && (
            <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
              Job: {job_id}
            </span>
          )}
        </div>
      </div>

      {/* Confidence Legend */}
      <div style={{ marginBottom: 16 }}>
        <ConfidenceLegend />
      </div>

      {/* 3D Viewer */}
      <Viewer3D
        pdbData={pdb_data}
        pdbUrl={pdb_url}
        pdbId={pdb_id}
        height={height - 150}
        jobId={job_id}
        status={status}
        message={message}
        sendMessage={sendMessage}
        structureName={name}
      />

      {/* Metrics */}
      <div style={{ marginTop: 16 }}>
        <MetricsPanel metrics={metrics} />
      </div>

      {/* Sequence */}
      <SequenceViewer
        sequence={sequence}
        confidenceScores={confidence_scores}
        secondaryStructure={secondary_structure}
      />

      {/* Binding Sites */}
      <BindingSites sites={ligand_binding_sites} />
    </div>
  );
}
