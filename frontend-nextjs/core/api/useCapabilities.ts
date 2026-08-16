'use client';

import { useQuery } from '@tanstack/react-query';

export interface ServiceCapability {
  wired: boolean;
  reachable: boolean;
  label: string;
  env: string;
}

export interface Capabilities {
  engine: { url: string; reachable: boolean; version: string | null };
  spineMode: 'local' | 'hosted';
  services: Record<string, ServiceCapability>;
  compliance: { wired: boolean; reachable: boolean; env: string };
  native: { isTauri: boolean; localFs: boolean; pymol: boolean };
}

async function fetchCapabilities(): Promise<Capabilities> {
  const res = await fetch('/api/local/capabilities', { cache: 'no-store' });
  if (!res.ok) throw new Error(`capabilities ${res.status}`);
  return res.json();
}

// Single source of truth for capability-gating across the UI (see
// web-gui-scope.md §6). Refetches on a 30s stale window and on window focus so
// newly-wired services light up without a manual reload.
export function useCapabilities() {
  return useQuery({
    queryKey: ['capabilities'],
    queryFn: fetchCapabilities,
    staleTime: 30_000,
    refetchOnWindowFocus: true,
  });
}
