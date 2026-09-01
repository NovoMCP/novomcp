'use client';

import { useEffect, useState, type ReactNode } from 'react';
import Link from 'next/link';

// OSS Control Panel dashboard. Reads local /api/local/health and
// /api/local/audit (which read the engine + ~/.novo/audit.jsonl directly, no
// managed backend needed). Leads with a status hero — engine health and how
// much of the 68-tool catalog is unlocked — then capability tiles, recent
// activity, and first-run actions. Every surface uses the marketplace's visual
// language so the app reads as one product.

interface HealthResp {
  engine_url: string;
  engine_reachable: boolean;
  version: string | null;
  tools_visible: number | null;
  tools_total: number | null;
  rest_paths: number | null;
  providers: Record<string, boolean>;
}

interface AuditResp {
  audit_path: string;
  error: string | null;
  entries: Array<{ event: string; ts?: string; payload?: { tool?: string; success?: boolean; ts?: string } }>;
}

// Capabilities shown as tiles: a monogram, a short name, and status.
const CAPABILITIES: Record<string, { short: string; mono: string }> = {
  admet: { short: 'ADMET', mono: 'AD' },
  docking: { short: 'Docking', mono: 'AG' },
  md: { short: 'Dynamics', mono: 'MD' },
  structure: { short: 'Structure', mono: 'OF' },
  qm: { short: 'Quantum', mono: 'QM' },
  nnp: { short: 'NN potentials', mono: 'NN' },
  compliance: { short: 'Compliance', mono: 'CO' },
  molecule_index: { short: 'Molecule index', mono: 'IX' },
  omics: { short: 'Omics', mono: 'OM' },
  literature: { short: 'Literature', mono: 'LI' },
  materials: { short: 'Materials', mono: 'MP' },
};

export default function DashboardPage() {
  const [health, setHealth] = useState<HealthResp | null>(null);
  const [audit, setAudit] = useState<AuditResp | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      fetch('/api/local/health').then((r) => (r.ok ? r.json() : null)),
      fetch('/api/local/audit?limit=6').then((r) => (r.ok ? r.json() : null)),
    ]).then(([h, a]) => {
      setHealth(h);
      setAudit(a);
      setLoading(false);
    });
  }, []);

  const engineOk = !!health?.engine_reachable;
  const providers = Object.entries(health?.providers ?? {}).filter(([key]) => CAPABILITIES[key]);
  const onCount = providers.filter(([, on]) => on).length;
  const ordered = [...providers].sort((a, b) => Number(b[1]) - Number(a[1]));
  const visible = health?.tools_visible ?? null;
  const total = health?.tools_total ?? 68;
  const pct = visible != null ? Math.round((visible / total) * 100) : 0;
  // First run: engine is up but the user hasn't done anything yet (no audit
  // history). Surface a welcome with the two things worth doing first.
  const firstRun = !loading && engineOk && (audit?.entries?.length ?? 0) === 0;

  return (
    <div className="max-w-5xl">
      {/* header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.16em] text-[var(--text-muted)]">
            <span
              className={`h-1.5 w-1.5 rounded-full ${engineOk ? 'bg-emerald-500' : 'bg-[var(--destructive)]'}`}
              style={engineOk ? { boxShadow: '0 0 0 3px rgba(116,176,131,.18)' } : undefined}
            />
            NovoMCP · Local single-user
          </div>
          <h1 className="text-3xl text-[var(--text)] mt-2" style={{ fontFamily: 'var(--serif)', fontWeight: 500 }}>
            Control Panel
          </h1>
        </div>
        {health?.version && (
          <span className="text-xs text-[var(--text-muted)] font-mono border border-[var(--border)] px-2.5 py-1 rounded-sm">
            v{health.version}
          </span>
        )}
      </div>

      {/* status hero */}
      <div className="mt-7 grid md:grid-cols-[1.4fr_1fr] gap-px bg-[var(--border)] border border-[var(--border)] rounded-lg overflow-hidden">
        <div className="bg-[var(--card)] px-6 py-6">
          <p className="text-[11px] uppercase tracking-wider text-[var(--text-muted)]">Engine</p>
          {loading ? (
            <div className="h-8 w-40 bg-[var(--bg-warm)] animate-pulse rounded mt-2" />
          ) : engineOk ? (
            <div className="flex items-center gap-3 mt-1" style={{ fontFamily: 'var(--serif)' }}>
              <span
                className="h-2.5 w-2.5 rounded-full bg-emerald-500 animate-pulse"
                style={{ boxShadow: '0 0 0 4px rgba(116,176,131,.18)' }}
              />
              <span className="text-3xl text-[var(--text)]">Healthy</span>
            </div>
          ) : (
            <div className="text-3xl text-[var(--destructive)] mt-1" style={{ fontFamily: 'var(--serif)' }}>
              Unreachable
            </div>
          )}
          <div className="flex flex-wrap gap-x-8 gap-y-4 mt-6">
            <HeroMeta k="Version" v={health?.version ?? '—'} />
            <HeroMeta k="REST endpoints" v={health?.rest_paths ?? '—'} />
            <HeroMeta k="Engine URL" v={health?.engine_url?.replace(/^https?:\/\//, '') ?? '—'} mono />
          </div>
          {!loading && !engineOk && (
            <p className="text-xs text-[var(--text-muted)] mt-5">
              Run <code className="font-mono">python main_https.py</code> from{' '}
              <code className="font-mono">orchestrator/</code>, then reload.
            </p>
          )}
        </div>
        <div className="bg-[var(--card)] px-6 py-6 flex items-center gap-5">
          <div
            className="relative h-[104px] w-[104px] flex-none rounded-full grid place-items-center"
            style={{ background: `conic-gradient(var(--accent) ${pct}%, var(--border) 0)` }}
            role="img"
            aria-label={`${visible ?? 0} of ${total} tools unlocked`}
          >
            <div className="absolute inset-[11px] rounded-full bg-[var(--card)]" />
            <div className="relative text-center" style={{ fontFamily: 'var(--serif)' }}>
              <span className="text-2xl text-[var(--text)] tabular-nums">{visible ?? '—'}</span>
              <span className="text-sm text-[var(--text-muted)] tabular-nums">/{total}</span>
            </div>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-wider text-[var(--text-muted)]">Tools unlocked</p>
            <p className="text-[13px] text-[var(--text-soft)] mt-1.5 max-w-[20ch]">
              Wire a service to unlock more of the catalog.
            </p>
          </div>
        </div>
      </div>

      {/* first-run welcome — brand-new user, engine up, nothing done yet */}
      {firstRun && (
        <div className="mt-6 rounded-lg border border-[var(--accent)]/25 bg-[var(--accent)]/[0.07] px-6 py-5">
          <p className="text-lg text-[var(--text)]" style={{ fontFamily: 'var(--serif)' }}>
            Welcome — you&apos;re running NovoMCP locally.
          </p>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            Nothing to provision. Two good ways to start:
          </p>
          <div className="flex flex-wrap gap-3 mt-4">
            <Link
              href="/profile"
              className="inline-flex items-center gap-2 bg-[var(--accent)] text-white text-sm font-medium px-4 py-2 rounded-md hover:brightness-105 transition"
            >
              Profile a molecule →
            </Link>
            <Link
              href="/connections"
              className="inline-flex items-center gap-2 border border-[var(--border)] text-[var(--text-soft)] text-sm px-4 py-2 rounded-md hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors"
            >
              Connect a service →
            </Link>
          </div>
        </div>
      )}

      {/* capabilities */}
      <div className="mt-9">
        <div className="flex items-baseline justify-between mb-3">
          <p className="text-[11px] uppercase tracking-wider text-[var(--text-muted)]">
            Capabilities{!loading && providers.length > 0 && ` · ${onCount} on`}
          </p>
          <Link href="/connections" className="text-[12.5px] text-[var(--accent)] hover:underline">
            Configure in Connections →
          </Link>
        </div>
        <div className="grid gap-2.5 [grid-template-columns:repeat(auto-fill,minmax(208px,1fr))]">
          {loading
            ? Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="h-[58px] bg-[var(--card)] border border-[var(--border)] rounded-md animate-pulse" />
              ))
            : ordered.map(([key, on]) => {
                const c = CAPABILITIES[key];
                return (
                  <div
                    key={key}
                    className={`flex items-center gap-3 bg-[var(--card)] border rounded-md px-3.5 py-3 ${
                      on ? 'border-emerald-500/30' : 'border-[var(--border)]'
                    }`}
                  >
                    <div
                      className="h-8 w-8 flex-none grid place-items-center rounded-lg bg-[var(--bg-warm)] border border-[var(--border)] text-[13px] text-[var(--text)]"
                      style={{ fontFamily: 'var(--serif)' }}
                    >
                      {c.mono}
                    </div>
                    <span className={`text-[13.5px] font-semibold truncate ${on ? 'text-[var(--text)]' : 'text-[var(--text-soft)]'}`}>
                      {c.short}
                    </span>
                    <span
                      className={`ml-auto h-1.5 w-1.5 flex-none rounded-full ${on ? 'bg-emerald-500' : 'bg-[var(--text-muted)] opacity-50'}`}
                      style={on ? { boxShadow: '0 0 0 3px rgba(116,176,131,.15)' } : undefined}
                    />
                  </div>
                );
              })}
        </div>
      </div>

      {/* activity + get started */}
      <div className="mt-9 grid lg:grid-cols-[1.3fr_1fr] gap-4">
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-lg overflow-hidden">
          <div className="px-5 py-3.5 border-b border-[var(--border)] flex items-center justify-between">
            <h2 className="text-[11px] uppercase tracking-wider text-[var(--text-muted)]">Recent activity</h2>
            <span className="text-[11px] font-mono text-[var(--text-muted)]">
              {audit?.audit_path?.replace(/^.*\/\.novo/, '~/.novo') ?? ''}
            </span>
          </div>
          <div className="px-5 py-1">
            {loading ? (
              <div className="py-8 text-sm text-[var(--text-muted)]">Loading…</div>
            ) : (audit?.entries?.length ?? 0) === 0 ? (
              <div className="py-8 text-sm text-[var(--text-muted)]">
                No tool calls yet — profile a molecule or connect an MCP client to see activity here.
              </div>
            ) : (
              audit?.entries.map((e, i) => {
                const tool = e.payload?.tool || e.event || 'unknown';
                const ts = e.ts || e.payload?.ts;
                const ok = e.payload?.success;
                return (
                  <div key={i} className="flex items-center gap-3 py-2.5 border-b border-[var(--border)] last:border-0">
                    <span className={`h-1.5 w-1.5 flex-none rounded-full ${ok === false ? 'bg-amber-500' : 'bg-emerald-500'}`} />
                    <span className="font-mono text-[13px] text-[var(--text)] truncate">{tool}</span>
                    {ts && <span className="ml-auto text-[11.5px] text-[var(--text-muted)] shrink-0">{new Date(ts).toLocaleString()}</span>}
                  </div>
                );
              })
            )}
          </div>
        </div>

        <div className="bg-[var(--card)] border border-[var(--border)] rounded-lg overflow-hidden">
          <div className="px-5 py-3.5 border-b border-[var(--border)]">
            <h2 className="text-[11px] uppercase tracking-wider text-[var(--text-muted)]">Get started</h2>
          </div>
          <div className="p-3">
            <ActionCard href="/profile" internal title="Profile a molecule" body="Paste a SMILES, get computed properties in about a second." />
            <ActionCard href="/connections" internal title="Connect a service" body="Plug in ADMET, docking, MD, QM from the marketplace." />
            <ActionCard
              href="https://docs.novomcp.com/connecting-mcp-clients/"
              title="Connect an MCP client"
              body="Claude, Cursor, Zed, and any MCP-compatible assistant."
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function HeroMeta({ k, v, mono }: { k: string; v: ReactNode; mono?: boolean }) {
  return (
    <div>
      <p className="text-[10.5px] uppercase tracking-wider text-[var(--text-muted)]">{k}</p>
      <p className={`mt-1 text-[var(--text-soft)] tabular-nums ${mono ? 'font-mono text-[12.5px]' : 'text-[15px]'}`}>{v}</p>
    </div>
  );
}

function ActionCard({ href, title, body, internal }: { href: string; title: string; body: string; internal?: boolean }) {
  const cls =
    'block border border-[var(--border)] rounded-md px-3.5 py-3 my-1.5 hover:border-[var(--accent)]/50 hover:bg-[var(--bg-warm)] transition-colors group';
  const inner = (
    <>
      <div className="flex items-center justify-between text-sm font-semibold text-[var(--text)]">
        {title}
        <span className="text-[var(--accent)] opacity-0 group-hover:opacity-100 transition-opacity">→</span>
      </div>
      <p className="text-[12.5px] text-[var(--text-muted)] mt-1 leading-snug">{body}</p>
    </>
  );
  return internal ? (
    <Link href={href} className={cls}>
      {inner}
    </Link>
  ) : (
    <a href={href} target="_blank" rel="noreferrer" className={cls}>
      {inner}
    </a>
  );
}
