import { NextResponse } from 'next/server';

const NOVOMCP_ENGINE_URL = process.env.NOVOMCP_ENGINE_URL || 'http://localhost:8018';
const PING_TIMEOUT_MS = 4000;

// The wireable NovoMCP compute services, keyed by capability. `env` is the var a
// self-hoster sets to the service URL; the Connections page surfaces these names
// for any service that isn't wired. Keep in sync with the providers map in
// /api/local/health.
const SERVICE_CATALOG: { key: string; label: string; env: string }[] = [
  { key: 'admet', label: 'ADMET (addie-models)', env: 'ADDIE_MODELS_URL' },
  { key: 'nnp', label: 'Neural-net potentials', env: 'NOVOMCP_NNP_URL' },
  { key: 'qm', label: 'Quantum chemistry', env: 'NOVOMCP_QM_URL' },
  { key: 'properties', label: 'Property models (pKa / sol / BDE)', env: 'NOVOMCP_PROPERTIES_URL' },
  { key: 'neb', label: 'Transition-state (NEB)', env: 'NOVOMCP_NEB_URL' },
  { key: 'docking', label: 'Docking (AutoDock-GPU)', env: 'AUTODOCK_GPU_URL' },
  { key: 'md', label: 'Molecular dynamics (GROMACS)', env: 'GROMACS_MD_URL' },
  { key: 'structure', label: 'Structure prediction', env: 'OPENFOLD3_URL' },
  { key: 'chem_props', label: 'RDKit properties', env: 'CHEM_PROPS_URL' },
  { key: 'molecule_index', label: 'Molecule similarity index', env: 'NOVOMCP_MOLECULE_INDEX_URL' },
];

// Ping a service /health. Returns true only on a 2xx within the timeout.
async function ping(url: string): Promise<boolean> {
  try {
    const res = await fetch(`${url.replace(/\/$/, '')}/health`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(PING_TIMEOUT_MS),
    });
    return res.ok;
  } catch {
    return false;
  }
}

// Capability map for the whole UI. Every surface gates on capability presence:
// a compliance service wired -> show the compliance panel; a GPU service
// reachable -> enable that tool; spineMode hosted -> show keys/team. The web
// build reports all native.* false; a Tauri wrapper overrides these on the
// client.
export async function GET() {
  const complianceUrl = process.env.NOVOMCP_COMPLIANCE_URL;

  // Engine health + version.
  const engineHealth = await fetch(`${NOVOMCP_ENGINE_URL}/health`, {
    cache: 'no-store',
    signal: AbortSignal.timeout(PING_TIMEOUT_MS),
  })
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);

  // Ping each wired service in parallel; unwired services skip the network.
  const services: Record<string, { wired: boolean; reachable: boolean; label: string; env: string }> = {};
  await Promise.all(
    SERVICE_CATALOG.map(async ({ key, label, env }) => {
      const url = process.env[env];
      const wired = !!url;
      services[key] = {
        wired,
        reachable: wired ? await ping(url as string) : false,
        label,
        env,
      };
    })
  );

  const complianceWired = !!complianceUrl;
  const complianceReachable = complianceWired ? await ping(complianceUrl as string) : false;

  return NextResponse.json({
    engine: {
      url: NOVOMCP_ENGINE_URL,
      reachable: engineHealth !== null,
      version: engineHealth?.version ?? engineHealth?.engine_version ?? null,
    },
    spineMode: process.env.NOVO_AUTH === 'hosted' ? 'hosted' : 'local',
    services,
    compliance: { wired: complianceWired, reachable: complianceReachable, env: 'NOVOMCP_COMPLIANCE_URL' },
    native: { isTauri: false, localFs: false, pymol: false },
  });
}
