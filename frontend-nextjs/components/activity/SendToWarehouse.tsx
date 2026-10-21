'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { Check, Database, Loader2, X } from 'lucide-react';

// "Send to warehouse": writes Activity (filtered calls, or one run's timeline)
// to a saved Snowflake or Databricks connection via /api/local/export. The
// table is created if it doesn't exist. Free here; the warehouse bills its own
// usage.

interface Conn {
  id: string;
  type: string;
  displayName: string;
}

type Payload = { kind: 'calls'; filters: Record<string, string> } | { kind: 'run'; runId: string };

interface Result {
  success: boolean;
  rows_written?: number;
  rows_failed?: number;
  target_location?: string;
  error?: string | null;
}

export default function SendToWarehouse({
  payload,
  defaultTable,
  variant = 'button',
}: {
  payload: Payload;
  defaultTable: string;
  variant?: 'button' | 'link';
}) {
  const [open, setOpen] = useState(false);
  const [conns, setConns] = useState<Conn[] | null>(null);
  const [connId, setConnId] = useState('');
  const [table, setTable] = useState(defaultTable);
  const [mode, setMode] = useState<'append' | 'replace'>('append');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open || conns) return;
    fetch('/api/local/connections', { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        const list = ((d?.connections ?? []) as Conn[]).filter((c) => c.type === 'snowflake' || c.type === 'databricks');
        setConns(list);
        if (list.length) setConnId(list[0].id);
      })
      .catch(() => setConns([]));
  }, [open, conns]);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  async function send() {
    setBusy(true);
    setResult(null);
    const res = await fetch('/api/local/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...payload, connectionId: connId, target: table, mode }),
    })
      .then((r) => r.json())
      .catch(() => ({ success: false, error: 'Could not reach the dashboard server.' }));
    setResult(res);
    setBusy(false);
  }

  const trigger =
    variant === 'link' ? (
      <button onClick={() => setOpen((o) => !o)} className="inline-flex items-center gap-1 text-[var(--accent)] hover:underline">
        <Database className="h-3.5 w-3.5" /> Send to warehouse
      </button>
    ) : (
      <button
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-[var(--border)] rounded text-[var(--text-soft)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors"
        title="Write this activity to a Snowflake or Databricks table"
      >
        <Database className="h-4 w-4" /> Send to warehouse
      </button>
    );

  return (
    <div ref={ref} className="relative inline-block text-left">
      {trigger}
      {open && (
        <div className="absolute right-0 z-20 mt-2 w-80 bg-[var(--card)] border border-[var(--border)] rounded-md shadow-lg p-4 text-sm">
          <div className="flex items-start justify-between gap-2">
            <p className="text-[var(--text)] font-medium">
              {payload.kind === 'run' ? 'Send this run' : 'Send these calls'} to a warehouse
            </p>
            <button onClick={() => setOpen(false)} className="text-[var(--text-muted)] hover:text-[var(--text)]" aria-label="Close">
              <X className="h-4 w-4" />
            </button>
          </div>

          {conns === null ? (
            <p className="mt-3 text-[var(--text-muted)] flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading connections…
            </p>
          ) : conns.length === 0 ? (
            <p className="mt-3 text-[var(--text-muted)]">
              No warehouse connected yet.{' '}
              <Link href="/connections" className="text-[var(--accent)] hover:underline">
                Add Snowflake or Databricks
              </Link>{' '}
              on the Connections page.
            </p>
          ) : (
            <div className="mt-3 space-y-3">
              <label className="block">
                <span className="text-[11px] uppercase tracking-wider text-[var(--text-muted)]">Connection</span>
                <select
                  value={connId}
                  onChange={(e) => setConnId(e.target.value)}
                  className="mt-1 w-full bg-[var(--bg)] border border-[var(--border)] rounded px-2 py-1.5 text-[var(--text)]"
                >
                  {conns.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.displayName} ({c.type === 'snowflake' ? 'Snowflake' : 'Databricks'})
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="text-[11px] uppercase tracking-wider text-[var(--text-muted)]">Table</span>
                <input
                  value={table}
                  onChange={(e) => setTable(e.target.value)}
                  className="mt-1 w-full bg-[var(--bg)] border border-[var(--border)] rounded px-2 py-1.5 font-mono text-xs text-[var(--text)]"
                  placeholder="table, schema.table or database.schema.table"
                />
                <span className="text-xs text-[var(--text-muted)]">Created if it doesn&rsquo;t exist.</span>
              </label>
              <div className="flex gap-4 text-xs text-[var(--text-soft)]">
                {(['append', 'replace'] as const).map((m) => (
                  <label key={m} className="inline-flex items-center gap-1.5">
                    <input type="radio" checked={mode === m} onChange={() => setMode(m)} />
                    {m === 'append' ? 'Add rows' : 'Replace table contents'}
                  </label>
                ))}
              </div>
              <button
                onClick={send}
                disabled={busy || !connId || !table.trim()}
                className="w-full inline-flex justify-center items-center gap-1.5 px-3 py-2 font-medium text-white bg-[var(--accent)] rounded hover:brightness-105 disabled:opacity-50 transition"
              >
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
                {busy ? 'Sending…' : 'Send'}
              </button>
              {result &&
                (result.success ? (
                  <p className="text-emerald-600 flex items-start gap-1.5">
                    <Check className="h-4 w-4 mt-0.5 shrink-0" />
                    <span>
                      Wrote {result.rows_written} {result.rows_written === 1 ? 'row' : 'rows'} to{' '}
                      <span className="font-mono text-xs break-all">{result.target_location}</span>.
                    </span>
                  </p>
                ) : (
                  <p className="text-amber-600 break-words">
                    {result.rows_written ? `Wrote ${result.rows_written}, ${result.rows_failed} failed. ` : ''}
                    {result.error || 'The export failed.'}
                  </p>
                ))}
              <p className="text-[11px] text-[var(--text-muted)] leading-relaxed">
                Each row also records who sent it and when (_exported_by, _export_id, _exported_at). Free here; your
                warehouse bills its own usage.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
