'use client';

import { useEffect, useState } from 'react';
import { Database, Plus, Save, Trash2, Loader2, AlertCircle, Zap, X } from 'lucide-react';

// Data connectors (Connections layer 5). Register a warehouse connection
// (Snowflake / Databricks), stored locally with the credentials in
// ~/.novo/connectors.json (0600); test it against the engine. Vendor-neutral
// pattern — the catalog grows over time. Credentials are write-only: the list
// never shows a stored secret's value.

type ConnType = 'snowflake' | 'databricks';

interface FieldDef { k: string; l: string; req?: boolean; ph?: string; secret?: boolean }

const TYPES: Record<ConnType, { label: string; config: FieldDef[]; creds: FieldDef[] }> = {
  snowflake: {
    label: 'Snowflake',
    config: [
      { k: 'account', l: 'Account', req: true, ph: 'xy12345.us-east-1' },
      { k: 'warehouse', l: 'Warehouse' },
      { k: 'database', l: 'Database' },
      { k: 'schema', l: 'Schema', ph: 'PUBLIC' },
    ],
    creds: [
      { k: 'username', l: 'Username', req: true },
      { k: 'password', l: 'Password', req: true, secret: true },
    ],
  },
  databricks: {
    label: 'Databricks',
    config: [
      { k: 'server_hostname', l: 'Server hostname', req: true, ph: 'dbc-xxxx.cloud.databricks.com' },
      { k: 'http_path', l: 'HTTP path', req: true, ph: '/sql/1.0/warehouses/xxxx' },
      { k: 'catalog', l: 'Catalog', ph: 'main' },
      { k: 'schema', l: 'Schema', ph: 'default' },
    ],
    creds: [{ k: 'access_token', l: 'Access token', req: true, secret: true }],
  },
};

interface ConnRow {
  id: string;
  type: ConnType;
  displayName: string;
  config: Record<string, string>;
  credentialKeys: string[];
  createdAt: string;
}

const input =
  'w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm focus:outline-none focus:border-[var(--accent)] transition-colors';
const labelCls = 'block text-xs uppercase tracking-wide text-[var(--text-muted)] mb-1.5';

export default function ConnectorsCard() {
  const [rows, setRows] = useState<ConnRow[]>([]);
  const [adding, setAdding] = useState(false);
  const [type, setType] = useState<ConnType>('snowflake');
  const [displayName, setDisplayName] = useState('');
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [tests, setTests] = useState<Record<string, { state: 'testing' | 'ok' | 'fail'; msg: string }>>({});

  async function load() {
    const r = await fetch('/api/local/connections');
    if (r.ok) setRows((await r.json()).connections ?? []);
  }
  useEffect(() => {
    load();
  }, []);

  const meta = TYPES[type];

  function resetForm() {
    setDisplayName('');
    setValues({});
    setError('');
  }

  async function create() {
    setBusy(true);
    setError('');
    const config: Record<string, string> = {};
    const credentials: Record<string, string> = {};
    for (const f of meta.config) if (values[f.k]?.trim()) config[f.k] = values[f.k].trim();
    for (const f of meta.creds) if (values[f.k]?.trim()) credentials[f.k] = values[f.k].trim();
    // Client-side required check
    const missing = [...meta.config, ...meta.creds].filter((f) => f.req && !values[f.k]?.trim());
    if (missing.length) {
      setError(`Required: ${missing.map((f) => f.l).join(', ')}`);
      setBusy(false);
      return;
    }
    try {
      const r = await fetch('/api/local/connections', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, displayName, config, credentials }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) setError(d.detail || d.error || 'Save failed');
      else {
        setAdding(false);
        resetForm();
        await load();
      }
    } finally {
      setBusy(false);
    }
  }

  async function del(id: string) {
    await fetch(`/api/local/connections/${id}`, { method: 'DELETE' });
    setTests((p) => {
      const n = { ...p };
      delete n[id];
      return n;
    });
    await load();
  }

  async function test(id: string) {
    setTests((p) => ({ ...p, [id]: { state: 'testing', msg: 'testing…' } }));
    const r = await fetch(`/api/local/connections/${id}/test`, { method: 'POST' });
    const d = await r.json().catch(() => ({}));
    const ok = !!d.ok;
    setTests((p) => ({ ...p, [id]: { state: ok ? 'ok' : 'fail', msg: ok ? 'connected' : (d.error || d.detail || 'failed') } }));
  }

  return (
    <div className="bg-[var(--card)] border border-[var(--border)]">
      <div className="px-6 py-4 border-b border-[var(--border)] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Database className="h-4 w-4 text-[var(--text-muted)]" />
          <h2 className="text-sm font-medium tracking-wide uppercase text-[var(--text-muted)]">Data connectors</h2>
        </div>
        {!adding && (
          <button
            onClick={() => { setAdding(true); resetForm(); }}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-[var(--border)] text-[var(--text-soft)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors"
          >
            <Plus className="h-3.5 w-3.5" /> Add
          </button>
        )}
      </div>

      <div className="px-6 py-5 space-y-4">
        <p className="text-xs text-[var(--text-muted)]">
          Connect your data stack to export results (Snowflake, Databricks). Credentials are stored locally; the
          managed deployment stores them in a secured vault instead.
        </p>

        {/* Existing connections */}
        {rows.length > 0 && (
          <div className="divide-y divide-[var(--border)] border border-[var(--border)]">
            {rows.map((c) => {
              const t = tests[c.id];
              return (
                <div key={c.id} className="px-4 py-3 flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm text-[var(--text)] truncate">
                      {c.displayName} <span className="text-[var(--text-muted)]">· {TYPES[c.type]?.label ?? c.type}</span>
                    </p>
                    <p className="text-xs text-[var(--text-muted)] truncate">
                      {Object.entries(c.config).map(([k, v]) => `${k}=${v}`).join(' · ') || '—'}
                    </p>
                    {t && (
                      <p className={`text-xs mt-0.5 ${t.state === 'ok' ? 'text-emerald-500' : t.state === 'fail' ? 'text-[var(--destructive)]' : 'text-[var(--text-muted)]'}`}>
                        {t.state === 'testing' && <Loader2 className="inline h-3 w-3 animate-spin mr-1" />}
                        {t.msg}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button onClick={() => test(c.id)} className="flex items-center gap-1 px-2 py-1 text-xs text-[var(--text-soft)] hover:text-[var(--accent)] transition-colors" title="Test connection">
                      <Zap className="h-3.5 w-3.5" /> Test
                    </button>
                    <button onClick={() => del(c.id)} className="p-1 text-[var(--text-muted)] hover:text-[var(--destructive)] transition-colors" title="Delete">
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Add form */}
        {adding && (
          <div className="border border-[var(--border)] p-4 space-y-4 bg-[var(--bg)]/40">
            <div className="flex items-center justify-between">
              <h3 className="text-xs uppercase tracking-wide text-[var(--text-muted)]">New connection</h3>
              <button onClick={() => { setAdding(false); resetForm(); }} className="text-[var(--text-muted)] hover:text-[var(--text)]"><X className="h-4 w-4" /></button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className={labelCls}>Type</label>
                <select value={type} onChange={(e) => { setType(e.target.value as ConnType); setValues({}); }} className={input}>
                  {(Object.keys(TYPES) as ConnType[]).map((t) => (
                    <option key={t} value={t}>{TYPES[t].label}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className={labelCls}>Name</label>
                <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder={meta.label} className={input} />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {meta.config.map((f) => (
                <Field key={f.k} f={f} value={values[f.k] ?? ''} onChange={(v) => setValues((p) => ({ ...p, [f.k]: v }))} />
              ))}
              {meta.creds.map((f) => (
                <Field key={f.k} f={f} value={values[f.k] ?? ''} onChange={(v) => setValues((p) => ({ ...p, [f.k]: v }))} />
              ))}
            </div>

            {error && (
              <div className="flex items-center gap-2 text-sm text-[var(--destructive)]">
                <AlertCircle className="h-4 w-4" /> {error}
              </div>
            )}

            <button
              onClick={create}
              disabled={busy}
              className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-[var(--accent)] hover:bg-[var(--accent)]/90 transition-colors disabled:opacity-50"
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Save connection
            </button>
          </div>
        )}

        {rows.length === 0 && !adding && (
          <p className="text-sm text-[var(--text-muted)]">No connections yet. Add one to export results to your data stack.</p>
        )}
      </div>
    </div>
  );
}

function Field({ f, value, onChange }: { f: FieldDef; value: string; onChange: (v: string) => void }) {
  return (
    <div>
      <label className={labelCls}>
        {f.l} {f.req && <span className="text-[var(--accent)] normal-case">*</span>}
      </label>
      <input
        type={f.secret ? 'password' : 'text'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
        autoComplete="off"
        placeholder={f.ph}
        className={`${input} font-mono`}
      />
    </div>
  );
}
