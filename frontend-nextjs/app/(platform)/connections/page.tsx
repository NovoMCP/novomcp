'use client';

import type { ReactNode } from 'react';
import { Server, Plug, CheckCircle2, Circle, AlertTriangle, RefreshCw, Loader2 } from 'lucide-react';
import { useCapabilities, type ServiceCapability } from '@/core/api/useCapabilities';
import AiProviderCard from '@/components/settings/AiProviderCard';
import ComplianceCard from '@/components/settings/ComplianceCard';

// Connections — a read-only view of what this deployment has wired: the engine
// and each optional compute service, with live health and the env var to set
// for anything that isn't connected yet. Configuration is env-driven today;
// in-app credential editing lands in a later phase.

type Row = { key: string; label: string; env: string; wired: boolean; reachable: boolean };

function statusOf(wired: boolean, reachable: boolean): { label: string; tone: 'ok' | 'warn' | 'off'; icon: ReactNode } {
  if (!wired) return { label: 'not configured', tone: 'off', icon: <Circle className="h-4 w-4" /> };
  if (reachable) return { label: 'connected', tone: 'ok', icon: <CheckCircle2 className="h-4 w-4" /> };
  return { label: 'wired · unreachable', tone: 'warn', icon: <AlertTriangle className="h-4 w-4" /> };
}

const TONE_CLASS: Record<'ok' | 'warn' | 'off', string> = {
  ok: 'text-emerald-500',
  warn: 'text-amber-500',
  off: 'text-[var(--text-muted)]',
};

export default function ConnectionsPage() {
  const { data: caps, isLoading, isFetching, refetch, error } = useCapabilities();

  const services: Row[] = caps
    ? [
        ...Object.entries(caps.services).map(([key, s]: [string, ServiceCapability]) => ({
          key,
          label: s.label,
          env: s.env,
          wired: s.wired,
          reachable: s.reachable,
        })),
        {
          key: 'compliance',
          label: 'Compliance service',
          env: caps.compliance.env,
          wired: caps.compliance.wired,
          reachable: caps.compliance.reachable,
        },
      ]
    : [];

  const connectedCount = services.filter((s) => s.wired && s.reachable).length;

  return (
    <div className="space-y-6 max-w-5xl">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-[var(--text)]" style={{ fontFamily: 'var(--serif)' }}>
            Connections
          </h1>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            What this deployment has wired — the engine and each optional service.
          </p>
        </div>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="flex items-center gap-2 px-3 py-1.5 text-sm border border-[var(--border)] text-[var(--text-soft)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors disabled:opacity-50"
        >
          {isFetching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
          Recheck
        </button>
      </div>

      {error && (
        <div className="flex items-start gap-2 px-4 py-3 border border-[var(--destructive)]/30 bg-[var(--destructive)]/5 text-sm text-[var(--destructive)]">
          <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>Couldn&apos;t load connection status. Is the dashboard&apos;s local API reachable?</span>
        </div>
      )}

      {/* Engine */}
      <Card icon={<Server className="h-4 w-4" />} title="Engine">
        {isLoading ? (
          <Skeleton />
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
            <Field label="Status">
              {caps?.engine.reachable ? (
                <span className="flex items-center gap-1.5 text-emerald-500">
                  <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
                  reachable
                </span>
              ) : (
                <span className="text-[var(--destructive)]">unreachable</span>
              )}
            </Field>
            <Field label="Version">{caps?.engine.version ?? '—'}</Field>
            <Field label="Mode">{caps?.spineMode ?? '—'}</Field>
            <Field label="Engine URL">
              <span className="text-xs font-mono break-all">{caps?.engine.url ?? '—'}</span>
            </Field>
          </div>
        )}
        {!isLoading && !caps?.engine.reachable && (
          <p className="mt-4 text-xs text-[var(--text-muted)]">
            The engine isn&apos;t responding. Run <code className="font-mono">python main_https.py</code> from the{' '}
            <code className="font-mono">orchestrator/</code> directory, then Recheck.
          </p>
        )}
      </Card>

      {/* Services */}
      <Card
        icon={<Plug className="h-4 w-4" />}
        title="Services"
        subtitle={caps ? `${connectedCount} of ${services.length} connected` : undefined}
      >
        {isLoading ? (
          <Skeleton rows={4} />
        ) : (
          <div className="divide-y divide-[var(--border)] -mx-6 -my-5">
            {services.map((s) => {
              const st = statusOf(s.wired, s.reachable);
              return (
                <div key={s.key} className="px-6 py-3 flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <p className="text-sm text-[var(--text)]">{s.label}</p>
                    <p className="text-xs text-[var(--text-muted)]">
                      {s.wired ? (
                        <code className="font-mono">{s.env}</code>
                      ) : (
                        <>
                          set <code className="font-mono">{s.env}</code> to connect
                        </>
                      )}
                    </p>
                  </div>
                  <span className={`flex items-center gap-1.5 text-xs shrink-0 ${TONE_CLASS[st.tone]}`}>
                    {st.icon}
                    {st.label}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {/* AI provider (SecretStore-backed, write-only keys) */}
      <AiProviderCard />

      {/* Compliance service consumer (SecretStore-backed, write-only key) */}
      <ComplianceCard />

      {/* How-to-wire note — configuration is env-driven for now */}
      <div className="border border-[var(--border)] bg-[var(--bg-warm)] px-5 py-4 text-sm text-[var(--text-soft)]">
        <p>
          Services are wired through environment variables today. Set the variable shown next to each one (in your{' '}
          <code className="font-mono">.env</code> or the engine&apos;s environment) and press Recheck. Editing credentials
          in-app is coming in a later release. See{' '}
          <a
            href="https://github.com/NovoMCP/novomcp/tree/main/docs/deploying-services"
            target="_blank"
            rel="noreferrer"
            className="text-[var(--accent)] hover:underline"
          >
            deploying services
          </a>{' '}
          for each service&apos;s setup.
        </p>
      </div>
    </div>
  );
}

function Card({ icon, title, subtitle, children }: { icon: ReactNode; title: string; subtitle?: string; children: ReactNode }) {
  return (
    <div className="bg-[var(--card)] border border-[var(--border)]">
      <div className="px-6 py-4 border-b border-[var(--border)] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-[var(--text-muted)]">{icon}</span>
          <h2 className="text-sm font-medium tracking-wide uppercase text-[var(--text-muted)]">{title}</h2>
        </div>
        {subtitle && <span className="text-xs text-[var(--text-muted)]">{subtitle}</span>}
      </div>
      <div className="px-6 py-5">{children}</div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-[var(--text-muted)] mb-1">{label}</p>
      <p className="text-base text-[var(--text)]">{children}</p>
    </div>
  );
}

function Skeleton({ rows = 1 }: { rows?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-5 bg-[var(--bg-warm)] animate-pulse" />
      ))}
    </div>
  );
}
