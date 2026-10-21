'use client';

import { Fragment, useCallback, useEffect, useState } from 'react';
import { Download, Search, Loader2, ChevronDown, ChevronRight, Copy, Check, X } from 'lucide-react';
import RunsView from '@/components/activity/RunsView';
import SendToWarehouse from '@/components/activity/SendToWarehouse';

// Activity — every tool call this install has run, read from the engine's
// local audit log (~/.novo/audit.jsonl). Filter it, open a call to see exactly
// what was run and what came back, copy that call to repeat it, or download
// the whole history as a spreadsheet (CSV) or the raw log (JSON lines). The
// Runs tab groups calls by run and adds the stages a funnel saved: molecules
// in and out, what was filtered and why, and the human decisions.

interface Entry {
  ts?: string;
  event?: string;
  payload?: {
    tool?: string;
    funnel_id?: string | null;
    success?: boolean;
    execution_time_ms?: number;
    error?: string | null;
    arguments?: Record<string, unknown>;
    outcome?: Record<string, unknown> | null;
  };
}

interface AuditResp {
  audit_path: string;
  error: string | null;
  total: number;
  tools: string[];
  entries: Entry[];
}

const PAGE = 50;

const select =
  'px-2.5 py-2 bg-[var(--bg-warm)] border border-[var(--border)] rounded text-sm text-[var(--text)] focus:outline-none focus:border-[var(--accent)]';

function query(f: { tool: string; status: string; run: string; q: string }, extra: Record<string, string>) {
  const p = new URLSearchParams(extra);
  if (f.tool) p.set('tool', f.tool);
  if (f.status) p.set('status', f.status);
  if (f.run) p.set('run', f.run);
  if (f.q.trim()) p.set('q', f.q.trim());
  return `/api/local/audit?${p.toString()}`;
}

// The input people recognise a call by: its SMILES, else its first short argument.
function inputOf(args?: Record<string, unknown>): string {
  if (!args) return '';
  if (typeof args.smiles === 'string') return args.smiles;
  for (const [k, v] of Object.entries(args)) {
    if (k === 'funnel_id') continue;
    if (typeof v === 'string' || typeof v === 'number') return `${k}: ${v}`;
  }
  return '';
}

// One line for the result: a compliance verdict and its flags, else a few key=value pairs.
function outcomeOf(o?: Record<string, unknown> | null): string {
  if (!o) return '';
  const flags = Array.isArray(o.flags) ? (o.flags as unknown[]).map(String) : [];
  if (typeof o.status === 'string') return [o.status, ...flags].join(' · ');
  const parts = Object.entries(o)
    .filter(([k]) => k !== 'flags')
    .slice(0, 3)
    .map(([k, v]) => `${k} ${typeof v === 'number' ? Number(v.toFixed(3)) : String(v)}`);
  return [...parts, ...flags].join(' · ');
}

export default function ActivityPage() {
  const [filters, setFilters] = useState({ tool: '', status: '', run: '', q: '' });
  const [search, setSearch] = useState('');
  const [data, setData] = useState<AuditResp | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState<number | null>(null);
  const [view, setView] = useState<'calls' | 'runs'>('calls');

  const load = useCallback(
    async (offset: number) => {
      setLoading(true);
      const res = await fetch(query(filters, { limit: String(PAGE), offset: String(offset) }), { cache: 'no-store' })
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null);
      setData(res);
      setEntries((prev) => (offset === 0 ? res?.entries ?? [] : [...prev, ...(res?.entries ?? [])]));
      setLoading(false);
    },
    [filters]
  );

  useEffect(() => {
    setOpen(null);
    load(0);
  }, [load]);

  // Search as you type, after a short pause.
  useEffect(() => {
    const t = setTimeout(() => setFilters((f) => (f.q === search ? f : { ...f, q: search })), 300);
    return () => clearTimeout(t);
  }, [search]);

  const total = data?.total ?? 0;
  const set = (k: 'tool' | 'status' | 'run', v: string) => setFilters((f) => ({ ...f, [k]: v }));

  return (
    <div className="max-w-6xl">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl text-[var(--text)]" style={{ fontFamily: 'var(--serif)', fontWeight: 500 }}>
            Activity
          </h1>
          <p className="text-sm text-[var(--text-muted)] mt-1 max-w-2xl">
            Every tool call this install has run: what went in, what came back. Open a call to copy it and run it
            again, or download the history for a spreadsheet or your own analysis. Runs shows each run as a
            timeline, with the stages it saved and the decisions behind them.
          </p>
        </div>
        <div className={`flex gap-2 shrink-0 ${view === 'calls' ? '' : 'invisible'}`}>
          <a
            href={query(filters, { format: 'csv' })}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-white bg-[var(--accent)] rounded hover:brightness-105 transition"
          >
            <Download className="h-4 w-4" /> Download CSV
          </a>
          <a
            href={query(filters, { format: 'jsonl' })}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-[var(--border)] rounded text-[var(--text-soft)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors"
            title="The raw log lines (JSON lines), for scripts and notebooks"
          >
            <Download className="h-4 w-4" /> Raw log
          </a>
          <SendToWarehouse payload={{ kind: 'calls', filters }} defaultTable="novomcp_activity" />
        </div>
      </div>

      {/* view tabs */}
      <div className="flex gap-1 mt-6 border-b border-[var(--border)]" role="tablist">
        {(['calls', 'runs'] as const).map((v) => (
          <button
            key={v}
            role="tab"
            aria-selected={view === v}
            onClick={() => setView(v)}
            className={`px-3.5 py-2.5 text-[13.5px] border-b-2 -mb-px transition-colors ${
              view === v ? 'border-[var(--accent)] text-[var(--text)]' : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text-soft)]'
            }`}
          >
            {v === 'calls' ? 'Calls' : 'Runs'}
          </button>
        ))}
      </div>

      {view === 'runs' ? (
        <div className="mt-4 bg-[var(--card)] border border-[var(--border)] rounded-lg overflow-hidden">
          <RunsView
            onShowCalls={(runId) => {
              setFilters((f) => ({ ...f, run: runId }));
              setView('calls');
            }}
          />
        </div>
      ) : (
        <>
          {/* filters */}
          <div className="flex flex-wrap items-center gap-2 mt-4">
            <div className="relative flex-1 min-w-[220px]">
              <Search className="h-4 w-4 absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--text-muted)]" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search SMILES, tools, results…"
                spellCheck={false}
                className="w-full pl-8 pr-3 py-2 bg-[var(--bg-warm)] border border-[var(--border)] rounded text-sm text-[var(--text)] focus:outline-none focus:border-[var(--accent)]"
              />
            </div>
            <select value={filters.tool} onChange={(e) => set('tool', e.target.value)} className={select} aria-label="Tool">
              <option value="">All tools</option>
              {(data?.tools ?? []).map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <select value={filters.status} onChange={(e) => set('status', e.target.value)} className={select} aria-label="Status">
              <option value="">Any result</option>
              <option value="ok">Succeeded</option>
              <option value="error">Failed</option>
            </select>
            {filters.run && (
              <button
                onClick={() => set('run', '')}
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-mono border border-[var(--accent)]/40 text-[var(--accent)] rounded-full"
                title="Showing one run; click to show all"
              >
                run {filters.run.slice(-8)} <X className="h-3 w-3" />
              </button>
            )}
          </div>

          <p className="text-xs text-[var(--text-muted)] mt-3">
            {loading && entries.length === 0 ? 'Loading…' : `${total} ${total === 1 ? 'call' : 'calls'}`}
            {data?.audit_path && (
              <span className="font-mono"> · {data.audit_path.replace(/^.*\/\.novo/, '~/.novo')}</span>
            )}
          </p>

          {/* table */}
          <div className="mt-3 bg-[var(--card)] border border-[var(--border)] rounded-lg overflow-x-auto">
            {!loading && entries.length === 0 ? (
              <div className="px-6 py-12 text-center text-sm text-[var(--text-muted)]">
                {data?.error === 'no_audit_yet' || total === 0
                  ? 'No tool calls yet. Profile a molecule or connect an MCP client, and each call shows up here.'
                  : 'No calls match these filters.'}
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wider text-[var(--text-muted)] border-b border-[var(--border)]">
                    <th className="w-6" />
                    <th className="px-3 py-2.5 font-normal">Time</th>
                    <th className="px-3 py-2.5 font-normal">Tool</th>
                    <th className="px-3 py-2.5 font-normal">Input</th>
                    <th className="px-3 py-2.5 font-normal">Result</th>
                    <th className="px-3 py-2.5 font-normal">Run</th>
                    <th className="px-3 py-2.5 font-normal text-right">Time taken</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((e, i) => {
                    const p = e.payload ?? {};
                    const failed = p.success === false;
                    const isOpen = open === i;
                    return (
                      <Fragment key={`${e.ts}-${i}`}>
                        <tr
                          onClick={() => setOpen(isOpen ? null : i)}
                          className="border-b border-[var(--border)] last:border-0 hover:bg-[var(--bg-warm)] cursor-pointer align-top"
                        >
                          <td className="pl-3 py-2.5 text-[var(--text-muted)]">
                            {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                          </td>
                          <td className="px-3 py-2.5 whitespace-nowrap text-[var(--text-soft)]">
                            {e.ts ? new Date(e.ts).toLocaleString() : ''}
                          </td>
                          <td className="px-3 py-2.5 font-mono text-[13px] text-[var(--text)] whitespace-nowrap">
                            <span
                              className={`inline-block h-1.5 w-1.5 rounded-full mr-2 align-middle ${failed ? 'bg-amber-500' : 'bg-emerald-500'}`}
                            />
                            {p.tool ?? e.event}
                          </td>
                          <td className="px-3 py-2.5 font-mono text-[12.5px] text-[var(--text-soft)] max-w-[260px] truncate">
                            {inputOf(p.arguments)}
                          </td>
                          <td className="px-3 py-2.5 text-[var(--text-soft)] max-w-[280px] truncate">
                            {failed ? <span className="text-amber-500">{p.error || 'failed'}</span> : outcomeOf(p.outcome)}
                          </td>
                          <td className="px-3 py-2.5">
                            {p.funnel_id && (
                              <button
                                onClick={(ev) => {
                                  ev.stopPropagation();
                                  set('run', p.funnel_id as string);
                                }}
                                className="font-mono text-xs text-[var(--accent)] hover:underline"
                                title="Show only this run"
                              >
                                {p.funnel_id.slice(-8)}
                              </button>
                            )}
                          </td>
                          <td className="px-3 py-2.5 text-right tabular-nums text-[var(--text-muted)] whitespace-nowrap">
                            {typeof p.execution_time_ms === 'number' ? `${(p.execution_time_ms / 1000).toFixed(1)} s` : ''}
                          </td>
                        </tr>
                        {isOpen && (
                          <tr className="border-b border-[var(--border)] bg-[var(--bg-warm)]">
                            <td />
                            <td colSpan={6} className="px-3 py-4">
                              <Details entry={e} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>

          {entries.length < total && (
            <div className="flex justify-center mt-4">
              <button
                onClick={() => load(entries.length)}
                disabled={loading}
                className="inline-flex items-center gap-2 px-4 py-2 text-sm border border-[var(--border)] rounded text-[var(--text-soft)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors disabled:opacity-50"
              >
                {loading && <Loader2 className="h-4 w-4 animate-spin" />}
                Show more ({total - entries.length} older)
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Details({ entry }: { entry: Entry }) {
  const p = entry.payload ?? {};
  const [copied, setCopied] = useState(false);
  const args = p.arguments ? Object.fromEntries(Object.entries(p.arguments).filter(([k]) => k !== 'funnel_id')) : null;
  const call = args ? JSON.stringify({ tool: p.tool, arguments: args }, null, 2) : null;

  async function copy() {
    if (!call) return;
    try {
      await navigator.clipboard.writeText(call);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard blocked; the call is still visible to select by hand
    }
  }

  if (!args) {
    return (
      <p className="text-sm text-[var(--text-muted)]">
        This call was recorded before inputs and results were logged, so only the tool, time and status are known.
      </p>
    );
  }

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <p className="text-[11px] uppercase tracking-wider text-[var(--text-muted)]">What was run</p>
          <button
            onClick={copy}
            className="inline-flex items-center gap-1 text-xs text-[var(--accent)] hover:underline"
            title="Copy the tool and its arguments, to run it again from any MCP client or the REST API"
          >
            {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            {copied ? 'Copied' : 'Copy call'}
          </button>
        </div>
        <pre className="text-xs font-mono text-[var(--text-soft)] bg-[var(--card)] border border-[var(--border)] rounded p-3 overflow-x-auto max-h-72">
          {call}
        </pre>
      </div>
      <div>
        <p className="text-[11px] uppercase tracking-wider text-[var(--text-muted)] mb-1.5">What came back</p>
        <pre className="text-xs font-mono text-[var(--text-soft)] bg-[var(--card)] border border-[var(--border)] rounded p-3 overflow-x-auto max-h-72">
          {p.success === false
            ? p.error || 'failed'
            : p.outcome
              ? JSON.stringify(p.outcome, null, 2)
              : 'Succeeded; no summary recorded for this tool.'}
        </pre>
        {p.funnel_id && <p className="text-xs text-[var(--text-muted)] mt-2 font-mono">run {p.funnel_id}</p>}
      </div>
    </div>
  );
}
