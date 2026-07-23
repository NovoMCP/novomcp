/**
 * NovoMCP Patient Stratification Viewer
 *
 * Renders stratify_patients output: clinical viability verdict,
 * population coverage by ancestry, CYP metabolism profile, PGx risk
 * alleles, and resistance mutations. Click any PGx allele or
 * mutation → Claude gets dosing / alternative-line guidance.
 */

import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types
// =============================================================================

interface PgxRiskAllele {
  gene?: string;
  allele?: string;
  effect?: string;
}

interface AncestryCoverage {
  ancestry?: string;
  cyp?: string;
  normal_metabolizer_pct?: number;
}

interface Pharmacogenomics {
  primary_metabolism?: string[];
  pgx_risk_alleles?: PgxRiskAllele[];
  // CPIC levels + probabilities exposed as flat CYP-prefixed fields.
  CYP3A4_cpic_level?: string;
  CYP3A4_substrate_probability?: number;
  CYP3A4_clinical_implications?: string;
  CYP2D6_cpic_level?: string;
  CYP2D6_substrate_probability?: number;
  CYP2D6_clinical_implications?: string;
  CYP2C9_cpic_level?: string;
  CYP2C9_substrate_probability?: number;
  CYP2C9_clinical_implications?: string;
  [key: string]: unknown;
}

interface PopulationCoverage {
  global_normal_metabolizer_pct?: number;
  by_ancestry?: AncestryCoverage[];
}

interface ResistanceMutation {
  mutation?: string;
  cancer_type?: string;
  clinvar_significance?: string;
  affects_binding_site?: boolean;
}

interface Resistance {
  known_mutations?: ResistanceMutation[];
  total_pathogenic_variants?: number;
  variants_near_binding_site?: number;
  resistance_risk?: string;
  error?: string;
}

interface StratifyPatientsToolInput {
  smiles?: string;
  target_gene?: string;
  indication?: string;

  pharmacogenomics?: Pharmacogenomics;
  population_coverage?: PopulationCoverage;
  resistance?: Resistance;

  summary?: {
    clinical_viability?: string;
    key_risks?: string[];
    recommended_actions?: string[];
  };

  wall_time_seconds?: number;
  warnings?: string[];
}

type StratifyPatientsProps = ViewProps<StratifyPatientsToolInput>;

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
        Stratifying patient population…
      </div>
    </div>
  );
}

// =============================================================================
// Viability verdict colors
// =============================================================================

function viabilityInfo(v?: string): { label: string; color: string; hint: string } {
  const key = (v || "").toLowerCase();
  switch (key) {
    case "high":
      return { label: "High viability", color: "var(--success)", hint: "Wide population coverage, minimal resistance risk" };
    case "moderate":
      return { label: "Moderate viability", color: "var(--accent)", hint: "Manageable PGx or resistance considerations" };
    case "low":
      return { label: "Low viability", color: "var(--warning)", hint: "Significant population or resistance caveats" };
    case "not_applicable":
      return { label: "Not applicable", color: "var(--text-muted)", hint: "Target not in the 56-pharmacogene panel" };
    default:
      return { label: v || "Unknown", color: "var(--text-muted)", hint: "" };
  }
}

// =============================================================================
// Resistance risk colors
// =============================================================================

function riskInfo(r?: string): { label: string; color: string } {
  const key = (r || "").toLowerCase();
  switch (key) {
    case "high":
      return { label: "High", color: "var(--danger)" };
    case "moderate":
      return { label: "Moderate", color: "var(--warning)" };
    case "low":
      return { label: "Low", color: "var(--accent)" };
    case "minimal":
      return { label: "Minimal", color: "var(--success)" };
    case "unknown":
      return { label: "Unknown", color: "var(--text-muted)" };
    default:
      return { label: r || "—", color: "var(--text-muted)" };
  }
}

// =============================================================================
// Viability verdict panel
// =============================================================================

function ViabilityPanel({
  viability,
  targetGene,
}: {
  viability?: string;
  targetGene?: string;
}) {
  if (!viability) return null;
  const info = viabilityInfo(viability);
  return (
    <div
      className="panel"
      style={{ borderLeft: `3px solid ${info.color}` }}
    >
      <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
        Clinical viability {targetGene && `· ${targetGene}`}
      </div>
      <div style={{ display: "flex", gap: 12, alignItems: "baseline", marginTop: 6, flexWrap: "wrap" }}>
        <span
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: 20,
            fontWeight: 600,
            color: info.color,
          }}
        >
          {info.label}
        </span>
        {info.hint && (
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{info.hint}</span>
        )}
      </div>
    </div>
  );
}

// =============================================================================
// Population coverage horizontal bar
// =============================================================================

function PopulationCoveragePanel({
  coverage,
}: {
  coverage?: PopulationCoverage;
}) {
  if (!coverage) return null;
  const global = coverage.global_normal_metabolizer_pct;
  const byAncestry = coverage.by_ancestry || [];

  if (global == null && byAncestry.length === 0) return null;

  const globalColor =
    global == null ? "var(--text-muted)"
    : global >= 70 ? "var(--success)"
    : global >= 50 ? "var(--accent)"
    : "var(--warning)";

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">Population Coverage</div>
      {global != null && (
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 12 }}>
          <span
            style={{
              fontSize: 22,
              fontFamily: "var(--font-mono)",
              fontWeight: 600,
              color: globalColor,
            }}
          >
            {global.toFixed(1)}%
          </span>
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
            global normal metabolizers (weighted by ancestry prevalence)
          </span>
        </div>
      )}
      {byAncestry.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {byAncestry.map((a, idx) => {
            const pct = a.normal_metabolizer_pct ?? 0;
            const color = pct >= 70 ? "var(--success)" : pct >= 50 ? "var(--accent)" : "var(--warning)";
            return (
              <div
                key={idx}
                style={{ display: "grid", gridTemplateColumns: "100px 60px 1fr", gap: 8, alignItems: "center", fontSize: 11 }}
              >
                <span style={{ color: "var(--text-muted)", textTransform: "capitalize" }}>
                  {a.ancestry?.replace("_", " ") || "—"}
                </span>
                <span style={{ fontFamily: "var(--font-mono)", color: color, fontWeight: 500, textAlign: "right" }}>
                  {pct.toFixed(0)}%
                </span>
                <div style={{ height: 6, background: "var(--bg-warm)", borderRadius: 2, overflow: "hidden" }}>
                  <div
                    style={{
                      width: `${Math.min(100, pct)}%`,
                      height: "100%",
                      background: color,
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 8, lineHeight: 1.5 }}>
        % normal metabolizers indicates what fraction of the population has standard
        PK for the primary CYP enzyme. Low coverage = many patients need dose
        adjustment or alternative therapy.
      </div>
    </div>
  );
}

// =============================================================================
// CYP metabolism profile (primary metabolism + substrate probabilities + CPIC
// levels, extracted from the flat pharmacogenomics fields).
// =============================================================================

const CYPS = ["CYP3A4", "CYP2D6", "CYP2C9"] as const;

function CypProfilePanel({
  pgx,
  sendMessage,
  targetGene,
  smiles,
}: {
  pgx?: Pharmacogenomics;
  sendMessage?: StratifyPatientsProps["sendMessage"];
  targetGene?: string;
  smiles?: string;
}) {
  if (!pgx) return null;

  const cypRows = CYPS.map((cyp) => {
    const probKey = `${cyp}_substrate_probability` as keyof Pharmacogenomics;
    const levelKey = `${cyp}_cpic_level` as keyof Pharmacogenomics;
    const implKey = `${cyp}_clinical_implications` as keyof Pharmacogenomics;
    const prob = pgx[probKey] as number | undefined;
    const level = pgx[levelKey] as string | undefined;
    const impl = pgx[implKey] as string | undefined;
    return { cyp, prob, level, impl };
  }).filter((r) => r.prob != null || r.level);

  if (cypRows.length === 0) {
    return (
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="panel-title">CYP Metabolism</div>
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          {pgx.primary_metabolism && pgx.primary_metabolism.length > 0
            ? pgx.primary_metabolism.join(", ")
            : "No CYP substrate data — pass admet_results from Stage 5 for full PGx analysis."}
        </div>
      </div>
    );
  }

  const askAboutCyp = sendMessage
    ? (cyp: string, prob?: number, level?: string) => {
        const smilesRef = smiles ? `\`${smiles}\`` : "this compound";
        const geneRef = targetGene ? ` for ${targetGene}` : "";
        sendMessage({
          role: "user",
          content: [
            {
              type: "text",
              text:
                `I clicked the **${cyp}** metabolism profile${geneRef}. ${smilesRef} has a ` +
                `${prob != null ? `${(prob * 100).toFixed(0)}% substrate probability` : "substrate probability"} ` +
                `for ${cyp}${level ? ` (CPIC level ${level})` : ""}. ` +
                `Which populations are most affected (poor/intermediate/ultra-rapid metabolizers), ` +
                `what's the standard dose-adjustment guidance, and should I look at alternative chemotypes ` +
                `that avoid ${cyp} as the primary metabolic route?`,
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
        <span>CYP Metabolism Profile</span>
        {askAboutCyp && (
          <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-muted)" }}>
            click any CYP for guidance
          </span>
        )}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {cypRows.map(({ cyp, prob, level, impl }) => (
          <div
            key={cyp}
            onClick={askAboutCyp ? () => askAboutCyp(cyp, prob, level) : undefined}
            style={{
              padding: "10px 12px",
              background: "var(--bg-warm)",
              borderRadius: 2,
              borderLeft: "3px solid var(--accent)",
              cursor: askAboutCyp ? "pointer" : undefined,
            }}
            title={askAboutCyp ? `Click for ${cyp} dosing guidance` : impl}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
              <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--accent)" }}>
                {cyp}
              </span>
              <div style={{ display: "flex", gap: 12, alignItems: "baseline" }}>
                {prob != null && (
                  <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text)" }}>
                    {(prob * 100).toFixed(0)}% substrate probability
                  </span>
                )}
                {level && (
                  <span
                    style={{
                      fontSize: 10,
                      padding: "2px 8px",
                      background: "var(--bg)",
                      borderRadius: 2,
                      color: "var(--text-muted)",
                      fontFamily: "var(--font-mono)",
                    }}
                    title="CPIC evidence level for actionable dosing guidance"
                  >
                    CPIC {level}
                  </span>
                )}
              </div>
            </div>
            {impl && (
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.5 }}>
                {impl}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// =============================================================================
// PGx Risk Alleles — click-to-ask
// =============================================================================

function PgxAllelesPanel({
  alleles,
  sendMessage,
  smiles,
}: {
  alleles?: PgxRiskAllele[];
  sendMessage?: StratifyPatientsProps["sendMessage"];
  smiles?: string;
}) {
  if (!alleles || alleles.length === 0) return null;

  const askAboutAllele = sendMessage
    ? (a: PgxRiskAllele) => {
        const smilesRef = smiles ? `\`${smiles}\`` : "this compound";
        sendMessage({
          role: "user",
          content: [
            {
              type: "text",
              text:
                `I clicked the **${a.gene} ${a.allele}** risk allele — function: "${a.effect || "?"}". ` +
                `How prevalent is this allele globally and per ancestry, what does ${a.effect} mean for ${smilesRef} ` +
                `(slower clearance, higher plasma levels, risk of accumulation), and what dose-adjustment or ` +
                `alternative-chemotype strategy should I consider?`,
            },
          ],
        });
      }
    : undefined;

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">
        PGx Risk Alleles ({alleles.length})
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {alleles.map((a, idx) => {
          const isLossOfFunction = a.effect === "no function";
          const color = isLossOfFunction ? "var(--danger)" : "var(--warning)";
          return (
            <div
              key={idx}
              onClick={askAboutAllele ? () => askAboutAllele(a) : undefined}
              style={{
                padding: "6px 10px",
                background: "var(--bg-warm)",
                borderRadius: 2,
                borderLeft: `3px solid ${color}`,
                cursor: askAboutAllele ? "pointer" : undefined,
              }}
              title={askAboutAllele ? `Click for ${a.gene} ${a.allele} guidance` : a.effect}
            >
              <div style={{ fontSize: 11, fontWeight: 500, color: "var(--text)", fontFamily: "var(--font-mono)" }}>
                {a.gene} {a.allele}
              </div>
              <div style={{ fontSize: 9, color, marginTop: 2 }}>
                {a.effect || "—"}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// =============================================================================
// Resistance panel — risk badge + known-mutation table
// =============================================================================

function ResistancePanel({
  resistance,
  targetGene,
  sendMessage,
}: {
  resistance?: Resistance;
  targetGene?: string;
  sendMessage?: StratifyPatientsProps["sendMessage"];
}) {
  if (!resistance) return null;
  if (resistance.error) {
    return (
      <div className="panel" style={{ marginTop: 16, borderLeft: "3px solid var(--warning)" }}>
        <div className="panel-title">Resistance Lookup Error</div>
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{resistance.error}</div>
      </div>
    );
  }

  const risk = riskInfo(resistance.resistance_risk);
  const mutations = resistance.known_mutations || [];

  const askAboutMutation = sendMessage
    ? (m: ResistanceMutation) => {
        sendMessage({
          role: "user",
          content: [
            {
              type: "text",
              text:
                `I clicked the **${targetGene} ${m.mutation}** resistance mutation. ` +
                `${m.cancer_type ? `Observed in ${m.cancer_type}. ` : ""}` +
                `ClinVar significance: ${m.clinvar_significance || "?"}. ` +
                `${m.affects_binding_site ? "Affects the binding site directly." : "Not at the binding site."} ` +
                `Is this a known acquired resistance mutation in clinical use, what chemotypes tend to ` +
                `retain activity against it, and should I be screening second-generation inhibitors against ` +
                `this variant?`,
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
        <span>Resistance Profile</span>
        <span
          style={{
            fontSize: 11,
            padding: "2px 8px",
            background: "var(--bg-warm)",
            borderLeft: `2px solid ${risk.color}`,
            borderRadius: 2,
            color: risk.color,
            fontWeight: 500,
          }}
        >
          {risk.label} risk
        </span>
      </div>
      <div style={{ display: "flex", gap: 16, marginBottom: 12, flexWrap: "wrap" }}>
        {resistance.total_pathogenic_variants != null && (
          <div
            style={{ padding: "6px 10px", background: "var(--bg-warm)", borderRadius: 2, minWidth: 110 }}
            title="Total pathogenic variants known in ClinVar for this gene"
          >
            <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase" }}>
              Pathogenic variants
            </div>
            <div style={{ fontSize: 16, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text)" }}>
              {resistance.total_pathogenic_variants}
            </div>
          </div>
        )}
        {resistance.variants_near_binding_site != null && (
          <div
            style={{ padding: "6px 10px", background: "var(--bg-warm)", borderRadius: 2, minWidth: 110 }}
            title="Variants located within the drug-binding site (structural impact on affinity)"
          >
            <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase" }}>
              At binding site
            </div>
            <div style={{ fontSize: 16, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--warning)" }}>
              {resistance.variants_near_binding_site}
            </div>
          </div>
        )}
      </div>
      {mutations.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)" }}>
                <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Mutation</th>
                <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Cancer Type</th>
                <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>ClinVar</th>
                <th style={{ textAlign: "center", padding: "6px 8px", color: "var(--text-muted)", fontSize: 10, fontWeight: 500 }}>Binding site?</th>
              </tr>
            </thead>
            <tbody>
              {mutations.slice(0, 25).map((m, i) => (
                <tr
                  key={i}
                  onClick={askAboutMutation ? () => askAboutMutation(m) : undefined}
                  style={{
                    borderBottom: "1px solid var(--border)",
                    cursor: askAboutMutation ? "pointer" : undefined,
                  }}
                  title={askAboutMutation ? `Click for ${m.mutation} resistance context` : undefined}
                >
                  <td style={{ padding: "6px 8px", fontFamily: "var(--font-mono)", fontWeight: 500, color: "var(--accent)" }}>
                    {m.mutation || "—"}
                  </td>
                  <td style={{ padding: "6px 8px", color: "var(--text)" }}>
                    {m.cancer_type || "—"}
                  </td>
                  <td
                    style={{
                      padding: "6px 8px",
                      fontSize: 10,
                      color:
                        m.clinvar_significance === "Pathogenic" || m.clinvar_significance === "Likely pathogenic"
                          ? "var(--danger)"
                          : "var(--text-muted)",
                    }}
                  >
                    {m.clinvar_significance || "—"}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "center", fontSize: 12 }}>
                    {m.affects_binding_site ? (
                      <span style={{ color: "var(--warning)", fontWeight: 500 }}>⚠</span>
                    ) : (
                      <span style={{ color: "var(--text-muted)" }}>—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {mutations.length > 25 && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6 }}>
              Showing top 25 of {mutations.length} known pathogenic variants.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Key risks + recommended actions
// =============================================================================

function ActionsList({
  title,
  items,
  color,
}: {
  title: string;
  items?: string[];
  color: string;
}) {
  if (!items || items.length === 0) return null;
  return (
    <div className="panel" style={{ marginTop: 16, borderLeft: `3px solid ${color}` }}>
      <div className="panel-title">{title}</div>
      <ul style={{ margin: 0, paddingLeft: 20, fontSize: 12, color: "var(--text)", lineHeight: 1.6 }}>
        {items.map((it, i) => (
          <li key={i}>{it}</li>
        ))}
      </ul>
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function StratifyPatientsViewer(props: StratifyPatientsProps) {
  const { toolInputs, toolInputsPartial, toolResult, sendMessage } = props;
  const data = useViewData<StratifyPatientsToolInput>(props);
  const isStreaming = !toolInputs && !toolResult;
  if (isStreaming) {
    return <LoadingShimmer />;
  }

  const smiles = data.smiles || toolInputs?.smiles || toolInputsPartial?.smiles;
  const targetGene = data.target_gene || (toolInputs as any)?.target_gene || (toolInputs as any)?.gene_symbol;
  const indication = data.indication || (toolInputs as any)?.indication;

  return (
    <div className="stratify-patients-viewer" style={{ width: "100%" }}>
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
            Patient Stratification {targetGene && `— ${targetGene}`}
          </div>
          {(smiles || indication) && (
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
              title={[smiles, indication].filter(Boolean).join(" · ")}
            >
              {smiles}
              {indication && <span style={{ marginLeft: 12 }}>· {indication}</span>}
            </div>
          )}
        </div>
        <div style={{ textAlign: "right" }}>
          {data.wall_time_seconds != null && (
            <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {data.wall_time_seconds.toFixed(1)} s
            </div>
          )}
        </div>
      </div>

      <ViabilityPanel
        viability={data.summary?.clinical_viability}
        targetGene={targetGene}
      />

      <PopulationCoveragePanel coverage={data.population_coverage} />

      <CypProfilePanel
        pgx={data.pharmacogenomics}
        sendMessage={sendMessage}
        targetGene={targetGene}
        smiles={smiles}
      />

      <PgxAllelesPanel
        alleles={data.pharmacogenomics?.pgx_risk_alleles}
        sendMessage={sendMessage}
        smiles={smiles}
      />

      <ResistancePanel
        resistance={data.resistance}
        targetGene={targetGene}
        sendMessage={sendMessage}
      />

      <ActionsList
        title="Key Risks"
        items={data.summary?.key_risks}
        color="var(--warning)"
      />

      <ActionsList
        title="Recommended Actions"
        items={data.summary?.recommended_actions}
        color="var(--accent)"
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
