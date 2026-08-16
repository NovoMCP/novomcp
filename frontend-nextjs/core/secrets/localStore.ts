import { promises as fs } from 'fs';
import path from 'path';
import os from 'os';

// SecretStore — `local` backend (see web-gui-scope.md §9). Reads/writes a
// dotenv-format file the engine loads at startup (~/.novo/credentials.env,
// override with NOVO_CREDENTIALS_PATH). Plaintext with 0600 perms, gitignored —
// the same posture as ~/.aws/credentials / ~/.netrc. Server-only (fs access);
// never import into a client component.

const CREDENTIALS_PATH =
  process.env.NOVO_CREDENTIALS_PATH || path.join(os.homedir(), '.novo', 'credentials.env');

// Minimal dotenv parse: KEY=VALUE lines, optional surrounding quotes, # comments.
function parseEnv(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    let val = line.slice(eq + 1).trim();
    if (
      (val.startsWith('"') && val.endsWith('"')) ||
      (val.startsWith("'") && val.endsWith("'"))
    ) {
      val = val.slice(1, -1).replace(/\\"/g, '"').replace(/\\\\/g, '\\');
    }
    if (key) out[key] = val;
  }
  return out;
}

function serializeEnv(map: Record<string, string>): string {
  const header =
    '# Managed by the NovoMCP dashboard. Loaded by the engine at startup.\n' +
    '# Plaintext — keep this file private (0600).\n';
  const lines = Object.entries(map)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${k}="${String(v).replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`);
  return header + lines.join('\n') + '\n';
}

export async function readCredentials(): Promise<Record<string, string>> {
  try {
    return parseEnv(await fs.readFile(CREDENTIALS_PATH, 'utf-8'));
  } catch (e: unknown) {
    if ((e as { code?: string })?.code === 'ENOENT') return {};
    throw e;
  }
}

// Merge updates into the existing file. A null/empty value removes the key.
// Other keys in the file (e.g. service URLs set elsewhere) are preserved.
export async function updateCredentials(updates: Record<string, string | null>): Promise<void> {
  const current = await readCredentials();
  for (const [k, v] of Object.entries(updates)) {
    if (v === null || v === '') delete current[k];
    else current[k] = v;
  }
  await fs.mkdir(path.dirname(CREDENTIALS_PATH), { recursive: true });
  await fs.writeFile(CREDENTIALS_PATH, serializeEnv(current), { mode: 0o600 });
  // writeFile mode is masked by umask; force 0600 explicitly.
  await fs.chmod(CREDENTIALS_PATH, 0o600).catch(() => {});
}

// Display form of the path (~ for home) — safe to show in the UI.
export function credentialsDisplayPath(): string {
  return CREDENTIALS_PATH.replace(os.homedir(), '~');
}
