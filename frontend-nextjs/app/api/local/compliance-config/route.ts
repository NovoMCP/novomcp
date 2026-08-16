import { NextRequest, NextResponse } from 'next/server';
import { readCredentials, updateCredentials, credentialsDisplayPath } from '@/core/secrets/localStore';

// Compliance-service consumer config. Connects the engine to an external
// compliance / regulatory-screening service (any compatible endpoint) via the
// capability-named env vars the engine reads at startup. Persists to the local
// SecretStore; the key is write-only (GET never returns it).

const URL_KEY = 'NOVOMCP_COMPLIANCE_URL';
const API_KEY = 'NOVOMCP_COMPLIANCE_API_KEY';

export async function GET() {
  const c = await readCredentials();
  return NextResponse.json({
    url: c[URL_KEY] || null,
    keySet: !!c[API_KEY],
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

  const url = typeof body.url === 'string' ? body.url.trim() : '';
  if (!/^https?:\/\/.+/i.test(url)) {
    return NextResponse.json(
      { error: 'invalid_url', detail: 'Enter a full http(s) URL for the compliance service.' },
      { status: 400 }
    );
  }

  const updates: Record<string, string | null> = { [URL_KEY]: url };
  // The key is optional (some services are open) and write-only — only update
  // it when supplied, so changing the URL doesn't require re-entering the key.
  const apiKey = typeof body.apiKey === 'string' ? body.apiKey.trim() : '';
  if (apiKey) updates[API_KEY] = apiKey;

  await updateCredentials(updates);
  return NextResponse.json({ ok: true, restartRequired: true });
}

export async function DELETE() {
  await updateCredentials({ [URL_KEY]: null, [API_KEY]: null });
  return NextResponse.json({ ok: true, restartRequired: true });
}
