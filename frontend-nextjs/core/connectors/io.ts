import type { Connector } from './registry';

// Client-side dispatch between the marketplace form and the per-kind local API
// routes. Each connector category maps to one route + payload shape; the form
// itself is generic and driven by the registry.

type Values = Record<string, string>;

// Prefill non-secret fields from the current config; report whether a secret
// (API key / password) is already stored so the form can say "leave blank to keep".
export async function loadConnector(c: Connector): Promise<{ values: Values; keySet: boolean }> {
  const get = async (url: string) => (await fetch(url, { cache: 'no-store' })).json();
  try {
    if (c.category === 'service' && c.urlEnv) {
      const d = await get(`/api/local/service-config?url_env=${encodeURIComponent(c.urlEnv)}`);
      return { values: { url: d.url || '' }, keySet: !!d.keySet };
    }
    if (c.category === 'compliance') {
      const d = await get('/api/local/compliance-config');
      return { values: { url: d.url || '' }, keySet: !!d.keySet };
    }
    if (c.category === 'observability') {
      const d = await get('/api/local/observability-config');
      return { values: { endpoint: d.endpoint || '', samplingRate: d.samplingRate || '' }, keySet: !!d.headersSet };
    }
    if (c.category === 'ai') {
      const d = await get('/api/local/llm-config');
      const p = d.providers?.[c.id] || {};
      const values: Values = {};
      for (const k of ['model', 'baseUrl', 'endpoint', 'deployment', 'url'] as const) {
        if (p[k]) values[k] = p[k];
      }
      return { values, keySet: !!p.keySet };
    }
    if (c.category === 'data') {
      const d = await get('/api/local/connections');
      const existing = (d.connections || []).find((x: { type: string }) => x.type === c.id);
      return { values: existing?.config || {}, keySet: !!existing };
    }
  } catch {
    /* fall through to empty */
  }
  return { values: {}, keySet: false };
}

export async function saveConnector(c: Connector, values: Values): Promise<{ ok: boolean; error?: string }> {
  let url = '';
  let method = 'PUT';
  let body: Record<string, unknown> = {};

  if (c.category === 'service') {
    url = '/api/local/service-config';
    body = { urlEnv: c.urlEnv, url: values.url, apiKey: values.apiKey };
  } else if (c.category === 'compliance') {
    url = '/api/local/compliance-config';
    body = { url: values.url, apiKey: values.apiKey };
  } else if (c.category === 'observability') {
    url = '/api/local/observability-config';
    body = { enabled: true, endpoint: values.endpoint, headers: values.headers, samplingRate: values.samplingRate };
  } else if (c.category === 'ai') {
    url = '/api/local/llm-config';
    body = { provider: c.id, ...values };
  } else if (c.category === 'data') {
    url = '/api/local/connections';
    method = 'POST';
    const config: Values = {};
    const credentials: Values = {};
    for (const f of c.fields) {
      const v = values[f.name];
      if (!v) continue;
      (f.bucket === 'credentials' ? credentials : config)[f.name] = v;
    }
    body = { type: c.id, config, credentials };
  }

  try {
    const r = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) return { ok: false, error: d.detail || d.error || `Request failed (${r.status})` };
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'Network error' };
  }
}
