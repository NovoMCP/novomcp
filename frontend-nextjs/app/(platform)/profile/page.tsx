'use client';

import { useState, type ReactNode } from 'react';
import { FlaskConical, Beaker, ShieldCheck, AlertCircle, Loader2 } from 'lucide-react';
import { useCapabilities } from '@/core/api/useCapabilities';

// Molecule profile — the first in-app science surface. Paste a SMILES, get the
// properties the engine computes locally (RDKit), plus ADMET when the ADMET
// service is wired and a compliance panel when a compliance service is wired.
// Everything is capability-gated: no dead cards, no fabricated fields.

interface ProfileResult {
  smiles: string;
  source?: string;
  in_database?: boolean;
  properties?: Record<string, number | boolean>;
  admet?: Record<string, unknown> | null;
  admet_available?: boolean;
}

const EXAMPLES = [
  { name: 'Aspirin', smiles: 'CC(=O)Oc1ccccc1C(=O)O' },
  { name: 'Caffeine', smiles: 'Cn1cnc2c1c(=O)n(C)c(=O)n2C' },
  { name: 'Ibuprofen', smiles: 'CC(C)Cc1ccc(C(C)C(=O)O)cc1' },
];

// Human labels + display order for the RDKit property block.
const PROPERTY_META: { key: string; label: string; unit?: string }[] = [
  { key: 'molecular_weight', label: 'Molecular weight', unit: 'g/mol' },
  { key: 'exact_mass', label: 'Exact mass', unit: 'g/mol' },
  { key: 'logp', label: 'LogP' },
  { key: 'tpsa', label: 'TPSA', unit: 'Å²' },
  { key: 'hbd', label: 'H-bond donors' },
  { key: 'hba', label: 'H-bond acceptors' },
  { key: 'rotatable_bonds', label: 'Rotatable bonds' },
  { key: 'aromatic_rings', label: 'Aromatic rings' },
  { key: 'heavy_atoms', label: 'Heavy atoms' },
  { key: 'qed', label: 'QED' },
  { key: 'lipinski_violations', label: 'Lipinski violations' },
];

function fmt(v: number | boolean | undefined): string {
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (typeof v !== 'number') return '—';
  return Number.isInteger(v) ? String(v) : v.toFixed(3).replace(/\.?0+$/, '');
}

export default function ProfilePage() {
  const [smiles, setSmiles] = useState('CC(=O)Oc1ccccc1C(=O)O');
  const [result, setResult] = useState<ProfileResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: caps } = useCapabilities();

  async function run(query: string) {
    const q = query.trim();
    if (!q) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/local/tools/get_molecule_profile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ arguments: { smiles: q } }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        setError(data?.detail || data?.error || `Request failed (${res.status})`);
        setResult(null);
      } else if (!data?.result) {
        setError(data?.detail || data?.error || 'Could not profile that SMILES. Check the structure and try again.');
        setResult(null);
      } else {
        setResult(data.result as ProfileResult);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed');
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  const admetWired = caps?.services?.admet?.wired ?? false;
  const complianceWired = caps?.compliance?.wired ?? false;
  const admet = result?.admet;
  const admetEntries = admet && typeof admet === 'object' ? Object.entries(admet) : [];

  return (
    <div className="space-y-6 max-w-5xl">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold text-[var(--text)]" style={{ fontFamily: 'var(--serif)' }}>
          Molecule Profile
        </h1>
        <p className="text-sm text-[var(--text-muted)] mt-1">
          Paste a SMILES to get computed properties in about a second — no account, no key.
        </p>
      </div>

      {/* Input */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          run(smiles);
        }}
        className="bg-[var(--card)] border border-[var(--border)] p-5 space-y-3"
      >
        <label className="block text-xs uppercase tracking-wide text-[var(--text-muted)]">SMILES</label>
        <div className="flex flex-col gap-3 sm:flex-row">
          <input
            type="text"
            value={smiles}
            onChange={(e) => setSmiles(e.target.value)}
            spellCheck={false}
            placeholder="e.g. CC(=O)Oc1ccccc1C(=O)O"
            className="flex-1 px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] font-mono text-sm focus:outline-none focus:border-[var(--accent)] transition-colors"
          />
          <button
            type="submit"
            disabled={loading || !smiles.trim()}
            className="flex items-center justify-center gap-2 px-5 py-2 text-sm font-medium text-white bg-[var(--accent)] hover:bg-[var(--accent)]/90 transition-colors disabled:opacity-50"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FlaskConical className="h-4 w-4" />}
            {loading ? 'Profiling…' : 'Profile'}
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <span className="text-xs text-[var(--text-muted)]">Try:</span>
          {EXAMPLES.map((ex) => (
            <button
              key={ex.name}
              type="button"
              onClick={() => {
                setSmiles(ex.smiles);
                run(ex.smiles);
              }}
              className="text-xs px-2.5 py-1 border border-[var(--border)] text-[var(--text-soft)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors"
            >
              {ex.name}
            </button>
          ))}
        </div>
      </form>

      {/* Error */}
      {error && (
        <div className="flex items-start gap-2 px-4 py-3 border border-[var(--destructive)]/30 bg-[var(--destructive)]/5 text-sm text-[var(--destructive)]">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Empty state (before first run, no error) */}
      {!result && !error && !loading && (
        <div className="border border-dashed border-[var(--border)] px-6 py-10 text-center text-sm text-[var(--text-muted)]">
          Enter a SMILES above and press Profile. Properties are computed locally by the engine;
          ADMET and compliance panels appear when those services are wired.
        </div>
      )}

      {/* Results */}
      {result && (
        <div className="space-y-6">
          {/* Properties */}
          <Card icon={<Beaker className="h-4 w-4" />} title="Properties" subtitle={result.source ? `source: ${result.source}` : undefined}>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-4">
              {PROPERTY_META.map(({ key, label, unit }) => (
                <div key={key}>
                  <p className="text-xs uppercase tracking-wide text-[var(--text-muted)] mb-1">{label}</p>
                  <p className="text-lg text-[var(--text)]">
                    {fmt(result.properties?.[key])}
                    {unit && result.properties?.[key] !== undefined && (
                      <span className="text-xs text-[var(--text-muted)] ml-1">{unit}</span>
                    )}
                  </p>
                </div>
              ))}
              <div>
                <p className="text-xs uppercase tracking-wide text-[var(--text-muted)] mb-1">Lipinski</p>
                <p className="text-lg">
                  {result.properties?.lipinski_pass ? (
                    <span className="text-emerald-500">pass</span>
                  ) : (
                    <span className="text-[var(--destructive)]">fail</span>
                  )}
                </p>
              </div>
            </div>
          </Card>

          {/* ADMET — gated on the ADMET service */}
          <Card icon={<FlaskConical className="h-4 w-4" />} title="ADMET">
            {result.admet_available && admetEntries.length > 0 ? (
              <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-4">
                {admetEntries.map(([k, v]) => (
                  <div key={k}>
                    <p className="text-xs uppercase tracking-wide text-[var(--text-muted)] mb-1">{k.replace(/_/g, ' ')}</p>
                    <p className="text-base text-[var(--text)]">
                      {typeof v === 'number' ? fmt(v) : String(v)}
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <NotWired
                text="ADMET predictions need the ADMET service."
                env={caps?.services?.admet?.env ?? 'ADDIE_MODELS_URL'}
                wired={admetWired}
              />
            )}
          </Card>

          {/* Compliance — gated on the compliance service */}
          <Card icon={<ShieldCheck className="h-4 w-4" />} title="Compliance">
            {complianceWired ? (
              <p className="text-sm text-[var(--text-soft)]">
                A compliance service is connected. Regulatory screening runs against it for known molecules.
              </p>
            ) : (
              <NotWired
                text="Regulatory screening and structural alerts need a compliance service."
                env={caps?.compliance?.env ?? 'NOVOMCP_COMPLIANCE_URL'}
                wired={false}
              />
            )}
          </Card>
        </div>
      )}
    </div>
  );
}

function Card({ icon, title, subtitle, children }: { icon: ReactNode; title: string; subtitle?: string; children: ReactNode }) {
  return (
    <div className="bg-[var(--card)] border border-[var(--border)]">
      <div className="px-6 py-4 border-b border-[var(--border)] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-[var(--text-muted)]">{icon}</span>
          <h2 className="text-sm font-medium tracking-wide uppercase text-[var(--text-muted)]">{title}</h2>
        </div>
        {subtitle && <span className="text-xs text-[var(--text-muted)] font-mono">{subtitle}</span>}
      </div>
      <div className="px-6 py-5">{children}</div>
    </div>
  );
}

function NotWired({ text, env, wired }: { text: string; env: string; wired: boolean }) {
  return (
    <p className="text-sm text-[var(--text-muted)]">
      {wired ? 'Service wired but no data returned for this molecule. ' : text + ' '}
      {!wired && (
        <>
          Set <code className="font-mono text-[var(--text-soft)]">{env}</code> to enable it — see{' '}
          <a
            href="https://github.com/NovoMCP/novomcp/tree/main/docs/deploying-services"
            target="_blank"
            rel="noreferrer"
            className="text-[var(--accent)] hover:underline"
          >
            deploying services
          </a>
          .
        </>
      )}
    </p>
  );
}
