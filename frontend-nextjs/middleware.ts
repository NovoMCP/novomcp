import { NextRequest, NextResponse } from 'next/server';

// Same-origin guard for the local self-host GUI's config API.
//
// The routes under /api/local/* let the local dashboard read and WRITE engine
// configuration (LLM provider + base URL, OTLP trace endpoint, compliance
// settings, connections). Those writes persist to the on-disk SecretStore the
// engine loads at boot. Because the GUI listens on localhost with no auth, a
// browser-based CSRF is the real threat: any web page the operator visits can
// fire a cross-origin `fetch("http://localhost:3000/api/local/...", {method:"PUT"})`
// and silently repoint the LLM base URL, plant a rogue OTLP exporter, or wipe
// config. The browser attaches the operator's implicit "credentials" (a plain
// localhost request needs none), so the write succeeds unless the server checks
// where the request came from.
//
// Defense: for state-changing methods on /api/local/*, require that the request
// originates from the GUI itself (same origin). GET is left open — those handlers
// already redact secrets (keys are write-only) so a cross-origin read leaks
// nothing actionable, and CORS blocks the attacker from reading the response body
// anyway.
//
// Two independent signals, EITHER of which proves same-origin:
//
//   1. `Sec-Fetch-Site: same-origin` — sent by all modern browsers on the
//      request itself; the browser computes it and a page cannot forge it. A
//      cross-site page's fetch carries `cross-site` (or `same-site`/`none`),
//      never `same-origin`.
//   2. `Origin` header host:port === the request's own Host. A cross-origin
//      page always sends `Origin: https://evil.com`, which will not match.
//
// If NEITHER header is present, the request is not a browser fetch at all
// (curl, a server-to-server call, a health probe). A browser CSRF ALWAYS emits
// an `Origin` on unsafe cross-origin requests — "no Origin and no
// Sec-Fetch-Site" therefore cannot be the CSRF we defend against, so we allow
// it. This keeps local tooling/scripts working without weakening the guard: the
// attack vector (a foreign web page) is exactly the case that DOES set these
// headers, and that case is rejected.

const GUARDED_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

// Cloud instance-metadata endpoint. Never a legitimate LLM/OTLP/compliance
// target for a self-host; deny it in stored URL-bearing bodies as a cheap
// SSRF-hardening measure. Loopback / private IPs are intentionally NOT blocked —
// pointing the engine at a local Ollama or a local OTLP collector is a
// first-class self-host workflow.
const METADATA_IP = '169.254.169.254';

function hostOf(value: string | null): string | null {
  if (!value) return null;
  try {
    // URL() needs a scheme; Origin always has one. Fall back for bare host:port.
    return new URL(value).host || null;
  } catch {
    return value.includes('://') ? null : value;
  }
}

function isSameOrigin(req: NextRequest): boolean {
  const secFetchSite = req.headers.get('sec-fetch-site');
  if (secFetchSite) {
    // Browser-supplied and unforgeable. Only "same-origin" clears the guard;
    // "same-site"/"cross-site"/"none" are all rejected.
    return secFetchSite === 'same-origin';
  }

  const origin = req.headers.get('origin');
  if (origin) {
    const originHost = hostOf(origin);
    const requestHost = req.headers.get('host');
    return !!originHost && !!requestHost && originHost === requestHost;
  }

  // No Sec-Fetch-Site AND no Origin → not a browser cross-origin fetch. Allow
  // non-browser clients (curl / scripts / probes) so local tooling keeps working.
  return true;
}

async function bodyTargetsMetadataIp(req: NextRequest): Promise<boolean> {
  const contentType = req.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) return false;
  try {
    // Read a clone so the route handler still sees the untouched body stream.
    const text = await req.clone().text();
    return text.includes(METADATA_IP);
  } catch {
    return false;
  }
}

export async function middleware(req: NextRequest) {
  if (!GUARDED_METHODS.has(req.method)) {
    return NextResponse.next();
  }

  if (!isSameOrigin(req)) {
    return NextResponse.json({ error: 'cross_origin_forbidden' }, { status: 403 });
  }

  if (await bodyTargetsMetadataIp(req)) {
    return NextResponse.json({ error: 'metadata_endpoint_forbidden' }, { status: 400 });
  }

  return NextResponse.next();
}

// Scope the middleware to exactly the local config API. The matcher only sees
// /api/local/* requests, and the method filter above narrows to unsafe verbs, so
// GET reads and all other routes are untouched.
export const config = {
  matcher: ['/api/local/:path*'],
};
