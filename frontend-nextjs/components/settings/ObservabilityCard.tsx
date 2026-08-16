'use client';

import { useEffect, useState } from 'react';
import { Activity, Save, Trash2, Loader2, CheckCircle2, AlertCircle } from 'lucide-react';

// Observability config (Connections layer 6). Sends request / tool-call traces
// via OpenTelemetry (OTLP) to any compatible backend. Vendor-neutral — one
// exporter reaches a local collector or a SaaS backend. This is the agent /
// request plane, distinct from the compliance service above (the chemical one).
// Persists to the SecretStore via /api/local/observability-config; auth headers
// are write-only; changes apply on engine restart.

interface Config {
  enabled: boolean;
  endpoint: string | null;
  serviceName: string | null;
  samplingRate: string | null;
  headersSet: boolean;
  path: string;
}

const input =
  'w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm focus:outline-none focus:border-[var(--accent)] transition-colors';
const labelCls = 'block text-xs uppercase tracking-wide text-[var(--text-muted)] mb-1.5';

export default function ObservabilityCard() {
  const [config, setConfig] = useState<Config | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [endpoint, setEndpoint] = useState('');
  const [serviceName, setServiceName] = useState('');
  const [samplingRate, setSamplingRate] = useState('');
  const [headers, setHeaders] = useState('');
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');
  const [error, setError] = useState('');

  async function load() {
    const res = await fetch('/api/local/observability-config');
    if (!res.ok) return;
    const data: Config = await res.json();
    setConfig(data);
    setEnabled(data.enabled);
    setEndpoint(data.endpoint ?? '');
    setServiceName(data.serviceName ?? '');
    setSamplingRate(data.samplingRate ?? '');
    setHeaders('');
  }

  useEffect(() => {
    load();
  }, []);

  async function save() {
    setSaving(true);
    setMsg('');
    setError('');
    try {
      const body: Record<string, unknown> = { enabled, endpoint: endpoint.trim() };
      if (serviceName.trim()) body.serviceName = serviceName.trim();
      if (samplingRate.trim()) body.samplingRate = samplingRate.trim();
      if (headers.trim()) body.headers = headers.trim();
      const res = await fetch('/api/local/observability-config', {
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
      await fetch('/api/local/observability-config', { method: 'DELETE' });
      setMsg('Cleared. Restart the engine to apply.');
      await load();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="bg-[var(--card)] border border-[var(--border)]">
      <div className="px-6 py-4 border-b border-[var(--border)] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-[var(--text-muted)]" />
          <h2 className="text-sm font-medium tracking-wide uppercase text-[var(--text-muted)]">Observability</h2>
        </div>
        <label className="flex items-center gap-2 text-xs text-[var(--text-soft)] cursor-pointer">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="accent-[var(--accent)]" />
          tracing enabled
        </label>
      </div>

      <div className="px-6 py-5 space-y-4">
        <p className="text-xs text-[var(--text-muted)]">
          Optional. Send request and tool-call traces (OpenTelemetry / OTLP) to any compatible backend — a local
          collector, Grafana, Honeycomb, Arize, Datadog, and others. This is agent / request observability, separate
          from the compliance service above.
        </p>

        <div>
          <label className={labelCls}>OTLP endpoint</label>
          <input
            type="text"
            value={endpoint}
            onChange={(e) => setEndpoint(e.target.value)}
            spellCheck={false}
            placeholder="localhost:4317  ·  or  https://otlp.example.com"
            className={`${input} font-mono`}
          />
        </div>

        <div>
          <label className={labelCls}>
            Auth headers <span className="normal-case tracking-normal text-[var(--text-muted)]">(optional)</span>
            {config?.headersSet && <span className="text-emerald-500 normal-case tracking-normal"> • set</span>}
          </label>
          <input
            type="password"
            value={headers}
            onChange={(e) => setHeaders(e.target.value)}
            autoComplete="off"
            placeholder={config?.headersSet ? 'leave blank to keep the current headers' : 'authorization=Bearer …'}
            className={`${input} font-mono`}
          />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className={labelCls}>Service name</label>
            <input value={serviceName} onChange={(e) => setServiceName(e.target.value)} spellCheck={false} placeholder="novomcp (default)" className={`${input} font-mono`} />
          </div>
          <div>
            <label className={labelCls}>Sampling rate (0–1)</label>
            <input value={samplingRate} onChange={(e) => setSamplingRate(e.target.value)} inputMode="decimal" placeholder="1.0 (default)" className={`${input} font-mono`} />
          </div>
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
            disabled={saving}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-[var(--accent)] hover:bg-[var(--accent)]/90 transition-colors disabled:opacity-50"
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Save
          </button>
          {(config?.endpoint || config?.enabled) && (
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
