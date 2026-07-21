'use client';

import { useState } from 'react';
import { useAuth } from '@/core/auth/provider';
import { useLlmConfig, useSetLlmConfig, useDeleteLlmConfig } from '@/core/api/admin-client';
import { KeyRound, Check, Loader2, Trash2, ShieldAlert } from 'lucide-react';

// Provider catalog for the Studio agent. The org brings its own key — NovoMCP
// never bills the platform's LLM key for Studio runs (compute billing is separate).
const PROVIDERS: Record<string, { label: string; defaultModel: string; keyHint: string }> = {
  anthropic: { label: 'Anthropic (Claude)', defaultModel: 'claude-opus-4-8', keyHint: 'sk-ant-…' },
  openai: { label: 'OpenAI (GPT)', defaultModel: 'gpt-4o', keyHint: 'sk-…' },
  gemini: { label: 'Google (Gemini)', defaultModel: 'gemini-2.5-flash', keyHint: 'AIza…' },
  mistral: { label: 'Mistral', defaultModel: 'mistral-large-latest', keyHint: '…' },
  cohere: { label: 'Cohere', defaultModel: 'command-r', keyHint: '…' },
};

const sectionClass = 'bg-[var(--card)] border border-[var(--border)]';
const sectionHeaderClass = 'px-6 py-4 border-b border-[var(--border)] flex items-center';
const labelClass = 'block text-sm font-medium text-[var(--text)] mb-1.5';
const inputClass =
  'w-full px-3 py-2 text-sm bg-[var(--bg-warm)] border border-[var(--border)] text-[var(--text)] focus:outline-none focus:border-[var(--accent)] transition-colors';

export default function LlmProviderCard() {
  const { user } = useAuth();
  const isAdmin = (user?.roles || []).includes('admin');

  const { data, isLoading } = useLlmConfig();
  const setMut = useSetLlmConfig();
  const delMut = useDeleteLlmConfig();

  const [provider, setProvider] = useState('anthropic');
  const [model, setModel] = useState(PROVIDERS.anthropic.defaultModel);
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [editing, setEditing] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [message, setMessage] = useState('');
  const [advanced, setAdvanced] = useState(false); // reveals the Base URL field

  // Only org admins can view/manage the shared key (the BFF enforces this too).
  if (!isAdmin) return null;

  const configured = data?.configured && data.config;

  const onProviderChange = (p: string) => {
    setProvider(p);
    setModel(PROVIDERS[p]?.defaultModel || '');
  };

  const onSave = async () => {
    setMessage('');
    try {
      await setMut.mutateAsync({
        provider,
        model: model.trim(),
        api_key: apiKey.trim(),
        base_url: baseUrl.trim() || undefined,
      });
      setMessage('Workbench AI provider saved');
      setApiKey('');
      setEditing(false);
      setTimeout(() => setMessage(''), 3000);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Failed to save provider');
    }
  };

  const onRemove = async () => {
    setMessage('');
    try {
      await delMut.mutateAsync();
      setConfirmRemove(false);
      setMessage('Provider removed');
      setTimeout(() => setMessage(''), 3000);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Failed to remove provider');
    }
  };

  const showForm = editing || !configured;
  const canSave = !!model.trim() && !!apiKey.trim() && !setMut.isPending;

  return (
    <div className={sectionClass}>
      <div className={`${sectionHeaderClass} justify-between`}>
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center bg-[var(--accent)] text-white">
            <KeyRound className="h-4 w-4" />
          </div>
          <div>
            <h2 className="text-base font-semibold text-[var(--text)]">Workbench AI Provider</h2>
            <p className="text-xs text-[var(--text-muted)]">
              The LLM that runs the Workbench agent. Your key, stored encrypted — used for every member of the org.
            </p>
          </div>
        </div>
        {message && (
          <div className="flex items-center gap-2 px-3 py-1.5 bg-[var(--success)]/10 border border-[var(--success)]/20">
            <Check className="h-4 w-4 text-[var(--success)]" />
            <span className="text-xs text-[var(--success)]">{message}</span>
          </div>
        )}
      </div>

      <div className="p-6 space-y-5">
        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading…
          </div>
        ) : (
          <>
            {/* Current status */}
            {configured && (
              <div className="flex items-center justify-between py-3 border-b border-[var(--border)]">
                <div className="flex items-center gap-3">
                  <span className="text-xs font-medium px-2 py-0.5 bg-[var(--success)]/10 text-[var(--success)]">
                    Configured
                  </span>
                  <span className="text-sm text-[var(--text)]">
                    {PROVIDERS[data!.config!.provider]?.label || data!.config!.provider}
                    <span className="text-[var(--text-muted)]"> · </span>
                    <span className="font-mono text-[var(--text-soft)]">{data!.config!.model}</span>
                  </span>
                  <span className="text-xs text-[var(--text-muted)] font-mono">key ••••••</span>
                </div>
                {!editing && (
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => {
                        onProviderChange(data!.config!.provider);
                        const existingBase = data!.config!.base_url ?? '';
                        setBaseUrl(existingBase);
                        setAdvanced(!!existingBase); // surface a custom endpoint so editing doesn't drop it
                        setEditing(true);
                      }}
                      className="px-3 py-1.5 text-sm border border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--accent)]/5 transition-colors"
                    >
                      Replace key
                    </button>
                    <button
                      onClick={() => setConfirmRemove(true)}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-sm border border-[var(--border)] text-[var(--text-soft)] hover:bg-[var(--bg-warm)] transition-colors"
                    >
                      <Trash2 className="h-3.5 w-3.5" /> Remove
                    </button>
                  </div>
                )}
              </div>
            )}

            {confirmRemove && (
              <div className="flex items-center justify-between gap-3 px-4 py-3 bg-[var(--bg-warm)] border border-[var(--border)]">
                <div className="flex items-center gap-2 text-sm text-[var(--text)]">
                  <ShieldAlert className="h-4 w-4 text-[var(--text-muted)]" />
                  Remove the provider? The Workbench agent will stop working for everyone in the org until a new key is set.
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={onRemove}
                    disabled={delMut.isPending}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-white bg-[var(--danger,#c0392b)] hover:opacity-90 transition-opacity disabled:opacity-50"
                  >
                    {delMut.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />} Remove
                  </button>
                  <button
                    onClick={() => setConfirmRemove(false)}
                    className="px-3 py-1.5 text-sm text-[var(--text-soft)] border border-[var(--border)] hover:bg-[var(--card)] transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* Set / replace form */}
            {showForm && (
              <div className="space-y-4">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className={labelClass}>Provider</label>
                    <select value={provider} onChange={(e) => onProviderChange(e.target.value)} className={inputClass}>
                      {Object.entries(PROVIDERS).map(([k, v]) => (
                        <option key={k} value={k}>{v.label}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className={labelClass}>Model</label>
                    <input
                      value={model}
                      onChange={(e) => setModel(e.target.value)}
                      placeholder={PROVIDERS[provider]?.defaultModel}
                      className={`${inputClass} font-mono`}
                    />
                  </div>
                </div>

                <div>
                  <label className={labelClass}>{configured ? 'New API key' : 'API key'}</label>
                  <input
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder={PROVIDERS[provider]?.keyHint}
                    autoComplete="off"
                    className={`${inputClass} font-mono`}
                  />
                  <p className="mt-1 text-xs text-[var(--text-muted)]">
                    Stored encrypted in your org&apos;s vault. It is never returned to the dashboard after saving.
                  </p>
                </div>

                {/* Advanced — custom endpoint. Hidden by default; most orgs use
                    the provider's standard API. */}
                <div>
                  <button
                    type="button"
                    onClick={() => setAdvanced((v) => !v)}
                    className="flex items-center gap-1.5 text-xs text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
                    aria-expanded={advanced}
                  >
                    <span className="inline-block w-3 text-center">{advanced ? '▾' : '▸'}</span>
                    Advanced
                  </button>
                  {advanced && (
                    <div className="mt-2">
                      <label className={labelClass}>
                        Base URL <span className="text-[var(--text-muted)] font-normal">(optional — gateway / proxy or OpenAI-compatible host)</span>
                      </label>
                      <input
                        value={baseUrl}
                        onChange={(e) => setBaseUrl(e.target.value)}
                        placeholder="https://…"
                        className={`${inputClass} font-mono`}
                      />
                      <p className="mt-1 text-xs text-[var(--text-muted)]">
                        Overrides the provider&apos;s default endpoint — for an LLM gateway/proxy or another
                        OpenAI-compatible host. Leave blank for the standard API. Enter the host root; the
                        path is added automatically.
                      </p>
                    </div>
                  )}
                </div>

                {message && !setMut.isSuccess && setMut.isError && (
                  <p className="text-xs text-[var(--danger,#c0392b)]">{message}</p>
                )}

                <div className="flex items-center gap-3">
                  <button
                    onClick={onSave}
                    disabled={!canSave}
                    className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-[var(--accent)] hover:bg-[var(--accent)]/90 transition-all duration-[400ms] disabled:opacity-50"
                  >
                    {setMut.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                    {configured ? 'Save changes' : 'Save provider'}
                  </button>
                  {configured && editing && (
                    <button
                      onClick={() => { setEditing(false); setApiKey(''); setMessage(''); }}
                      className="px-4 py-2 text-sm font-medium text-[var(--text-soft)] border border-[var(--border)] hover:bg-[var(--bg-warm)] transition-colors"
                    >
                      Cancel
                    </button>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
