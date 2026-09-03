'use client';

import { useEffect, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { Sun, Moon, Server, KeyRound, FileText, BookOpen, Github, ArrowRight } from 'lucide-react';
import { useTheme } from '@/core/providers/ThemeProvider';
import HostedAccountSettings from '@/components/settings/HostedAccountSettings';

// Settings. In OSS single-user local mode (the default) this is a lean,
// functional surface: appearance, where local config/audit live, and an about
// card — no hosted account concepts that don't apply. Hosted deploys
// (NEXT_PUBLIC_REQUIRE_AUTH) additionally render the managed account settings.
// Keys and service connections live in Connections, not here.

const REQUIRE_AUTH = process.env.NEXT_PUBLIC_REQUIRE_AUTH === 'true';

interface LocalInfo {
  engineUrl?: string;
  version?: string | null;
  credPath?: string;
  auditPath?: string;
}

export default function SettingsPage() {
  const { theme, toggleTheme } = useTheme();
  const [info, setInfo] = useState<LocalInfo>({});

  useEffect(() => {
    const get = (u: string) => fetch(u, { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    Promise.all([get('/api/local/health'), get('/api/local/llm-config'), get('/api/local/audit?limit=1')]).then(
      ([h, c, a]) => {
        setInfo({
          engineUrl: h?.engine_url,
          version: h?.version ?? null,
          credPath: c?.path,
          auditPath: a?.audit_path ? String(a.audit_path).replace(/^.*\/\.novo/, '~/.novo') : undefined,
        });
      }
    );
  }, []);

  return (
    <div className="max-w-3xl">
      <div>
        <h1 className="text-2xl text-[var(--text)]" style={{ fontFamily: 'var(--serif)', fontWeight: 500 }}>
          Settings
        </h1>
        <p className="text-sm text-[var(--text-muted)] mt-1">
          {REQUIRE_AUTH ? 'Your account, appearance, and preferences.' : 'Appearance and where this local install keeps things.'}
        </p>
      </div>

      <div className="mt-6 space-y-5">
        {/* Appearance — a real local preference */}
        <Panel icon={<Sun className="h-3.5 w-3.5" />} title="Appearance">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-[var(--text)]">Theme</p>
              <p className="text-xs text-[var(--text-muted)] mt-0.5">Applies across the dashboard and viewers.</p>
            </div>
            <div className="flex rounded-md border border-[var(--border)] overflow-hidden">
              <ThemeOption label="Light" icon={<Sun className="h-3.5 w-3.5" />} active={theme === 'light'} onClick={() => theme !== 'light' && toggleTheme()} />
              <ThemeOption label="Dark" icon={<Moon className="h-3.5 w-3.5" />} active={theme === 'dark'} onClick={() => theme !== 'dark' && toggleTheme()} />
            </div>
          </div>
        </Panel>

        {REQUIRE_AUTH ? (
          <HostedAccountSettings />
        ) : (
          /* Local environment — what this install is wired to + where it stores things */
          <Panel icon={<Server className="h-3.5 w-3.5" />} title="Local environment">
            <div className="divide-y divide-[var(--border)] -my-1">
              <InfoRow icon={<Server className="h-4 w-4" />} label="Engine URL" value={info.engineUrl || 'http://localhost:8018'} />
              <InfoRow icon={<KeyRound className="h-4 w-4" />} label="Credential store" value={info.credPath || '~/.novo/credentials.env'} />
              <InfoRow icon={<FileText className="h-4 w-4" />} label="Audit log" value={info.auditPath || '~/.novo/audit.jsonl'} />
            </div>
            <div className="mt-4 pt-4 border-t border-[var(--border)]">
              <Link href="/connections" className="inline-flex items-center gap-1.5 text-sm text-[var(--accent)] hover:underline">
                Manage API keys and services in Connections <ArrowRight className="h-3.5 w-3.5" />
              </Link>
              <p className="text-xs text-[var(--text-muted)] mt-2">
                These paths are overridable with <code className="font-mono">NOVO_CREDENTIALS_PATH</code> and{' '}
                <code className="font-mono">NOVO_AUDIT_PATH</code>.
              </p>
            </div>
          </Panel>
        )}

        {/* About */}
        <Panel icon={<BookOpen className="h-3.5 w-3.5" />} title="About">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <p className="text-sm text-[var(--text)]">
                NovoMCP{info.version ? <span className="text-[var(--text-muted)]"> · v{info.version}</span> : null}
              </p>
              <p className="text-xs text-[var(--text-muted)] mt-0.5">The computational chemistry engine, running locally.</p>
            </div>
            <div className="flex gap-2">
              <a href="https://docs.novomcp.com/" target="_blank" rel="noreferrer"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm border border-[var(--border)] rounded text-[var(--text-soft)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors">
                <BookOpen className="h-3.5 w-3.5" /> Docs
              </a>
              <a href="https://github.com/NovoMCP/novomcp" target="_blank" rel="noreferrer"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm border border-[var(--border)] rounded text-[var(--text-soft)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors">
                <Github className="h-3.5 w-3.5" /> GitHub
              </a>
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function ThemeOption({ label, icon, active, onClick }: { label: string; icon: ReactNode; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`flex items-center gap-1.5 px-3 py-1.5 text-sm transition-colors ${
        active ? 'bg-[var(--accent)]/12 text-[var(--text)]' : 'text-[var(--text-muted)] hover:text-[var(--text)]'
      }`}
    >
      <span className={active ? 'text-[var(--accent)]' : ''}>{icon}</span>
      {label}
    </button>
  );
}

function InfoRow({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="flex items-center gap-3 py-2.5">
      <span className="text-[var(--text-muted)] shrink-0">{icon}</span>
      <span className="text-sm text-[var(--text-soft)] w-32 shrink-0">{label}</span>
      <span className="text-[13px] font-mono text-[var(--text)] truncate">{value}</span>
    </div>
  );
}

function Panel({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <div className="bg-[var(--card)] border border-[var(--border)] rounded-lg overflow-hidden">
      <div className="px-6 py-3.5 border-b border-[var(--border)]">
        <h2 className="text-[11px] uppercase tracking-wider text-[var(--text-muted)] flex items-center gap-2">
          <span className="text-[var(--text-muted)]">{icon}</span>
          {title}
        </h2>
      </div>
      <div className="px-6 py-5">{children}</div>
    </div>
  );
}
