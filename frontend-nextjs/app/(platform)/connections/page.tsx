'use client';

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { RefreshCw, Loader2, X, ArrowUpRight, CheckCircle2, AlertTriangle } from 'lucide-react';
import { useCapabilities } from '@/core/api/useCapabilities';
import {
  CONNECTORS,
  CATEGORIES,
  CATEGORY_LABEL,
  type Connector,
  type Category,
} from '@/core/connectors/registry';
import { loadConnector, saveConnector } from '@/core/connectors/io';

// Connections — a connector marketplace. Every service NovoMCP can talk to
// (ours or third-party) is a tile with live status; clicking one opens a detail
// panel with what it is, how the engine uses it, and a form that writes the
// right config to the local store. One registry drives the whole surface.

type Status = 'ok' | 'warn' | 'off';
const PILL: Record<Status, string> = {
  ok: 'text-emerald-500 border-emerald-500/25 bg-emerald-500/10',
  warn: 'text-amber-500 border-amber-500/25 bg-amber-500/10',
  off: 'text-[var(--text-muted)] border-[var(--border)] bg-[var(--bg-warm)]',
};
const DOT: Record<Status, string> = {
  ok: 'bg-emerald-500',
  warn: 'bg-amber-500',
  off: 'bg-[var(--text-muted)]',
};

export default function ConnectionsPage() {
  const { data: caps, isFetching, refetch } = useCapabilities();
  const [cfg, setCfg] = useState<{
    ai: Record<string, boolean>;
    obs: boolean;
    data: Set<string>;
    services: Set<string>;
    compliance: boolean;
  } | null>(null);
  const [active, setActive] = useState<Category | 'all'>('all');
  const [selected, setSelected] = useState<Connector | null>(null);

  const loadCfg = useCallback(async () => {
    const get = (u: string) => fetch(u, { cache: 'no-store' }).then((r) => r.json()).catch(() => null);
    const [llm, obs, conn, svc, comp] = await Promise.all([
      get('/api/local/llm-config'),
      get('/api/local/observability-config'),
      get('/api/local/connections'),
      get('/api/local/service-config'),
      get('/api/local/compliance-config'),
    ]);
    const ai: Record<string, boolean> = {
      openai: !!llm?.providers?.openai?.keySet,
      anthropic: !!llm?.providers?.anthropic?.keySet,
      azure: !!llm?.providers?.azure?.keySet,
      ollama: !!llm?.providers?.ollama?.url,
    };
    setCfg({
      ai,
      obs: !!(obs?.enabled && obs?.endpoint),
      data: new Set<string>((conn?.connections || []).map((x: { type: string }) => x.type)),
      services: new Set<string>(svc?.configured || []),
      compliance: !!comp?.url,
    });
  }, []);
  useEffect(() => {
    loadCfg();
  }, [loadCfg]);

  const statusOf = useCallback(
    (c: Connector): { tone: Status; label: string } => {
      // Configured = a value is saved in the local store — what the engine will
      // actually load. This is the authoritative "did the user set it" signal.
      let configured = false;
      if (c.category === 'service') configured = !!(c.urlEnv && cfg?.services.has(c.urlEnv));
      else if (c.category === 'compliance') configured = !!cfg?.compliance;
      else if (c.category === 'ai') configured = !!cfg?.ai[c.id];
      else if (c.category === 'observability') configured = !!cfg?.obs;
      else if (c.category === 'data') configured = !!cfg?.data.has(c.id);

      // Reachability is only known when this server shares the engine's env
      // (docker-compose / hosted). In pure local self-host it stays unknown, so
      // we don't claim "unreachable" for something we simply can't ping.
      let reach: boolean | null = null;
      if (c.category === 'service') {
        const s = caps?.services?.[c.id];
        if (s?.wired) reach = s.reachable;
      } else if (c.category === 'compliance') {
        const s = caps?.compliance;
        if (s?.wired) reach = s.reachable;
      }

      if (reach === true) return { tone: 'ok', label: 'connected' };
      if (reach === false) return { tone: 'warn', label: 'unreachable' };
      if (configured) return { tone: 'ok', label: 'configured' };
      return { tone: 'off', label: 'not configured' };
    },
    [caps, cfg]
  );

  const connectedCount = useMemo(
    () => CONNECTORS.filter((c) => statusOf(c).tone === 'ok').length,
    [statusOf]
  );
  const counts = useMemo(() => {
    const m: Record<string, number> = { all: CONNECTORS.length };
    for (const cat of CATEGORIES.slice(1)) m[cat.id] = CONNECTORS.filter((c) => c.category === cat.id).length;
    return m;
  }, []);

  const list = active === 'all' ? CONNECTORS : CONNECTORS.filter((c) => c.category === active);

  const recheck = () => {
    refetch();
    loadCfg();
  };

  return (
    <div className="max-w-6xl">
      {/* header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-[var(--text)]" style={{ fontFamily: 'var(--serif)' }}>
            Connections
          </h1>
          <p className="text-sm text-[var(--text-muted)] mt-1 max-w-xl">
            Plug in a service and NovoMCP starts using it — ours or your own. Set a value, save, and the tools it
            unlocks appear.
          </p>
        </div>
        <div className="flex items-center gap-4 shrink-0">
          <div className="text-right">
            <div className="text-2xl font-semibold text-[var(--text)] tabular-nums" style={{ fontFamily: 'var(--serif)' }}>
              {connectedCount}
            </div>
            <div className="text-[11px] uppercase tracking-wider text-[var(--text-muted)]">
              of {CONNECTORS.length} connected
            </div>
          </div>
          <button
            onClick={recheck}
            disabled={isFetching}
            className="flex items-center gap-2 px-3 py-1.5 text-sm border border-[var(--border)] text-[var(--text-soft)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors disabled:opacity-50"
          >
            {isFetching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            Recheck
          </button>
        </div>
      </div>

      {/* category tabs */}
      <div className="flex gap-1 mt-6 mb-5 border-b border-[var(--border)] overflow-x-auto" role="tablist">
        {CATEGORIES.map((cat) => {
          const on = active === cat.id;
          return (
            <button
              key={cat.id}
              role="tab"
              aria-selected={on}
              onClick={() => setActive(cat.id)}
              className={`flex items-center gap-2 whitespace-nowrap px-3.5 py-2.5 text-[13.5px] border-b-2 -mb-px transition-colors ${
                on
                  ? 'border-[var(--accent)] text-[var(--text)]'
                  : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text-soft)]'
              }`}
            >
              {cat.name}
              <span
                className={`text-[11px] tabular-nums px-1.5 rounded-full border ${
                  on ? 'text-[var(--accent)] border-[var(--accent)]/30' : 'text-[var(--text-muted)] border-[var(--border)]'
                }`}
              >
                {counts[cat.id]}
              </span>
            </button>
          );
        })}
      </div>

      {/* grid */}
      <div className="grid gap-3.5 [grid-template-columns:repeat(auto-fill,minmax(268px,1fr))]">
        {list.map((c) => {
          const st = statusOf(c);
          return (
            <button
              key={c.id}
              onClick={() => setSelected(c)}
              className="group text-left bg-[var(--card)] border border-[var(--border)] rounded-md p-4 flex flex-col gap-3 hover:border-[var(--accent)]/50 hover:-translate-y-0.5 transition-all focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
            >
              <div className="flex items-center gap-3">
                <Monogram text={c.monogram} />
                <div className="min-w-0">
                  <h3 className="text-[15px] font-semibold text-[var(--text)] leading-tight">{c.name}</h3>
                  <p className="text-xs text-[var(--text-muted)] mt-0.5 leading-snug">{c.role}</p>
                </div>
              </div>
              <div className="flex items-center justify-between mt-auto">
                <StatusPill tone={st.tone} label={st.label} />
                <span className="text-xs text-[var(--accent)] opacity-0 group-hover:opacity-100 transition-opacity">
                  Configure →
                </span>
              </div>
            </button>
          );
        })}
      </div>

      {selected && (
        <Detail
          connector={selected}
          status={statusOf(selected)}
          onClose={() => setSelected(null)}
          onSaved={() => {
            loadCfg();
            refetch();
          }}
        />
      )}
    </div>
  );
}

function Monogram({ text, size = 'sm' }: { text: string; size?: 'sm' | 'lg' }) {
  const dim = size === 'lg' ? 'w-13 h-13 text-xl' : 'w-10 h-10 text-base';
  return (
    <div
      className={`${dim} flex-none grid place-items-center rounded-lg bg-[var(--bg-warm)] border border-[var(--border)] text-[var(--text)]`}
      style={{ fontFamily: 'var(--serif)', width: size === 'lg' ? '3.25rem' : undefined, height: size === 'lg' ? '3.25rem' : undefined }}
    >
      {text}
    </div>
  );
}

function StatusPill({ tone, label }: { tone: Status; label: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 text-[11px] px-2 py-0.5 rounded-full border ${PILL[tone]}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${DOT[tone]}`} />
      {label}
    </span>
  );
}

function Detail({
  connector,
  status,
  onClose,
  onSaved,
}: {
  connector: Connector;
  status: { tone: Status; label: string };
  onClose: () => void;
  onSaved: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [keySet, setKeySet] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    loadConnector(connector).then(({ values, keySet }) => {
      if (!alive) return;
      setValues(values);
      setKeySet(keySet);
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, [connector]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const set = (name: string, v: string) => {
    setValues((p) => ({ ...p, [name]: v }));
    setSaved(false);
  };

  const submit = async () => {
    setSaving(true);
    setErr(null);
    const res = await saveConnector(connector, values);
    setSaving(false);
    if (res.ok) {
      setSaved(true);
      onSaved();
    } else {
      setErr(res.error || 'Could not save.');
    }
  };

  return (
    <>
      <div className="fixed inset-0 bg-black/50 z-40 animate-[fadein_.2s_ease]" onClick={onClose} aria-hidden />
      <aside
        role="dialog"
        aria-label={`${connector.name} configuration`}
        className="fixed top-0 right-0 h-full w-[min(460px,94vw)] bg-[var(--bg)] border-l border-[var(--border)] z-50 overflow-y-auto shadow-2xl"
      >
        <button
          onClick={onClose}
          aria-label="Close"
          className="absolute top-4 right-4 p-1.5 text-[var(--text-muted)] hover:text-[var(--text)] rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
        >
          <X className="h-5 w-5" />
        </button>

        <div className="p-7 pr-8">
          <div className="flex gap-4 items-start">
            <Monogram text={connector.monogram} size="lg" />
            <div className="min-w-0 pt-0.5">
              <div className="text-[10.5px] uppercase tracking-wider text-[var(--text-muted)]">
                {CATEGORY_LABEL[connector.category]}
              </div>
              <h2 className="text-xl text-[var(--text)] leading-tight mt-0.5" style={{ fontFamily: 'var(--serif)' }}>
                {connector.name}
              </h2>
              <div className="mt-2">
                <StatusPill tone={status.tone} label={status.label} />
              </div>
            </div>
          </div>

          <Section title="What it is">{connector.what}</Section>
          <Section title="When you'd use it">{connector.use}</Section>
          <div className="mt-6">
            <SectionLabel>How NovoMCP uses it</SectionLabel>
            <div className="mt-2 text-sm text-[var(--text-soft)] bg-[var(--accent)]/10 border border-[var(--accent)]/20 rounded-md px-3.5 py-3 leading-relaxed">
              {connector.uses}
            </div>
          </div>

          <form
            className="mt-7 flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            {loading ? (
              <div className="h-24 bg-[var(--bg-warm)] animate-pulse rounded" />
            ) : (
              connector.fields.map((f) => (
                <div key={f.name}>
                  <label htmlFor={f.name} className="block text-xs text-[var(--text-soft)] mb-1.5">
                    {f.label}{' '}
                    {f.required ? (
                      <span className="text-[var(--accent)]">*</span>
                    ) : (
                      <span className="text-[var(--text-muted)]">(optional)</span>
                    )}
                  </label>
                  <input
                    id={f.name}
                    type={f.secret ? 'password' : 'text'}
                    value={values[f.name] ?? ''}
                    onChange={(e) => set(f.name, e.target.value)}
                    placeholder={f.secret && keySet ? '•••• set — leave blank to keep' : f.placeholder}
                    autoComplete="off"
                    className="w-full bg-[var(--bg-warm)] border border-[var(--border)] text-[var(--text)] font-mono text-[13px] px-3 py-2.5 rounded focus:outline-none focus:border-[var(--accent)]"
                  />
                  {f.hint && <p className="text-[11.5px] text-[var(--text-muted)] mt-1.5">{f.hint}</p>}
                </div>
              ))
            )}

            {err && (
              <p className="flex items-start gap-1.5 text-[13px] text-[var(--destructive)]">
                <AlertTriangle className="h-4 w-4 mt-px shrink-0" />
                {err}
              </p>
            )}

            <button
              type="submit"
              disabled={saving || loading}
              className="mt-1 inline-flex items-center justify-center gap-2 bg-[var(--accent)] text-white font-medium text-sm px-4 py-2.5 rounded hover:brightness-105 transition disabled:opacity-60"
            >
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Save &amp; connect
            </button>

            {saved && (
              <p className="flex items-center gap-2 text-[13px] text-emerald-500">
                <CheckCircle2 className="h-4 w-4" />
                Saved. NovoMCP picks it up on the next engine start; press Recheck to refresh status.
              </p>
            )}
          </form>

          <a
            href={`https://docs.novomcp.com/${connector.guide}`}
            target="_blank"
            rel="noreferrer"
            className="mt-5 inline-flex items-center gap-1.5 text-[13.5px] text-[var(--accent)] hover:underline"
          >
            Open the setup guide <ArrowUpRight className="h-3.5 w-3.5" />
          </a>
        </div>
      </aside>
    </>
  );
}

function SectionLabel({ children }: { children: ReactNode }) {
  return <h4 className="text-[11px] uppercase tracking-wider text-[var(--text-muted)]">{children}</h4>;
}
function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mt-6">
      <SectionLabel>{title}</SectionLabel>
      <p className="mt-2 text-sm text-[var(--text-soft)] leading-relaxed">{children}</p>
    </div>
  );
}
