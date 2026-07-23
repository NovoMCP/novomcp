'use client';

// Canonical read-only funnel audit renderer. Used by both the dashboard's
// Pipeline Audit page and the shareable /f/[id] viewer (Studio "FunnelViewer"
// surface). Reads via useFunnelAudit → BFF → managed backend (JWT, org-scoped).
import { useState } from 'react';
import { useFunnelAudit } from '@/core/api/admin-client';
import { ChevronDown, ChevronUp, Clock, X } from 'lucide-react';
import { resolveSurface, resolveClient, SURFACE_TONE_CLASSES } from './surfaceLabels';

export function formatTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ', ' +
    d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}

function StageRow({ stage, expanded, onToggle }: { stage: any; expanded: boolean; onToggle: () => void }) {
  const hasHuman = stage.human_decision || stage.human_prompt;
  const filtered = stage.molecules_filtered || {};
  const filteredTotal = Object.values(filtered).reduce((a: number, b: any) => a + (Number(b) || 0), 0);

  return (
    <div className="border-b border-[var(--border)] last:border-b-0">
      <div
        className="grid items-center cursor-pointer hover:bg-[var(--bg-warm)] transition-colors"
        style={{ gridTemplateColumns: '32px 40px 1fr 80px 80px 80px 80px' }}
        onClick={onToggle}
      >
        <div className="px-2 py-3 text-center">
          {expanded ? <ChevronUp className="h-3 w-3 text-[var(--text-muted)] mx-auto" /> : <ChevronDown className="h-3 w-3 text-[var(--text-muted)] mx-auto" />}
        </div>
        <div className="py-3 text-xs text-[var(--text-muted)] text-center">{stage.stage_index}</div>
        <div className="py-3">
          <div className="text-sm font-medium text-[var(--text)]">{stage.stage_label}</div>
          <div className="text-xs text-[var(--text-muted)]">{stage.tool_name || '—'}</div>
        </div>
        <div className="py-3 text-xs text-center">
          {stage.molecules_in != null ? (
            <span className="text-[var(--text)]">{stage.molecules_in}</span>
          ) : '—'}
        </div>
        <div className="py-3 text-xs text-center">
          {stage.molecules_out != null ? (
            <span className={filteredTotal > 0 ? 'text-[var(--accent)]' : 'text-[var(--success)]'}>
              {stage.molecules_out}
              {filteredTotal > 0 && <span className="text-[var(--destructive)]"> (−{filteredTotal})</span>}
            </span>
          ) : '—'}
        </div>
        <div className="py-3 text-xs text-center text-[var(--text-muted)]">
          {stage.credits_consumed ? `${stage.credits_consumed} cr` : '—'}
        </div>
        <div className="py-3 text-center">
          {stage.human_reviewed === true && (
            <span className="text-[10px] px-1.5 py-0.5 bg-green-500/10 text-green-600" title="Human reviewed">Reviewed</span>
          )}
          {stage.human_reviewed === false && (
            <span className="text-[10px] px-1.5 py-0.5 bg-amber-500/10 text-amber-600" title="Autonomous — not reviewed">Auto</span>
          )}
          {stage.human_reviewed == null && hasHuman && (
            <span className="text-[10px] px-1 py-0.5 bg-[var(--accent)]/10 text-[var(--accent)]" title="Legacy — review status unknown">👤</span>
          )}
        </div>
      </div>

      {expanded && (
        <div className="px-6 py-4 bg-[var(--bg-warm)] space-y-3 text-xs">
          {/* AI Recommendation */}
          {stage.ai_recommendation && (
            <div>
              <span className="font-medium text-[var(--text-muted)] uppercase text-[10px] tracking-wider">AI Recommendation</span>
              <p className="text-[var(--text-soft)] mt-1">{stage.ai_recommendation}</p>
            </div>
          )}

          {/* Human Decision */}
          {stage.human_decision && (
            <div>
              <span className="font-medium text-[var(--accent)] uppercase text-[10px] tracking-wider">Human Decision</span>
              <p className="text-[var(--text)] mt-1 font-medium">{stage.human_decision}</p>
              {stage.human_prompt && (
                <p className="text-[var(--text-muted)] mt-1 italic">&ldquo;{stage.human_prompt}&rdquo;</p>
              )}
              {stage.decision_reasoning && (
                <p className="text-[var(--text-soft)] mt-1">Reasoning: {stage.decision_reasoning}</p>
              )}
            </div>
          )}

          {/* Filtering breakdown */}
          {filteredTotal > 0 && (
            <div>
              <span className="font-medium text-[var(--text-muted)] uppercase text-[10px] tracking-wider">Molecules Filtered</span>
              <div className="flex flex-wrap gap-2 mt-1">
                {Object.entries(filtered).map(([reason, count]) => (
                  <span key={reason} className="px-2 py-0.5 bg-[var(--destructive)]/10 text-[var(--destructive)] text-[10px]">
                    {String(count)} {reason.replace(/_/g, ' ')}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* System metadata.
           *
           * `surface` and `client` get special treatment — surface as a
           * coloured chip (drives the analytics slice the user actually
           * cares about: "which client made this call?"), client as a
           * smaller secondary line under it. Everything else in
           * system_metadata still falls through to the generic key/value
           * grid below — so adding a new metadata key server-side shows
           * up automatically without a frontend change.
           */}
          {stage.system_metadata && Object.keys(stage.system_metadata).length > 0 && (() => {
            const meta = stage.system_metadata as Record<string, unknown>;
            const { surface: rawSurface, client: rawClient, ...rest } = meta;
            const surface = resolveSurface(rawSurface);
            const client = resolveClient(rawClient);
            const hasSurface = surface.raw.length > 0;
            const hasClient = client !== null;
            const restEntries = Object.entries(rest);
            return (
              <div>
                <span className="font-medium text-[var(--text-muted)] uppercase text-[10px] tracking-wider">System Preparation</span>
                {(hasSurface || hasClient) && (
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
                    {hasSurface && (
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium ${SURFACE_TONE_CLASSES[surface.meta.tone]}`}
                            title={surface.raw}>
                        <span aria-hidden="true">{surface.meta.icon}</span>
                        <span>{surface.meta.label}</span>
                      </span>
                    )}
                    {hasClient && (
                      <span className="text-[11px] text-[var(--text-muted)]">
                        <span className="text-[var(--text-muted)]/70">client:</span>{' '}
                        <span className="text-[var(--text)]">{client}</span>
                      </span>
                    )}
                  </div>
                )}
                {restEntries.length > 0 && (
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-1">
                    {restEntries.map(([k, v]) => (
                      <div key={k}>
                        <span className="text-[var(--text-muted)]">{k.replace(/_/g, ' ')}:</span>{' '}
                        <span className="text-[var(--text)]">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })()}

          {/* Curation method */}
          {stage.curation_method && (
            <div>
              <span className="font-medium text-[var(--text-muted)] uppercase text-[10px] tracking-wider">Curation Method</span>
              <pre className="text-[10px] text-[var(--text-soft)] bg-[var(--card)] p-2 border border-[var(--border)] mt-1 overflow-x-auto">
                {JSON.stringify(stage.curation_method, null, 2)}
              </pre>
            </div>
          )}

          {/* Tool arguments */}
          {stage.tool_arguments && (
            <div>
              <span className="font-medium text-[var(--text-muted)] uppercase text-[10px] tracking-wider">Tool Arguments</span>
              <pre className="text-[10px] text-[var(--text-soft)] bg-[var(--card)] p-2 border border-[var(--border)] mt-1 overflow-x-auto">
                {JSON.stringify(stage.tool_arguments, null, 2)}
              </pre>
            </div>
          )}

          {/* Context forward */}
          {stage.context_forward && (
            <div>
              <span className="font-medium text-[var(--text-muted)] uppercase text-[10px] tracking-wider">Context → Next Stage</span>
              <div className="flex flex-wrap gap-2 mt-1">
                {Object.entries(stage.context_forward).map(([k, v]) => (
                  <span key={k} className="px-2 py-0.5 bg-[var(--bg-warm)] border border-[var(--border)] text-[10px]">
                    {k}: {typeof v === 'string' && v.length > 40 ? v.slice(0, 40) + '...' : String(v)}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Timestamp */}
          <div className="text-[var(--text-muted)]">
            <Clock className="h-3 w-3 inline mr-1" />{formatTime(stage.timestamp)}
            {stage.execution_time_ms && ` · ${stage.execution_time_ms}ms`}
          </div>
        </div>
      )}
    </div>
  );
}

export function FunnelDetail({ funnelId, onCollapse }: { funnelId: string; onCollapse?: () => void }) {
  const { data, isLoading, error } = useFunnelAudit(funnelId);
  const [expandedStages, setExpandedStages] = useState<Set<number>>(new Set());

  const toggleStage = (idx: number) => {
    setExpandedStages((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  const collapseAllStages = () => setExpandedStages(new Set());

  if (isLoading) return <div className="px-6 py-4 text-sm text-[var(--text-muted)]">Loading audit log...</div>;
  if (error) return <div className="px-6 py-4 text-sm text-[var(--destructive)]">Failed to load audit: {(error as Error).message}</div>;
  if (!data?.stages?.length) return <div className="px-6 py-4 text-sm text-[var(--text-muted)]">No audit data.</div>;

  const stages = data.stages;

  return (
    <div className="border-t border-[var(--border)] bg-[var(--bg)] ml-6 border-l-2 border-l-[var(--accent)]/30">
      {/* Summary bar */}
      <div className="px-6 py-3 bg-[var(--bg-warm)] flex items-center gap-4 text-xs border-b border-[var(--border)]">
        <span className="text-[var(--text-muted)]">{stages.length} stages</span>
        <span className="text-[var(--text-muted)]">{data.total_credits?.toFixed(1)} credits</span>
        <span className="text-[var(--text-muted)]">{formatTime(stages[0]?.timestamp)} → {formatTime(stages[stages.length - 1]?.timestamp)}</span>
        <div className="ml-auto flex items-center gap-2">
          {expandedStages.size > 0 && (
            <button
              onClick={collapseAllStages}
              className="flex items-center gap-1 px-2 py-1 text-[10px] font-medium text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
            >
              <X className="h-3 w-3" />
              Collapse all
            </button>
          )}
          {onCollapse && (
            <button
              onClick={onCollapse}
              className="flex items-center gap-1 px-2 py-1 text-[10px] font-medium text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
              title="Close funnel"
            >
              <ChevronUp className="h-3 w-3" />
              Close
            </button>
          )}
        </div>
      </div>

      {/* Stage header */}
      <div
        className="grid text-[10px] font-medium text-[var(--text-muted)] uppercase tracking-wider border-b border-[var(--border)] bg-[var(--bg-warm)]"
        style={{ gridTemplateColumns: '32px 40px 1fr 80px 80px 80px 80px' }}
      >
        <div className="px-2 py-2" />
        <div className="py-2 text-center">#</div>
        <div className="py-2">Stage</div>
        <div className="py-2 text-center">In</div>
        <div className="py-2 text-center">Out</div>
        <div className="py-2 text-center">Cost</div>
        <div className="py-2 text-center text-[10px] uppercase tracking-wider" title="Human review status">Review</div>
      </div>

      {/* Stage rows — scrollable container */}
      <div className="max-h-[60vh] overflow-y-auto">
        {stages.map((stage: any) => (
          <StageRow
            key={stage.stage_index}
            stage={stage}
            expanded={expandedStages.has(stage.stage_index)}
            onToggle={() => toggleStage(stage.stage_index)}
          />
        ))}
      </div>
    </div>
  );
}
