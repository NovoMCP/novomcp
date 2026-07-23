'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '@/core/auth/provider';

// Typed fetch wrapper that calls the BFF proxy
function useAdminFetch() {
  const { user } = useAuth();

  return async (path: string, options?: RequestInit) => {
    const res = await fetch(`/api/mcp${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${user?.accessToken}`,
        'X-Org-ID': user?.orgId || '',
        'X-User-ID': user?.id || '',
        'X-User-Roles': (user?.roles || []).join(','),
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

// ---- API Keys ----

export function useKeys() {
  const fetch = useAdminFetch();
  const { user } = useAuth();
  return useQuery({
    queryKey: ['mcp-keys', user?.orgId],
    queryFn: () => fetch(`/admin/keys?org_id=${user?.orgId}`),
    enabled: !!user,
  });
}

export function useCreateKey() {
  const fetch = useAdminFetch();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { org_id: string; email: string; name?: string; role: string }) =>
      fetch('/admin/keys', { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mcp-keys'] }),
  });
}

export function useRevokeKey() {
  const fetch = useAdminFetch();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (keyId: string) => fetch(`/admin/keys/${keyId}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mcp-keys'] }),
  });
}

// ---- Org LLM provider (Studio BYO key) ----

export interface LlmConfigStatus {
  configured: boolean;
  config: { provider: string; model: string; base_url?: string | null; updated_by?: string | null; updated_at?: string | null } | null;
}

export function useLlmConfig() {
  const fetch = useAdminFetch();
  const { user } = useAuth();
  return useQuery<LlmConfigStatus>({
    queryKey: ['llm-config', user?.orgId],
    queryFn: () => fetch('/org/llm-config'),
    enabled: !!user?.orgId,
  });
}

export function useSetLlmConfig() {
  const fetch = useAdminFetch();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { provider: string; model: string; api_key: string; base_url?: string }) =>
      fetch('/org/llm-config', { method: 'PUT', body: JSON.stringify(data) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['llm-config'] }),
  });
}

export function useDeleteLlmConfig() {
  const fetch = useAdminFetch();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => fetch('/org/llm-config', { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['llm-config'] }),
  });
}

// ---- Novo Compute Keys ----

/**
 * @deprecated 2026-06-14 — replaced by `useCreateMyComputeKey` (per-user
 * self-service `POST /me/compute-key`). The old org-wide endpoint is
 * still functional but will be removed after the sunset date set on
 * the backend response headers (`Sunset: Wed, 14 Oct 2026`). Frontend
 * callers were removed in NovoMCP #111 + the Keys page cleanup;
 * keeping the hook export for one release to avoid a breaking API
 * change on any out-of-tree consumer.
 */
export function useCreateComputeKey() {
  const fetch = useAdminFetch();
  const { user } = useAuth();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      fetch(`/admin/compute-keys?org_id=${user?.orgId}`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mcp-keys'] }),
  });
}

// Admin revoke for another user's compute key. Targets the
// mcp_api_keys row id, not the user's id — that row is what `GET
// /admin/keys` returns to the Keys page table.
export function useAdminRevokeComputeKey() {
  const fetch = useAdminFetch();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (keyId: string) =>
      fetch(`/admin/keys/${keyId}/compute-key`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mcp-keys'] }),
  });
}

// ---- Self-service Novo Core key (per-user, /me/api-key) ----
// Symmetric to the compute trio below. Any authenticated user can mint
// their own nmcp_ key without admin involvement — closes the
// "wait for admin to provision me" friction. Role inherits from
// profiles.role_name server-side. Admin can still mint on behalf of
// another email via the legacy /admin/keys flow.

export function useCreateMyApiKey() {
  const fetch = useAdminFetch();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => fetch('/me/api-key', { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mcp-keys'] });
      qc.invalidateQueries({ queryKey: ['mcp-user-profile'] });
    },
  });
}

export function useRotateMyApiKey() {
  const fetch = useAdminFetch();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => fetch('/me/api-key/rotate', { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mcp-keys'] });
      qc.invalidateQueries({ queryKey: ['mcp-user-profile'] });
    },
  });
}

export function useRevokeMyApiKey() {
  const fetch = useAdminFetch();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => fetch('/me/api-key', { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mcp-keys'] });
      qc.invalidateQueries({ queryKey: ['mcp-user-profile'] });
    },
  });
}

// ---- Self-service Novo Compute key (per-user, /me/compute-key) ----
// Replaces useCreateComputeKey's org-wide model. Each user manages their
// own ncmcp_ credential; all calls still draw from the org credit pool.
// The user's compute_key_prefix on their mcp_api_keys row is the source
// of truth for "do I have an active compute key?" — already surfaced on
// the /admin/keys list response.

export function useCreateMyComputeKey() {
  const fetch = useAdminFetch();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => fetch('/me/compute-key', { method: 'POST' }),
    onSuccess: () => {
      // Both queries carry compute_key_prefix and both surfaces (Keys page
      // table + personal Novo Compute card / dashboard widget) need to
      // refresh after a mint/rotate/revoke. Invalidating only mcp-keys
      // left the personal card and dashboard reading the prior value
      // until the next natural refetch.
      qc.invalidateQueries({ queryKey: ['mcp-keys'] });
      qc.invalidateQueries({ queryKey: ['mcp-user-profile'] });
    },
  });
}

export function useRotateMyComputeKey() {
  const fetch = useAdminFetch();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => fetch('/me/compute-key/rotate', { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mcp-keys'] });
      qc.invalidateQueries({ queryKey: ['mcp-user-profile'] });
    },
  });
}

export function useRevokeMyComputeKey() {
  const fetch = useAdminFetch();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => fetch('/me/compute-key', { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mcp-keys'] });
      qc.invalidateQueries({ queryKey: ['mcp-user-profile'] });
    },
  });
}

// ---- Org Usage / Credits ----

export function useOrgUsage() {
  const fetch = useAdminFetch();
  const { user } = useAuth();
  return useQuery({
    queryKey: ['mcp-usage', user?.orgId],
    queryFn: () => fetch(`/org/${user?.orgId}/usage`),
    enabled: !!user?.orgId,
    staleTime: 60_000,
  });
}

// ---- Per-user usage ----

export function usePerUserUsage() {
  const fetch = useAdminFetch();
  const { user } = useAuth();
  return useQuery({
    queryKey: ['mcp-usage-by-user', user?.orgId],
    queryFn: () => fetch(`/admin/orgs/${user?.orgId}/usage/by-user`),
    enabled: !!user?.orgId,
  });
}

// ---- OAuth ----

export function useInitiateOAuth() {
  const fetch = useAdminFetch();
  return useMutation({
    mutationFn: (data: {
      org_id: string;
      connector_type: string;
      user_id: string;
      display_name: string;
      description?: string;
      config?: Record<string, any>;
      redirect_uri: string;
    }) => fetch('/oauth/initiate', { method: 'POST', body: JSON.stringify(data) }),
  });
}

// ---- Connections ----

export function useConnections() {
  const fetch = useAdminFetch();
  const { user } = useAuth();
  return useQuery({
    queryKey: ['mcp-connections', user?.orgId],
    queryFn: () => fetch(`/admin/connections?org_id=${user?.orgId}`),
    enabled: !!user?.orgId,
  });
}

export function useCreateConnection() {
  const fetch = useAdminFetch();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      org_id: string;
      display_name: string;
      connector_type: string;
      description?: string;
      config: Record<string, any>;
      credentials: Record<string, any>;
    }) => fetch('/admin/connections', { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mcp-connections'] }),
  });
}

export function useTestConnection() {
  const fetch = useAdminFetch();
  return useMutation({
    mutationFn: (connectionId: string) =>
      fetch(`/admin/connections/${connectionId}/test`, { method: 'POST' }),
  });
}

export function useDeleteConnection() {
  const fetch = useAdminFetch();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: string) =>
      fetch(`/admin/connections/${connectionId}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mcp-connections'] }),
  });
}

// ---- Policies ----

export function usePolicies() {
  const fetch = useAdminFetch();
  const { user } = useAuth();
  return useQuery({
    queryKey: ['mcp-policies', user?.orgId],
    queryFn: () => fetch(`/policies/${user?.orgId}`),
    enabled: !!user?.orgId,
  });
}

export function useCreatePolicy() {
  const fetch = useAdminFetch();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Record<string, any>) =>
      fetch('/policies', { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mcp-policies'] }),
  });
}

export function useDeletePolicy() {
  const fetch = useAdminFetch();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (policyId: string) => fetch(`/policies/${policyId}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mcp-policies'] }),
  });
}

// ---- Audit Log ----

export function useAuditLog(params?: { page?: number; per_page?: number; user_id?: string; tool_name?: string }) {
  const fetch = useAdminFetch();
  const { user } = useAuth();
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set('page', String(params.page));
  if (params?.per_page) searchParams.set('per_page', String(params.per_page));
  if (params?.user_id) searchParams.set('user_id', params.user_id);
  if (params?.tool_name) searchParams.set('tool_name', params.tool_name);
  const qs = searchParams.toString();

  return useQuery({
    queryKey: ['mcp-audit', user?.orgId, params],
    queryFn: () => fetch(`/admin/orgs/${user?.orgId}/audit${qs ? `?${qs}` : ''}`),
    enabled: !!user?.orgId,
  });
}

// ---- Org Details (Team page) ----

export function useOrgDetails() {
  const fetch = useAdminFetch();
  const { user } = useAuth();
  return useQuery({
    queryKey: ['mcp-org', user?.orgId],
    queryFn: () => fetch(`/admin/orgs/${user?.orgId}`),
    enabled: !!user?.orgId,
  });
}

// ---- Add Team Member ----

export function useAddTeamMember() {
  const fetch = useAdminFetch();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { org_name: string; tier: string; email: string; first_name: string; last_name: string; role: string; org_id: string }) =>
      fetch('/admin/onboard', { method: 'POST', body: JSON.stringify(data) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mcp-org'] }),
  });
}

// ---- User Profile (for settings) ----

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

export function useResendVerification() {
  const fetch = useAdminFetch();
  const { user } = useAuth();
  return useMutation({
    mutationFn: () =>
      fetch('/resend-verification', {
        method: 'POST',
        body: JSON.stringify({ email: user?.email }),
      }),
  });
}

// ---- Async Jobs (Pipeline Job Tracker) ----

export function useAsyncJobs(filters?: { status?: string; service?: string }) {
  const fetch = useAdminFetch();
  const { user } = useAuth();

  const params = new URLSearchParams();
  if (filters?.status) params.set('status', filters.status);
  if (filters?.service) params.set('service', filters.service);
  const qs = params.toString();

  return useQuery({
    queryKey: ['async-jobs', user?.orgId, filters?.status, filters?.service],
    queryFn: () => fetch(`/jobs${qs ? `?${qs}` : ''}`),
    enabled: !!user,
    refetchInterval: 30_000, // Poll every 30s for running jobs
  });
}

export function useJobContext(jobId: string) {
  const fetch = useAdminFetch();
  return useQuery({
    queryKey: ['job-context', jobId],
    queryFn: () => fetch(`/jobs/${jobId}/context`),
    enabled: !!jobId,
  });
}

export function usePipelineAudit(pipelineId: string) {
  const fetch = useAdminFetch();
  return useQuery({
    queryKey: ['pipeline-audit', pipelineId],
    queryFn: () => fetch(`/pipelines/${pipelineId}/audit`),
    enabled: !!pipelineId,
  });
}

export function useFunnelRuns(limit: number = 20) {
  const fetch = useAdminFetch();
  const { user } = useAuth();
  return useQuery({
    queryKey: ['funnel-runs', user?.orgId],
    queryFn: () => fetch(`/funnel?limit=${limit}`),
    enabled: !!user,
  });
}

export function useFunnelAudit(funnelId: string) {
  const fetch = useAdminFetch();
  return useQuery({
    queryKey: ['funnel-audit', funnelId],
    queryFn: () => fetch(`/funnel/${funnelId}/log`),
    enabled: !!funnelId,
  });
}

// ---- File Intelligence Layer ----

export function useFiles(filters?: { file_type?: string; status?: string }) {
  const fetch = useAdminFetch();
  const { user } = useAuth();

  return useQuery({
    queryKey: ['files', user?.orgId, filters?.file_type, filters?.status],
    queryFn: async () => {
      const args: Record<string, unknown> = { limit: 100 };
      if (filters?.file_type) args.file_type = filters.file_type;
      if (filters?.status) args.status = filters.status;
      const resp = await fetch('/files', {
        method: 'POST',
        body: JSON.stringify({ arguments: args }),
      });
      // novomcp tool endpoint returns {result: {...}, usage: {...}}
      return resp.result || resp;
    },
    enabled: !!user,
    refetchInterval: 15_000,
  });
}
