'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard,
  FlaskConical,
  Plug,
  Settings,
  LogOut,
  Sun,
  Moon,
  LucideIcon,
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
  // These items either require the managed backend (billing/team/keys tied to a
  // hosted account) or point at a separate service the OSS user isn't
  // running (Workbench = NovoWorkbench cloud SPA, ships as its own repo).
  hostedOnly?: boolean;
}

// OSS single-user mode is the default . Hosted deploys opt
// in with NEXT_PUBLIC_REQUIRE_AUTH=true.
const REQUIRE_AUTH = process.env.NEXT_PUBLIC_REQUIRE_AUTH === 'true';

const NAV_ITEMS: NavItem[] = [
  { name: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
  { name: 'Profile', href: '/profile', icon: FlaskConical },
  { name: 'Connections', href: '/connections', icon: Plug },
  { name: 'Settings', href: '/settings', icon: Settings },
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
    // Inset rounded pill, filled on the active route — same rounded/tinted
    // language as the cards and marketplace tiles (replaces the old left rail).
    const className = `group flex items-center gap-3 mx-3 px-3 py-2 rounded-md text-sm transition-colors ${
      isActive
        ? 'bg-[var(--accent)]/12 text-[var(--text)] font-medium'
        : 'text-[var(--text-soft)] hover:text-[var(--text)] hover:bg-[var(--bg)]/50'
    }`;
    const icon = (
      <Icon className={`h-[18px] w-[18px] shrink-0 ${isActive ? 'text-[var(--accent)]' : ''}`} />
    );

    // The Studio SPA is a separate service → full-page navigation via a plain
    // anchor, not Next client-side routing.
    if (item.external) {
      return (
        <a key={item.name} href={item.href} onClick={onNavigate} className={className}>
          {icon}
          <span>{item.name}</span>
        </a>
      );
    }

    return (
      <Link key={item.name} href={item.href} onClick={onNavigate} className={className}>
        {icon}
        <span>{item.name}</span>
      </Link>
    );
  };

  return (
    <div
      className="flex flex-col w-full bg-[var(--bg-warm)] border-r border-[var(--border)]"
      style={{ width: 'var(--sidebar-width)' }}
    >
      {/* Logo — monogram mark + wordmark, matching the tile language */}
      <div className="px-5 py-5 border-b border-[var(--border)] flex items-center justify-between">
        <Link href="/dashboard" onClick={onNavigate} className="flex items-center gap-2.5">
          <span
            className="h-7 w-7 grid place-items-center rounded-md bg-[var(--accent)] text-white text-[15px] leading-none"
            style={{ fontFamily: 'var(--serif)' }}
            aria-hidden
          >
            N
          </span>
          <span className="text-xl font-semibold tracking-wide" style={{ fontFamily: 'var(--serif)' }}>
            NovoMCP
          </span>
        </Link>
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
        <div className="space-y-1">{allUserItems.map(renderNavLink)}</div>

        {adminItems.length > 0 && (
          <>
            <div className="mx-5 my-3 border-t border-[var(--border)]" />
            <p className="px-6 mb-2 text-[10px] font-medium uppercase tracking-[0.1em] text-[var(--text-muted)]">
              Admin
            </p>
            <div className="space-y-1">{adminItems.map(renderNavLink)}</div>
          </>
        )}
      </nav>

      {/* User section */}
      <div className="px-5 py-4 border-t border-[var(--border)]">
        <div className="mb-3 flex items-center gap-2">
          <span
            className="h-2 w-2 rounded-full bg-emerald-500 shrink-0"
            style={{ boxShadow: '0 0 0 3px rgba(116,176,131,.16)' }}
            aria-hidden
          />
          <div className="min-w-0">
            <p className="text-sm font-medium text-[var(--text)] truncate">{user?.name || 'Local'}</p>
            <p className="text-xs text-[var(--text-muted)] truncate">
              {REQUIRE_AUTH ? user?.email : 'Local single-user mode'}
            </p>
          </div>
        </div>
        {REQUIRE_AUTH && (
          <button
            onClick={logout}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm text-[var(--text-soft)] hover:text-[var(--text)] hover:bg-[var(--bg)]/50 transition-colors"
          >
            <LogOut className="h-4 w-4" />
            <span>Sign out</span>
          </button>
        )}
      </div>
    </div>
  );
}
