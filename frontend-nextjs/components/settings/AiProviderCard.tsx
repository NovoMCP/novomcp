'use client';

import { useEffect, useState } from 'react';
import { Bot, Save, Trash2, Loader2, CheckCircle2, AlertCircle } from 'lucide-react';

// AI provider config (Connections layer 3). Writes provider keys to the local
// SecretStore via /api/local/llm-config; the engine reads them at startup, so a
// change needs an engine restart. Only the four providers the engine actually
// resolves from env are offered (see docs/configuring-llm.md). Keys are
// write-only — the current key is shown as "set", never echoed back.

type Provider = 'openai' | 'anthropic' | 'ollama' | 'azure';

const PROVIDER_META: Record<Provider, { label: string; needsKey: boolean; keyHint?: string }> = {
  openai: { label: 'OpenAI (and OpenAI-compatible)', needsKey: true, keyHint: 'sk-…' },
  anthropic: { label: 'Anthropic', needsKey: true, keyHint: 'sk-ant-…' },
  ollama: { label: 'Ollama (local, no key)', needsKey: false },
  azure: { label: 'Azure OpenAI', needsKey: true, keyHint: 'key' },
};

interface Config {
  active: string | null;
  path: string;
  providers: {
    openai: { keySet: boolean; model: string | null; baseUrl: string | null };
    anthropic: { keySet: boolean; model: string | null };
    ollama: { url: string | null; model: string | null };
    azure: { keySet: boolean; endpoint: string | null; deployment: string | null };
  };
}

const input =
  'w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm focus:outline-none focus:border-[var(--accent)] transition-colors';
const labelCls = 'block text-xs uppercase tracking-wide text-[var(--text-muted)] mb-1.5';

export default function AiProviderCard() {
  const [config, setConfig] = useState<Config | null>(null);
  const [provider, setProvider] = useState<Provider>('openai');
  const [fields, setFields] = useState<Record<string, string>>({});
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');
  const [error, setError] = useState('');

  async function load() {
    const res = await fetch('/api/local/llm-config');
    if (!res.ok) return;
    const data: Config = await res.json();
    setConfig(data);
    const p = (data.active && data.active in PROVIDER_META ? data.active : 'openai') as Provider;
    setProvider(p);
    prefill(p, data);
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function prefill(p: Provider, data: Config) {
    setApiKey('');
    if (p === 'openai') setFields({ model: data.providers.openai.model ?? '', baseUrl: data.providers.openai.baseUrl ?? '' });
    else if (p === 'anthropic') setFields({ model: data.providers.anthropic.model ?? '' });
    else if (p === 'ollama') setFields({ url: data.providers.ollama.url ?? '', model: data.providers.ollama.model ?? '' });
    else setFields({ endpoint: data.providers.azure.endpoint ?? '', deployment: data.providers.azure.deployment ?? '' });
  }

  function onProviderChange(p: Provider) {
    setProvider(p);
    setMsg('');
    setError('');
    if (config) prefill(p, config);
  }

  const keySet =
    provider === 'openai' ? config?.providers.openai.keySet
    : provider === 'anthropic' ? config?.providers.anthropic.keySet
    : provider === 'azure' ? config?.providers.azure.keySet
    : false;

  async function save() {
    setSaving(true);
    setMsg('');
    setError('');
    try {
      const body: Record<string, string> = { provider };
      if (apiKey.trim()) body.apiKey = apiKey.trim();
      for (const [k, v] of Object.entries(fields)) if (v.trim()) body[k] = v.trim();
      const res = await fetch('/api/local/llm-config', {
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
      await fetch('/api/local/llm-config', { method: 'DELETE' });
      setMsg('Cleared. Restart the engine to apply.');
      await load();
    } finally {
      setSaving(false);
    }
  }

  const meta = PROVIDER_META[provider];

  return (
    <div className="bg-[var(--card)] border border-[var(--border)]">
      <div className="px-6 py-4 border-b border-[var(--border)] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-[var(--text-muted)]" />
          <h2 className="text-sm font-medium tracking-wide uppercase text-[var(--text-muted)]">AI provider</h2>
        </div>
        {config?.active && (
          <span className="text-xs text-[var(--text-muted)]">
            active: <span className="text-[var(--text-soft)]">{config.active}</span>
          </span>
        )}
      </div>

      <div className="px-6 py-5 space-y-4">
        <p className="text-xs text-[var(--text-muted)]">
          Optional. Powers LLM-driven features (intent recognition, planning, semantic tool search).
          Tools work without it. Only providers the engine resolves from the environment are listed.
        </p>

        <div>
          <label className={labelCls}>Provider</label>
          <select value={provider} onChange={(e) => onProviderChange(e.target.value as Provider)} className={input}>
            {(Object.keys(PROVIDER_META) as Provider[]).map((p) => (
              <option key={p} value={p}>{PROVIDER_META[p].label}</option>
            ))}
          </select>
        </div>

        {meta.needsKey && (
          <div>
            <label className={labelCls}>
              API key {keySet && <span className="text-emerald-500 normal-case tracking-normal">• set</span>}
            </label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              autoComplete="off"
              placeholder={keySet ? 'leave blank to keep the current key' : meta.keyHint}
              className={`${input} font-mono`}
            />
          </div>
        )}

        {/* Per-provider fields */}
        {provider === 'openai' && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Field label="Model" value={fields.model ?? ''} onChange={(v) => setFields({ ...fields, model: v })} placeholder="gpt-4o-mini (default)" />
            <Field label="Base URL (optional)" value={fields.baseUrl ?? ''} onChange={(v) => setFields({ ...fields, baseUrl: v })} placeholder="https://api.openai.com/v1" />
          </div>
        )}
        {provider === 'anthropic' && (
          <Field label="Model" value={fields.model ?? ''} onChange={(v) => setFields({ ...fields, model: v })} placeholder="claude-sonnet-4-5 (default)" />
        )}
        {provider === 'ollama' && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Field label="Server URL" value={fields.url ?? ''} onChange={(v) => setFields({ ...fields, url: v })} placeholder="http://localhost:11434" />
            <Field label="Model" value={fields.model ?? ''} onChange={(v) => setFields({ ...fields, model: v })} placeholder="llama3.2 (default)" />
          </div>
        )}
        {provider === 'azure' && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Field label="Endpoint" value={fields.endpoint ?? ''} onChange={(v) => setFields({ ...fields, endpoint: v })} placeholder="https://<resource>.openai.azure.com/" />
            <Field label="Deployment" value={fields.deployment ?? ''} onChange={(v) => setFields({ ...fields, deployment: v })} placeholder="gpt-4o" />
          </div>
        )}

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
          {config?.active && (
            <button
              onClick={clear}
              disabled={saving}
              className="flex items-center gap-2 px-3 py-2 text-sm text-[var(--text-soft)] border border-[var(--border)] hover:border-[var(--destructive)] hover:text-[var(--destructive)] transition-colors disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Clear
            </button>
          )}
          <a
            href="https://docs.novomcp.com/configuring-llm/"
            target="_blank"
            rel="noreferrer"
            className="ml-auto text-xs text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
          >
            configuring-llm ↗
          </a>
        </div>
      </div>
    </div>
  );
}

function Field({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <div>
      <label className={labelCls}>{label}</label>
      <input value={value} onChange={(e) => onChange(e.target.value)} spellCheck={false} placeholder={placeholder} className={`${input} font-mono`} />
    </div>
  );
}
