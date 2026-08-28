import { NextRequest, NextResponse } from 'next/server';
import { readCredentials, updateCredentials, credentialsDisplayPath } from '@/core/secrets/localStore';

// Compute-service config for local self-host. Every NovoMCP compute service is
// wired by a `<X>_URL` env var (and an optional `<X>_API_KEY` for auth-gated
// deployments) that the engine reads at startup — see docs/deploying-services.
// This route writes those vars to the SecretStore so services get the same
// in-app "plug it in" flow as the other connectors, instead of hand-edited env.
//
// The URL var is allowlisted: the route only ever writes one of the known
// service vars, so it can't be used to set arbitrary environment values. The
// key var is derived from the URL var (FOO_URL -> FOO_API_KEY), matching the
// engine's naming, and is write-only (GET never returns it).

const ALLOWED_URL_ENVS = new Set([
  'ADDIE_MODELS_URL',
  'AUTODOCK_GPU_URL',
  'GROMACS_MD_URL',
  'OPENFOLD3_URL',
  'NOVOMCP_QM_URL',
  'NOVOMCP_NNP_URL',
  'NOVOMCP_PROPERTIES_URL',
  'NOVOMCP_NEB_URL',
  'CHEM_PROPS_URL',
  'NOVOMCP_MOLECULE_INDEX_URL',
  'LEAD_OPTIMIZATION_URL',
  'MOLMIM_OPTIMIZER_URL',
]);

// FOO_URL -> FOO_API_KEY (the engine's per-service key convention).
function keyEnvFor(urlEnv: string): string {
  return urlEnv.replace(/_URL$/, '_API_KEY');
}

export async function GET(req: NextRequest) {
  const c = await readCredentials();
  const urlEnv = req.nextUrl.searchParams.get('url_env') || '';

  // No url_env → the configured-state of every service in one call (what the
  // marketplace grid needs to show status without a request per tile).
  if (!urlEnv) {
    const configured = [...ALLOWED_URL_ENVS].filter((e) => !!c[e]);
    return NextResponse.json({ configured, path: credentialsDisplayPath() });
  }

  if (!ALLOWED_URL_ENVS.has(urlEnv)) {
    return NextResponse.json({ error: 'unknown_service' }, { status: 400 });
  }
  return NextResponse.json({
    url: c[urlEnv] || null,
    keySet: !!c[keyEnvFor(urlEnv)],
    path: credentialsDisplayPath(),
  });
}

export async function PUT(req: NextRequest) {
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'invalid_json' }, { status: 400 });
  }

  const urlEnv = typeof body.urlEnv === 'string' ? body.urlEnv : '';
  if (!ALLOWED_URL_ENVS.has(urlEnv)) {
    return NextResponse.json({ error: 'unknown_service' }, { status: 400 });
  }

  const url = typeof body.url === 'string' ? body.url.trim() : '';
  if (!/^https?:\/\/.+/i.test(url)) {
    return NextResponse.json(
      { error: 'invalid_url', detail: 'Enter a full http(s) URL for the service.' },
      { status: 400 }
    );
  }

  const updates: Record<string, string | null> = { [urlEnv]: url };
  // Key is optional (most self-host services are open) and write-only — only
  // update it when supplied, so changing the URL doesn't clear an existing key.
  const apiKey = typeof body.apiKey === 'string' ? body.apiKey.trim() : '';
  if (apiKey) updates[keyEnvFor(urlEnv)] = apiKey;

  await updateCredentials(updates);
  return NextResponse.json({ ok: true, restartRequired: true });
}

export async function DELETE(req: NextRequest) {
  let urlEnv = req.nextUrl.searchParams.get('url_env') || '';
  if (!urlEnv) {
    try {
      const body = await req.json();
      if (typeof body?.urlEnv === 'string') urlEnv = body.urlEnv;
    } catch {
      /* no body — fall through to validation */
    }
  }
  if (!ALLOWED_URL_ENVS.has(urlEnv)) {
    return NextResponse.json({ error: 'unknown_service' }, { status: 400 });
  }
  await updateCredentials({ [urlEnv]: null, [keyEnvFor(urlEnv)]: null });
  return NextResponse.json({ ok: true, restartRequired: true });
}
