import { NextResponse } from 'next/server';
import { AUDIT_PATH, readAuditLines, runStages, toStage } from '@/core/audit/localAudit';

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

export async function GET(req: Request) {
  const params = new URL(req.url).searchParams;
  const id = params.get('id');
  const { lines, error } = await readAuditLines();
  const records = lines.map((l) => l.entry);

  if (id) {
    const stages = runStages(records, id);
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
