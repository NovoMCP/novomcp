import { NextResponse } from 'next/server';
import { removeConnection, updateConnection } from '@/core/secrets/connectorStore';

// PATCH  { displayName?, config?, credentials? }  rename or edit; blank credentials keep their value
// DELETE                                          remove the connection

function strings(v: unknown): Record<string, string> | undefined {
  if (!v || typeof v !== 'object') return undefined;
  const out: Record<string, string> = {};
  for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
    if (typeof val === 'string' && val.trim()) out[k] = val.trim();
  }
  return out;
}

export async function PATCH(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await req.json().catch(() => null);
  if (!body || typeof body !== 'object') return NextResponse.json({ error: 'invalid_json' }, { status: 400 });
  const conn = await updateConnection(id, {
    displayName: typeof body.displayName === 'string' ? body.displayName : undefined,
    config: strings(body.config),
    credentials: strings(body.credentials),
  });
  if (!conn) return NextResponse.json({ ok: false, error: 'not_found' }, { status: 404 });
  return NextResponse.json({ ok: true, id: conn.id, displayName: conn.displayName });
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const ok = await removeConnection(id);
  return NextResponse.json({ ok }, { status: ok ? 200 : 404 });
}
