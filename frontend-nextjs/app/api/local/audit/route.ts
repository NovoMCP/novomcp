import { NextResponse } from 'next/server';
import { promises as fs } from 'fs';
import path from 'path';
import os from 'os';

// Reads the last N lines of ~/.novo/audit.jsonl (the FileAuditSink default
// location the engine writes to). No managed backend needed. Users see their
// own tool-call history without wiring managed backend.
//
// Override the path with NOVO_AUDIT_PATH if the engine was started with a
// different sink location.

const AUDIT_PATH = process.env.NOVO_AUDIT_PATH || path.join(os.homedir(), '.novo', 'audit.jsonl');
const DEFAULT_LIMIT = 20;

export async function GET(req: Request) {
  const url = new URL(req.url);
  const limitParam = parseInt(url.searchParams.get('limit') || '', 10);
  const limit = Number.isFinite(limitParam) && limitParam > 0 ? Math.min(limitParam, 200) : DEFAULT_LIMIT;

  const entries: any[] = [];
  let error: string | null = null;

  try {
    const contents = await fs.readFile(AUDIT_PATH, 'utf-8');
    const lines = contents.split('\n').filter((l) => l.trim().length > 0);
    // Tail the last N lines, then parse each; drop unparseable lines silently.
    const tail = lines.slice(-limit).reverse();
    for (const line of tail) {
      try {
        entries.push(JSON.parse(line));
      } catch {
        // skip malformed line
      }
    }
  } catch (e: any) {
    if (e.code === 'ENOENT') {
      error = 'no_audit_yet';
    } else {
      error = e.message || 'read_failed';
    }
  }

  return NextResponse.json({
    audit_path: AUDIT_PATH,
    error,
    count: entries.length,
    entries,
  });
}
