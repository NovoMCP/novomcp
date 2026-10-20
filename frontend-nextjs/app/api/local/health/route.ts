import { NextResponse } from 'next/server';
import { engineConfigLookup } from '@/core/secrets/localStore';

const NOVOMCP_ENGINE_URL = process.env.NOVOMCP_ENGINE_URL || 'http://localhost:8018';

// Aggregates local engine status for the OSS dashboard. Combines /health,
// /mcp/tools (visible tools count), and /v1/openapi.json (path count) into
// one response the dashboard renders without needing three separate fetches.
// Fails soft: any sub-fetch that errors returns null instead of failing the
// whole endpoint, so the dashboard can render partial state.
export async function GET() {
  const lookup = await engineConfigLookup();
  const [health, tools, openapi, platform] = await Promise.all([
    fetch(`${NOVOMCP_ENGINE_URL}/health`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null),
    fetch(`${NOVOMCP_ENGINE_URL}/mcp/tools`, {
      cache: 'no-store',
      headers: { Authorization: 'Bearer local-dev' },
    })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null),
    fetch(`${NOVOMCP_ENGINE_URL}/v1/openapi.json`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null),
    fetch(`${NOVOMCP_ENGINE_URL}/mcp/tools/get_platform_info`, {
      method: 'POST',
      cache: 'no-store',
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer local-dev',
      },
      body: JSON.stringify({ arguments: { info_type: 'update' } }),
    })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null),
  ]);

  return NextResponse.json({
    engine_url: NOVOMCP_ENGINE_URL,
    engine_reachable: health !== null,
    health,
    version: health?.version ?? null,
    tools_visible: tools?.tools?.length ?? null,
    // Full catalog size from the engine (falls back to null if an older engine
    // doesn't report it). The dashboard renders "<visible> of <total>".
    tools_total: health?.tools_total ?? null,
    tool_names: (tools?.tools ?? []).map((t: any) => t.name),
    rest_paths: openapi?.paths ? Object.keys(openapi.paths).length : null,
    update_status: platform?.result?.update_status ?? null,
    // Which optional services are wired: this server's env, then the
    // credential file Connections writes (the order the engine loads them), so
    // the dashboard agrees with Connections without pinging each service.
    providers: {
      admet: !!lookup('ADDIE_MODELS_URL'),
      docking: !!lookup('AUTODOCK_GPU_URL'),
      md: !!lookup('GROMACS_MD_URL'),
      structure: !!lookup('OPENFOLD3_URL'),
      qm: !!lookup('NOVOMCP_QM_URL'),
      nnp: !!lookup('NOVOMCP_NNP_URL'),
      compliance: !!lookup('NOVOMCP_COMPLIANCE_URL'),
      molecule_index: !!lookup('NOVOMCP_MOLECULE_INDEX_URL'),
      omics: !!lookup('NOVOMCP_DB_HOST'),
      literature: !!lookup('PINECONE_API_KEY'),
      materials: !!lookup('MP_API_KEY'),
    },
  });
}
