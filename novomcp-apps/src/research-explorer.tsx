/**
 * NovoMCP Research Explorer Component
 *
 * Interactive research results explorer with timeline, filtering,
 * and source navigation for literature and patent search results.
 */
import { useState } from "react";
import type { ViewProps } from "./create-app.tsx";
import { useViewData } from "./use-view-data.ts";

// =============================================================================
// Types
// =============================================================================

interface ResearchResult {
  id: string;
  title: string;
  authors?: string | string[];  // Backend sends string, UI expects array
  source?: "pubmed" | "patent" | "biorxiv" | "chembl" | "clinical_trial";
  date?: string;
  year?: number | string;
  abstract?: string;
  url?: string;
  doi?: string;
  relevance_score?: number;
  relevance?: number;  // Backend field name
  score?: number;  // Another backend field name
  highlights?: string[];
  metadata?: {
    journal?: string;
    patent_number?: string;
    trial_id?: string;
    phase?: string;
    status?: string;
  };
}

interface ResearchToolInput {
  query?: string;
  results?: ResearchResult[];
  papers?: ResearchResult[];  // Backend returns 'papers' for literature search
  total_count?: number;
  total_results?: number;  // Backend field name
  sources?: {
    pubmed?: number;
    patent?: number;
    biorxiv?: number;
    chembl?: number;
    clinical_trial?: number;
  };
  tool_suggestions?: unknown[];  // Backend includes suggestions
  height?: number;
}

type ResearchExplorerProps = ViewProps<ResearchToolInput>;

// =============================================================================
// Loading Shimmer
// =============================================================================

function LoadingShimmer({ height }: { height: number }) {
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
        Searching research databases...
      </div>
    </div>
  );
}

// =============================================================================
// Source Badge
// =============================================================================

function SourceBadge({ source }: { source: ResearchResult["source"] }) {
  const config: Record<string, { label: string; color: string }> = {
    pubmed: { label: "PubMed", color: "#2E7D32" },
    patent: { label: "Patent", color: "#1565C0" },
    biorxiv: { label: "bioRxiv", color: "#B8704B" },
    chembl: { label: "ChEMBL", color: "#7B1FA2" },
    clinical_trial: { label: "Clinical Trial", color: "#C62828" },
  };

  const { label, color } = (source && config[source]) || { label: source || "Unknown", color: "var(--text-muted)" };

  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        background: `${color}15`,
        color: color,
        fontSize: 10,
        fontWeight: 500,
        borderRadius: 2,
        textTransform: "uppercase",
        letterSpacing: "0.03em",
      }}
    >
      {label}
    </span>
  );
}

// =============================================================================
// Result Card
// =============================================================================

function ResultCard({
  result,
  expanded,
  onToggle,
  onOpenLink
}: {
  result: ResearchResult;
  expanded: boolean;
  onToggle: () => void;
  onOpenLink?: (params: { url: string }) => void;
}) {
  return (
    <div
      style={{
        padding: 16,
        background: "var(--bg-card)",
        border: "1px solid var(--border)",
        borderRadius: 2,
        marginBottom: 12,
        transition: "all 200ms ease",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
            <SourceBadge source={result.source} />
            {result.year && (
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                {result.year}
              </span>
            )}
            {(result.relevance_score ?? result.relevance ?? result.score) !== undefined && (
              <span
                style={{
                  fontSize: 10,
                  color: "var(--accent)",
                  fontFamily: "var(--font-mono)",
                }}
              >
                {((result.relevance_score ?? result.relevance ?? result.score ?? 0) * 100).toFixed(0)}% match
              </span>
            )}
          </div>
          <h3
            style={{
              fontSize: 14,
              fontWeight: 500,
              color: "var(--text)",
              lineHeight: 1.4,
              cursor: "pointer",
            }}
            onClick={onToggle}
          >
            {result.title}
          </h3>
          {result.authors && (
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
              {typeof result.authors === 'string'
                ? result.authors
                : (result.authors.slice(0, 3).join(", ") + (result.authors.length > 3 ? ` +${result.authors.length - 3} more` : ""))
              }
            </div>
          )}
        </div>
        <button
          onClick={onToggle}
          style={{
            padding: "6px 10px",
            background: "var(--bg-warm)",
            border: "1px solid var(--border)",
            borderRadius: 2,
            cursor: "pointer",
            fontSize: 11,
            color: "var(--text-soft)",
          }}
        >
          {expanded ? "Less" : "More"}
        </button>
      </div>

      {/* Expanded Content */}
      {expanded && (
        <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
          {/* Abstract */}
          {result.abstract && (
            <div style={{ marginBottom: 12 }}>
              <div
                style={{
                  fontSize: 10,
                  fontWeight: 500,
                  textTransform: "uppercase",
                  letterSpacing: "0.08em",
                  color: "var(--text-muted)",
                  marginBottom: 6,
                }}
              >
                Abstract
              </div>
              <p style={{ fontSize: 12, color: "var(--text-soft)", lineHeight: 1.6 }}>
                {result.abstract}
              </p>
            </div>
          )}

          {/* Highlights */}
          {result.highlights && result.highlights.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div
                style={{
                  fontSize: 10,
                  fontWeight: 500,
                  textTransform: "uppercase",
                  letterSpacing: "0.08em",
                  color: "var(--text-muted)",
                  marginBottom: 6,
                }}
              >
                Key Findings
              </div>
              <ul style={{ margin: 0, paddingLeft: 16 }}>
                {result.highlights.map((h, i) => (
                  <li key={i} style={{ fontSize: 12, color: "var(--text-soft)", marginBottom: 4 }}>
                    {h}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Metadata */}
          {result.metadata && (
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 12 }}>
              {result.metadata.journal && (
                <div>
                  <span style={{ fontSize: 10, color: "var(--text-muted)" }}>Journal: </span>
                  <span style={{ fontSize: 11, color: "var(--text)" }}>{result.metadata.journal}</span>
                </div>
              )}
              {result.metadata.patent_number && (
                <div>
                  <span style={{ fontSize: 10, color: "var(--text-muted)" }}>Patent: </span>
                  <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text)" }}>
                    {result.metadata.patent_number}
                  </span>
                </div>
              )}
              {result.metadata.trial_id && (
                <div>
                  <span style={{ fontSize: 10, color: "var(--text-muted)" }}>Trial: </span>
                  <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text)" }}>
                    {result.metadata.trial_id}
                  </span>
                </div>
              )}
              {result.metadata.phase && (
                <div>
                  <span style={{ fontSize: 10, color: "var(--text-muted)" }}>Phase: </span>
                  <span style={{ fontSize: 11, color: "var(--text)" }}>{result.metadata.phase}</span>
                </div>
              )}
            </div>
          )}

          {/* Links */}
          <div style={{ display: "flex", gap: 8 }}>
            {result.url && (
              <button
                className="btn"
                onClick={() => onOpenLink && onOpenLink({ url: result.url! })}
                style={{ fontSize: 10 }}
              >
                View Source
              </button>
            )}
            {result.doi && (
              <button
                className="btn"
                onClick={() => onOpenLink && onOpenLink({ url: `https://doi.org/${result.doi}` })}
                style={{ fontSize: 10 }}
              >
                DOI: {result.doi}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Source Filter
// =============================================================================

function SourceFilter({
  sources,
  activeSource,
  onSourceChange,
}: {
  sources?: ResearchToolInput["sources"];
  activeSource: string | null;
  onSourceChange: (source: string | null) => void;
}) {
  if (!sources) return null;

  const sourceList = [
    { key: "pubmed", label: "PubMed", count: sources.pubmed },
    { key: "patent", label: "Patents", count: sources.patent },
    { key: "biorxiv", label: "bioRxiv", count: sources.biorxiv },
    { key: "chembl", label: "ChEMBL", count: sources.chembl },
    { key: "clinical_trial", label: "Trials", count: sources.clinical_trial },
  ].filter((s) => s.count && s.count > 0);

  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
      <button
        className={`btn ${activeSource === null ? "active" : ""}`}
        onClick={() => onSourceChange(null)}
      >
        All
      </button>
      {sourceList.map((s) => (
        <button
          key={s.key}
          className={`btn ${activeSource === s.key ? "active" : ""}`}
          onClick={() => onSourceChange(s.key)}
        >
          {s.label} ({s.count})
        </button>
      ))}
    </div>
  );
}

// =============================================================================
// Main Component
// =============================================================================

export default function ResearchExplorer({
  toolInputs,
  toolInputsPartial,
  toolResult,
  openLink,
}: ResearchExplorerProps) {
  const height = toolInputs?.height ?? toolInputsPartial?.height ?? 600;
  const isStreaming = !toolInputs && !toolResult;

  const [activeSource, setActiveSource] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  if (isStreaming) {
    return <LoadingShimmer height={height} />;
  }

  const resultData = useViewData<Record<string, any>>({ toolInputs, toolResult });
  const { query, results, papers, preprints, patents, trials, total_count, total_results, sources, search_type } = resultData as ResearchToolInput & { preprints?: any[]; patents?: any[]; trials?: any[]; search_type?: string };

  // Normalize results from different backend formats
  const rawResults = results || papers || preprints || patents || trials || [];

  // Transform results to standard format based on source
  const allResults: ResearchResult[] = rawResults.map((r: any, idx: number) => {
    // ChEMBL compound results
    if (r.chembl_id && !r.source) {
      return {
        id: r.chembl_id,
        title: r.name || r.chembl_id,
        source: "chembl" as const,
        abstract: r.indication_class || (r.smiles ? `SMILES: ${r.smiles}` : undefined),
        metadata: {
          journal: r.molecule_type ? `${r.molecule_type}${r.max_phase ? ` • Phase ${r.max_phase}` : ""}` : undefined,
        },
        year: r.first_approval,
        relevance_score: r.pchembl_value ? r.pchembl_value / 10 : undefined,  // Normalize pChEMBL to 0-1
        url: `https://www.ebi.ac.uk/chembl/compound_report_card/${r.chembl_id}`,
      };
    }
    // ChEMBL target results
    if (r.target_chembl_id || (r.chembl_id && r.target_type)) {
      return {
        id: r.target_chembl_id || r.chembl_id,
        title: r.name || r.pref_name || r.chembl_id,
        source: "chembl" as const,
        abstract: r.organism ? `Organism: ${r.organism}` : undefined,
        metadata: {
          journal: r.target_type,
        },
        url: `https://www.ebi.ac.uk/chembl/target_report_card/${r.target_chembl_id || r.chembl_id}`,
      };
    }
    // ChEMBL activity results
    if (r.activity_id) {
      return {
        id: String(r.activity_id),
        title: `${r.target_name || r.target_chembl_id} - ${r.molecule_chembl_id}`,
        source: "chembl" as const,
        abstract: r.standard_value ? `${r.standard_type}: ${r.standard_value} ${r.standard_units || ""}` : undefined,
        relevance_score: r.pchembl_value ? r.pchembl_value / 10 : undefined,
        url: `https://www.ebi.ac.uk/chembl/compound_report_card/${r.molecule_chembl_id}`,
      };
    }
    // bioRxiv results
    if (r.doi && r.server) {
      return {
        id: r.doi,
        title: r.title,
        source: "biorxiv" as const,
        abstract: r.abstract,
        authors: r.authors,
        date: r.date,
        year: r.date ? new Date(r.date).getFullYear() : undefined,
        url: r.url,
        doi: r.doi,
      };
    }
    // Clinical trial results
    if (r.nct_id || r.nctId) {
      return {
        id: r.nct_id || r.nctId,
        title: r.title || r.briefTitle,
        source: "clinical_trial" as const,
        abstract: r.description || r.briefSummary,
        metadata: {
          trial_id: r.nct_id || r.nctId,
          phase: r.phase,
          status: r.status || r.overallStatus,
        },
        url: `https://clinicaltrials.gov/study/${r.nct_id || r.nctId}`,
      };
    }
    // Patent results (from search_patents)
    if (r.patent_number || r.applicant) {
      return {
        id: r.id || r.patent_number || `patent-${idx}`,
        title: r.title,
        source: "patent" as const,
        abstract: r.abstract,
        authors: r.applicant,  // applicant stored in authors field
        year: r.filing_date,
        relevance_score: r.relevance,
        metadata: {
          patent_number: r.patent_number,
        },
        url: r.patent_number ? `https://patents.google.com/patent/${r.patent_number}` : undefined,
      };
    }
    // Already formatted or literature/patent results
    return {
      ...r,
      id: r.id || r.pmid || r.patent_number || `result-${idx}`,
      source: r.source || (r.pmid ? "pubmed" : r.patent_number ? "patent" : undefined),
    };
  });

  const count = total_count ?? total_results ?? allResults.length;

  const filteredResults = activeSource
    ? allResults.filter((r) => r.source === activeSource)
    : allResults;

  const toggleExpanded = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  return (
    <div className="research-explorer" style={{ width: "100%" }}>
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
            Research Explorer
          </div>
        </div>
        {count > 0 && (
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            {count} results found
          </div>
        )}
      </div>

      {/* Search Query */}
      {query && (
        <div
          style={{
            padding: "10px 14px",
            background: "var(--bg-warm)",
            borderRadius: 2,
            marginBottom: 16,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Query:</span>
          <span style={{ fontSize: 12, color: "var(--text)", fontWeight: 500 }}>
            {query}
          </span>
        </div>
      )}

      {/* Source Filters */}
      <div style={{ marginBottom: 16 }}>
        <SourceFilter
          sources={sources}
          activeSource={activeSource}
          onSourceChange={setActiveSource}
        />
      </div>

      {/* Results */}
      <div style={{ maxHeight: height - 200, overflowY: "auto" }}>
        {filteredResults && filteredResults.length > 0 ? (
          filteredResults.map((result) => (
            <ResultCard
              key={result.id}
              result={result}
              expanded={expandedIds.has(result.id)}
              onToggle={() => toggleExpanded(result.id)}
              onOpenLink={openLink}
            />
          ))
        ) : (
          <div
            style={{
              padding: 40,
              textAlign: "center",
              color: "var(--text-muted)",
              fontSize: 13,
            }}
          >
            No results found
          </div>
        )}
      </div>
    </div>
  );
}
