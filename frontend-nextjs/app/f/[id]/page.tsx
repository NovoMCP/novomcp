'use client';

// Shareable, read-only funnel viewer — the lightweight "FunnelViewer" surface
// of Studio. Short URL (/f/{id}) anyone in the org can open; renders the same
// audit timeline as the dashboard's Pipeline Audit, with no dashboard chrome.
// (The interactive /studio/funnels/{id} cockpit comes with the Studio SPA.)
// See docs/NovoMCP/Product/web-studio-scope.md.
import { use, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/core/auth/provider';
import { FunnelDetail } from '@/components/funnel/FunnelDetail';
import { FlaskConical, ExternalLink } from 'lucide-react';

export default function SharedFunnelPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { isAuthenticated, isLoading } = useAuth();
  const router = useRouter();

  // Same-origin auth: requires a session today (org-scoped). Public funnels may
  // be opened up later — at which point this guard becomes conditional.
  // OSS mode: auth provider auto-provisions a local user, so this branch
  // never fires; the env guard prevents a rogue /login redirect anyway.
  useEffect(() => {
    if (process.env.NEXT_PUBLIC_REQUIRE_AUTH === 'true' && !isLoading && !isAuthenticated) {
      router.push(`/login?next=${encodeURIComponent(`/f/${id}`)}`);
    }
  }, [isAuthenticated, isLoading, router, id]);

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--bg)]">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[var(--accent)]" />
      </div>
    );
  }

  if (!isAuthenticated) return null;

  return (
    <div className="min-h-screen bg-[var(--bg)]">
      {/* Focused top bar — no dashboard sidebar */}
      <header className="border-b border-[var(--border)] bg-[var(--bg)]/95 backdrop-blur-sm sticky top-0 z-10">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-3 flex items-center gap-3">
          <a href="/dashboard" className="flex items-center gap-2 group" title="NovoMCP">
            <FlaskConical className="h-5 w-5 text-[var(--accent)]" />
            <span className="text-sm font-semibold tracking-wide group-hover:text-[var(--accent)] transition-colors" style={{ fontFamily: 'var(--serif)' }}>
              NovoMCP
            </span>
          </a>
          <span className="text-[var(--text-muted)]">/</span>
          <span className="text-sm text-[var(--text-muted)]">Funnel</span>
          <code className="text-sm text-[var(--text)] font-mono truncate">{id}</code>
          <a
            href={`/audit/pipelines?funnel_id=${encodeURIComponent(id)}`}
            className="ml-auto flex items-center gap-1.5 text-xs text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
            title="Open in the full Pipeline Audit dashboard"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            Open in dashboard
          </a>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 sm:px-6 py-6">
        <div className="mb-4">
          <h1 className="text-xl font-semibold text-[var(--text)]" style={{ fontFamily: 'var(--serif)' }}>
            Discovery funnel
          </h1>
          <p className="text-sm text-[var(--text-muted)]">
            Read-only reproducibility trail — every stage, every decision.
          </p>
        </div>

        <div className="bg-[var(--card)] border border-[var(--border)]">
          {/* FunnelDetail renders its own loading / empty / error states. */}
          <FunnelDetail funnelId={id} />
        </div>
      </main>
    </div>
  );
}
