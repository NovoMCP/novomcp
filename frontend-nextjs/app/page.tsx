'use client';

import { useAuth } from '@/core/auth/provider';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';

const REQUIRE_AUTH = process.env.NEXT_PUBLIC_REQUIRE_AUTH === 'true';

export default function HomePage() {
  const { isAuthenticated, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading) {
      if (isAuthenticated) {
        router.push('/dashboard');
      } else if (REQUIRE_AUTH) {
        // Hosted deploy: bounce to login. In OSS mode the auth provider
        // auto-provisions a local user, so isAuthenticated is always true.
        router.push('/login');
      } else {
        // OSS mode but somehow unauthenticated (shouldn't happen). Land on
        // dashboard anyway — the platform layout won't kick us out.
        router.push('/dashboard');
      }
    }
  }, [isAuthenticated, isLoading, router]);

  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[var(--accent)]"></div>
    </div>
  );
}
