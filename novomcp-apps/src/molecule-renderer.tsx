/**
 * Shared NGL-backed 3D molecule renderer.
 *
 * Consolidates the dynamic-import + Stage setup + loadFile + error
 * handling that transition-state, generate-dynamics, and nnp-results
 * were each re-implementing. Handles:
 *   - Non-Error throws from NGL (surfaces the real reason rather than
 *     "Failed to render geometry")
 *   - React strict-mode double-mount races (cancelled guard)
 *   - Malformed XYZ (header-count validation before NGL gets invoked)
 *   - Dispose safety on unmount
 *
 * Consumers that need imperative access to the Stage/component (e.g.
 * generate-dynamics for trajectory playback) pass an onReady callback
 * which receives the live refs once the load settles. Static consumers
 * just render and forget.
 *
 * structure-viewer does NOT use this — its representation-switching,
 * URL-fetch, and picker-click logic all run against the same refs and
 * would require a bigger imperative handle than is worth the coupling.
 */

import { useEffect, useRef, useState } from "react";

export interface MoleculeRef {
  stage: any;
  component: any;
  trajectory: any | null;
}

export interface MoleculeRendererProps {
  /** XYZ file contents. Mutually exclusive with `pdb`. */
  xyz?: string;
  /** PDB file contents. Mutually exclusive with `xyz`. */
  pdb?: string;
  /** Render height in px. Width is 100% of parent. */
  height: number;
  /** Load as trajectory (multi-model PDB → frame iteration). */
  asTrajectory?: boolean;
  /** NGL representation type. */
  representation?: "ball+stick" | "cartoon" | "licorice" | "spacefill";
  /** Extra params forwarded to addRepresentation. */
  representationParams?: Record<string, unknown>;
  /** Called once load settles; use for trajectory playback, picker wiring, etc. */
  onReady?: (ref: MoleculeRef) => void;
}

// Surface the real thrown value — NGL throws non-Error objects on parse
// failures, and the generic fallback masked the true reason.
function describeError(e: unknown): string {
  if (e instanceof Error) return e.message || e.name || "unknown error";
  if (typeof e === "string") return e;
  if (e && typeof e === "object") {
    const anyE = e as any;
    if (typeof anyE.message === "string") return anyE.message;
    try {
      return JSON.stringify(e).slice(0, 160);
    } catch {
      return String(e);
    }
  }
  return String(e);
}

// XYZ sanity check — NGL silently fails on malformed input. Enforcing the
// N-atoms header matches the actual atom-line count turns "Failed to render"
// into an actionable error.
function validateXyz(xyz: string): string | null {
  const lines = xyz.trim().split(/\r?\n/);
  const declared = parseInt(lines[0] ?? "", 10);
  const bodyLines = lines.slice(2).filter((l) => l.trim().length > 0);
  if (!Number.isFinite(declared) || declared <= 0) {
    return `XYZ missing atom-count header (got "${lines[0]?.slice(0, 40)}")`;
  }
  if (bodyLines.length !== declared) {
    return `XYZ header says ${declared} atoms but ${bodyLines.length} atom lines present`;
  }
  return null;
}

// NGL 2.4.0 ships no XYZ parser — passing `{ ext: "xyz" }` throws
// `autoLoad: ext 'xyz' unknown`. Convert to a minimal single-residue PDB so
// NGL's pdb parser (which IS registered) can render the geometry. The PDB is
// synthetic: all atoms in one chain "A", residue "MOL 1", HETATM records, no
// CONECT (NGL infers bonds from distance for ball+stick). Good enough for
// small-molecule geometry display.
function xyzToPdb(xyz: string): string {
  const lines = xyz.trim().split(/\r?\n/);
  const n = parseInt(lines[0] ?? "0", 10);
  const atomLines = lines.slice(2, 2 + n);
  const pdb: string[] = [];
  atomLines.forEach((line, i) => {
    const parts = line.trim().split(/\s+/);
    if (parts.length < 4) return;
    const sym = parts[0].slice(0, 2);
    const x = parseFloat(parts[1]);
    const y = parseFloat(parts[2]);
    const z = parseFloat(parts[3]);
    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) return;
    const serial = String(i + 1).padStart(5, " ");
    // Atom name is element + index, left-justified in 4-char field
    const atomName = (sym + String(i + 1)).slice(0, 4).padEnd(4, " ");
    const xs = x.toFixed(3).padStart(8, " ");
    const ys = y.toFixed(3).padStart(8, " ");
    const zs = z.toFixed(3).padStart(8, " ");
    const element = sym.toUpperCase().padStart(2, " ");
    // PDB HETATM format — columns matter: the fixed-width layout is what
    // NGL's parser expects.
    pdb.push(
      `HETATM${serial} ${atomName} MOL A   1    ${xs}${ys}${zs}  1.00  0.00          ${element}`,
    );
  });
  pdb.push("END");
  return pdb.join("\n");
}

export default function MoleculeRenderer({
  xyz,
  pdb,
  height,
  asTrajectory = false,
  representation = "ball+stick",
  representationParams,
  onReady,
}: MoleculeRendererProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<any>(null);
  const [error, setError] = useState<string | null>(null);

  // XYZ isn't supported by NGL 2.4.0 — transparently convert to PDB so
  // consumers can keep passing native XYZ strings (from xTB, NNP, NEB, etc.)
  // without knowing NGL's format limitations.
  const payload = xyz ? xyzToPdb(xyz) : pdb;
  const loadExt: "pdb" = "pdb";

  useEffect(() => {
    if (!containerRef.current || !payload) return;
    let cancelled = false;

    if (xyz) {
      const xyzErr = validateXyz(xyz);
      if (xyzErr) {
        setError(xyzErr);
        return;
      }
    }

    const load = async () => {
      try {
        const NGL = await import("ngl");
        if (cancelled) return;

        const styles = getComputedStyle(document.documentElement);
        const bgColor = styles.getPropertyValue("--bg-card").trim() || "#FFFFFF";

        stageRef.current = new NGL.Stage(containerRef.current!, {
          backgroundColor: bgColor,
          quality: "high",
        });
        const blob = new Blob([payload], { type: "text/plain" });
        const comp = await stageRef.current.loadFile(blob, {
          ext: loadExt,
          ...(asTrajectory ? { asTrajectory: true } : {}),
        });
        if (cancelled) return;

        const defaultParams =
          representation === "ball+stick" ? { aspectRatio: 2.0 } : {};
        comp.addRepresentation(representation, {
          ...defaultParams,
          ...(representationParams ?? {}),
        });

        let trajectory: any = null;
        if (asTrajectory) {
          const traj = comp.addTrajectory();
          trajectory = traj?.trajectory ?? null;
        }

        stageRef.current.autoView();

        onReady?.({
          stage: stageRef.current,
          component: comp,
          trajectory,
        });
      } catch (e) {
        if (cancelled) return;
        console.error("[MoleculeRenderer] NGL render error:", e);
        setError(describeError(e));
      }
    };
    load();

    return () => {
      cancelled = true;
      try {
        stageRef.current?.dispose();
      } catch { /* ignore dispose errors */ }
    };
    // Intentionally omit onReady / representationParams — consumers that need
    // to rebuild on prop change should remount. Including them would cause a
    // full Stage dispose/re-create on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payload, asTrajectory, representation]);

  if (error) {
    return (
      <div
        style={{
          padding: 16,
          background: "var(--bg-warm)",
          borderRadius: 2,
          fontSize: 12,
          color: "var(--text-muted)",
          textAlign: "center",
        }}
      >
        Could not render geometry: {error}
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      style={{
        width: "100%",
        height,
        borderRadius: 2,
        overflow: "hidden",
        background: "var(--bg-card, transparent)",
      }}
    />
  );
}
