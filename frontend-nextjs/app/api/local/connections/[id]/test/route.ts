import { NextResponse } from 'next/server';
import { getConnection } from '@/core/secrets/connectorStore';

const NOVOMCP_ENGINE_URL = process.env.NOVOMCP_ENGINE_URL || 'http://localhost:8018';
const ENGINE_KEY = process.env.NOVOMCP_API_KEY || 'local-dev';

// Test a stored connection: read it server-side (with credentials) and ask the
// engine to instantiate the adapter and run test_connection. Credentials never
// reach the client — they go engine-side only.
export async function POST(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const conn = await getConnection(id);
  if (!conn) return NextResponse.json({ ok: false, error: 'not_found' }, { status: 404 });

  try {
    const res = await fetch(`${NOVOMCP_ENGINE_URL}/local/connectors/execute`, {
      method: 'POST',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${ENGINE_KEY}` },
      body: JSON.stringify({
        connector_type: conn.type,
        config: conn.config,
        credentials: conn.credentials,
        action: 'test',
      }),
      signal: AbortSignal.timeout(60000),
    });
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: { 'Content-Type': res.headers.get('Content-Type') || 'application/json' },
    });
  } catch (e: unknown) {
    const timedOut = (e as { name?: string })?.name === 'TimeoutError' || (e as { name?: string })?.name === 'AbortError';
    return NextResponse.json(
      {
        ok: false,
        error: timedOut ? 'engine_timeout' : 'service_unavailable',
        detail: timedOut
          ? 'The engine did not respond in time.'
          : `Could not reach the engine at ${NOVOMCP_ENGINE_URL}.`,
      },
      { status: timedOut ? 504 : 503 }
    );
  }
}
