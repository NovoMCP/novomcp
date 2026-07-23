'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  Activity,
  Zap,
  FileText,
  ArrowRight,
  CheckCircle2,
  Circle,
  Server,
  BookOpen,
  Terminal,
  Package,
} from 'lucide-react';

// OSS Control Panel dashboard. Reads local /api/local/health and
// /api/local/audit (which read the engine + ~/.novo/audit.jsonl directly, no
// managed backend needed). Renders engine status, tool inventory, provider
// wiring, recent audit — the four things an OSS user actually wants to see
// on first boot.
//
// Hosted deploys (NEXT_PUBLIC_REQUIRE_AUTH=true) still use the same page;
// the "Local single-user mode" chip becomes the user's email + org.

interface HealthResp {
  engine_url: string;
  engine_reachable: boolean;
  health: { status?: string; timestamp?: string; services_available?: number } | null;
  tools_visible: number | null;
  tool_names: string[];
  rest_paths: number | null;
  update_status: { current_version?: string; latest_version?: string; is_newer?: boolean; release_url?: string } | null;
  providers: Record<string, boolean>;
}

interface AuditResp {
  audit_path: string;
  error: string | null;
  count: number;
  entries: Array<{ event: string; ts?: string; payload?: any }>;
}

const PROVIDER_LABELS: Record<string, { label: string; unlocks: string; envVar: string }> = {
  admet: { label: 'ADMET predictions', unlocks: 'predict_admet', envVar: 'ADDIE_MODELS_URL' },
  docking: { label: 'Molecular docking', unlocks: 'dock_molecules, dock_with_strain', envVar: 'AUTODOCK_GPU_URL' },
  md: { label: 'Molecular dynamics', unlocks: 'run_molecular_dynamics, generate_dynamics', envVar: 'GROMACS_MD_URL' },
  structure: { label: 'Structure prediction', unlocks: 'predict_structure, get_protein_structure', envVar: 'OPENFOLD3_URL' },
  qm: { label: 'Quantum mechanics', unlocks: 'run_qm_calculation, run_conformer_search', envVar: 'NOVOMCP_QM_URL' },
  nnp: { label: 'Neural network potentials', unlocks: 'compute_energy, optimize_geometry_nnp', envVar: 'NOVOMCP_NNP_URL' },
  compliance: { label: 'Regulatory compliance', unlocks: 'check_compliance', envVar: 'NOVOMCP_COMPLIANCE_URL' },
  molecule_index: { label: 'Molecule index', unlocks: 'search_similar, filter_molecules, tree tools', envVar: 'NOVOMCP_MOLECULE_INDEX_URL' },
  omics: { label: 'Omics data', unlocks: 'target_discovery, validate_target, stratify_patients', envVar: 'NOVOMCP_DB_HOST' },
  literature: { label: 'Literature search', unlocks: 'search_literature, search_patents', envVar: 'PINECONE_API_KEY' },
  clinical_outcomes: { label: 'Clinical outcomes', unlocks: 'predict_clinical_outcomes', envVar: 'NOVOEXPERT_URL' },
  materials: { label: 'Materials Project', unlocks: 'search_materials_project', envVar: 'MP_API_KEY' },
};

export default function DashboardPage() {
  const [health, setHealth] = useState<HealthResp | null>(null);
  const [audit, setAudit] = useState<AuditResp | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      fetch('/api/local/health').then((r) => (r.ok ? r.json() : null)),
      fetch('/api/local/audit?limit=10').then((r) => (r.ok ? r.json() : null)),
    ]).then(([h, a]) => {
      setHealth(h);
      setAudit(a);
      setLoading(false);
    });
  }, []);

  const enabledProviders = Object.entries(health?.providers ?? {}).filter(([, on]) => on);
  const disabledProviders = Object.entries(health?.providers ?? {}).filter(([, on]) => !on);
  const engineOk = !!health?.engine_reachable;
  const versionLine = health?.update_status?.current_version
    ? `v${health.update_status.current_version}${health.update_status.is_newer ? ` — v${health.update_status.latest_version} available` : ''}`
    : null;

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-semibold text-[var(--text)]" style={{ fontFamily: 'var(--serif)' }}>
            NovoMCP Control Panel
          </h1>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            Local single-user mode • {enabledProviders.length} of {enabledProviders.length + disabledProviders.length} optional services configured
          </p>
        </div>
        {versionLine && (
          <a
            href={health?.update_status?.release_url || '#'}
            target="_blank"
            rel="noreferrer"
            className={`text-xs px-3 py-1.5 border ${health?.update_status?.is_newer ? 'border-[var(--accent)] text-[var(--accent)]' : 'border-[var(--border)] text-[var(--text-muted)]'}`}
          >
            {versionLine}
          </a>
        )}
      </div>

      {/* Engine status card */}
      <div className="bg-[var(--card)] border border-[var(--border)]">
        <div className="px-6 py-4 border-b border-[var(--border)] flex items-center gap-2">
          <Server className="h-4 w-4 text-[var(--text-muted)]" />
          <h2 className="text-sm font-medium tracking-wide uppercase text-[var(--text-muted)]">
            Engine
          </h2>
        </div>
        <div className="px-6 py-5 grid grid-cols-2 md:grid-cols-4 gap-6">
          <Stat
            label="Status"
            value={
              loading ? '—' : engineOk ? (
                <span className="flex items-center gap-1.5 text-emerald-500">
                  <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
                  healthy
                </span>
              ) : (
                <span className="text-red-500">unreachable</span>
              )
            }
          />
          <Stat label="Tools available" value={health?.tools_visible ?? '—'} />
          <Stat label="REST endpoints" value={health?.rest_paths ?? '—'} />
          <Stat label="Engine URL" value={<span className="text-xs font-mono truncate">{health?.engine_url ?? '—'}</span>} />
        </div>
        {!loading && !engineOk && (
          <div className="px-6 py-3 border-t border-[var(--border)] text-xs text-red-500 bg-red-500/5">
            The engine at {health?.engine_url} isn&apos;t responding. Run <code className="font-mono">python main_https.py</code> from the <code className="font-mono">orchestrator/</code> directory.
          </div>
        )}
      </div>

      {/* Two-column grid: providers + recent audit */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Providers */}
        <div className="bg-[var(--card)] border border-[var(--border)] flex flex-col">
          <div className="px-6 py-4 border-b border-[var(--border)] flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Package className="h-4 w-4 text-[var(--text-muted)]" />
              <h2 className="text-sm font-medium tracking-wide uppercase text-[var(--text-muted)]">
                Optional services
              </h2>
            </div>
            <a
              href="https://github.com/NovoMCP/novomcp/blob/main/docs/product-roadmap.md"
              target="_blank"
              rel="noreferrer"
              className="text-xs text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
            >
              roadmap ↗
            </a>
          </div>
          <div className="divide-y divide-[var(--border)] max-h-96 overflow-y-auto">
            {enabledProviders.map(([key]) => (
              <ProviderRow key={key} providerKey={key} enabled />
            ))}
            {disabledProviders.map(([key]) => (
              <ProviderRow key={key} providerKey={key} enabled={false} />
            ))}
          </div>
        </div>

        {/* Recent audit */}
        <div className="bg-[var(--card)] border border-[var(--border)]">
          <div className="px-6 py-4 border-b border-[var(--border)] flex items-center justify-between">
            <div className="flex items-center gap-2">
              <FileText className="h-4 w-4 text-[var(--text-muted)]" />
              <h2 className="text-sm font-medium tracking-wide uppercase text-[var(--text-muted)]">
                Recent activity
              </h2>
            </div>
            <span className="text-xs text-[var(--text-muted)] font-mono">
              {audit?.audit_path?.replace(/^.*\/\.novo/, '~/.novo') ?? ''}
            </span>
          </div>
          <div className="divide-y divide-[var(--border)] max-h-96 overflow-y-auto">
            {loading ? (
              <div className="px-6 py-8 text-sm text-[var(--text-muted)]">Loading…</div>
            ) : audit?.error === 'no_audit_yet' ? (
              <div className="px-6 py-8 text-sm text-[var(--text-muted)]">
                No tool calls yet. Try one from the CLI:
                <pre className="mt-2 text-xs bg-[var(--bg)] p-3 font-mono border border-[var(--border)]">{`curl -X POST ${health?.engine_url}/mcp/tools/calculate_properties \\
  -H 'Authorization: Bearer x' \\
  -H 'Content-Type: application/json' \\
  -d '{"arguments": {"smiles": "CC(=O)Oc1ccccc1C(=O)O"}}'`}</pre>
              </div>
            ) : (audit?.entries.length ?? 0) === 0 ? (
              <div className="px-6 py-8 text-sm text-[var(--text-muted)]">No entries</div>
            ) : (
              audit?.entries.map((entry, i) => (
                <AuditRow key={i} entry={entry} />
              ))
            )}
          </div>
        </div>
      </div>

      {/* Quick actions */}
      <div className="bg-[var(--card)] border border-[var(--border)]">
        <div className="px-6 py-4 border-b border-[var(--border)] flex items-center gap-2">
          <Zap className="h-4 w-4 text-[var(--text-muted)]" />
          <h2 className="text-sm font-medium tracking-wide uppercase text-[var(--text-muted)]">
            Get started
          </h2>
        </div>
        <div className="px-6 py-5 grid grid-cols-1 md:grid-cols-3 gap-4">
          <QuickAction
            icon={<Terminal className="h-5 w-5" />}
            title="Use from the terminal"
            body="Call tools via curl or the JSON-RPC MCP surface."
            href="https://github.com/NovoMCP/novomcp/blob/main/docs/quickstart.md"
          />
          <QuickAction
            icon={<Activity className="h-5 w-5" />}
            title="Connect any MCP client"
            body="Works with any MCP-compatible AI assistant — Claude Desktop, Cursor, Codex, Zed, Cline, and others."
            href="https://github.com/NovoMCP/novomcp/blob/main/docs/connecting-mcp-clients.md"
          />
          <QuickAction
            icon={<BookOpen className="h-5 w-5" />}
            title="Deploy more services"
            body="Add ADMET, docking, MD, QM to unlock more tools."
            href="https://github.com/NovoMCP/novomcp/tree/main/docs/deploying-services"
          />
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-[var(--text-muted)] mb-1">{label}</p>
      <p className="text-lg text-[var(--text)]">{value}</p>
    </div>
  );
}

function ProviderRow({ providerKey, enabled }: { providerKey: string; enabled: boolean }) {
  const meta = PROVIDER_LABELS[providerKey];
  if (!meta) return null;
  return (
    <div className="px-6 py-3 flex items-center justify-between gap-4">
      <div className="flex items-center gap-3 min-w-0">
        {enabled ? (
          <CheckCircle2 className="h-4 w-4 text-emerald-500 shrink-0" />
        ) : (
          <Circle className="h-4 w-4 text-[var(--text-muted)] shrink-0" />
        )}
        <div className="min-w-0">
          <p className={`text-sm ${enabled ? 'text-[var(--text)]' : 'text-[var(--text-muted)]'}`}>{meta.label}</p>
          <p className="text-xs text-[var(--text-muted)] truncate">{meta.unlocks}</p>
        </div>
      </div>
      <code className="text-xs text-[var(--text-muted)] font-mono shrink-0">{meta.envVar}</code>
    </div>
  );
}

function AuditRow({ entry }: { entry: any }) {
  const tool = entry.payload?.tool || entry.event || 'unknown';
  const ts = entry.ts || entry.payload?.ts || '';
  const success = entry.payload?.success;
  return (
    <div className="px-6 py-3 flex items-center justify-between gap-3">
      <div className="min-w-0 flex-1">
        <p className="text-sm text-[var(--text)] font-mono truncate">{tool}</p>
        {ts && <p className="text-xs text-[var(--text-muted)]">{new Date(ts).toLocaleString()}</p>}
      </div>
      {success !== undefined && (
        <span className={`text-xs px-2 py-0.5 ${success ? 'text-emerald-500' : 'text-red-500'}`}>
          {success ? 'ok' : 'error'}
        </span>
      )}
    </div>
  );
}

function QuickAction({ icon, title, body, href }: { icon: React.ReactNode; title: string; body: string; href: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="group block p-4 border border-[var(--border)] hover:border-[var(--accent)]/50 transition-colors"
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-[var(--text-muted)] group-hover:text-[var(--accent)] transition-colors">{icon}</span>
        <ArrowRight className="h-4 w-4 text-[var(--text-muted)] opacity-0 group-hover:opacity-100 transition-opacity" />
      </div>
      <p className="text-sm font-medium text-[var(--text)] mb-1">{title}</p>
      <p className="text-xs text-[var(--text-muted)]">{body}</p>
    </a>
  );
}
