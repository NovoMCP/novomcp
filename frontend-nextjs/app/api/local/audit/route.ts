import { NextResponse } from 'next/server';
import { promises as fs } from 'fs';
import path from 'path';
import os from 'os';

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

const AUDIT_PATH = process.env.NOVO_AUDIT_PATH || path.join(os.homedir(), '.novo', 'audit.jsonl');
const DEFAULT_LIMIT = 20;
const MAX_LIMIT = 500;

interface Entry {
  ts?: string;
  event?: string;
  user?: string | null;
  org?: string | null;
  payload?: {
    tool?: string;
    funnel_id?: string | null;
    success?: boolean;
    execution_time_ms?: number;
    surface?: string;
    error?: string | null;
    arguments?: Record<string, unknown>;
    outcome?: Record<string, unknown> | null;
  };
}

interface Line {
  raw: string;
  entry: Entry;
}

function matches(line: Line, f: { tool: string; status: string; run: string; q: string }): boolean {
  const p = line.entry.payload ?? {};
  if (f.tool && p.tool !== f.tool) return false;
  if (f.status === 'ok' && p.success === false) return false;
  if (f.status === 'error' && p.success !== false) return false;
  if (f.run && p.funnel_id !== f.run) return false;
  if (f.q && !line.raw.toLowerCase().includes(f.q)) return false;
  return true;
}

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

  let lines: Line[] = [];
  let error: string | null = null;
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
  } catch (e: unknown) {
    const err = e as { code?: string; message?: string };
    error = err.code === 'ENOENT' ? 'no_audit_yet' : err.message || 'read_failed';
  }

  const tools = Array.from(new Set(lines.map((l) => l.entry.payload?.tool).filter(Boolean) as string[])).sort();
  lines = lines.filter((l) => matches(l, filters)).reverse(); // newest first

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
