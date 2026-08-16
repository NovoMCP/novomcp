import { NextRequest, NextResponse } from 'next/server';

const NOVOMCP_ENGINE_URL = process.env.NOVOMCP_ENGINE_URL || 'http://localhost:8018';
// Local engine bearer. In OSS local mode the engine's LocalAuthGate accepts any
// token; 'local-dev' matches what the other /api/local/* routes send. A cloud
// deployment pointing NOVOMCP_ENGINE_URL at a hosted engine can set a real key.
const ENGINE_KEY = process.env.NOVOMCP_API_KEY || 'local-dev';
// Generous ceiling: synchronous compute tools (e.g. docking) can take a while;
// async tools return a job id fast. Past this we surface a 504 rather than hang.
const TIMEOUT_MS = 120_000;

// Tool names are lowercase snake_case identifiers. Constrain to prevent path
// traversal / injection into the engine URL.
const TOOL_NAME_RE = /^[a-z][a-z0-9_]*$/;

// Local/self-host BFF for single tool calls. The client posts { arguments } and
// this forwards to the engine's POST /mcp/tools/{name}, keeping the engine URL
// and key server-side. Fails soft: engine unreachable -> structured 503 (same
// shape the /api/mcp stub returns) so client hooks render an empty/teaching
// state instead of a hung spinner. Engine-returned errors pass through with
// their status so real tool errors reach the user unmodified.
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ name: string }> }
) {
  const { name } = await params;
  if (!TOOL_NAME_RE.test(name)) {
    return NextResponse.json(
      { error: 'invalid_tool_name', detail: `Not a valid tool name: ${name}` },
      { status: 400 }
    );
  }

  // Body is optional; default to empty arguments. Accept either the engine's
  // { arguments: {...} } envelope or a bare arguments object for convenience.
  let args: unknown = {};
  const raw = await req.text();
  if (raw.trim().length > 0) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return NextResponse.json(
        { error: 'invalid_json', detail: 'Request body must be JSON.' },
        { status: 400 }
      );
    }
    args =
      parsed && typeof parsed === 'object' && 'arguments' in (parsed as object)
        ? (parsed as { arguments: unknown }).arguments
        : parsed;
  }
  if (args === null || typeof args !== 'object') {
    return NextResponse.json(
      { error: 'invalid_arguments', detail: 'arguments must be a JSON object.' },
      { status: 400 }
    );
  }

  try {
    const res = await fetch(`${NOVOMCP_ENGINE_URL}/mcp/tools/${name}`, {
      method: 'POST',
      cache: 'no-store',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${ENGINE_KEY}`,
      },
      body: JSON.stringify({ arguments: args }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });

    // Pass the engine response through verbatim (status + body) so real tool
    // errors and payloads reach the client unchanged.
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: {
        'Content-Type': res.headers.get('Content-Type') || 'application/json',
      },
    });
  } catch (e: unknown) {
    const errName = (e as { name?: string })?.name;
    const timedOut = errName === 'TimeoutError' || errName === 'AbortError';
    return NextResponse.json(
      {
        error: timedOut ? 'engine_timeout' : 'service_unavailable',
        detail: timedOut
          ? `The engine did not respond within ${TIMEOUT_MS / 1000}s.`
          : `Could not reach the engine at ${NOVOMCP_ENGINE_URL}. Is it running? (python main_https.py from orchestrator/)`,
      },
      { status: timedOut ? 504 : 503 }
    );
  }
}
