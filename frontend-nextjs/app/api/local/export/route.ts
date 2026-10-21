import { NextResponse } from 'next/server';
import { matchesCall, readAuditLines, runStages } from '@/core/audit/localAudit';
import { getConnection } from '@/core/secrets/connectorStore';

const NOVOMCP_ENGINE_URL = process.env.NOVOMCP_ENGINE_URL || 'http://localhost:8018';
const ENGINE_KEY = process.env.NOVOMCP_API_KEY || 'local-dev';

const WAREHOUSES = new Set(['snowflake', 'databricks']);

// Send Activity to a saved warehouse connection (Snowflake, Databricks).
//
// POST { connectionId, kind: 'calls' | 'run', filters?, runId?, target?, mode? }
//   calls  the calls matching the Activity filters, one row per call
//   run    one run's timeline (calls and saved stages), one row per step
//
// Rows are built here from the audit file; the engine writes them (creating
// the table if needed), adds _exported_by / _export_id / _exported_at to each
// row, and records the export in the audit file. Free on a self-hosted
// install; the warehouse bills its own usage.

function callRow(e: { ts?: string; event?: string; payload?: Record<string, unknown> }) {
  const p = (e.payload ?? {}) as Record<string, unknown>;
  const args = (p.arguments ?? {}) as Record<string, unknown>;
  return {
    time_utc: e.ts ?? null,
    tool: (p.tool as string) ?? e.event ?? null,
    run_id: (p.funnel_id as string) ?? null,
    status: p.success === false ? 'error' : 'ok',
    duration_ms: typeof p.execution_time_ms === 'number' ? p.execution_time_ms : null,
    smiles: typeof args.smiles === 'string' ? args.smiles : null,
    arguments: p.arguments ?? null,
    outcome: p.outcome ?? null,
    error: (p.error as string) ?? null,
  };
}

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  const connectionId = String(body.connectionId ?? '');
  const kind = body.kind === 'run' ? 'run' : 'calls';

  const conn = connectionId ? await getConnection(connectionId) : undefined;
  if (!conn || !WAREHOUSES.has(conn.type)) {
    return NextResponse.json({ success: false, error: 'Pick a saved Snowflake or Databricks connection.' }, { status: 400 });
  }

  const { lines } = await readAuditLines();
  let rows: Record<string, unknown>[];
  let target: string;
  let runId: string | null = null;
  if (kind === 'run') {
    runId = String(body.runId ?? '');
    rows = runStages(lines.map((l) => l.entry), runId).map((st) => ({ ...st, run_id: runId }));
    target = 'novomcp_run_audit';
  } else {
    const f = body.filters ?? {};
    const filters = {
      tool: String(f.tool ?? ''),
      status: String(f.status ?? ''),
      run: String(f.run ?? ''),
      q: String(f.q ?? '').trim().toLowerCase(),
    };
    rows = lines.filter((l) => l.entry.event === 'tool_call' && matchesCall(l, filters)).map((l) => callRow(l.entry));
    target = 'novomcp_activity';
  }
  if (rows.length === 0) {
    return NextResponse.json({ success: false, error: 'Nothing to send: no matching activity.' }, { status: 400 });
  }
  if (typeof body.target === 'string' && body.target.trim()) target = body.target.trim();

  try {
    const res = await fetch(`${NOVOMCP_ENGINE_URL}/local/connectors/export`, {
      method: 'POST',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${ENGINE_KEY}` },
      body: JSON.stringify({
        connection_id: conn.id,
        rows,
        source_tool: kind === 'run' ? 'run_audit' : 'activity',
        target,
        write_mode: body.mode === 'replace' ? 'replace' : 'append',
        funnel_id: runId,
      }),
      signal: AbortSignal.timeout(320000),
    });
    const out = await res.json().catch(() => ({ success: false, error: `Engine answered ${res.status}` }));
    return NextResponse.json({ ...out, rows_sent: rows.length }, { status: res.ok ? 200 : res.status });
  } catch (e: unknown) {
    const timedOut = (e as { name?: string })?.name === 'TimeoutError';
    return NextResponse.json(
      {
        success: false,
        error: timedOut ? 'The engine did not finish in time.' : `Could not reach the engine at ${NOVOMCP_ENGINE_URL}.`,
      },
      { status: timedOut ? 504 : 503 }
    );
  }
}
