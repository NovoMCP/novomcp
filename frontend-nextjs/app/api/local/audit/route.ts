import { NextResponse } from 'next/server';
import { AUDIT_PATH, matchesCall, readAuditLines, type Line } from '@/core/audit/localAudit';

// Reads ~/.novo/audit.jsonl (the FileAuditSink default location the engine
// writes to). No managed backend needed: users see their own tool-call history.
//
// Override the path with NOVO_AUDIT_PATH if the engine was started with a
// different sink location.
//
// GET /api/local/audit                      newest first, paged (limit, offset)
//   filters: tool, status (ok | error), run (funnel_id), q (text search)
//   format=csv    every matching entry as a spreadsheet download
//   format=jsonl  every matching line, unchanged, as a download
// The dashboard calls it with just ?limit=6.

const DEFAULT_LIMIT = 20;
const MAX_LIMIT = 500;

// One spreadsheet row per call. SMILES gets its own column because it's what
// people sort and search by; arguments and outcome stay JSON so nothing is lost.
const CSV_COLUMNS = ['time_utc', 'tool', 'run', 'status', 'duration_ms', 'smiles', 'outcome', 'arguments', 'error'];

function csvCell(v: unknown): string {
  if (v === undefined || v === null) return '';
  const s = typeof v === 'string' ? v : JSON.stringify(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function toCsv(lines: Line[]): string {
  const rows = lines.map(({ entry }) => {
    const p = entry.payload ?? {};
    const args = p.arguments ?? {};
    return [
      entry.ts,
      p.tool ?? entry.event,
      p.funnel_id,
      p.success === false ? 'error' : 'ok',
      p.execution_time_ms,
      typeof args.smiles === 'string' ? args.smiles : '',
      p.outcome,
      p.arguments,
      p.error,
    ]
      .map(csvCell)
      .join(',');
  });
  return [CSV_COLUMNS.join(','), ...rows].join('\n') + '\n';
}

export async function GET(req: Request) {
  const url = new URL(req.url);
  const params = url.searchParams;
  const limitParam = parseInt(params.get('limit') || '', 10);
  const limit = Number.isFinite(limitParam) && limitParam > 0 ? Math.min(limitParam, MAX_LIMIT) : DEFAULT_LIMIT;
  const offsetParam = parseInt(params.get('offset') || '', 10);
  const offset = Number.isFinite(offsetParam) && offsetParam > 0 ? offsetParam : 0;
  const filters = {
    tool: params.get('tool') || '',
    status: params.get('status') || '',
    run: params.get('run') || '',
    q: (params.get('q') || '').trim().toLowerCase(),
  };
  const format = params.get('format');

  const read = await readAuditLines();
  let lines: Line[] = read.lines;
  const error = read.error;

  const tools = Array.from(new Set(lines.map((l) => l.entry.payload?.tool).filter(Boolean) as string[])).sort();
  lines = lines.filter((l) => matchesCall(l, filters)).reverse(); // newest first

  if (format === 'csv' || format === 'jsonl') {
    const stamp = new Date().toISOString().slice(0, 10);
    const body =
      format === 'csv'
        ? toCsv(lines)
        : lines
            .slice()
            .reverse() // the file's own order, oldest first
            .map((l) => l.raw)
            .join('\n') + (lines.length ? '\n' : '');
    return new NextResponse(body, {
      headers: {
        'Content-Type': format === 'csv' ? 'text/csv; charset=utf-8' : 'application/x-ndjson',
        'Content-Disposition': `attachment; filename="novomcp-activity-${stamp}.${format}"`,
      },
    });
  }

  const page = lines.slice(offset, offset + limit).map((l) => l.entry);
  return NextResponse.json({
    audit_path: AUDIT_PATH,
    error,
    total: lines.length,
    count: page.length,
    offset,
    tools,
    entries: page,
  });
}
