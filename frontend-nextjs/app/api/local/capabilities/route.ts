import { NextResponse } from 'next/server';
import { engineConfigLookup } from '@/core/secrets/localStore';

const NOVOMCP_ENGINE_URL = process.env.NOVOMCP_ENGINE_URL || 'http://localhost:8018';
const PING_TIMEOUT_MS = 4000;

// The open-source compute services a self-hoster runs and wires in, keyed by
// capability. `env` is the var that holds the service URL; the Connections page
// surfaces these names for any service that isn't wired. Keep in sync with the
// providers map in /api/local/health.
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

// A /health answer only says the host is up. For a keyed compliance service,
// also check the key: POST /api/check with no SMILES. The service authenticates
// first, so 401/403 means the key was refused; any other answer means it was
// accepted, and no check runs, so nothing is billed (NovoMCP's service at
// api.novomcp.com/v1/compliance returns 422 here). null = not checked.
async function complianceKeyAccepted(url: string, key: string): Promise<boolean | null> {
  try {
    const res = await fetch(`${url.replace(/\/$/, '')}/api/check`, {
      method: 'POST',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': key },
      body: '{}',
      signal: AbortSignal.timeout(PING_TIMEOUT_MS),
    });
    if (res.status === 401 || res.status === 403) return false;
    return true;
  } catch {
    return null;
  }
}

// Capability map for the whole UI. Every surface gates on capability presence:
// a compliance service wired -> show the compliance panel; a GPU service
// reachable -> enable that tool; spineMode hosted -> show keys/team. Values come
// from this server's env, then the credential file Connections writes, the same
// order the engine loads them. The web build reports all native.* false; a
// Tauri wrapper overrides these on the client.
export async function GET() {
  const lookup = await engineConfigLookup();
  const complianceUrl = lookup('NOVOMCP_COMPLIANCE_URL');
  const complianceKey = lookup('NOVOMCP_COMPLIANCE_API_KEY');

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
      const url = lookup(env);
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
  const complianceKeyOk =
    complianceReachable && complianceKey ? await complianceKeyAccepted(complianceUrl as string, complianceKey) : null;

  return NextResponse.json({
    engine: {
      url: NOVOMCP_ENGINE_URL,
      reachable: engineHealth !== null,
      version: engineHealth?.version ?? engineHealth?.engine_version ?? null,
    },
    spineMode: process.env.NOVO_AUTH === 'hosted' ? 'hosted' : 'local',
    services,
    compliance: {
      wired: complianceWired,
      reachable: complianceReachable,
      keyAccepted: complianceKeyOk,
      hosted: complianceWired && isHostedCompliance(complianceUrl as string),
      env: 'NOVOMCP_COMPLIANCE_URL',
    },
    native: { isTauri: false, localFs: false, pymol: false },
  });
}

// NovoMCP's compliance service on the paid API: checks are billed to the key's
// api.novomcp.com account.
function isHostedCompliance(url: string): boolean {
  try {
    return new URL(url).hostname === 'api.novomcp.com';
  } catch {
    return false;
  }
}
