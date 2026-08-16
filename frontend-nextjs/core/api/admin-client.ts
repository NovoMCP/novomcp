'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '@/core/auth/provider';

// Typed fetch wrapper for the BFF proxy. In self-host (local) mode /api/mcp
// returns a structured 503, so these hooks degrade to empty state rather than
// hanging. A deployment that wires its own backend replaces the /api/mcp route.
function useAdminFetch() {
  const { user } = useAuth();

  return async (path: string, options?: RequestInit) => {
    const res = await fetch(`/api/mcp${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${user?.accessToken}`,
        'X-User-ID': user?.id || '',
        ...options?.headers,
      },
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(err.error || err.detail || `Request failed: ${res.status}`);
    }
    return res.json();
  };
}

// ---- User profile (the one surface the local dashboard uses) ----

export function useUserProfile() {
  const fetch = useAdminFetch();
  const { user } = useAuth();
  return useQuery({
    queryKey: ['mcp-user-profile', user?.id],
    queryFn: () => fetch(`/user/me`),
    enabled: !!user,
  });
}

export function useUpdateUserProfile() {
  const fetch = useAdminFetch();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<{
      first_name: string;
      last_name: string;
      job_title: string;
      department: string;
      timezone: string;
      language_preference: string;
    }>) => fetch('/user/me', { method: 'PATCH', body: JSON.stringify(data) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mcp-user-profile'] }),
  });
}

// ---- Funnel audit (read a run's log; used by the funnel viewer) ----

export function useFunnelAudit(funnelId: string) {
  const fetch = useAdminFetch();
  return useQuery({
    queryKey: ['funnel-audit', funnelId],
    queryFn: () => fetch(`/funnel/${funnelId}/log`),
    enabled: !!funnelId,
  });
}
