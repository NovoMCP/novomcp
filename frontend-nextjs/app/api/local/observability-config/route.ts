import { NextRequest, NextResponse } from 'next/server';
import { readCredentials, updateCredentials, credentialsDisplayPath } from '@/core/secrets/localStore';

// Observability config — sends request / tool-call traces (OpenTelemetry / OTLP)
// to any compatible backend. Vendor-neutral: one OTLP exporter reaches a local
// collector, Grafana, Honeycomb, Arize, Datadog, etc. Persists to the local
// SecretStore; the engine reads these at startup. Auth headers are write-only.

const K = {
  enabled: 'OTEL_TRACING_ENABLED',
  endpoint: 'OTEL_EXPORTER_OTLP_ENDPOINT',
  headers: 'OTEL_EXPORTER_OTLP_HEADERS',
  service: 'OTEL_SERVICE_NAME',
  sampling: 'OTEL_SAMPLING_RATE',
};
const ALL = Object.values(K);

function str(v: unknown): string | undefined {
  return typeof v === 'string' && v.trim() ? v.trim() : undefined;
}

export async function GET() {
  const c = await readCredentials();
  return NextResponse.json({
    enabled: c[K.enabled] === 'true',
    endpoint: c[K.endpoint] || null,
    serviceName: c[K.service] || null,
    samplingRate: c[K.sampling] || null,
    headersSet: !!c[K.headers],
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

  const enabled = body.enabled === true || body.enabled === 'true';
  const endpoint = str(body.endpoint) ?? '';
  if (enabled && !endpoint) {
    return NextResponse.json(
      { error: 'endpoint_required', detail: 'An OTLP endpoint is required to enable tracing.' },
      { status: 400 }
    );
  }
  // Accept http(s)://… or host:port (OTLP/gRPC collectors use host:4317).
  if (endpoint && !/^(https?:\/\/.+|[\w.-]+:\d+)$/i.test(endpoint)) {
    return NextResponse.json(
      { error: 'invalid_endpoint', detail: 'Use an http(s):// URL or host:port.' },
      { status: 400 }
    );
  }

  let sampling: string | null = null;
  if (body.samplingRate !== undefined && body.samplingRate !== '' && body.samplingRate !== null) {
    const n = Number(body.samplingRate);
    if (!Number.isFinite(n) || n < 0 || n > 1) {
      return NextResponse.json(
        { error: 'invalid_sampling', detail: 'Sampling rate must be between 0 and 1.' },
        { status: 400 }
      );
    }
    sampling = String(n);
  }

  const updates: Record<string, string | null> = {
    [K.enabled]: enabled ? 'true' : 'false',
    [K.endpoint]: endpoint || null,
    [K.service]: str(body.serviceName) ?? null,
    [K.sampling]: sampling,
  };
  // Headers carry auth — write-only, so only update when supplied.
  if (str(body.headers)) updates[K.headers] = str(body.headers)!;

  await updateCredentials(updates);
  return NextResponse.json({ ok: true, restartRequired: true });
}

export async function DELETE() {
  await updateCredentials(Object.fromEntries(ALL.map((k) => [k, null])));
  return NextResponse.json({ ok: true, restartRequired: true });
}
