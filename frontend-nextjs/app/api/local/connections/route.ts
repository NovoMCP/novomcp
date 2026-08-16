import { NextRequest, NextResponse } from 'next/server';
import { randomUUID } from 'crypto';
import { readConnections, addConnection, type Connection } from '@/core/secrets/connectorStore';

// Local connection registry. GET lists connections with credentials MASKED
// (only which credential keys are set, never their values). POST registers a
// new connection to the local store.

const SUPPORTED = ['snowflake', 'databricks'];

export async function GET() {
  const list = await readConnections();
  return NextResponse.json({
    connections: list.map((c) => ({
      id: c.id,
      type: c.type,
      displayName: c.displayName,
      config: c.config,
      credentialKeys: Object.keys(c.credentials || {}),
      createdAt: c.createdAt,
    })),
  });
}

export async function POST(req: NextRequest) {
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'invalid_json' }, { status: 400 });
  }

  const type = String(body.type ?? '').toLowerCase();
  if (!SUPPORTED.includes(type)) {
    return NextResponse.json(
      { error: 'unsupported_type', detail: `type must be one of: ${SUPPORTED.join(', ')}` },
      { status: 400 }
    );
  }

  const asRecord = (v: unknown): Record<string, string> => {
    const out: Record<string, string> = {};
    if (v && typeof v === 'object') {
      for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
        if (typeof val === 'string' && val.trim()) out[k] = val.trim();
      }
    }
    return out;
  };

  const displayName =
    typeof body.displayName === 'string' && body.displayName.trim() ? body.displayName.trim() : type;

  const conn: Connection = {
    id: randomUUID(),
    type,
    displayName,
    config: asRecord(body.config),
    credentials: asRecord(body.credentials),
    createdAt: new Date().toISOString(),
  };
  await addConnection(conn);
  return NextResponse.json({ ok: true, id: conn.id });
}
