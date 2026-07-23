import { NextResponse } from 'next/server';

const NOVOMCP_ENGINE_URL = process.env.NOVOMCP_ENGINE_URL || 'http://localhost:8018';

// Aggregates local engine status for the OSS dashboard. Combines /health,
// /mcp/tools (visible tools count), and /v1/openapi.json (path count) into
// one response the dashboard renders without needing three separate fetches.
// Fails soft: any sub-fetch that errors returns null instead of failing the
// whole endpoint, so the dashboard can render partial state.
export async function GET() {
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
    tools_visible: tools?.tools?.length ?? null,
    tool_names: (tools?.tools ?? []).map((t: any) => t.name),
    rest_paths: openapi?.paths ? Object.keys(openapi.paths).length : null,
    update_status: platform?.result?.update_status ?? null,
    // Which optional services are wired locally. Read from Next.js env
    // (server-side) so the dashboard can render capability chips without
    // pinging each service.
    providers: {
      admet: !!process.env.ADDIE_MODELS_URL,
      docking: !!process.env.AUTODOCK_GPU_URL,
      md: !!process.env.GROMACS_MD_URL,
      structure: !!process.env.OPENFOLD3_URL,
      qm: !!process.env.NOVOMCP_QM_URL,
      nnp: !!process.env.NOVOMCP_NNP_URL,
      compliance: !!process.env.NOVOMCP_COMPLIANCE_URL,
      molecule_index: !!process.env.NOVOMCP_MOLECULE_INDEX_URL,
      omics: !!process.env.NOVOMCP_DB_HOST,
      literature: !!process.env.PINECONE_API_KEY,
      clinical_outcomes: !!process.env.NOVOEXPERT_URL,
      materials: !!process.env.MP_API_KEY,
    },
  });
}
