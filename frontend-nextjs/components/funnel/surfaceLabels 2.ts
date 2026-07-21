// Surface + client lookup tables for the audit-row display.
//
// Two-tier taxonomy:
//   Tier 1 (surface) — small fixed set, drives the chip. Server emits the
//     value via the `X-Novo-Surface` header; persisted to
//     funnel_audit_log.system_metadata.surface. Unknown values render as
//     a neutral "Unknown surface" chip.
//   Tier 2 (client) — free-form string from `X-Novo-Client` /
//     MCP clientInfo.name. Persisted to system_metadata.client.
//     Display table normalizes known names; everything else falls back
//     to the raw string.
//
// Adding a new surface: add a row to SURFACE_TABLE. The chip render uses
// the values directly; no other code change required.
// Adding a new client: add a row to CLIENT_TABLE for nice display, OR do
// nothing — unknown clients render their raw value (e.g.
// "NovoMCP-WordAddin/1.2.3").

export interface SurfaceMeta {
  label: string;          // Display name on the chip
  icon: string;           // Single-char/emoji icon prefix on the chip
  tone: 'word' | 'chrome' | 'mcp' | 'api' | 'dashboard' | 'workbench' | 'unknown';
}

/** Tier-1 surface tag → display metadata. Keys match the `X-Novo-Surface`
 *  value the client sends. Versioned tags (e.g. `-v1`) survive a major
 *  contract change without breaking the lookup. */
export const SURFACE_TABLE: Record<string, SurfaceMeta> = {
  'word-addin-v1':         { label: 'Word add-in',         icon: '📝', tone: 'word' },
  'chrome-ext-v1':         { label: 'Chrome extension',    icon: '🌐', tone: 'chrome' },
  'mcp-v1':                { label: 'MCP',                 icon: '🤖', tone: 'mcp' },
  'api-v1':                { label: 'API',                 icon: '🔌', tone: 'api' },
  'dashboard-v1':          { label: 'Dashboard',           icon: '📊', tone: 'dashboard' },
  'workbench-cloud-v1':    { label: 'Workbench (cloud)',   icon: '🧪', tone: 'workbench' },
  'workbench-desktop-v1':  { label: 'Workbench (desktop)', icon: '🧪', tone: 'workbench' },
};

const SURFACE_UNKNOWN: SurfaceMeta = { label: 'Unknown surface', icon: '•', tone: 'unknown' };

export function resolveSurface(raw: unknown): { meta: SurfaceMeta; raw: string } {
  if (typeof raw !== 'string' || raw.length === 0) {
    return { meta: SURFACE_UNKNOWN, raw: '' };
  }
  return { meta: SURFACE_TABLE[raw] ?? SURFACE_UNKNOWN, raw };
}

/** Tone → Tailwind classes for the chip background + text. Keep these
 *  short — one chip per audit row, not the whole page's color story. */
export const SURFACE_TONE_CLASSES: Record<SurfaceMeta['tone'], string> = {
  word:      'bg-blue-500/10 text-blue-700 border border-blue-500/20',
  chrome:    'bg-amber-500/10 text-amber-700 border border-amber-500/20',
  mcp:       'bg-violet-500/10 text-violet-700 border border-violet-500/20',
  api:       'bg-slate-500/10 text-slate-700 border border-slate-500/20',
  dashboard: 'bg-emerald-500/10 text-emerald-700 border border-emerald-500/20',
  workbench: 'bg-rose-500/10 text-rose-700 border border-rose-500/20',
  unknown:   'bg-[var(--bg-warm)] text-[var(--text-muted)] border border-[var(--border)]',
};

/** Tier-2 client → nice display name. Anything not in here renders raw. */
export const CLIENT_TABLE: Record<string, string> = {
  // MCP clients (MCP `clientInfo.name`)
  'claude-ai':        'Claude.ai',
  'claude-code':      'Claude Code',
  'cursor':           'Cursor',
  'chatgpt':          'ChatGPT',
  'gemini':           'Gemini',
  'windsurf':         'Windsurf',
  'cline':            'Cline',
  'continue':         'Continue',
  'novo-workbench':   'Novo Workbench',

  // API clients
  'curl':             'curl',
  'python-sdk':       'Python SDK',
  'node-sdk':         'Node SDK',
  'postman':          'Postman',
  'insomnia':         'Insomnia',
  'hex.tech-notebook':'Hex notebook',
  'hex.tech-mcp':     'Hex (MCP)',
};

export function resolveClient(raw: unknown): string | null {
  if (typeof raw !== 'string' || raw.length === 0) return null;
  return CLIENT_TABLE[raw] ?? raw;
}
