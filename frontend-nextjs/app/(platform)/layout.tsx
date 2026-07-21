'use client';

import { useAuth } from '@/core/auth/provider';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import Sidebar from '@/ui/layouts/Sidebar';
import MobileSidebar from '@/ui/layouts/MobileSidebar';

// OSS single-user mode: default is auth-less (cleave §2). Only redirect to
// /login when the hosted deploy has opted into auth. In OSS mode the auth
// provider auto-provisions a local user, so this branch never fires anyway;
// the env-guard is defense in depth against a future auth-provider regression.
const REQUIRE_AUTH = process.env.NEXT_PUBLIC_REQUIRE_AUTH === 'true';

export default function PlatformLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { isAuthenticated, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (REQUIRE_AUTH && !isLoading && !isAuthenticated) {
      router.push('/login');
    }
  }, [isAuthenticated, isLoading, router]);

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--bg)]">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[var(--accent)]" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return null;
  }

  return (
    <div className="h-screen flex bg-[var(--bg)]">
      {/* Desktop sidebar */}
      <div className="hidden lg:flex">
        <Sidebar />
      </div>
      <main className="flex-1 overflow-y-auto">
        {/* Mobile top bar */}
        <div className="sticky top-0 z-40 flex items-center gap-3 border-b border-[var(--border)] bg-[var(--bg)]/95 backdrop-blur-sm px-4 py-3 lg:hidden">
          <MobileSidebar />
          <h1 className="text-sm font-semibold tracking-wide" style={{ fontFamily: 'var(--serif)' }}>NovoMCP</h1>
        </div>
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-6 lg:py-10">
          {children}
        </div>
      </main>
    </div>
  );
}
