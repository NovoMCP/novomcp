import React from "react";
import { useViewData } from "./use-view-data.ts";
import type { ViewProps } from "./create-app.tsx";

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */

interface ClusterStats {
  mw_min?: number;
  mw_max?: number;
  mw_mean?: number;
  qed_mean?: number;
  qed_max?: number;
  toxicity_mean?: number;
  gi_high_pct?: number;
  bbb_yes_pct?: number;
  pains_clean_pct?: number;
  brenk_clean_pct?: number;
  clean_pct?: number;
  alert_free_pct?: number;
  controlled_count?: number;
}

interface Cluster {
  cluster_id?: string;
  id?: string;
  description?: string;
  molecule_count?: number;
  stats?: ClusterStats;
  top_scaffolds?: Record<string, number>;
  sample_cids?: string[];
  children_count?: number;
  similarity?: number | null;
}

interface ClusterExplorerInput {
  level?: number;
  total_regions?: number;
  total_children?: number;
  query?: string;
  regions?: Cluster[];
  children?: Cluster[];
  parent_cluster?: string;
  parent_description?: string;
  child_level?: number;
  navigation_hint?: string;
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

const fmt = (n: number | undefined | null, d = 1) =>
  n != null ? n.toFixed(d) : "—";

const pct = (n: number | undefined | null) =>
  n != null ? `${n.toFixed(0)}%` : "—";

const StatBar: React.FC<{ label: string; value?: number | null; max?: number; color?: string }> = ({
  label, value, max = 100, color = "var(--accent, #B8704B)",
}) => {
  if (value == null) return null;
  const w = Math.min(100, (value / max) * 100);
  return (
    <div style={{ marginBottom: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--text-muted, #888)" }}>
        <span>{label}</span>
        <span>{label.includes("%") || label.includes("pct") ? pct(value) : fmt(value)}</span>
      </div>
      <div style={{ height: 4, background: "var(--border, #e0dcd6)", borderRadius: 2 }}>
        <div style={{ height: 4, width: `${w}%`, background: color, borderRadius: 2 }} />
      </div>
    </div>
  );
};

const Chip: React.FC<{ text: string }> = ({ text }) => (
  <span
    style={{
      display: "inline-block",
      padding: "1px 6px",
      margin: "1px 2px",
      fontSize: 10,
      borderRadius: 3,
      background: "var(--bg-warm, #f8f6f3)",
      border: "1px solid var(--border, #e0dcd6)",
      color: "var(--text-muted, #888)",
    }}
  >
    {text}
  </span>
);

/* ------------------------------------------------------------------ */
/* Main Component                                                      */
/* ------------------------------------------------------------------ */

export default function ClusterExplorer(props: ViewProps) {
  const data = useViewData<ClusterExplorerInput>(props);
  const sendMessage = props.sendMessage as ((msg: { role: string; content: { type: string; text: string }[] }) => void) | undefined;

  const clusters: Cluster[] = data?.regions ?? data?.children ?? [];
  const isExplore = !!data?.regions;
  const isDrill = !!data?.children;
  const parentId = data?.parent_cluster;
  const childLevel = data?.child_level ?? (isExplore ? 1 : 2);
  const isLeaf = childLevel >= 3;

  if (!clusters.length) {
    return (
      <div style={{ padding: 16, color: "var(--text-muted, #888)", fontStyle: "italic" }}>
        No clusters found. Try broadening your query or relaxing constraints.
      </div>
    );
  }

  const handleDrill = (cluster: Cluster) => {
    if (!sendMessage) return;
    const cid = cluster.cluster_id || cluster.id || "";
    if (isLeaf && cluster.sample_cids?.length) {
      sendMessage({
        role: "user",
        content: [{ type: "text", text:
          `Compare the molecules in cluster ${cid}: ${cluster.sample_cids.slice(0, 10).join(", ")}. ` +
          `Rank by QED and show full profiles.`
        }],
      });
    } else {
      sendMessage({
        role: "user",
        content: [{ type: "text", text:
          `Drill into cluster ${cid} to see its sub-clusters. ` +
          (cluster.description ? `This region: ${cluster.description.slice(0, 120)}` : "")
        }],
      });
    }
  };

  return (
    <div style={{ padding: 12, fontFamily: "var(--font-sans, system-ui, sans-serif)", background: "var(--bg)" }}>
      {/* Header */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text, #2D2A26)" }}>
          {isExplore ? "Chemical Space Regions" : `Sub-clusters of ${parentId}`}
        </div>
        {data?.parent_description && (
          <div style={{ fontSize: 11, color: "var(--text-muted, #888)", marginTop: 2 }}>
            {data.parent_description}
          </div>
        )}
        <div style={{ fontSize: 11, color: "var(--text-muted, #888)", marginTop: 2 }}>
          Level {childLevel} &middot; {clusters.length} clusters
          {data?.query && <> &middot; Query: &ldquo;{data.query}&rdquo;</>}
        </div>
      </div>

      {/* Cluster Cards */}
      <div style={{ display: "grid", gap: 8, gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
        {clusters.map((cluster, i) => {
          const cid = cluster.cluster_id || cluster.id || `cluster-${i}`;
          const stats = cluster.stats || {};
          const scaffolds = cluster.top_scaffolds || {};
          const topScaffoldNames = Object.entries(scaffolds)
            .sort(([, a], [, b]) => (b as number) - (a as number))
            .slice(0, 5)
            .map(([name]) => name);

          return (
            <div
              key={cid}
              onClick={() => handleDrill(cluster)}
              style={{
                padding: 10,
                border: "1px solid var(--border, #e0dcd6)",
                borderRadius: 6,
                cursor: sendMessage ? "pointer" : "default",
                background: "var(--bg-white, #fff)",
                transition: "border-color 0.15s",
              }}
              title={sendMessage ? (isLeaf ? "Click to compare molecules" : "Click to drill deeper") : undefined}
              onMouseOver={(e) => { if (sendMessage) (e.currentTarget as HTMLDivElement).style.borderColor = "var(--accent, #B8704B)"; }}
              onMouseOut={(e) => { (e.currentTarget as HTMLDivElement).style.borderColor = "var(--border, #e0dcd6)"; }}
            >
              {/* Cluster ID + Molecule Count */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text, #2D2A26)" }}>{cid}</span>
                <span style={{ fontSize: 10, color: "var(--accent, #B8704B)", fontWeight: 500 }}>
                  {cluster.molecule_count != null ? `${(cluster.molecule_count / 1000).toFixed(0)}K mols` : ""}
                </span>
              </div>

              {/* Similarity badge */}
              {cluster.similarity != null && (
                <div style={{
                  display: "inline-block", fontSize: 10, padding: "1px 5px", marginBottom: 4,
                  borderRadius: 3, background: cluster.similarity > 0.7 ? "#d4edda" : "#fff3cd",
                  color: cluster.similarity > 0.7 ? "#155724" : "#856404",
                }}>
                  Similarity: {(cluster.similarity).toFixed(3)}
                </div>
              )}

              {/* Description */}
              {cluster.description && (
                <div style={{ fontSize: 11, color: "var(--text-muted, #888)", marginBottom: 6, lineHeight: 1.35 }}>
                  {cluster.description.length > 160 ? cluster.description.slice(0, 160) + "..." : cluster.description}
                </div>
              )}

              {/* Stats */}
              <div style={{ marginBottom: 4 }}>
                {stats.mw_mean != null && (
                  <div style={{ fontSize: 10, color: "var(--text-muted, #888)", marginBottom: 2 }}>
                    MW: {fmt(stats.mw_min, 0)}–{fmt(stats.mw_max, 0)} (avg {fmt(stats.mw_mean, 0)})
                  </div>
                )}
                <StatBar label="QED (mean)" value={stats.qed_mean} max={1} />
                <StatBar label="Clean %" value={stats.clean_pct} color="#28a745" />
                <StatBar label="GI absorption %" value={stats.gi_high_pct} color="#17a2b8" />
                <StatBar label="BBB penetrant %" value={stats.bbb_yes_pct} color="#6f42c1" />
                <StatBar label="PAINS-free %" value={stats.pains_clean_pct} color="#28a745" />
              </div>

              {/* Top scaffolds */}
              {topScaffoldNames.length > 0 && (
                <div style={{ marginTop: 4 }}>
                  {topScaffoldNames.map((s) => <Chip key={s} text={s} />)}
                </div>
              )}

              {/* Leaf level: show sample CIDs */}
              {isLeaf && cluster.sample_cids && cluster.sample_cids.length > 0 && (
                <div style={{ fontSize: 10, color: "var(--text-muted, #888)", marginTop: 4 }}>
                  Sample CIDs: {cluster.sample_cids.slice(0, 5).join(", ")}
                  {cluster.sample_cids.length > 5 && ` +${cluster.sample_cids.length - 5} more`}
                </div>
              )}

              {/* Children count */}
              {!isLeaf && cluster.children_count != null && cluster.children_count > 0 && (
                <div style={{ fontSize: 10, color: "var(--accent, #B8704B)", marginTop: 4 }}>
                  {cluster.children_count} sub-clusters →
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Navigation hint */}
      {data?.navigation_hint && (
        <div style={{ marginTop: 10, fontSize: 11, color: "var(--text-muted, #888)", fontStyle: "italic" }}>
          {data.navigation_hint}
        </div>
      )}
    </div>
  );
}
