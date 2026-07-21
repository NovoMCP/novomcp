/**
 * NovoMCP Target Validation Viewer
 *
 * Renders the adversarial target-validation report from validate_target:
 *   - Confidence gauge + recommendation verdict (proceed / proceed-with-
 *     caution / reconsider) as the headline verdict.
 *   - Four evidence-stream cards (omics / clinical trials / literature /
 *     ChEMBL) showing the supporting vs contradicting counts each stream
 *     contributed.
 *   - Risk factors and strengths rendered as bullet chips in parallel
 *     panels so the "why" of the verdict is immediately visible.
 *   - Click-to-ask on the verdict card, each evidence stream, and each
 *     individual risk/strength so the user can drill down.
 *
 * This is a flagship funnel-entry-point viewer — validate_target is the
 * human-decision gate between target_discovery and committing compute
 * credits on docking + MD.
 */

import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types (mirrors validate_target's response assembly in tools.py)
// =============================================================================

interface OmicsEvidence {
  composite_score?: number;
  genetic_score?: number | null;
  expression_score?: number | null;
  tractable?: boolean;
  known_drugs?: number;
  high_competition?: boolean;
  suggested_pdb_id?: string | null;
}

interface TrialEntry {
  nct_id?: string;
  title?: string;
  phase?: string;
  status?: string;
  reason?: string;
}

interface ClinicalTrialsEvidence {
  completed?: number;
  terminated?: number;
  phase3_failures?: number;
  key_failures?: TrialEntry[];
  key_successes?: TrialEntry[];
}

interface LiteraturePaper {
  title?: string;
  year?: number | string;
  journal?: string;
  url?: string;
}

interface LiteratureEvidence {
  supporting_papers?: number;
  contradicting_papers?: number;
  top_supporting?: LiteraturePaper[];
  top_contradicting?: LiteraturePaper[];
}

interface ChemblEvidence {
  activity_count?: number;
  best_pchembl?: number;
  assay_types?: string[];
}

interface ValidateTargetInput {
  target?: string;
  disease?: string;
  confidence_score?: number;
  confidence_level?: "high" | "medium" | "low" | string;
  recommendation?: "proceed" | "proceed_with_caution" | "reconsider" | string;
  /**
   * Context-aware maturity classification. mature_validated targets (>5 approved
   * drugs AND >=50 completed trials) don't get penalized for high competition or
   * terminated-trial counts — those are hallmarks of a proven druggable target,
   * not warnings. The viewer surfaces the classification next to the confidence
   * level so the reader knows which scoring regime applied.
   */
  target_maturity?: "mature_validated" | "emerging" | "novel" | string;
  evidence?: {
    omics?: OmicsEvidence;
    clinical_trials?: ClinicalTrialsEvidence;
    literature?: LiteratureEvidence;
    chembl?: ChemblEvidence;
  };
  risk_factors?: string[];
  strengths?: string[];
  partial_data?: string[] | null;
}

type ValidateTargetProps = ViewProps<ValidateTargetInput>;

// =============================================================================
// Verdict styling
// =============================================================================

function verdictStyle(recommendation?: string): {
  color: string;
  bgVar: string;
  label: string;
} {
  switch (recommendation) {
    case "proceed":
      return { color: "var(--success)", bgVar: "var(--success-bg)", label: "Proceed" };
    case "proceed_with_caution":
      return { color: "var(--warning)", bgVar: "var(--warning-bg)", label: "Proceed with caution" };
    case "reconsider":
      return { color: "var(--danger)", bgVar: "var(--danger-bg)", label: "Reconsider" };
    default:
      return { color: "var(--text-muted)", bgVar: "var(--bg-warm)", label: recommendation || "—" };
  }
}

// =============================================================================
// Confidence gauge (horizontal bar with color-banded thresholds)
// =============================================================================

function ConfidenceGauge({ score }: { score: number }) {
  const width = 520;
  const height = 56;
  const padL = 16;
  const padR = 16;
  const trackY = 26;
  const trackH = 8;
  const usableW = width - padL - padR;
  const clamped = Math.max(0, Math.min(1, score));
  const markerX = padL + clamped * usableW;

  // Thresholds (from validate_target scoring calibration): 0.70+ high, 0.40-0.70 medium, <0.40 low
  const lowEnd = padL + 0.4 * usableW;
  const highEnd = padL + 0.7 * usableW;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: "auto", display: "block" }}>
      {/* Bands */}
      <rect x={padL} y={trackY} width={lowEnd - padL} height={trackH} fill="var(--danger)" opacity={0.35} />
      <rect x={lowEnd} y={trackY} width={highEnd - lowEnd} height={trackH} fill="var(--warning)" opacity={0.35} />
      <rect x={highEnd} y={trackY} width={padL + usableW - highEnd} height={trackH} fill="var(--success)" opacity={0.35} />

      {/* Marker pin + score label */}
      <line x1={markerX} x2={markerX} y1={trackY - 5} y2={trackY + trackH + 5} stroke="var(--text)" strokeWidth={2} />
      <circle cx={markerX} cy={trackY - 8} r={5} fill="var(--text)" />
      <text x={markerX} y={trackY - 16} textAnchor="middle" fontSize={11} fontWeight={600} fontFamily="var(--font-mono)" fill="var(--text)">
        {score.toFixed(2)}
      </text>

      {/* Axis ticks */}
      {[0, 0.4, 0.7, 1.0].map((v) => (
        <text key={v} x={padL + v * usableW} y={trackY + trackH + 14} textAnchor="middle" fontSize={9} fill="var(--text-muted)" fontFamily="var(--font-mono)">
          {v.toFixed(1)}
        </text>
      ))}
    </svg>
  );
}

// =============================================================================
// Evidence stream card
// =============================================================================

function EvidenceCard({
  title,
  weight,
  onClick,
  children,
}: {
  title: string;
  weight: string;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className="panel"
      onClick={onClick}
      style={{
        cursor: onClick ? "pointer" : undefined,
        transition: "border-color 200ms",
      }}
      title={onClick ? `Click to ask Claude about ${title.toLowerCase()} evidence` : undefined}
    >
      <div
        className="panel-title"
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
      >
        <span>{title}</span>
        <span style={{ fontSize: 9, fontWeight: 400, color: "var(--text-muted)" }}>{weight}</span>
      </div>
      {children}
    </div>
  );
}

function MetricRow({ label, value, emphasis }: { label: string; value: string | number; emphasis?: "good" | "bad" | "neutral" }) {
  const color =
    emphasis === "good" ? "var(--success)" :
    emphasis === "bad" ? "var(--danger)" :
    "var(--text)";
  return (
    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginTop: 4 }}>
      <span style={{ color: "var(--text-muted)" }}>{label}</span>
      <span style={{ color, fontFamily: "var(--font-mono)", fontWeight: 500 }}>{value}</span>
    </div>
  );
}

// =============================================================================
// Bullet list panel for risks / strengths
// =============================================================================

function BulletPanel({
  title,
  items,
  color,
  onClickItem,
}: {
  title: string;
  items: string[];
  color: string;
  onClickItem?: (item: string) => void;
}) {
  if (items.length === 0) {
    return (
      <div className="panel" style={{ borderLeft: `3px solid ${color}` }}>
        <div className="panel-title">{title}</div>
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>None reported.</div>
      </div>
    );
  }
  return (
    <div className="panel" style={{ borderLeft: `3px solid ${color}` }}>
      <div
        className="panel-title"
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
      >
        <span>{title} ({items.length})</span>
        <span style={{ fontSize: 9, fontWeight: 400, color: "var(--text-muted)" }}>click to ask</span>
      </div>
      <ul style={{ margin: 0, paddingLeft: 16, listStyle: "none" }}>
        {items.map((item, i) => (
          <li
            key={i}
            onClick={onClickItem ? () => onClickItem(item) : undefined}
            style={{
              fontSize: 12,
              lineHeight: 1.5,
              color: "var(--text)",
              padding: "4px 0",
              position: "relative",
              paddingLeft: 14,
              cursor: onClickItem ? "pointer" : undefined,
            }}
          >
            <span
              style={{
                position: "absolute",
                left: 0,
                top: 10,
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: color,
              }}
            />
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

// =============================================================================
// Main viewer
// =============================================================================

export default function ValidateTargetViewer(props: ValidateTargetProps) {
  const { toolInputs, toolResult, sendMessage } = props;
  const data = useViewData<ValidateTargetInput>(props);

  const isStreaming = !toolInputs && !toolResult;
  if (isStreaming) {
    return (
      <div className="loading">
        <div className="loading-spinner" />
        <span>Validating target…</span>
      </div>
    );
  }

  const target = data.target || toolInputs?.target || "Target";
  const disease = data.disease || toolInputs?.disease || "—";
  const score = data.confidence_score ?? 0;
  const level = data.confidence_level || "";
  const verdict = verdictStyle(data.recommendation);
  const ev = data.evidence || {};
  const omics = ev.omics || {};
  const trials = ev.clinical_trials || {};
  const lit = ev.literature || {};
  const chembl = ev.chembl || {};

  const askVerdict = () => {
    sendMessage({
      role: "user",
      content: [
        {
          type: "text",
          text:
            `The adversarial validation of ${target} for ${disease} came back with a ` +
            `${verdict.label} verdict (confidence ${score.toFixed(2)}, ${level}). ` +
            `Walk me through the reasoning — which evidence streams drove this, and is ` +
            `this strong enough to commit compute credits to docking + MD, or should I ` +
            `look at a different target?`,
        },
      ],
    });
  };

  const askStream = (stream: string, extra: string) => () => {
    sendMessage({
      role: "user",
      content: [
        {
          type: "text",
          text:
            `Dig deeper into the ${stream} evidence for ${target} ${disease}. ${extra} ` +
            `What does this mean for the target-validation verdict, and what follow-up ` +
            `searches or analyses would sharpen the picture?`,
        },
      ],
    });
  };

  const askItem = (kind: "risk" | "strength", item: string) => {
    sendMessage({
      role: "user",
      content: [
        {
          type: "text",
          text:
            `Elaborate on this ${kind} from the ${target} ${disease} validation: ` +
            `"${item}". How material is it to the go/no-go decision, and what would ` +
            `change my confidence on it?`,
        },
      ],
    });
  };

  return (
    <div className="validate-target-viewer" style={{ width: "100%" }}>
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
          <div style={{ fontSize: 10, fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-muted)" }}>
            Novo<span style={{ color: "var(--accent)" }}>MCP</span>
          </div>
          <div style={{ fontFamily: "var(--font-serif)", fontSize: 18, color: "var(--text)", marginTop: 4 }}>
            Target Validation
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
            <code style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--accent)" }}>{target}</code>
            {" · "}
            {disease}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div
            style={{
              fontSize: 10,
              fontWeight: 600,
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              color: verdict.color,
              background: verdict.bgVar,
              padding: "4px 10px",
              borderRadius: 2,
              display: "inline-block",
            }}
          >
            {level || "—"}
          </div>
          {data.target_maturity && (
            <div
              style={{
                fontSize: 9,
                color: "var(--text-muted)",
                marginTop: 4,
                fontFamily: "var(--font-mono)",
              }}
              title={
                data.target_maturity === "mature_validated"
                  ? "Target has >5 approved drugs + >=50 completed trials. Competition and terminated-trial counts are not penalized — they're signals of clinical validation at this maturity level."
                  : data.target_maturity === "emerging"
                    ? "Target has some clinical activity but isn't fully validated. Standard scoring applies."
                    : "Novel target — no approved drugs, limited trial history. Standard scoring applies."
              }
            >
              {data.target_maturity.replace(/_/g, " ")}
            </div>
          )}
        </div>
      </div>

      {/* Verdict card + confidence gauge */}
      <div
        className="panel"
        onClick={askVerdict}
        style={{
          marginBottom: 16,
          borderLeft: `3px solid ${verdict.color}`,
          cursor: "pointer",
        }}
        title="Click to ask Claude why this verdict"
      >
        <div
          className="panel-title"
          style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
        >
          <span>Recommendation</span>
          <span style={{ fontSize: 9, fontWeight: 400, color: "var(--text-muted)" }}>click to ask</span>
        </div>
        <div style={{ fontFamily: "var(--font-serif)", fontSize: 22, color: verdict.color, marginBottom: 10 }}>
          {verdict.label}
        </div>
        <ConfidenceGauge score={score} />
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4, lineHeight: 1.5 }}>
          Confidence score combines clinical trials (3×), ChEMBL bioactivity (2×), literature (1×),
          and omics (1×). Red band &lt; 0.40 (reconsider), yellow 0.40-0.70 (caution), green ≥ 0.70 (proceed).
        </div>
      </div>

      {/* Evidence streams — 2×2 grid */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 16 }}>
        <EvidenceCard
          title="Clinical Trials"
          weight="3× weight"
          onClick={askStream(
            "clinical trial",
            `Completed: ${trials.completed ?? 0}, terminated: ${trials.terminated ?? 0}, Phase-3 failures: ${trials.phase3_failures ?? 0}.`,
          )}
        >
          <MetricRow label="Completed" value={trials.completed ?? 0} emphasis={(trials.completed ?? 0) > 0 ? "good" : "neutral"} />
          <MetricRow label="Terminated" value={trials.terminated ?? 0} emphasis={(trials.terminated ?? 0) > 0 ? "bad" : "neutral"} />
          <MetricRow label="Phase-3 failures" value={trials.phase3_failures ?? 0} emphasis={(trials.phase3_failures ?? 0) > 0 ? "bad" : "neutral"} />
          {(trials.key_failures?.length ?? 0) > 0 && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 8, lineHeight: 1.5 }}>
              Key failures: {(trials.key_failures ?? []).map((f) => f.nct_id || f.title).slice(0, 3).join(", ")}
            </div>
          )}
        </EvidenceCard>

        <EvidenceCard
          title="ChEMBL Bioactivity"
          weight="2× weight"
          onClick={askStream(
            "ChEMBL bioactivity",
            `Activity count: ${chembl.activity_count ?? 0}, best pChEMBL: ${chembl.best_pchembl ?? "?"}.`,
          )}
        >
          <MetricRow label="Activities" value={chembl.activity_count ?? 0} emphasis={(chembl.activity_count ?? 0) > 10 ? "good" : "neutral"} />
          <MetricRow label="Best pChEMBL" value={chembl.best_pchembl != null ? chembl.best_pchembl.toFixed(2) : "—"} emphasis={(chembl.best_pchembl ?? 0) > 7 ? "good" : "neutral"} />
          {(chembl.assay_types?.length ?? 0) > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>
              {(chembl.assay_types ?? []).slice(0, 5).map((t) => (
                <span
                  key={t}
                  style={{ fontSize: 10, padding: "2px 6px", background: "var(--bg-warm)", borderRadius: 2, color: "var(--text-soft)" }}
                >
                  {t}
                </span>
              ))}
            </div>
          )}
        </EvidenceCard>

        <EvidenceCard
          title="Literature"
          weight="1× weight"
          onClick={askStream(
            "literature",
            `Supporting: ${lit.supporting_papers ?? 0}, contradicting: ${lit.contradicting_papers ?? 0}.`,
          )}
        >
          <MetricRow label="Supporting papers" value={lit.supporting_papers ?? 0} emphasis={(lit.supporting_papers ?? 0) > 5 ? "good" : "neutral"} />
          <MetricRow label="Contradicting papers" value={lit.contradicting_papers ?? 0} emphasis={(lit.contradicting_papers ?? 0) > 0 ? "bad" : "neutral"} />
          {(lit.top_supporting?.length ?? 0) > 0 && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 8, lineHeight: 1.5, maxHeight: 48, overflow: "hidden" }}>
              Top: {(lit.top_supporting ?? []).slice(0, 2).map((p) => (p.title || "").slice(0, 50)).join(" · ")}
            </div>
          )}
        </EvidenceCard>

        <EvidenceCard
          title="Omics"
          weight="1× weight"
          onClick={askStream(
            "omics",
            `Composite score: ${omics.composite_score != null ? omics.composite_score.toFixed(2) : "?"}, tractable: ${omics.tractable ? "yes" : "no"}, known drugs: ${omics.known_drugs ?? 0}.`,
          )}
        >
          <MetricRow label="Composite score" value={omics.composite_score != null ? omics.composite_score.toFixed(2) : "—"} emphasis={(omics.composite_score ?? 0) > 0.5 ? "good" : "neutral"} />
          <MetricRow label="Tractable" value={omics.tractable ? "yes" : "no"} emphasis={omics.tractable ? "good" : "bad"} />
          <MetricRow label="Known drugs" value={omics.known_drugs ?? 0} emphasis={omics.high_competition ? "bad" : "neutral"} />
          {omics.suggested_pdb_id && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 8 }}>
              PDB: <code style={{ fontFamily: "var(--font-mono)", color: "var(--accent)" }}>{omics.suggested_pdb_id}</code>
              {omics.high_competition && <span style={{ marginLeft: 8, color: "var(--warning)" }}>⚠ crowded</span>}
            </div>
          )}
        </EvidenceCard>
      </div>

      {/* Risks + Strengths */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 16 }}>
        <BulletPanel
          title="Risk Factors"
          items={data.risk_factors ?? []}
          color="var(--danger)"
          onClickItem={(item) => askItem("risk", item)}
        />
        <BulletPanel
          title="Strengths"
          items={data.strengths ?? []}
          color="var(--success)"
          onClickItem={(item) => askItem("strength", item)}
        />
      </div>

      {/* Partial-data warning */}
      {data.partial_data && data.partial_data.length > 0 && (
        <div
          className="panel"
          style={{ borderLeft: "3px solid var(--warning)", background: "var(--warning-bg)" }}
        >
          <div className="panel-title" style={{ color: "var(--warning)" }}>Partial Evidence</div>
          <div style={{ fontSize: 11, color: "var(--text)", lineHeight: 1.5 }}>
            The following evidence sources failed or returned no data:{" "}
            <code style={{ fontFamily: "var(--font-mono)" }}>{data.partial_data.join(", ")}</code>.
            The confidence score penalizes for missing streams — consider rerunning or pursuing
            those checks manually before committing compute.
          </div>
        </div>
      )}
    </div>
  );
}
