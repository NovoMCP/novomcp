'use client';

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Loader2, Pencil, Plus, Trash2, Zap } from 'lucide-react';
import type { Connector } from '@/core/connectors/registry';

// Saved connections for one data connector (Snowflake, Databricks): list them,
// name them, edit, test or remove each one, or add another. Editing updates the
// connection in place; leaving a secret blank keeps the stored one.

interface Saved {
  id: string;
  type: string;
  displayName: string;
  config: Record<string, string>;
  createdAt?: string;
}

type TestState = { busy: boolean; ok?: boolean; error?: string | null };

function whereOf(c: Saved): string {
  return c.config.database || c.config.catalog || c.config.account || c.config.server_hostname || '';
}

export default function DataConnectionsPanel({ connector, onChanged }: { connector: Connector; onChanged: () => void }) {
  const [saved, setSaved] = useState<Saved[] | null>(null);
  const [editing, setEditing] = useState<string | 'new'>('new');
  const [name, setName] = useState('');
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [tests, setTests] = useState<Record<string, TestState>>({});

  const startEdit = useCallback((c: Saved | null) => {
    setMsg(null);
    if (c) {
      setEditing(c.id);
      setName(c.displayName && c.displayName !== c.type ? c.displayName : '');
      setValues({ ...c.config });
    } else {
      setEditing('new');
      setName('');
      setValues({});
    }
  }, []);

  const load = useCallback(async () => {
    const d = await fetch('/api/local/connections', { cache: 'no-store' })
      .then((r) => r.json())
      .catch(() => ({}));
    const mine: Saved[] = (d.connections || []).filter((c: Saved) => c.type === connector.id);
    setSaved(mine);
    return mine;
  }, [connector.id]);

  useEffect(() => {
    load().then((mine) => startEdit(mine[0] ?? null));
  }, [load, startEdit]);

  const submit = async () => {
    setBusy(true);
    setMsg(null);
    const config: Record<string, string> = {};
    const credentials: Record<string, string> = {};
    for (const f of connector.fields) {
      const v = (values[f.name] || '').trim();
      if (!v) continue;
      (f.bucket === 'credentials' ? credentials : config)[f.name] = v;
    }
    const isNew = editing === 'new';
    const missing = connector.fields.filter(
      (f) => f.required && !(values[f.name] || '').trim() && (isNew || f.bucket !== 'credentials')
    );
    if (missing.length) {
      setBusy(false);
      setMsg({ ok: false, text: `Fill in ${missing.map((f) => f.label).join(', ')}.` });
      return;
    }
    const res = await fetch(isNew ? '/api/local/connections' : `/api/local/connections/${editing}/`, {
      method: isNew ? 'POST' : 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(isNew ? { type: connector.id, displayName: name, config, credentials } : { displayName: name, config, credentials }),
    })
      .then(async (r) => ({ ok: r.ok, d: await r.json().catch(() => ({})) }))
      .catch(() => ({ ok: false, d: { error: 'Network error' } }));
    setBusy(false);
    if (!res.ok) {
      setMsg({ ok: false, text: res.d.detail || res.d.error || 'Could not save.' });
      return;
    }
    const id = isNew ? res.d.id : editing;
    const mine = await load();
    startEdit(mine.find((c) => c.id === id) ?? null);
    setMsg({ ok: true, text: isNew ? 'Added. Test it below.' : 'Saved.' });
    onChanged();
  };

  const test = async (id: string) => {
    setTests((t) => ({ ...t, [id]: { busy: true } }));
    const d = await fetch(`/api/local/connections/${id}/test/`, { method: 'POST' })
      .then((r) => r.json())
      .catch(() => ({ ok: false, error: 'Could not reach the dashboard server.' }));
    setTests((t) => ({ ...t, [id]: { busy: false, ok: !!d.ok, error: d.ok ? null : d.detail || d.error || 'Failed' } }));
  };

  const remove = async (c: Saved) => {
    await fetch(`/api/local/connections/${c.id}/`, { method: 'DELETE' }).catch(() => null);
    const mine = await load();
    if (editing === c.id) startEdit(mine[0] ?? null);
    onChanged();
  };

  if (saved === null) return <div className="mt-7 h-24 bg-[var(--bg-warm)] animate-pulse rounded" />;

  return (
    <div className="mt-7">
      {saved.length > 0 && (
        <div className="mb-6">
          <h4 className="text-[11px] uppercase tracking-wider text-[var(--text-muted)]">Saved connections</h4>
          <ul className="mt-2 divide-y divide-[var(--border)] border border-[var(--border)] rounded-md">
            {saved.map((c) => {
              const t = tests[c.id];
              const active = editing === c.id;
              return (
                <li key={c.id} className={`px-3 py-2.5 ${active ? 'bg-[var(--accent)]/5' : ''}`}>
                  <div className="flex items-center gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm text-[var(--text)] truncate">
                        {c.displayName && c.displayName !== c.type ? c.displayName : connector.name}
                      </div>
                      <div className="text-xs font-mono text-[var(--text-muted)] truncate">{whereOf(c)}</div>
                    </div>
                    <button
                      onClick={() => test(c.id)}
                      disabled={t?.busy}
                      className="p-1.5 text-[var(--text-muted)] hover:text-[var(--accent)]"
                      title="Test the connection"
                    >
                      {t?.busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4" />}
                    </button>
                    <button onClick={() => startEdit(c)} className="p-1.5 text-[var(--text-muted)] hover:text-[var(--accent)]" title="Edit or rename">
                      <Pencil className="h-4 w-4" />
                    </button>
                    <button onClick={() => remove(c)} className="p-1.5 text-[var(--text-muted)] hover:text-[var(--destructive)]" title="Remove">
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                  {t && !t.busy && (
                    <p className={`mt-1.5 text-xs break-words ${t.ok ? 'text-emerald-500' : 'text-[var(--destructive)]'}`}>
                      {t.ok ? 'Connected.' : t.error}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
          {editing !== 'new' && (
            <button onClick={() => startEdit(null)} className="mt-2 inline-flex items-center gap-1 text-[13px] text-[var(--accent)] hover:underline">
              <Plus className="h-3.5 w-3.5" /> Add another {connector.name} connection
            </button>
          )}
        </div>
      )}

      <h4 className="text-[11px] uppercase tracking-wider text-[var(--text-muted)]">
        {editing === 'new' ? `New ${connector.name} connection` : 'Edit connection'}
      </h4>
      <form
        className="mt-3 flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <div>
          <label htmlFor="conn-name" className="block text-xs text-[var(--text-soft)] mb-1.5">
            Name <span className="text-[var(--text-muted)]">(optional)</span>
          </label>
          <input
            id="conn-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={`e.g. Lab ${connector.name}`}
            autoComplete="off"
            className="w-full bg-[var(--bg-warm)] border border-[var(--border)] text-[var(--text)] text-[13px] px-3 py-2.5 rounded focus:outline-none focus:border-[var(--accent)]"
          />
        </div>
        {connector.fields.map((f) => (
          <div key={f.name}>
            <label htmlFor={`f-${f.name}`} className="block text-xs text-[var(--text-soft)] mb-1.5">
              {f.label}{' '}
              {f.required ? <span className="text-[var(--accent)]">*</span> : <span className="text-[var(--text-muted)]">(optional)</span>}
            </label>
            <input
              id={`f-${f.name}`}
              type={f.secret ? 'password' : 'text'}
              value={values[f.name] ?? ''}
              onChange={(e) => setValues((p) => ({ ...p, [f.name]: e.target.value }))}
              placeholder={f.bucket === 'credentials' && editing !== 'new' ? '•••• saved — leave blank to keep' : f.placeholder}
              autoComplete="off"
              className="w-full bg-[var(--bg-warm)] border border-[var(--border)] text-[var(--text)] font-mono text-[13px] px-3 py-2.5 rounded focus:outline-none focus:border-[var(--accent)]"
            />
            {f.hint && <p className="text-[11.5px] text-[var(--text-muted)] mt-1.5">{f.hint}</p>}
          </div>
        ))}

        {msg && (
          <p className={`flex items-start gap-1.5 text-[13px] ${msg.ok ? 'text-emerald-500' : 'text-[var(--destructive)]'}`}>
            {msg.ok ? <CheckCircle2 className="h-4 w-4 mt-px shrink-0" /> : <AlertTriangle className="h-4 w-4 mt-px shrink-0" />}
            {msg.text}
          </p>
        )}

        <div className="flex gap-2">
          <button
            type="submit"
            disabled={busy}
            className="flex-1 inline-flex items-center justify-center gap-2 bg-[var(--accent)] text-white font-medium text-sm px-4 py-2.5 rounded hover:brightness-105 transition disabled:opacity-60"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {editing === 'new' ? 'Add connection' : 'Save changes'}
          </button>
          {editing === 'new' && saved.length > 0 && (
            <button
              type="button"
              onClick={() => startEdit(saved[0])}
              className="px-4 py-2.5 text-sm border border-[var(--border)] rounded text-[var(--text-soft)] hover:border-[var(--accent)]"
            >
              Cancel
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
