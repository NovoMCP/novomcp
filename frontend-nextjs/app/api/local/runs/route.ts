import { NextResponse } from 'next/server';
import { promises as fs } from 'fs';
import path from 'path';
import os from 'os';

// Runs, read from the engine's local audit file (~/.novo/audit.jsonl).
//
// A run is everything filed under one funnel_id: its tool calls (tool_call
// events, shown as exploration steps with their inputs and results) and the
// stages the model saved with save_funnel_stage (funnel_stage events: the
// stage, molecules in and out, what was filtered and why, the human decision).
// Mirrors the engine's local get_funnel_audit, read straight from the file so
// opening the page doesn't itself add tool calls to the history.
//
// GET /api/local/runs                 every run, most recent first
// GET /api/local/runs?id=<funnel_id>  that run's timeline
//                     &format=json    ... as a download

const AUDIT_PATH = process.env.NOVO_AUDIT_PATH || path.join(os.homedir(), '.novo', 'audit.jsonl');

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

type Payload = Record<string, unknown> & { funnel_id?: string; tool?: string };
interface AuditRecord {
  ts?: string;
  event?: string;
  payload?: Payload;
}
type Stage = Record<string, unknown> & { event_type: string; timestamp: string; stage_label?: string };

function title(s: string): string {
  return s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function toStage(rec: AuditRecord): Stage | null {
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

async function readRecords(): Promise<{ records: AuditRecord[]; error: string | null }> {
  try {
    const contents = await fs.readFile(AUDIT_PATH, 'utf-8');
    const records: AuditRecord[] = [];
    for (const line of contents.split('\n')) {
      if (!line.trim()) continue;
      try {
        records.push(JSON.parse(line));
      } catch {
        // skip malformed line
      }
    }
    return { records, error: null };
  } catch (e: unknown) {
    const err = e as { code?: string; message?: string };
    return { records: [], error: err.code === 'ENOENT' ? 'no_audit_yet' : err.message || 'read_failed' };
  }
}

export async function GET(req: Request) {
  const params = new URL(req.url).searchParams;
  const id = params.get('id');
  const { records, error } = await readRecords();

  if (id) {
    const stages = records
      .filter((r) => r.payload?.funnel_id === id)
      .map(toStage)
      .filter((s): s is Stage => s !== null)
      .sort((a, b) => a.timestamp.localeCompare(b.timestamp));
    const body = { funnel_id: id, stage_count: stages.length, stages };
    if (params.get('format') === 'json') {
      return new NextResponse(JSON.stringify(body, null, 2) + '\n', {
        headers: {
          'Content-Type': 'application/json',
          'Content-Disposition': `attachment; filename="novomcp-run-${id}.json"`,
        },
      });
    }
    return NextResponse.json(body);
  }

  const runs = new Map<
    string,
    { funnel_id: string; started_at: string; last_activity: string; tool_calls: number; checkpoints: number; last_stage: string | null }
  >();
  for (const rec of records) {
    const fid = rec.payload?.funnel_id;
    const st = toStage(rec);
    if (!fid || !st) continue;
    const run = runs.get(fid) ?? {
      funnel_id: fid,
      started_at: st.timestamp,
      last_activity: st.timestamp,
      tool_calls: 0,
      checkpoints: 0,
      last_stage: null,
    };
    if (st.event_type === 'exploration') run.tool_calls += 1;
    else run.checkpoints += 1;
    if (st.timestamp < run.started_at) run.started_at = st.timestamp;
    if (st.timestamp >= run.last_activity) {
      run.last_activity = st.timestamp;
      run.last_stage = st.stage_label ?? null;
    }
    runs.set(fid, run);
  }
  const list = Array.from(runs.values()).sort((a, b) => b.last_activity.localeCompare(a.last_activity));
  return NextResponse.json({ audit_path: AUDIT_PATH, error, total: list.length, runs: list });
}
