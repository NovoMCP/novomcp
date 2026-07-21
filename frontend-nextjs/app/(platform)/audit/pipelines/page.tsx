'use client';

import { useState, useEffect, useRef } from 'react';
import { useSearchParams } from 'next/navigation';
import { useAuth } from '@/core/auth/provider';
import { useFunnelRuns, useFunnelAudit } from '@/core/api/admin-client';
import { FlaskConical, ChevronDown, ChevronUp } from 'lucide-react';
import { FunnelDetail, formatTime } from '@/components/funnel/FunnelDetail';


export default function PipelinesAuditPage() {
  const { user } = useAuth();
  const { data, isLoading, error } = useFunnelRuns();
  const [expandedFunnel, setExpandedFunnel] = useState<string | null>(null);
  const searchParams = useSearchParams();
  const deepLinkFunnelId = searchParams?.get('funnel_id') || null;
  const targetRowRef = useRef<HTMLDivElement | null>(null);

  const funnels = data?.funnels || [];

  // Auto-expand on deep-link from Chrome extension / Word add-in / Workbench.
  // The chrome-ext sidebar renders an "Open in NovoMCP" CTA that lands here
  // with ?funnel_id=… — we expand that funnel and scroll it into view.
  // If the funnel_id isn't in the current list (different org, recent run
  // not yet synced), the FunnelDetail still fetches by ID directly via
  // get_funnel_audit so the timeline renders regardless.
  useEffect(() => {
    if (!deepLinkFunnelId) return;
    if (isLoading) return;
    setExpandedFunnel(deepLinkFunnelId);
    requestAnimationFrame(() => {
      targetRowRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }, [deepLinkFunnelId, isLoading]);

  const collapseFunnel = () => setExpandedFunnel(null);

  // If the deep-linked funnel isn't in the list yet (race condition with
  // recent runs / different org), surface it explicitly at the top so the
  // user doesn't think the link is broken.
  const deepLinkInList = deepLinkFunnelId
    ? funnels.some((f: any) => f.funnel_id === deepLinkFunnelId)
    : true;
  const showDeepLinkBanner = deepLinkFunnelId && !deepLinkInList && !isLoading;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <a href="/audit/tools" className="text-sm text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors">Tools</a>
          <span className="text-sm text-[var(--text-muted)]">/</span>
          <span className="text-sm text-[var(--accent)] font-medium">Pipelines</span>
        </div>
        <h1 className="text-2xl font-semibold text-[var(--text)]" style={{ fontFamily: 'var(--serif)' }}>Pipeline Audit Log</h1>
        <p className="text-sm text-[var(--text-muted)]">Reproducibility trail for discovery funnel runs — every stage, every decision</p>
      </div>

      {/* Funnel list */}
      <div className="bg-[var(--card)] border border-[var(--border)]">
        {isLoading && (
          <div className="px-6 py-8 text-center text-[var(--text-muted)]">Loading...</div>
        )}
        {!isLoading && error && (
          <div className="px-6 py-8 text-center text-[var(--destructive)]">
            Failed to load pipeline runs: {(error as Error).message}
          </div>
        )}
        {!isLoading && funnels.length === 0 && (
          <div className="px-6 py-12 text-center">
            <FlaskConical className="h-8 w-8 text-[var(--text-muted)] mx-auto mb-2" />
            <p className="text-[var(--text-muted)]">No pipeline audit logs yet</p>
            <p className="text-xs text-[var(--text-muted)] mt-1">
              Run an interactive discovery funnel via Claude to generate an audit trail
            </p>
          </div>
        )}
        {showDeepLinkBanner && (
          <div className="border-b border-[var(--border)]">
            <div
              ref={targetRowRef}
              className="px-6 py-4 bg-[var(--bg-warm)]"
            >
              <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider mb-1">Linked from another surface</div>
              <div className="font-mono text-sm text-[var(--text)]">{deepLinkFunnelId}</div>
              <div className="text-xs text-[var(--text-muted)] mt-1">
                Loading audit timeline directly…
              </div>
            </div>
            <FunnelDetail funnelId={deepLinkFunnelId!} onCollapse={collapseFunnel} />
          </div>
        )}
        {funnels.map((funnel: any) => (
          <div key={funnel.funnel_id} ref={funnel.funnel_id === deepLinkFunnelId ? targetRowRef : undefined}>
            <div
              className={`flex items-center gap-4 px-6 py-4 cursor-pointer hover:bg-[var(--bg)] transition-colors border-b border-[var(--border)] ${funnel.funnel_id === deepLinkFunnelId ? 'bg-[var(--bg-warm)]' : ''}`}
              onClick={() => setExpandedFunnel(expandedFunnel === funnel.funnel_id ? null : funnel.funnel_id)}
            >
              <div className="flex-shrink-0">
                {expandedFunnel === funnel.funnel_id ?
                  <ChevronUp className="h-4 w-4 text-[var(--text-muted)]" /> :
                  <ChevronDown className="h-4 w-4 text-[var(--text-muted)]" />
                }
              </div>
              <div className="flex-1 min-w-0">
                <div className="font-mono text-sm text-[var(--text)] truncate">{funnel.funnel_id}</div>
                <div className="text-xs text-[var(--text-muted)]">
                  {funnel.stage_count} stages · {formatTime(funnel.started_at)}
                </div>
              </div>
              <div className="text-xs text-[var(--text-muted)] flex-shrink-0">
                {funnel.total_credits?.toFixed(1)} credits
              </div>
            </div>
            {expandedFunnel === funnel.funnel_id && (
              <FunnelDetail funnelId={funnel.funnel_id} onCollapse={collapseFunnel} />
            )}
          </div>
        ))}
      </div>

    </div>
  );
}
