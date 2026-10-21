import { promises as fs } from 'fs';
import path from 'path';
import os from 'os';

// Reading the engine's local audit file (~/.novo/audit.jsonl, or
// NOVO_AUDIT_PATH). Server-only. Shared by the Activity routes: calls
// (/api/local/audit), runs (/api/local/runs) and warehouse export
// (/api/local/export).

export const AUDIT_PATH = process.env.NOVO_AUDIT_PATH || path.join(os.homedir(), '.novo', 'audit.jsonl');

export interface Payload {
  tool?: string;
  funnel_id?: string | null;
  success?: boolean;
  execution_time_ms?: number;
  surface?: string;
  error?: string | null;
  arguments?: Record<string, unknown>;
  outcome?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface Entry {
  ts?: string;
  event?: string;
  user?: string | null;
  org?: string | null;
  payload?: Payload;
}

export interface Line {
  raw: string;
  entry: Entry;
}

export interface CallFilters {
  tool: string;
  status: string;
  run: string;
  q: string;
}

export async function readAuditLines(): Promise<{ lines: Line[]; error: string | null }> {
  const lines: Line[] = [];
  try {
    const contents = await fs.readFile(AUDIT_PATH, 'utf-8');
    for (const raw of contents.split('\n')) {
      if (!raw.trim()) continue;
      try {
        lines.push({ raw, entry: JSON.parse(raw) });
      } catch {
        // skip malformed line
      }
    }
    return { lines, error: null };
  } catch (e: unknown) {
    const err = e as { code?: string; message?: string };
    return { lines, error: err.code === 'ENOENT' ? 'no_audit_yet' : err.message || 'read_failed' };
  }
}

export function matchesCall(line: Line, f: CallFilters): boolean {
  const p = line.entry.payload ?? {};
  if (f.tool && p.tool !== f.tool) return false;
  if (f.status === 'ok' && p.success === false) return false;
  if (f.status === 'error' && p.success !== false) return false;
  if (f.run && p.funnel_id !== f.run) return false;
  if (f.q && !line.raw.toLowerCase().includes(f.q)) return false;
  return true;
}

// ── runs ────────────────────────────────────────────────────────────────
// A run is everything filed under one funnel_id: its tool calls (tool_call
// events, shown as exploration steps with their inputs and results) and the
// stages the model saved with save_funnel_stage (funnel_stage events).

// Tool calls that read or write the trail itself, not steps of the run
// (the engine's FUNNEL_AUTOLOG_SKIP, plus list_funnels).
const NOT_STEPS = new Set([
  'save_funnel_stage',
  'save_funnel_context',
  'save_funnel_memory',
  'get_funnel_audit',
  'get_funnel_context',
  'get_pipeline_audit',
  'search_prior_runs',
  'run_novo_ag',
  'list_funnels',
]);

export type Stage = Record<string, unknown> & { event_type: string; timestamp: string; stage_label?: string };

function title(s: string): string {
  return s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function toStage(rec: Entry): Stage | null {
  const p = rec.payload ?? {};
  if (rec.event === 'funnel_stage') {
    return { ...p, event_type: String(p.event_type ?? 'checkpoint'), timestamp: String(p.timestamp ?? rec.ts ?? '') } as Stage;
  }
  if (rec.event === 'tool_call' && p.tool && !NOT_STEPS.has(p.tool)) {
    const ok = p.success !== false;
    return {
      event_type: 'exploration',
      stage_name: p.tool,
      stage_label: title(p.tool),
      tool_name: p.tool,
      tool_arguments: p.arguments ?? null,
      results_summary: ok ? p.outcome ?? null : { error: p.error ?? 'failed' },
      success: ok,
      execution_time_ms: p.execution_time_ms ?? null,
      timestamp: rec.ts ?? '',
    };
  }
  return null;
}

export function runStages(entries: Entry[], runId: string): Stage[] {
  return entries
    .filter((r) => r.payload?.funnel_id === runId)
    .map(toStage)
    .filter((s): s is Stage => s !== null)
    .sort((a, b) => a.timestamp.localeCompare(b.timestamp));
}
