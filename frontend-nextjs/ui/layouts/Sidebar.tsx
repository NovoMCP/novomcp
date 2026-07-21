'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard,
  Key,
  Settings,
  Users,
  CreditCard,
  Plug,
  Shield,
  FileText,
  FlaskConical,
  FileUp,
  Atom,
  LogOut,
  Sun,
  Moon,
  LucideIcon
} from 'lucide-react';
import { useAuth } from '@/core/auth/provider';
import { useTheme } from '@/core/providers/ThemeProvider';

interface NavItem {
  name: string;
  href: string;
  icon: LucideIcon;
  adminOnly?: boolean;
  // Served by a different service (the Studio SPA at /studio), so it needs a
  // full-page navigation rather than Next client-side routing.
  external?: boolean;
  // Hidden in OSS single-user mode (NEXT_PUBLIC_REQUIRE_AUTH != 'true').
  // These items either require the hosted spine (billing/team/keys tied to a
  // hosted account) or point at a separate service the OSS user isn't
  // running (Workbench = NovoWorkbench cloud SPA, ships as its own repo).
  hostedOnly?: boolean;
}

// OSS single-user mode is the default (per cleave §2). Hosted deploys opt
// in with NEXT_PUBLIC_REQUIRE_AUTH=true.
const REQUIRE_AUTH = process.env.NEXT_PUBLIC_REQUIRE_AUTH === 'true';

const NAV_ITEMS: NavItem[] = [
  { name: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
  // NovoWorkbench cloud runtime — separate repo, self-host separately in OSS.
  { name: 'Workbench', href: '/workbench/', icon: Atom, external: true, hostedOnly: true },
  // Jobs / Files / Audit read from dashboard-aggregator today. OSS v0.2 will
  // route Jobs+Files through the engine (async_jobs / file_intelligence
  // endpoints exist) and Audit through the local ~/.novo/audit.jsonl sink.
  // Hidden for v0.1 to avoid the "spinner forever" trap on an unwired
  // aggregator.
  { name: 'Pipeline Jobs', href: '/jobs', icon: FlaskConical, hostedOnly: true },
  { name: 'Files', href: '/files', icon: FileUp, hostedOnly: true },
  // /keys, /team, /billing are hosted-spine features (nmcp_* key issuance,
  // Stripe billing, org membership). Not implemented in OSS.
  { name: 'API Keys', href: '/keys', icon: Key, hostedOnly: true },
  { name: 'Settings', href: '/settings', icon: Settings },
  { name: 'Organization', href: '/team', icon: Users, adminOnly: true, hostedOnly: true },
  { name: 'Billing', href: '/billing', icon: CreditCard, adminOnly: true, hostedOnly: true },
  // Connections + Policies both call novomcp-auth (not wired in OSS).
  // v0.2 target: connections wire through the pluggable-provider config
  // (compliance/index/observability keys); policies read from a local YAML.
  { name: 'Connections', href: '/connections', icon: Plug, adminOnly: true, hostedOnly: true },
  { name: 'Policies', href: '/policies', icon: Shield, adminOnly: true, hostedOnly: true },
  { name: 'Audit Log', href: '/audit', icon: FileText, adminOnly: true, hostedOnly: true },
];

interface SidebarProps {
  onNavigate?: () => void;
}

export default function Sidebar({ onNavigate }: SidebarProps) {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();

  const isAdmin = user?.roles?.includes('admin');

  const visibleItems = NAV_ITEMS.filter(
    (item) => (!item.adminOnly || isAdmin) && (REQUIRE_AUTH || !item.hostedOnly)
  );

  const allUserItems = visibleItems.filter((item) => !item.adminOnly);
  const adminItems = visibleItems.filter((item) => item.adminOnly);

  const renderNavLink = (item: NavItem) => {
    const isActive = pathname === item.href || pathname.startsWith(`${item.href}/`);
    const Icon = item.icon;
    const className = `group flex items-center gap-3 px-4 py-2.5 text-sm transition-all duration-[var(--ease)] ${
      isActive
        ? 'text-[var(--text)] border-l-2 border-[var(--accent)] bg-[var(--bg)]/60 font-medium'
        : 'text-[var(--text-soft)] border-l-2 border-transparent hover:text-[var(--text)] hover:bg-[var(--bg)]/40'
    }`;

    // The Studio SPA is a separate service → full-page navigation via a plain
    // anchor, not Next client-side routing.
    if (item.external) {
      return (
        <a key={item.name} href={item.href} onClick={onNavigate} className={className}>
          <Icon className="h-[18px] w-[18px] shrink-0" />
          <span>{item.name}</span>
        </a>
      );
    }

    return (
      <Link key={item.name} href={item.href} onClick={onNavigate} className={className}>
        <Icon className="h-[18px] w-[18px] shrink-0" />
        <span>{item.name}</span>
      </Link>
    );
  };

  return (
    <div
      className="flex flex-col w-full bg-[var(--bg-warm)] border-r border-[var(--border)]"
      style={{ width: 'var(--sidebar-width)' }}
    >
      {/* Logo */}
      <div className="px-6 py-6 border-b border-[var(--border)] flex items-center justify-between">
        <h1
          className="text-xl font-semibold tracking-wide"
          style={{ fontFamily: 'var(--serif)' }}
        >
          NovoMCP
        </h1>
        <button
          onClick={toggleTheme}
          className="p-1.5 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors duration-200"
          aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
        >
          {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-4">
        <div className="space-y-0.5">
          {allUserItems.map(renderNavLink)}
        </div>

        {adminItems.length > 0 && (
          <>
            <div className="mx-4 my-3 border-t border-[var(--border)]" />
            <p className="px-6 mb-2 text-[10px] font-medium uppercase tracking-[0.1em] text-[var(--text-muted)]">
              Admin
            </p>
            <div className="space-y-0.5">
              {adminItems.map(renderNavLink)}
            </div>
          </>
        )}
      </nav>

      {/* User section */}
      <div className="px-5 py-4 border-t border-[var(--border)]">
        <div className="mb-3">
          <p className="text-sm font-medium text-[var(--text)] truncate">
            {user?.name}
          </p>
          <p className="text-xs text-[var(--text-muted)] truncate">
            {REQUIRE_AUTH ? user?.email : 'Local single-user mode'}
          </p>
        </div>
        {REQUIRE_AUTH && (
          <button
            onClick={logout}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--text-soft)] hover:text-[var(--text)] hover:bg-[var(--bg)]/50 transition-all duration-200"
          >
            <LogOut className="h-4 w-4" />
            <span>Sign out</span>
          </button>
        )}
      </div>
    </div>
  );
}
