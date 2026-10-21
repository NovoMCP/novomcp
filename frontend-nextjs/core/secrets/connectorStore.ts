import { promises as fs } from 'fs';
import path from 'path';
import os from 'os';

// Local connection registry for self-host connectors. Stores each connection
// (metadata + credentials) in ~/.novo/connectors.json (0600), the same
// local-plaintext posture as the SecretStore. Server-only (fs access). The
// engine is stateless about connectors — it receives config + credentials per
// call — so this file is the single source of truth for what's configured.

const CONNECTORS_PATH =
  process.env.NOVO_CONNECTORS_PATH || path.join(os.homedir(), '.novo', 'connectors.json');

export interface Connection {
  id: string;
  type: string;
  displayName: string;
  config: Record<string, string>;
  credentials: Record<string, string>;
  createdAt: string;
}

export async function readConnections(): Promise<Connection[]> {
  try {
    const parsed = JSON.parse(await fs.readFile(CONNECTORS_PATH, 'utf-8'));
    return Array.isArray(parsed) ? parsed : [];
  } catch (e: unknown) {
    if ((e as { code?: string })?.code === 'ENOENT') return [];
    throw e;
  }
}

async function writeConnections(list: Connection[]): Promise<void> {
  const dir = path.dirname(CONNECTORS_PATH);
  await fs.mkdir(dir, { recursive: true, mode: 0o700 });
  await fs.chmod(dir, 0o700).catch(() => {});  // mkdir mode is umask-masked; force 0700.
  await fs.writeFile(CONNECTORS_PATH, JSON.stringify(list, null, 2), { mode: 0o600 });
  await fs.chmod(CONNECTORS_PATH, 0o600).catch(() => {});
}

export async function addConnection(conn: Connection): Promise<void> {
  const list = await readConnections();
  list.push(conn);
  await writeConnections(list);
}

export async function removeConnection(id: string): Promise<boolean> {
  const list = await readConnections();
  const next = list.filter((c) => c.id !== id);
  if (next.length === list.length) return false;
  await writeConnections(next);
  return true;
}

// Rename a connection or change its settings. Credentials left blank keep
// their stored value, so a form never has to show a secret to edit around it.
export async function updateConnection(
  id: string,
  patch: { displayName?: string; config?: Record<string, string>; credentials?: Record<string, string> }
): Promise<Connection | undefined> {
  const list = await readConnections();
  const conn = list.find((c) => c.id === id);
  if (!conn) return undefined;
  if (patch.displayName !== undefined) conn.displayName = patch.displayName.trim() || conn.type;
  if (patch.config) conn.config = patch.config;
  if (patch.credentials) {
    for (const [k, v] of Object.entries(patch.credentials)) if (v) conn.credentials[k] = v;
  }
  await writeConnections(list);
  return conn;
}

export async function getConnection(id: string): Promise<Connection | undefined> {
  return (await readConnections()).find((c) => c.id === id);
}
