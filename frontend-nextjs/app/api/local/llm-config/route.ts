import { NextRequest, NextResponse } from 'next/server';
import { readCredentials, updateCredentials, credentialsDisplayPath } from '@/core/secrets/localStore';

// LLM provider config for local self-host. Persists to the SecretStore (dotenv
// file the engine loads at startup — see docs/configuring-llm.md for the env
// contract). The engine resolves the provider from these vars on boot, so a
// change needs an engine restart. Keys are write-only: GET never returns a key.

const PROVIDERS = ['openai', 'anthropic', 'ollama', 'azure'] as const;
type Provider = (typeof PROVIDERS)[number];

// Every LLM-related var this route owns — cleared on DELETE.
const ALL_LLM_KEYS = [
  'NOVO_LLM',
  'OPENAI_API_KEY', 'OPENAI_MODEL', 'OPENAI_BASE_URL',
  'ANTHROPIC_API_KEY', 'ANTHROPIC_MODEL',
  'OLLAMA_URL', 'OLLAMA_MODEL',
  'AZURE_OPENAI_API_KEY', 'AZURE_OPENAI_ENDPOINT', 'AZURE_OPENAI_DEPLOYMENT',
];

function str(v: unknown): string | undefined {
  return typeof v === 'string' && v.trim() ? v.trim() : undefined;
}

export async function GET() {
  const c = await readCredentials();
  return NextResponse.json({
    active: c.NOVO_LLM || null,
    path: credentialsDisplayPath(),
    providers: {
      openai: { keySet: !!c.OPENAI_API_KEY, model: c.OPENAI_MODEL || null, baseUrl: c.OPENAI_BASE_URL || null },
      anthropic: { keySet: !!c.ANTHROPIC_API_KEY, model: c.ANTHROPIC_MODEL || null },
      ollama: { url: c.OLLAMA_URL || null, model: c.OLLAMA_MODEL || null },
      azure: { keySet: !!c.AZURE_OPENAI_API_KEY, endpoint: c.AZURE_OPENAI_ENDPOINT || null, deployment: c.AZURE_OPENAI_DEPLOYMENT || null },
    },
  });
}

export async function PUT(req: NextRequest) {
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'invalid_json' }, { status: 400 });
  }

  const provider = String(body.provider ?? '').toLowerCase() as Provider;
  if (!PROVIDERS.includes(provider)) {
    return NextResponse.json(
      { error: 'invalid_provider', detail: `provider must be one of: ${PROVIDERS.join(', ')}` },
      { status: 400 }
    );
  }

  const current = await readCredentials();
  const updates: Record<string, string | null> = { NOVO_LLM: provider };
  // The API key is only updated when supplied — the UI doesn't re-send it just
  // to change a model — so a value of `null` here means "leave as-is", not clear.
  const keyEnv: Record<Provider, string | null> = {
    openai: 'OPENAI_API_KEY', anthropic: 'ANTHROPIC_API_KEY', azure: 'AZURE_OPENAI_API_KEY', ollama: null,
  };

  if (provider === 'openai') {
    if (str(body.apiKey)) updates.OPENAI_API_KEY = str(body.apiKey)!;
    updates.OPENAI_MODEL = str(body.model) ?? null;
    updates.OPENAI_BASE_URL = str(body.baseUrl) ?? null;
  } else if (provider === 'anthropic') {
    if (str(body.apiKey)) updates.ANTHROPIC_API_KEY = str(body.apiKey)!;
    updates.ANTHROPIC_MODEL = str(body.model) ?? null;
  } else if (provider === 'ollama') {
    updates.OLLAMA_URL = str(body.url) ?? null;
    updates.OLLAMA_MODEL = str(body.model) ?? null;
  } else if (provider === 'azure') {
    if (str(body.apiKey)) updates.AZURE_OPENAI_API_KEY = str(body.apiKey)!;
    updates.AZURE_OPENAI_ENDPOINT = str(body.endpoint) ?? null;
    updates.AZURE_OPENAI_DEPLOYMENT = str(body.deployment) ?? null;
  }

  // Key-based providers need a key — supplied now or already stored.
  const kEnv = keyEnv[provider];
  if (kEnv && !updates[kEnv] && !current[kEnv]) {
    return NextResponse.json(
      { error: 'api_key_required', detail: `${provider} needs an API key.` },
      { status: 400 }
    );
  }

  await updateCredentials(updates);
  return NextResponse.json({ ok: true, restartRequired: true });
}

export async function DELETE() {
  await updateCredentials(Object.fromEntries(ALL_LLM_KEYS.map((k) => [k, null])));
  return NextResponse.json({ ok: true, restartRequired: true });
}
