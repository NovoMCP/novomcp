import { NextRequest, NextResponse } from 'next/server';

// Defaults are empty in OSS mode — hosted spine services (dashboard-aggregator,
// novomcp-auth) aren't wired in OSS. When empty, we fast-fail the proxy call
// with a structured 503 instead of hanging on a bad URL.
const AGGREGATOR_URL = process.env.DASHBOARD_AGGREGATOR_URL || '';
const AUTH_URL = process.env.NOVOMCP_AUTH_URL || '';
const NOVOMCP_ENGINE_URL = process.env.NOVOMCP_ENGINE_URL || 'http://localhost:8018';
const ADMIN_KEY = process.env.MCP_ADMIN_KEY || '';

// Routes that require admin role
const ADMIN_ROUTES = ['/admin/orgs', '/admin/connections', '/admin/keys', '/admin/compute-keys', '/policies', '/org/llm-config'];

// Public routes that skip JWT/role checks (password flows)
const PUBLIC_ROUTES = ['/password/forgot', '/password/reset'];

function isAdminRoute(path: string): boolean {
  return ADMIN_ROUTES.some((r) => path.startsWith(r));
}

function isPublicRoute(path: string): boolean {
  return PUBLIC_ROUTES.some((r) => path === r);
}

function getUpstreamUrl(slug: string[]): { url: string; service: 'aggregator' | 'auth' | 'quanta' } {
  const path = '/' + slug.join('/');
  // Policy endpoints route to novomcp-auth
  if (path.startsWith('/policies')) {
    return { url: `${AUTH_URL}${path}`, service: 'auth' };
  }
  // Async jobs endpoints route to dashboard-aggregator /api/v1/jobs/
  if (path.startsWith('/jobs')) {
    return { url: `${AGGREGATOR_URL}/api/v1${path}`, service: 'aggregator' };
  }
  // Pipeline audit endpoints route to dashboard-aggregator /api/v1/pipelines/
  if (path.startsWith('/pipelines')) {
    return { url: `${AGGREGATOR_URL}/api/v1${path}`, service: 'aggregator' };
  }
  // Funnel audit endpoints route to dashboard-aggregator /api/v1/funnel/
  if (path.startsWith('/funnel')) {
    return { url: `${AGGREGATOR_URL}/api/v1${path}`, service: 'aggregator' };
  }
  // File intelligence endpoints route to dashboard-aggregator (same pattern
  // as /jobs, /pipelines, /funnel). Dashboard-aggregator queries Cosmos DB
  // directly via its /api/v1/files endpoint — no novomcp proxy needed.
  if (path.startsWith('/files')) {
    return { url: `${AGGREGATOR_URL}/api/v1${path}`, service: 'aggregator' };
  }
  // Org BYO-LLM config (Studio agent) routes to novomcp's /v1/org/llm-config.
  // The vault (Aurora + Secrets Manager) lives in novomcp, not the aggregator.
  if (path.startsWith('/org/llm-config')) {
    return { url: `${NOVOMCP_ENGINE_URL}/v1${path}`, service: 'quanta' };
  }
  // Everything else routes to dashboard-aggregator's /mcp prefix
  return { url: `${AGGREGATOR_URL}/mcp${path}`, service: 'aggregator' };
}

async function handler(req: NextRequest, { params }: { params: Promise<{ slug: string[] }> }) {
  const { slug } = await params;
  const path = '/' + slug.join('/');
  const { url, service } = getUpstreamUrl(slug);

  // OSS fast-fail: if the target service URL isn't configured, return 503
  // immediately with a structured error. Prevents pages from hanging on
  // unwired hosted-spine calls (dashboard-aggregator, novomcp-auth).
  if (!url || url.startsWith('/mcp/') || url.startsWith('/api/v1/') || url.startsWith('/proxy/')) {
    return NextResponse.json(
      {
        error: 'service_unavailable',
        detail: `The ${service} service is not configured on this install. This feature requires a hosted spine (v0.2 will surface the equivalent through the local engine).`,
        service,
        path,
      },
      { status: 503 }
    );
  }

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    // Tag every dashboard-originated upstream call so audit rows render
    // a "Dashboard" chip on the Pipeline Audit page instead of an
    // "Unknown surface" fallback. Harmless on read-only aggregator
    // routes (audit lookups don't write rows); meaningful on novomcp
    // tool routes that auto-log into funnel_audit_log.system_metadata.
    // Persisted dual-write happens server-side in
    // novomcp/mcp/tools.py::_execute_save_funnel_stage.
    'X-Novo-Surface': 'dashboard-v1',
  };

  // Public routes: skip auth, don't forward credentials
  if (isPublicRoute(path)) {
    // No Authorization or X-Admin-Key headers for public routes
  } else {
    // Extract JWT from Authorization header
    const authHeader = req.headers.get('Authorization');
    if (!authHeader?.startsWith('Bearer ')) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    // For admin routes, validate role from the JWT claims. Exception: reading the
    // org LLM provider status (GET /org/llm-config) is allowed for any org member
    // — Studio shows it read-only; it never returns the key. Setting/clearing it
    // (PUT/DELETE) stays admin-only.
    const isLlmConfigRead = path.startsWith('/org/llm-config') && req.method === 'GET';
    if (isAdminRoute(path) && !isLlmConfigRead) {
      const userRoles = req.headers.get('X-User-Roles') || '';
      if (!userRoles.includes('admin')) {
        return NextResponse.json({ error: 'Forbidden: admin role required' }, { status: 403 });
      }
    }

    headers['X-Admin-Key'] = ADMIN_KEY;
    // novomcp tool endpoints use API key auth (Bearer), not JWT.
    // Use the admin key for internal service-to-service calls.
    if (service === 'quanta') {
      headers['Authorization'] = `Bearer ${ADMIN_KEY}`;
    } else {
      headers['Authorization'] = authHeader;
    }
  }

  // Forward org/user context headers
  const orgId = req.headers.get('X-Org-ID');
  const userId = req.headers.get('X-User-ID');
  if (orgId) headers['X-Org-ID'] = orgId;
  if (userId) headers['X-User-ID'] = userId;

  // Build upstream URL with query params
  const upstreamUrl = new URL(url);
  req.nextUrl.searchParams.forEach((value, key) => {
    upstreamUrl.searchParams.set(key, value);
  });

  const fetchOptions: RequestInit = {
    method: req.method,
    headers,
  };

  if (req.method !== 'GET' && req.method !== 'HEAD') {
    try {
      fetchOptions.body = await req.text();
    } catch {
      // No body
    }
  }

  try {
    const response = await fetch(upstreamUrl.toString(), fetchOptions);
    const data = await response.text();

    return new NextResponse(data, {
      status: response.status,
      headers: { 'Content-Type': response.headers.get('Content-Type') || 'application/json' },
    });
  } catch (error) {
    console.error('BFF proxy error:', error);
    return NextResponse.json({ error: 'Upstream service unavailable' }, { status: 502 });
  }
}

export const GET = handler;
export const POST = handler;
export const PUT = handler;
export const DELETE = handler;
export const PATCH = handler;
