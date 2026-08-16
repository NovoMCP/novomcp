'use client';

import { useEffect, useState } from 'react';
import { ShieldCheck, Save, Trash2, Loader2, CheckCircle2, AlertCircle, Circle } from 'lucide-react';
import { useCapabilities } from '@/core/api/useCapabilities';

// Compliance-service consumer config (Connections layer 4). Connects the engine
// to an external compliance / regulatory-screening service via /api/local/
// compliance-config. Vendor-neutral: works with any compatible endpoint. The
// key is write-only; live reachability comes from the capability map.

interface Config {
  url: string | null;
  keySet: boolean;
  path: string;
}

const input =
  'w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm focus:outline-none focus:border-[var(--accent)] transition-colors';
const labelCls = 'block text-xs uppercase tracking-wide text-[var(--text-muted)] mb-1.5';

export default function ComplianceCard() {
  const [config, setConfig] = useState<Config | null>(null);
  const [url, setUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');
  const [error, setError] = useState('');
  const { data: caps } = useCapabilities();

  async function load() {
    const res = await fetch('/api/local/compliance-config');
    if (!res.ok) return;
    const data: Config = await res.json();
    setConfig(data);
    setUrl(data.url ?? '');
    setApiKey('');
  }

  useEffect(() => {
    load();
  }, []);

  async function save() {
    setSaving(true);
    setMsg('');
    setError('');
    try {
      const body: Record<string, string> = { url: url.trim() };
      if (apiKey.trim()) body.apiKey = apiKey.trim();
      const res = await fetch('/api/local/compliance-config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) setError(data.detail || data.error || `Save failed (${res.status})`);
      else {
        setMsg('Saved. Restart the engine to apply.');
        await load();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  async function clear() {
    setSaving(true);
    setMsg('');
    setError('');
    try {
      await fetch('/api/local/compliance-config', { method: 'DELETE' });
      setMsg('Cleared. Restart the engine to apply.');
      await load();
    } finally {
      setSaving(false);
    }
  }

  const live = caps?.compliance;

  return (
    <div className="bg-[var(--card)] border border-[var(--border)]">
      <div className="px-6 py-4 border-b border-[var(--border)] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-[var(--text-muted)]" />
          <h2 className="text-sm font-medium tracking-wide uppercase text-[var(--text-muted)]">Compliance service</h2>
        </div>
        {live &&
          (live.wired && live.reachable ? (
            <span className="flex items-center gap-1.5 text-xs text-emerald-500">
              <CheckCircle2 className="h-3.5 w-3.5" /> connected
            </span>
          ) : live.wired ? (
            <span className="flex items-center gap-1.5 text-xs text-amber-500">
              <AlertCircle className="h-3.5 w-3.5" /> wired · unreachable
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
              <Circle className="h-3.5 w-3.5" /> not connected
            </span>
          ))}
      </div>

      <div className="px-6 py-5 space-y-4">
        <p className="text-xs text-[var(--text-muted)]">
          Optional. Connect a regulatory / structural-screening service to enrich molecule profiles with
          policy decisions — restricted-substance flags, structural alerts, and pass / flag / block outcomes.
          Works with any compatible compliance endpoint.
        </p>

        <div>
          <label className={labelCls}>Service URL</label>
          <input
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            spellCheck={false}
            placeholder="https://compliance.example.com"
            className={`${input} font-mono`}
          />
        </div>

        <div>
          <label className={labelCls}>
            API key <span className="normal-case tracking-normal text-[var(--text-muted)]">(optional)</span>
            {config?.keySet && <span className="text-emerald-500 normal-case tracking-normal"> • set</span>}
          </label>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            autoComplete="off"
            placeholder={config?.keySet ? 'leave blank to keep the current key' : 'if the service requires one'}
            className={`${input} font-mono`}
          />
        </div>

        {(msg || error) && (
          <div className={`flex items-center gap-2 text-sm ${error ? 'text-[var(--destructive)]' : 'text-emerald-500'}`}>
            {error ? <AlertCircle className="h-4 w-4" /> : <CheckCircle2 className="h-4 w-4" />}
            {error || msg}
          </div>
        )}

        <div className="flex items-center gap-3 pt-1">
          <button
            onClick={save}
            disabled={saving || !url.trim()}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-[var(--accent)] hover:bg-[var(--accent)]/90 transition-colors disabled:opacity-50"
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Save
          </button>
          {config?.url && (
            <button
              onClick={clear}
              disabled={saving}
              className="flex items-center gap-2 px-3 py-2 text-sm text-[var(--text-soft)] border border-[var(--border)] hover:border-[var(--destructive)] hover:text-[var(--destructive)] transition-colors disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Clear
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
