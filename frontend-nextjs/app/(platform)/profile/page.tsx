'use client';

import { useState, type ReactNode } from 'react';
import Link from 'next/link';
import { FlaskConical, ShieldCheck, AlertCircle, Loader2, ArrowRight } from 'lucide-react';
import { useCapabilities } from '@/core/api/useCapabilities';

// Molecule profile — the first in-app science surface. Paste a SMILES, get the
// properties the engine computes locally (RDKit). The result leads with a
// summary strip of the headline numbers, then the full property grid, then
// ADMET and compliance panels that light up when those services are wired.
// Everything is capability-gated: no dead cards, no fabricated fields.

interface ProfileResult {
  smiles: string;
  source?: string;
  properties?: Record<string, number | boolean>;
  admet?: Record<string, unknown> | null;
  admet_available?: boolean;
}

const EXAMPLES = [
  { name: 'Aspirin', smiles: 'CC(=O)Oc1ccccc1C(=O)O' },
  { name: 'Caffeine', smiles: 'Cn1cnc2c1c(=O)n(C)c(=O)n2C' },
  { name: 'Ibuprofen', smiles: 'CC(C)Cc1ccc(C(C)C(=O)O)cc1' },
];

// Headline metrics (summary strip) + the rest of the property grid.
const SUMMARY: { key: string; label: string; unit?: string }[] = [
  { key: 'molecular_weight', label: 'Mol. weight', unit: 'g/mol' },
  { key: 'logp', label: 'LogP' },
  { key: 'qed', label: 'QED' },
];
const DETAIL: { key: string; label: string; unit?: string }[] = [
  { key: 'exact_mass', label: 'Exact mass', unit: 'g/mol' },
  { key: 'tpsa', label: 'TPSA', unit: 'Å²' },
  { key: 'hbd', label: 'H-bond donors' },
  { key: 'hba', label: 'H-bond acceptors' },
  { key: 'rotatable_bonds', label: 'Rotatable bonds' },
  { key: 'aromatic_rings', label: 'Aromatic rings' },
  { key: 'heavy_atoms', label: 'Heavy atoms' },
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
      if (!res.ok || !data?.result) {
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

  const props = result?.properties;
  const admetWired = caps?.services?.admet?.wired ?? false;
  const complianceWired = caps?.compliance?.wired ?? false;
  const admet = result?.admet;
  const admetEntries = admet && typeof admet === 'object' ? Object.entries(admet) : [];
  const lipinskiPass = props?.lipinski_pass;

  return (
    <div className="max-w-4xl">
      {/* header */}
      <div>
        <h1 className="text-2xl text-[var(--text)]" style={{ fontFamily: 'var(--serif)', fontWeight: 500 }}>
          Molecule Profile
        </h1>
        <p className="text-sm text-[var(--text-muted)] mt-1">
          Paste a SMILES to get computed properties in about a second — no account, no key.
        </p>
      </div>

      {/* input */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          run(smiles);
        }}
        className="bg-[var(--card)] border border-[var(--border)] rounded-lg p-5 mt-6"
      >
        <label className="block text-[11px] uppercase tracking-wider text-[var(--text-muted)] mb-2">SMILES</label>
        <div className="flex flex-col gap-3 sm:flex-row">
          <input
            type="text"
            value={smiles}
            onChange={(e) => setSmiles(e.target.value)}
            spellCheck={false}
            placeholder="e.g. CC(=O)Oc1ccccc1C(=O)O"
            className="flex-1 px-3 py-2.5 bg-[var(--bg-warm)] border border-[var(--border)] rounded text-[var(--text)] font-mono text-sm focus:outline-none focus:border-[var(--accent)] transition-colors"
          />
          <button
            type="submit"
            disabled={loading || !smiles.trim()}
            className="flex items-center justify-center gap-2 px-5 py-2.5 text-sm font-medium text-white bg-[var(--accent)] rounded hover:brightness-105 transition disabled:opacity-50"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FlaskConical className="h-4 w-4" />}
            {loading ? 'Profiling…' : 'Profile'}
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-2 pt-3">
          <span className="text-xs text-[var(--text-muted)]">Try:</span>
          {EXAMPLES.map((ex) => (
            <button
              key={ex.name}
              type="button"
              onClick={() => {
                setSmiles(ex.smiles);
                run(ex.smiles);
              }}
              className="text-xs px-2.5 py-1 border border-[var(--border)] rounded text-[var(--text-soft)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors"
            >
              {ex.name}
            </button>
          ))}
        </div>
      </form>

      {error && (
        <div className="flex items-start gap-2 px-4 py-3 mt-5 border border-[var(--destructive)]/30 bg-[var(--destructive)]/5 rounded-lg text-sm text-[var(--destructive)]">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {!result && !error && !loading && (
        <div className="border border-dashed border-[var(--border)] rounded-lg px-6 py-12 mt-5 text-center text-sm text-[var(--text-muted)]">
          Enter a SMILES above and press Profile. Properties are computed locally by the engine; ADMET and compliance
          panels appear when those services are connected.
        </div>
      )}

      {result && props && (
        <div className="mt-6 space-y-5">
          {/* summary strip — headline numbers */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-[var(--border)] border border-[var(--border)] rounded-lg overflow-hidden">
            {SUMMARY.map((s) => (
              <div key={s.key} className="bg-[var(--card)] px-5 py-5">
                <p className="text-[10.5px] uppercase tracking-wider text-[var(--text-muted)]">{s.label}</p>
                <p className="text-2xl text-[var(--text)] mt-1.5 tabular-nums" style={{ fontFamily: 'var(--serif)' }}>
                  {fmt(props[s.key])}
                  {s.unit && props[s.key] !== undefined && (
                    <span className="text-xs text-[var(--text-muted)] ml-1">{s.unit}</span>
                  )}
                </p>
              </div>
            ))}
            <div className="bg-[var(--card)] px-5 py-5">
              <p className="text-[10.5px] uppercase tracking-wider text-[var(--text-muted)]">Lipinski</p>
              <p className="text-2xl mt-1.5" style={{ fontFamily: 'var(--serif)' }}>
                {lipinskiPass ? (
                  <span className="text-emerald-500">Pass</span>
                ) : (
                  <span className="text-[var(--destructive)]">Fail</span>
                )}
              </p>
            </div>
          </div>

          {/* full property grid */}
          <Panel title="Properties" note={result.source ? `source: ${result.source}` : undefined}>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-5">
              {DETAIL.map(({ key, label, unit }) => (
                <div key={key}>
                  <p className="text-[10.5px] uppercase tracking-wider text-[var(--text-muted)] mb-1">{label}</p>
                  <p className="text-[15px] text-[var(--text)] tabular-nums">
                    {fmt(props[key])}
                    {unit && props[key] !== undefined && <span className="text-xs text-[var(--text-muted)] ml-1">{unit}</span>}
                  </p>
                </div>
              ))}
            </div>
          </Panel>

          {/* ADMET */}
          <Panel title="ADMET" icon={<FlaskConical className="h-3.5 w-3.5" />}>
            {result.admet_available && admetEntries.length > 0 ? (
              <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-5">
                {admetEntries.map(([k, v]) => (
                  <div key={k}>
                    <p className="text-[10.5px] uppercase tracking-wider text-[var(--text-muted)] mb-1">{k.replace(/_/g, ' ')}</p>
                    <p className="text-[15px] text-[var(--text)] tabular-nums">{typeof v === 'number' ? fmt(v) : String(v)}</p>
                  </div>
                ))}
              </div>
            ) : (
              <NotWired text="ADMET predictions need the ADMET service." wired={admetWired} />
            )}
          </Panel>

          {/* Compliance */}
          <Panel title="Compliance" icon={<ShieldCheck className="h-3.5 w-3.5" />}>
            {complianceWired ? (
              <p className="text-sm text-[var(--text-soft)]">
                A compliance service is connected. Regulatory screening runs against it for known molecules.
              </p>
            ) : (
              <NotWired text="Regulatory screening and structural alerts need a compliance service." wired={false} />
            )}
          </Panel>
        </div>
      )}
    </div>
  );
}

function Panel({ title, note, icon, children }: { title: string; note?: string; icon?: ReactNode; children: ReactNode }) {
  return (
    <div className="bg-[var(--card)] border border-[var(--border)] rounded-lg overflow-hidden">
      <div className="px-6 py-3.5 border-b border-[var(--border)] flex items-center justify-between">
        <h2 className="text-[11px] uppercase tracking-wider text-[var(--text-muted)] flex items-center gap-2">
          {icon && <span className="text-[var(--text-muted)]">{icon}</span>}
          {title}
        </h2>
        {note && <span className="text-xs text-[var(--text-muted)] font-mono">{note}</span>}
      </div>
      <div className="px-6 py-5">{children}</div>
    </div>
  );
}

function NotWired({ text, wired }: { text: string; wired: boolean }) {
  if (wired) {
    return <p className="text-sm text-[var(--text-muted)]">Service connected, but it returned no data for this molecule.</p>;
  }
  return (
    <div className="flex flex-col items-start gap-3">
      <p className="text-sm text-[var(--text-muted)]">{text}</p>
      <Link href="/connections" className="inline-flex items-center gap-1.5 text-sm text-[var(--accent)] hover:underline">
        Connect it in Connections <ArrowRight className="h-3.5 w-3.5" />
      </Link>
    </div>
  );
}
