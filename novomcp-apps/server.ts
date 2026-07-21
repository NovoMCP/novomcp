/**
 * NovoMCP Apps Server
 *
 * MCP Apps gateway that proxies to novomcp backend while providing
 * interactive UI visualizations using the @modelcontextprotocol/ext-apps SDK.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * SCHEMA-SYNC POLICY (cross-repo invariant)
 * ─────────────────────────────────────────────────────────────────────────
 * Every Zod inputSchema below MUST stay in sync with the corresponding tool's
 * input schema in novomcp/mcp/tools.py (MCP_TOOLS dict). When you add a
 * parameter to a tool's executor in novomcp, ALSO add it to the Zod
 * schema for that tool here.
 *
 * Why: this server is the user-facing MCP endpoint (compute.novomcp.com/mcp).
 * MCP clients see ONLY what these Zod schemas declare. novomcp accepts
 * extra params transparently — but the gateway strips anything Zod doesn't
 * know about, which means a param added to novomcp without being mirrored
 * here is silently invisible to users. New tools fail loud ("tool not found");
 * new params fail silent (default behavior, no error). See the 2026-05-14
 * Ship 2 debugging session for the canonical example: adaptive_equilibration
 * lived in novomcp for two days before this repo caught up, and tests
 * showed the flag was being dropped before it reached the backend.
 *
 * Future drift checker: a CI test that diffs the Zod keys here against the
 * MCP_TOOLS entries in novomcp would catch this class of bug. Tracked
 * separately.
 */
import {
  RESOURCE_MIME_TYPE,
  registerAppResource,
  registerAppTool,
} from "@modelcontextprotocol/ext-apps/server";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { ReadResourceResult } from "@modelcontextprotocol/sdk/types.js";
import fs from "node:fs/promises";
import path from "node:path";
import { z } from "zod";

// Backend API configuration — MUST point to novomcp internal URL, NOT to ourselves (ai.novomcp.com)
const NOVOMCP_ENGINE_URL = process.env.NOVOMCP_ENGINE_URL;
if (!NOVOMCP_ENGINE_URL) {
  throw new Error("NOVOMCP_ENGINE_URL environment variable is required");
}

// =============================================================================
// Numeric Formatting Utility
// =============================================================================
// Standardizes numeric values across all clients (terminal, web, desktop)
// to ensure consistent display regardless of how the client renders them.

function formatNumericValues(obj: unknown, depth = 0): unknown {
  if (depth > 15) return obj; // Prevent infinite recursion

  if (typeof obj === "number") {
    if (!Number.isFinite(obj)) return obj; // NaN, Infinity
    if (Number.isInteger(obj)) return obj; // Keep integers as-is

    // Probabilities/scores (0-1 range): 2 decimal places
    if (obj >= 0 && obj <= 1) {
      return Math.round(obj * 100) / 100;
    }
    // Small decimals (e.g., LogP -2 to 10): 2 decimal places
    if (Math.abs(obj) < 100) {
      return Math.round(obj * 100) / 100;
    }
    // Medium numbers (e.g., molecular weight 100-1000): 2 decimal places
    if (Math.abs(obj) < 10000) {
      return Math.round(obj * 100) / 100;
    }
    // Large numbers: round to integer
    return Math.round(obj);
  }

  if (Array.isArray(obj)) {
    return obj.map((item) => formatNumericValues(item, depth + 1));
  }

  if (obj && typeof obj === "object") {
    const formatted: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
      formatted[key] = formatNumericValues(value, depth + 1);
    }
    return formatted;
  }

  return obj;
}

// Works both from source (server.ts) and compiled (dist/server.js)
const DIST_DIR = import.meta.filename.endsWith(".ts")
  ? path.join(import.meta.dirname, "dist/apps")
  : path.join(import.meta.dirname, "apps");

// Resource URIs for UI apps
const UI_RESOURCES = {
  moleculeViewer: "ui://novomcp/molecule-viewer",
  admetDashboard: "ui://novomcp/admet-dashboard",
  researchExplorer: "ui://novomcp/research-explorer",
  structureViewer: "ui://novomcp/structure-viewer",
  creditUsage: "ui://novomcp/credit-usage",
  favesDashboard: "ui://novomcp/faves-dashboard",
  jobs: "ui://novomcp/jobs",
  funnels: "ui://novomcp/funnels",
  mdResults: "ui://novomcp/md-results",
  pipelineAudit: "ui://novomcp/pipeline-audit",
  dockingViewer: "ui://novomcp/docking-viewer",
  leadComparison: "ui://novomcp/lead-comparison",
  frontierOrbitals: "ui://novomcp/frontier-orbitals",
  qmHessian: "ui://novomcp/qm-hessian",
  transitionState: "ui://novomcp/transition-state",
  excitedStates: "ui://novomcp/excited-states",
  redoxPotential: "ui://novomcp/redox-potential",
  reactionThermo: "ui://novomcp/reaction-thermo",
  materialsProject: "ui://novomcp/materials-project",
  qmCalculation: "ui://novomcp/qm-calculation",
  targetDiscovery: "ui://novomcp/target-discovery",
  clinicalOutcomes: "ui://novomcp/clinical-outcomes",
  stratifyPatients: "ui://novomcp/stratify-patients",
  conformerSearch: "ui://novomcp/conformer-search",
  generateDynamics: "ui://novomcp/generate-dynamics",
  predictPka: "ui://novomcp/predict-pka",
  predictSolubility: "ui://novomcp/predict-solubility",
  predictBde: "ui://novomcp/predict-bde",
  nnpResults: "ui://novomcp/nnp-results",
  validateTarget: "ui://novomcp/validate-target",
  resultsTable: "ui://novomcp/results-table",
  clusterExplorer: "ui://novomcp/cluster-explorer",
};

// =============================================================================
// Backend Proxy
// =============================================================================

// Tools that may take longer (ML inference, structure prediction)
const LONG_RUNNING_TOOLS = new Set([
  "predict_admet", "predict_clinical_outcomes", "predict_structure", "get_structure_result",
  "optimize_molecule", "get_molecule_profile", "screen_library",
  "check_compliance",
  "push_to_destination",
  "pull_from_source",
  "lead_optimization",
  "validate_target",
  "dock_molecules",
  "run_molecular_dynamics",
  "run_qm_calculation",
  "run_qm_hessian",
  "predict_frontier_orbitals",
  "run_excited_states",
  "optimize_geometry_nnp",
  "find_transition_state",
  "predict_redox_potential",
  "predict_reaction_thermodynamics",
  "run_conformer_search",
  "dock_with_strain",
  "generate_dynamics",
  "parameterize_metal",
]);
const DEFAULT_TIMEOUT_MS = 30_000;
const LONG_TIMEOUT_MS = 300_000; // 5 min — covers cold-start for heavy GPU/compute images (GROMACS, AlphaFlow)

// Max input payload size per tool call (500KB)
const MAX_INPUT_SIZE = 512_000;

// Structured error class for backend failures. Gives us error codes Claude can
// parse and correlation IDs that match server-side logs.
export class BackendError extends Error {
  code: string;
  upstreamStatus?: number;
  correlationId: string;
  toolName: string;

  constructor(params: {
    code: string;
    message: string;
    toolName: string;
    upstreamStatus?: number;
    correlationId?: string;
  }) {
    super(params.message);
    this.name = "BackendError";
    this.code = params.code;
    this.toolName = params.toolName;
    this.upstreamStatus = params.upstreamStatus;
    this.correlationId = params.correlationId || crypto.randomUUID();
  }
}

function createBackendCaller(apiKey: string, clientTag: string = "") {
  return async function callEngine(toolName: string, args: Record<string, unknown>): Promise<unknown> {
    // Every call gets a correlation ID we log locally and return in errors
    // so users can match client-side errors to server-side log entries.
    const correlationId = crypto.randomUUID();

    // Validate input size to prevent oversized payloads
    const serialized = JSON.stringify({ arguments: args });
    if (serialized.length > MAX_INPUT_SIZE) {
      throw new BackendError({
        code: "INPUT_TOO_LARGE",
        message: `Input too large (${Math.round(serialized.length / 1024)}KB). Maximum is ${MAX_INPUT_SIZE / 1024}KB.`,
        toolName,
        correlationId,
      });
    }

    const timeoutMs = LONG_RUNNING_TOOLS.has(toolName) ? LONG_TIMEOUT_MS : DEFAULT_TIMEOUT_MS;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    try {
      console.log(`[${correlationId}] ${toolName} → POST ${NOVOMCP_ENGINE_URL}/mcp/tools/${toolName}`);
      const response = await fetch(`${NOVOMCP_ENGINE_URL}/mcp/tools/${toolName}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Correlation-Id": correlationId,
          // Surface + client tags persist into the backend's audit row
          // via funnel_audit_log.system_metadata.{surface,client}.
          // Surface is always mcp-v1 here (this proxy IS the MCP surface);
          // client is the upstream caller (claude-code, cursor, ...) or ""
          // when the MCP `clientInfo` and User-Agent are both unavailable.
          "X-Novo-Surface": "mcp-v1",
          ...(clientTag ? { "X-Novo-Client": clientTag } : {}),
          ...(apiKey ? { "Authorization": `Bearer ${apiKey}` } : {}),
        },
        body: serialized,
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorText = await response.text();
        let detail: string;
        try {
          const parsed = JSON.parse(errorText);
          // FastAPI's HTTPException can carry `detail` as either a string
          // OR an object — novomcp's router merges ToolResult.data into
          // the error detail so callers see structured fields like
          // error_code / suggested_symbol / retry_with. If we just `||`
          // assign that into a string variable it stringifies to
          // "[object Object]" at message-format time (reported by screen_library
          // failures). Extract the readable message where possible, fall back
          // to a compact JSON dump of the object so Claude sees something
          // useful instead of the object-toString sentinel.
          const raw = parsed.detail ?? parsed.error ?? "";
          if (typeof raw === "string") {
            detail = raw;
          } else if (raw && typeof raw === "object") {
            const obj = raw as Record<string, unknown>;
            detail =
              typeof obj.error === "string" ? obj.error :
              typeof obj.message === "string" ? obj.message :
              typeof obj.detail === "string" ? obj.detail :
              JSON.stringify(raw);
          } else {
            detail = String(raw);
          }
        } catch {
          detail = "";
        }
        const safeDetail = detail.length > 200 ? detail.slice(0, 200) : detail;

        // Classify by HTTP status for meaningful error codes
        let code = "UPSTREAM_ERROR";
        if (response.status === 400) code = "VALIDATION_ERROR";
        else if (response.status === 401 || response.status === 403) code = "AUTH_ERROR";
        else if (response.status === 402) code = "CREDIT_REQUIRED";
        else if (response.status === 404) code = "TOOL_NOT_FOUND";
        else if (response.status === 429) code = "RATE_LIMITED";
        else if (response.status >= 500) code = "UPSTREAM_5XX";

        console.error(`[${correlationId}] ${toolName} failed: HTTP ${response.status} code=${code} detail="${safeDetail}"`);

        throw new BackendError({
          code,
          message: safeDetail || `${toolName} failed with HTTP ${response.status}`,
          toolName,
          upstreamStatus: response.status,
          correlationId,
        });
      }

      const data = await response.json();
      const result = data.result || data;

      console.log(`[${correlationId}] ${toolName} ✓ success`);
      // Format all numeric values for consistent display across clients
      return formatNumericValues(result);
    } catch (e: unknown) {
      if (e instanceof DOMException && e.name === "AbortError") {
        console.error(`[${correlationId}] ${toolName} TIMEOUT after ${timeoutMs / 1000}s`);
        throw new BackendError({
          code: "TIMEOUT",
          message: `${toolName} timed out after ${timeoutMs / 1000}s. Try a simpler query or retry.`,
          toolName,
          correlationId,
        });
      }
      if (e instanceof BackendError) throw e;
      // Network error, parse error, or other unexpected
      const msg = e instanceof Error ? e.message : String(e);
      console.error(`[${correlationId}] ${toolName} NETWORK_ERROR: ${msg}`);
      throw new BackendError({
        code: "NETWORK_ERROR",
        message: `${toolName} network error: ${msg.slice(0, 200)}`,
        toolName,
        correlationId,
      });
    } finally {
      clearTimeout(timer);
    }
  };
}

/**
 * Format a BackendError into a tool handler error return.
 *
 * IMPORTANT: We return the error info as two content blocks (human message
 * + JSON error data) instead of using `structuredContent`. The MCP SDK
 * validates `structuredContent` against the tool's `outputSchema` even when
 * `isError: true`, which caused schema mismatches to bubble up as the generic
 * "Error occurred during tool execution" at Anthropic's MCP client layer.
 *
 * Claude can parse the JSON content block to extract `error.code`,
 * `error.correlation_id`, etc. without requiring a schema match.
 */
export function formatToolError(e: unknown) {
  if (e instanceof BackendError) {
    const userMessage =
      e.code === "TIMEOUT"
        ? `The ${e.toolName} tool timed out. This usually means an upstream API is slow. Try a simpler query or retry in a moment.`
        : e.code === "RATE_LIMITED"
        ? `The ${e.toolName} tool hit a rate limit from its upstream API. Please wait 60 seconds and retry.`
        : e.code === "UPSTREAM_5XX"
        ? `The ${e.toolName} tool's upstream API returned a server error (HTTP ${e.upstreamStatus}). This is usually transient — try again.`
        : e.code === "CREDIT_REQUIRED"
        ? `The ${e.toolName} tool requires additional credits. Check your account balance.`
        : e.code === "AUTH_ERROR"
        ? `The ${e.toolName} tool is not available at your current tier.`
        : `The ${e.toolName} tool failed: ${e.message}`;

    // Include structured error info INLINE in the content text (as JSON)
    // rather than in `structuredContent`. The MCP SDK validates
    // structuredContent against the tool's outputSchema even when isError=true,
    // which causes schema mismatches to bubble up as generic "Error occurred
    // during tool execution" at the Anthropic client layer. Embedding the
    // error in content text keeps the info accessible to Claude without
    // tripping schema validation.
    const errorJson = JSON.stringify({
      error: {
        code: e.code,
        message: e.message,
        tool: e.toolName,
        upstream_status: e.upstreamStatus,
        correlation_id: e.correlationId,
      },
    });

    return {
      content: [
        { type: "text" as const, text: userMessage },
        { type: "text" as const, text: errorJson },
      ],
      isError: true,
    };
  }

  // Fallback for non-BackendError exceptions
  const msg = e instanceof Error ? e.message : String(e);
  return {
    content: [{ type: "text" as const, text: `Unexpected error: ${msg.slice(0, 300)}` }],
    isError: true,
  };
}

// =============================================================================
// Server Setup
// =============================================================================

export function createServer(apiKey: string = "", clientTag: string = ""): McpServer {
  const SERVER_MODE = process.env.NOVO_SERVER_MODE || "core"; // "core" or "compute"
  const isCore = SERVER_MODE === "core";
  const isCompute = SERVER_MODE === "compute";

  // Surfaced to the model in every conversation that uses this connector. Claude
  // shares ONE MCP connection per connector across all chat windows and issues no
  // per-conversation session id (verified) — so the LLM's own context is the only
  // per-conversation boundary the server can key a funnel on. Hence: have the
  // model mint a per-conversation funnel_id and carry it on every call.
  // Single source of truth mirrors novomcp/mcp/router.py SERVER_INSTRUCTIONS.
  // Keep the two in sync (the front serves THIS to Claude; router.py serves it to
  // api.novomcp.com direct clients).
  const FUNNEL_INSTRUCTIONS =
`NovoMCP is a drug discovery + materials science engine. Every conversation must mint and carry a unique funnel_id for audit isolation and cross-run learning.

FUNNEL_ID PROTOCOL — apply before any tool call:
1. At conversation start, mint funnel_id = \`funnel_{topic_short}_{YYYYMMDD}_{HHMMSS}\` using the current UTC time. topic_short is a 2-4 char abbreviation of the focus (e.g. "aml" for acute myeloid leukemia, "gbm" for glioblastoma, "alz" for Alzheimer's, "mat" for materials work).
2. NEVER reuse a funnel_id across conversations or topics. New conversation = new id. Topic pivot mid-conversation = new id.
3. Pass funnel_id as an argument on every funnel-eligible tool call (target_discovery, validate_target, search_chembl, predict_admet, dock_molecules, run_molecular_dynamics, lead_optimization, predict_clinical_outcomes, stratify_patients, generate_dynamics, …). The server keys its audit log on it.
4. You do NOT need to call save_funnel_stage for ordinary tool calls — every call is auto-logged server-side under the funnel_id you carry. Only call save_funnel_stage to record an explicit human-reviewed checkpoint.
5. For autonomous full-funnel runs ("Novo AG", "/agm"), invoke run_novo_ag — it returns the canonical 12-stage protocol that supersedes these notes.

Why this matters: a user may run parallel conversations (e.g. cancer in one chat, Alzheimer's in another, materials in a third). Each is a distinct discovery track and must have its own audit trail. Without your explicit minting, the server falls back to a user-keyed slot that cannot distinguish parallel conversations from the same account.`;

  const server = new McpServer(
    {
      name: isCompute ? "Novo Compute" : "Novo",
      version: "1.0.0",
    },
    { instructions: FUNNEL_INSTRUCTIONS }
  );

  // ---------------------------------------------------------------------------
  // Funnel-id schema augmentation — mirrors novomcp _inject_funnel_id_into_schemas.
  // Claude connects through THIS front, which serves its own tools/list from the
  // local Zod schemas below. So funnel_id must be DECLARED here or (a) the model
  // never sees it advertised and (b) strict z.object() strips it off the args
  // before we proxy to novomcp — collapsing every call to the user-slot.
  // registerAppTool() funnels through server.registerTool, so patching that one
  // method covers both direct and UI-tool registrations. Idempotent: skips tools
  // whose schema already declares funnel_id (save_funnel_* etc.).
  // ---------------------------------------------------------------------------
  const FUNNEL_ELIGIBLE_TOOLS = new Set<string>([
    "search_prior_runs",
    "target_discovery", "validate_target",
    "search_literature", "search_biorxiv", "search_patents", "search_chembl",
    "predict_admet", "predict_pka", "predict_solubility",
    "check_compliance",
    "lead_optimization", "optimize_molecule",
    "dock_molecules", "dock_with_strain",
    "predict_clinical_outcomes",
    "run_molecular_dynamics", "generate_dynamics",
    "stratify_patients",
    "save_funnel_context", "get_funnel_context", "get_funnel_audit",
  ]);
  const FUNNEL_ID_FIELD = z.string().optional().describe(
    "Conversation-scoped audit/learning identifier. Mint once at the start of " +
    "every conversation as `funnel_{topic_short}_{YYYYMMDD}_{HHMMSS}` (UTC) and pass " +
    "on every subsequent tool call. topic_short: 2-4 char abbreviation of the " +
    "focus (e.g. 'aml', 'gbm', 'alz', 'mat'). NEVER reuse across conversations or " +
    "topics. The server keys its audit log on this id — omitting it falls back to " +
    "a user-keyed slot that cannot isolate parallel conversations from the same " +
    "account. For autonomous full-funnel runs, run_novo_ag returns the canonical " +
    "12-stage protocol."
  );
  const _origRegisterTool = server.registerTool.bind(server);
  (server as any).registerTool = (name: string, config: any, handler: any) => {
    if (FUNNEL_ELIGIBLE_TOOLS.has(name) && config) {
      const sc = config.inputSchema;
      if (sc instanceof z.ZodObject) {
        if (!("funnel_id" in sc.shape)) {
          config = { ...config, inputSchema: sc.extend({ funnel_id: FUNNEL_ID_FIELD }) };
        }
      } else if (sc && typeof sc === "object") {
        if (!("funnel_id" in sc)) {
          config = { ...config, inputSchema: { ...sc, funnel_id: FUNNEL_ID_FIELD } };
        }
      } else if (!sc) {
        config = { ...config, inputSchema: { funnel_id: FUNNEL_ID_FIELD } };
      }
    }
    return _origRegisterTool(name, config, handler);
  };

  // Create backend caller with user's API key and the MCP client tag
  // (claude-code, cursor, ...) so the backend audit row records which
  // host the tool call came from.
  const callEngine = createBackendCaller(apiKey, clientTag);

  // Generic output schema for all tools that return JSON results from novomcp.
  // Claude AI requires outputSchema to discover and use tools.
  const mcpResultSchema = z.object({}).passthrough();

  // Helper: get seed molecule properties for lead-comparison viewer
  async function enrichSeedProperties(
    caller: typeof callEngine,
    smiles: string
  ): Promise<Record<string, unknown>> {
    try {
      const props = await caller("get_molecule_info", { smiles }) as any;
      return {
        smiles,
        source: "seed",
        mw: props?.mw ?? props?.molecular_weight,
        logp: props?.logp ?? props?.log_p,
        tpsa: props?.tpsa,
        qed: props?.qed,
        sa_score: props?.sa_score ?? props?.synthetic_accessibility,
        hbd: props?.hbd ?? props?.h_bond_donors,
        hba: props?.hba ?? props?.h_bond_acceptors,
        rotatable_bonds: props?.rotatable_bonds,
        lipinski_violations: props?.lipinski_violations ?? 0,
        veber_violations: props?.veber_violations ?? 0,
      };
    } catch {
      return { smiles, source: "seed" };
    }
  }

  // =========================================================================
  // CORE-ONLY: profile + ADMET tools
  // =========================================================================
  if (isCore) {

  // =========================================================================
  // Tool: get_molecule_profile (with UI)
  // =========================================================================
  registerAppTool(
    server,
    "get_molecule_profile",
    {
      title: "Get Molecule Profile",
      description:
        "Full molecular profile with ADMET predictions and regulatory compliance. This is the PRIMARY tool for profiling any molecule. Always returns complete data including ADMET — no follow-up tools needed.",
      inputSchema: {
        smiles: z.string().describe("SMILES string representing the molecular structure"),
      },
      outputSchema: z.object({
        smiles: z.string(),
        source: z.string().optional(),
        in_database: z.boolean().optional(),
        properties: z.record(z.string(), z.unknown()).nullish(),
        admet: z.record(z.string(), z.unknown()).nullish(),
        admet_available: z.boolean().optional(),
        compliance: z.record(z.string(), z.unknown()).nullish(),
        structural_alerts: z.record(z.string(), z.unknown()).nullish(),
        note: z.string().optional(),
      }).passthrough(),
      _meta: { ui: { resourceUri: UI_RESOURCES.moleculeViewer } },
    },
    async (args) => {
      try {
        const result = await callEngine("get_molecule_profile", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result as Record<string, unknown>,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // Tool: predict_admet (with UI)
  // =========================================================================
  registerAppTool(
    server,
    "predict_admet",
    {
      title: "Predict ADMET",
      description:
        "Predict toxicity and ADMET properties: cardiotoxicity, hepatotoxicity, nephrotoxicity, carcinogenicity, CYP450 inhibition (1A2/2C9/2C19/2D6/3A4 substrate + inhibitor), nuclear receptor activity (AR/ER/PR/GR/PPAR), stress response (p53, oxidative stress), absorption, distribution, metabolism, excretion. Returns per-model probabilities with severity categories. 40+ ML models from addie-models backend. Normally called automatically by get_molecule_profile; use directly for ADMET-only queries.",
      inputSchema: {
        smiles: z.string().describe("SMILES string of the molecule"),
        models: z
          .array(z.string())
          .optional()
          .describe("Specific ADMET models to run (default: all)"),
      },
      outputSchema: z.object({
        smiles: z.string(),
        source: z.string().nullish(),
        // ADMET categories from addie-models backend
        absorption: z.record(z.string(), z.number()).nullish(),
        distribution: z.record(z.string(), z.number()).nullish(),
        metabolism: z.record(z.string(), z.number()).nullish(),
        excretion: z.record(z.string(), z.number()).nullish(),
        toxicity: z.record(z.string(), z.unknown()).nullish(),
        nuclear_receptors: z.record(z.string(), z.number()).nullish(),
        stress_response: z.record(z.string(), z.number()).nullish(),
        properties: z.record(z.string(), z.number()).nullish(),
        raw_predictions: z.record(z.string(), z.unknown()).nullish(),
        // Legacy format support
        predictions: z.record(z.string(), z.unknown()).nullish(),
        summary: z.record(z.string(), z.unknown()).nullish(),
      }).passthrough(),
      _meta: { ui: { resourceUri: UI_RESOURCES.admetDashboard } },
    },
    async (args) => {
      try {
        const result = await callEngine("predict_admet", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result as Record<string, unknown>,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // Tool: predict_clinical_outcomes (NovoExpert v3 Phase I clearance)
  // =========================================================================
  server.registerTool(
    "predict_clinical_outcomes",
    {
      title: "Predict Clinical Outcomes",
      description:
        "Predict Phase I clinical trial clearance probability for a small molecule. " +
        "Automatically gathers all 63 required features by orchestrating chem-props, " +
        "faves-compliance, and addie-models in parallel, then calls NovoExpert v3. " +
        "Returns calibrated probability, SHAP explanations, and domain competence " +
        "assessment. Validated for CARDIOVASCULAR and mainstream compounds (AUROC " +
        "0.72-0.76). NOT valid for oncology, CNS, or infectious disease — check " +
        "competence_check before acting on predictions.",
      inputSchema: z.object({
        smiles: z.string().describe("SMILES string of the molecule to evaluate"),
        therapeutic_area: z
          .enum([
            "ONCOLOGY", "CARDIOVASCULAR", "CNS_NEURO", "INFECTIOUS",
            "METABOLIC", "IMMUNO_INFLAM", "RENAL_GU", "RESPIRATORY",
            "GI", "PAIN_ANALGESIA", "ENDOCRINE", "OPHTH_DERM", "OTHER", "UNKNOWN",
          ])
          .default("UNKNOWN")
          .describe("Therapeutic area for competence assessment"),
        target_type: z
          .enum([
            "SINGLE PROTEIN", "PROTEIN FAMILY", "PROTEIN COMPLEX",
            "PROTEIN COMPLEX GROUP", "NUCLEIC-ACID",
            "PROTEIN NUCLEIC-ACID COMPLEX", "ORGANISM", "CELL-LINE",
            "SMALL MOLECULE", "UNKNOWN",
          ])
          .default("UNKNOWN")
          .describe("Target type from ChEMBL"),
        action_type: z
          .enum([
            "INHIBITOR", "ANTAGONIST", "AGONIST", "BLOCKER", "ACTIVATOR",
            "MODULATOR", "PARTIAL AGONIST", "SUBSTRATE", "RELEASING AGENT",
            "UNKNOWN",
          ])
          .default("UNKNOWN")
          .describe("Mechanism of action"),
        top_k_shap: z
          .number()
          .int()
          .min(1)
          .max(63)
          .default(10)
          .describe("Number of top SHAP features to return"),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.clinicalOutcomes } },
    },
    async (args) => {
      try {
        const result = await callEngine("predict_clinical_outcomes", args) as Record<string, unknown>;
        if (result && !result.smiles && args.smiles) {
          result.smiles = args.smiles;
        }
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  } // end isCore — get_molecule_profile + predict_admet + predict_clinical_outcomes

  // =========================================================================
  // Tool: get_protein_structure (with UI) - Smart resolver [COMPUTE]
  if (isCompute) {
  // =========================================================================
  registerAppTool(
    server,
    "get_protein_structure",
    {
      title: "Get Protein Structure",
      description:
        "Smart protein structure resolver with interactive 3D visualization. Accepts: (1) PDB ID (e.g., '1M17'), (2) Protein name (e.g., 'EGFR', 'CDK2'), or (3) Amino acid sequence. First tries RCSB PDB (validated experimental structures), falls back to OpenFold3 prediction if needed.",
      inputSchema: {
        target: z.string().describe("Protein identifier: PDB ID (e.g., '1M17'), protein name (e.g., 'EGFR'), or UniProt ID (e.g., 'P00533')"),
        sequence: z.string().optional().describe("Optional amino acid sequence for prediction if PDB not found"),
        include_ligands: z.boolean().default(true).describe("Include bound ligands in the structure"),
      },
      outputSchema: z.object({
        target: z.string().nullish(),
        pdb_id: z.string().nullish(),
        source: z.string().nullish(),
        pdb_data: z.string().nullish(),
        pdb_url: z.string().nullish(),
        pdb_size: z.number().nullish(),
        name: z.string().nullish(),
        resolution: z.number().nullish(),
        method: z.string().nullish(),
        organism: z.string().nullish(),
        chains: z.array(z.string()).nullish(),
        ligands: z.array(z.string()).nullish(),
        sequence_length: z.number().nullish(),
        message: z.string().nullish(),
      }).passthrough(),
      _meta: { ui: { resourceUri: UI_RESOURCES.structureViewer } },
    },
    async (args) => {
      try {
        const result = await callEngine("get_protein_structure", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result as Record<string, unknown>,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // Tool: predict_structure (with UI)
  // =========================================================================
  registerAppTool(
    server,
    "predict_structure",
    {
      title: "Predict Protein Structure",
      description:
        "Predict 3D protein structure using OpenFold3 with interactive visualization. Supports proteins, DNA, RNA, and protein-ligand complexes.",
      inputSchema: {
        molecules: z.array(
          z.object({
            type: z.enum(["protein", "dna", "rna", "ligand"]).describe("Molecule type"),
            id: z.string().describe("Identifier for this molecule"),
            sequence: z.string().optional().describe("Amino acid or nucleotide sequence"),
            smiles: z.string().optional().describe("SMILES string (for ligands)"),
          })
        ).optional().describe("Molecules to predict structure for (required unless top-level `sequence` or `smiles` is provided)"),
        sequence: z.string().optional().describe(
          "Convenience shortcut: single protein/DNA/RNA sequence. Auto-wrapped into " +
          "molecules=[{type: inferred, id: 'target', sequence}]. Type inferred from " +
          "alphabet (ACGTU + N → nucleotide, otherwise protein)."
        ),
        smiles: z.string().optional().describe(
          "Convenience shortcut: single ligand SMILES. Auto-wrapped into " +
          "molecules=[{type: 'ligand', id: 'ligand_1', smiles}]."
        ),
        output_format: z.enum(["pdb", "cif"]).default("pdb"),
      },
      outputSchema: z.object({
        job_id: z.string().optional(),
        status: z.string().optional(),
        pdb_data: z.string().optional(),
        metrics: z.record(z.string(), z.unknown()).optional(),
      }).passthrough(),
      _meta: { ui: { resourceUri: UI_RESOURCES.structureViewer } },
    },
    async (args) => {
      try {
        const result = await callEngine("predict_structure", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result as Record<string, unknown>,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // Tool: get_structure_result (with UI)
  // =========================================================================
  registerAppTool(
    server,
    "get_structure_result",
    {
      title: "Get Structure Result (Deprecated)",
      description:
        "Deprecated — use get_job_status for structure prediction results. Retained for backward compatibility.",
      inputSchema: {
        job_id: z.string().describe("Job ID from predict_structure or other async operation"),
        service: z.enum(["openfold3", "gromacs", "novo-quantum", "lead-optimization", "auto"]).default("auto").describe("Service that created the job. Use 'auto' to detect automatically from job_id prefix."),
      },
      outputSchema: z.object({
        status: z.string(),
        pdb_data: z.string().optional(),
        metrics: z.record(z.string(), z.unknown()).optional(),
        confidence_scores: z.array(z.number()).optional(),
      }).passthrough(),
      _meta: { ui: { resourceUri: UI_RESOURCES.structureViewer } },
    },
    async (args) => {
      try {
        const result = await callEngine("get_structure_result", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result as Record<string, unknown>,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );
  } // end isCompute — structure tools

  // =========================================================================
  // CORE-ONLY: literature / patent / chembl / clinical-trials search
  // =========================================================================
  if (isCore) {

  // =========================================================================
  // Tool: search_literature (with UI)
  // =========================================================================
  registerAppTool(
    server,
    "search_literature",
    {
      title: "Search Literature",
      description:
        "Peer-reviewed published drug discovery research across 14,398 curated papers (Pinecone semantic search). Returns papers with titles, abstracts, authors, and relevance scores. Covers ADMET research, target validation, and medicinal chemistry.",
      inputSchema: {
        query: z.string().describe("Search query"),
        top_k: z.number().int().min(1).max(20).default(10).describe("Number of papers to return"),
        year_min: z.number().int().optional().describe("Minimum publication year filter"),
      },
      outputSchema: z.object({
        query: z.string().optional(),
        papers: z.array(z.record(z.string(), z.unknown())).optional(),
        total_results: z.number().optional(),
        tool_suggestions: z.array(z.unknown()).optional(),
      }).passthrough(),
      _meta: { ui: { resourceUri: UI_RESOURCES.researchExplorer } },
    },
    async (args) => {
      try {
        const result = await callEngine("search_literature", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result as Record<string, unknown>,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // Tool: search_patents (with UI)
  // =========================================================================
  registerAppTool(
    server,
    "search_patents",
    {
      title: "Search Patents",
      description:
        "Granted and pending USPTO pharmaceutical patent filings (2,416 documents, Pinecone semantic search). Returns patent titles, abstracts, applicants, and filing dates. Useful for IP landscape analysis.",
      inputSchema: {
        query: z.string().describe("Search query"),
        top_k: z.number().int().min(1).max(20).default(10).describe("Number of patents to return"),
        year_min: z.number().int().optional().describe("Minimum filing year filter"),
      },
      outputSchema: z.object({
        query: z.string().optional(),
        patents: z.array(z.record(z.string(), z.unknown())).optional(),
        total_results: z.number().optional(),
      }).passthrough(),
      _meta: { ui: { resourceUri: UI_RESOURCES.researchExplorer } },
    },
    async (args) => {
      try {
        const result = await callEngine("search_patents", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result as Record<string, unknown>,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // Tool: search_biorxiv (with UI)
  // =========================================================================
  registerAppTool(
    server,
    "search_biorxiv",
    {
      title: "Search bioRxiv",
      description:
        "Pre-publication preprints from bioRxiv and medRxiv, prior to peer review (live API query). Returns preprints with titles, abstracts, authors, DOIs, and publication dates. Cutting-edge research before formal publication.",
      inputSchema: {
        query: z.string().describe("Search query"),
        server: z.enum(["biorxiv", "medrxiv"]).default("biorxiv"),
        top_k: z.number().int().min(1).max(30).default(10),
        days_back: z.number().int().default(365),
      },
      outputSchema: z.object({
        query: z.string().optional(),
        server: z.string().optional(),
        date_range: z.union([z.string(), z.record(z.string(), z.unknown())]).optional(),
        preprints: z.array(z.record(z.string(), z.unknown())).optional(),
        total_results: z.number().optional(),
      }).passthrough(),
      _meta: { ui: { resourceUri: UI_RESOURCES.researchExplorer } },
    },
    async (args) => {
      try {
        const result = await callEngine("search_biorxiv", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result as Record<string, unknown>,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // Tool: search_chembl (with UI)
  // =========================================================================
  registerAppTool(
    server,
    "search_chembl",
    {
      title: "Search ChEMBL",
      description:
        "Measured bioactivity data from ChEMBL — 2.4M compounds with assay activities, targets, IC50/Ki values. Returns compound structures, target information, and activity values. Search by compound, target, or activity type.",
      inputSchema: {
        query: z.string().describe("Search query"),
        search_type: z.enum(["compound", "target", "activity"]).default("compound"),
        top_k: z.number().int().min(1).max(25).default(10),
      },
      outputSchema: z.object({
        query: z.string(),
        search_type: z.string().optional(),
        results: z.array(z.record(z.string(), z.unknown())).optional(),
        total_results: z.number().optional(),
        tool_suggestions: z.array(z.unknown()).optional(),
      }).passthrough(),
      _meta: { ui: { resourceUri: UI_RESOURCES.researchExplorer } },
    },
    async (args) => {
      try {
        const result = await callEngine("search_chembl", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result as Record<string, unknown>,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // Tool: search_clinical_trials (with UI)
  // =========================================================================
  registerAppTool(
    server,
    "search_clinical_trials",
    {
      title: "Search Clinical Trials",
      description:
        "Registered clinical trial records from ClinicalTrials.gov, including recruitment status and trial phase (live API query). " +
        "Accepts either `query` (general search) or `condition` (disease filter) or both. " +
        "At least one of query or condition must be provided.",
      inputSchema: {
        query: z.string().optional().describe("General search query (e.g., target + disease)"),
        condition: z.string().optional().describe("Disease/condition filter (e.g., 'glioblastoma')"),
        status: z.enum(["RECRUITING", "ACTIVE_NOT_RECRUITING", "COMPLETED", "TERMINATED", "ALL"]).default("ALL"),
        phase: z.enum(["PHASE1", "PHASE2", "PHASE3", "PHASE4", "ALL"]).default("ALL"),
        top_k: z.number().int().min(1).max(25).default(10),
      },
      outputSchema: z.object({
        query: z.string().optional(),
        status_filter: z.string().optional(),
        phase_filter: z.string().optional(),
        trials: z.array(z.record(z.string(), z.unknown())).optional(),
        total_results: z.number().optional(),
        total_count: z.number().optional(),
      }).passthrough(),
      _meta: { ui: { resourceUri: UI_RESOURCES.researchExplorer } },
    },
    async (args) => {
      try {
        const result = await callEngine("search_clinical_trials", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result as Record<string, unknown>,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  } // end isCore — search tools

  // =========================================================================
  // Tool: get_credit_usage (with UI)
  // =========================================================================
  registerAppTool(
    server,
    "get_credit_usage",
    {
      title: "Get Credit Usage",
      description:
        "Check your NovoMCP account credit balance, usage statistics, and research value realized. Shows included credits, overage costs, and tier information with an interactive dashboard. 1 credit = $1.",
      inputSchema: {
        type: z.enum(["summary", "detailed"]).optional().describe("Level of detail (default: summary)"),
      },
      outputSchema: z.object({
        org_name: z.string().optional(),
        tier: z.string().optional(),
        credits_available: z.number().optional(),
        credits_used_total: z.number().optional(),
        max_credits: z.number().optional(),
        usage_percent: z.number().optional(),
        credits_remaining_percent: z.number().optional(),
        status: z.string().optional(),
        alert: z.string().nullable().optional(),
        summary: z.string().optional(),
        // Hybrid billing model fields
        credits_included: z.number().optional().describe("Monthly included credits"),
        overage_rate: z.number().optional().describe("$/credit for overage"),
        overage_credits: z.number().optional().describe("Credits beyond included"),
        overage_cost: z.number().optional().describe("Dollar cost of overage"),
        period_start: z.string().optional().describe("Billing period start"),
        period_end: z.string().optional().describe("Billing period end"),
      }).passthrough(),
      _meta: { ui: { resourceUri: UI_RESOURCES.creditUsage } },
    },
    async (args) => {
      try {
        const result = await callEngine("get_credit_usage", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result as Record<string, unknown>,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // CORE-ONLY: compliance, optimization, connectors, target/lead workflow
  // =========================================================================
  if (isCore) {

  // =========================================================================
  // Tool: check_compliance (with UI) - FAVES Compliance Dashboard
  // =========================================================================
  registerAppTool(
    server,
    "check_compliance",
    {
      title: "Check Compliance",
      description:
        "Check regulatory and compliance status against DEA (controlled substances), FDA (drug approval), EPA (environmental/pesticide), EU REACH (chemical registration), CWC (chemical weapons convention), BTWC (biological weapons convention), OPCW (international chemical weapons treaty), and Australia Schedule. Context-dependent assessment keyed on intended_use + jurisdiction + therapeutic_area — returns PROCEED / STOP / CAUTION with risk factors, regulatory pathway, and jurisdiction-specific recommendations. Renders interactive FAVES dashboard.",
      inputSchema: {
        smiles: z.string().describe("SMILES string of the molecule"),
        context: z.object({
          intended_use: z.enum(["pharmaceutical", "research", "industrial", "agricultural", "cosmetic"]).describe("Primary intended use — routes to different regulatory frameworks: pharmaceutical (FDA IND/NDA, EMA), research (laboratory/academic, DEA Schedule I exceptions), industrial (REACH, OSHA), agricultural (EPA pesticide, FIFRA), cosmetic (FDA cosmetic, EU CPR)."),
          jurisdiction: z.enum(["US", "EU", "UK", "CA", "AU", "JP", "CN", "GLOBAL"]).describe("Regulatory jurisdiction. US=DEA+FDA+EPA, EU=EMA+EU REACH+EMCDDA, UK=MHRA, CA=Health Canada, AU=TGA+Australia Schedule, JP=PMDA, CN=NMPA, GLOBAL=CWC+BTWC+OPCW international treaties (chemical and biological weapons conventions)."),
          therapeutic_area: z.string().optional().describe("Therapeutic area if pharmaceutical (e.g., oncology, cardiology, neurology, immunology, infectious_disease, metabolic, rare_disease)."),
        }).describe("Context for compliance evaluation — determines which agencies and treaty frameworks apply (DEA, FDA, CWC, EPA, EU REACH, BTWC, Australia Schedule, OPCW)."),
      },
      outputSchema: z.object({
        smiles: z.string().nullish(),
        context: z.record(z.string(), z.unknown()).nullish(),
        base_compliance: z.record(z.string(), z.unknown()).nullish(),
        context_compliance: z.record(z.string(), z.unknown()).nullish(),
        overall_status: z.string().nullish(),
        recommendations: z.array(z.string()).nullish(),
        regulatory_pathway: z.record(z.string(), z.unknown()).nullish(),
        risk_assessment: z.record(z.string(), z.unknown()).nullish(),
      }).passthrough(),
      _meta: { ui: { resourceUri: UI_RESOURCES.favesDashboard } },
    },
    async (args) => {
      try {
        const result = await callEngine("check_compliance", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result as Record<string, unknown>,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // Tool: optimize_molecule (with proper schema)
  // =========================================================================
  registerAppTool(
    server,
    "optimize_molecule",
    {
      title: "Optimize Molecule (MolMIM AI)",
      description:
        "AI-powered molecular optimization using NVIDIA MolMIM. Generates variants by learned chemical transformations, optimized for QED, LogP, synthetic accessibility, and Tanimoto similarity to the input. Best for fine-tuning properties while staying structurally close to the input. NOT for scaffold hopping — use lead_optimization for that. Runs FAVES compliance on all generated variants.",
      inputSchema: z.object({
        smiles: z.string().describe("SMILES string of the molecule to optimize"),
        objectives: z.object({
          qed: z.number().min(0).max(1).optional().describe("Target QED score (0-1, higher is more drug-like)"),
          logp: z.number().optional().describe("Target LogP value (typically 1-5 for oral drugs)"),
          sa_score: z.number().min(1).max(10).optional().describe("Target synthetic accessibility (1-10, lower is easier to synthesize)"),
          similarity: z.number().min(0).max(1).optional().describe("Minimum Tanimoto similarity to input molecule (0-1)"),
        }).optional().describe("Optimization objectives. If not specified, defaults to QED=0.8, LogP=3.0"),
        num_variants: z.number().int().min(1).max(50).default(10).describe("Number of optimized variants to generate (max 50)"),
        exclude_controlled: z.boolean().default(true).describe("Exclude variants flagged by FAVES compliance"),
        similarity_range: z.object({
          min: z.number().min(0).max(1).optional().describe("Lower Tanimoto bound (default 0.3)"),
          max: z.number().min(0).max(1).optional().describe("Upper Tanimoto bound (default 0.85)"),
        }).optional().describe(
          "Optional Tanimoto similarity window (to seed) for filtering variants. Default 0.3-0.85. " +
          "Theo's tighter ranges: 0.80-0.85 for SAR-preserving tweaks, 0.75-0.85 for a patent-safe family."
        ),
        patent_risk_thresholds: z.object({
          low: z.number().min(0).max(1).optional().describe("Tc < low → 'novel'. Default 0.4."),
          high: z.number().min(0).max(1).optional().describe("Tc ≥ high → 'high' (same patent family). Default 0.7."),
        }).optional().describe(
          "Optional override for patent_risk classification breakpoints. Variants with Tc ≥ high are " +
          "tagged 'high' (same patent family), Tc between low and high are 'low' (patentable scaffold " +
          "hop), Tc < low are 'novel'. Defaults: {low: 0.4, high: 0.7}."
        ),
      }),
      outputSchema: z.object({}).passthrough(),
      _meta: { ui: { resourceUri: UI_RESOURCES.leadComparison } },
    },
    async (args) => {
      try {
        const result = await callEngine("optimize_molecule", args) as any;

        // Enrich with seed properties and normalize for lead-comparison viewer
        const seed = await enrichSeedProperties(callEngine, args.smiles as string);
        const variants = (result?.variants || result?.checked_variants || []).map((v: any) => ({
          smiles: v.smiles,
          source: "molmim" as const,
          modification: v.modification,
          mw: v.mw ?? v.molecular_weight,
          logp: v.logp ?? v.log_p,
          tpsa: v.tpsa,
          qed: v.qed,
          sa_score: v.sa_score ?? v.synthetic_accessibility,
          hbd: v.hbd ?? v.h_bond_donors,
          hba: v.hba ?? v.h_bond_acceptors,
          rotatable_bonds: v.rotatable_bonds,
          lipinski_violations: v.lipinski_violations,
          veber_violations: v.veber_violations,
          compliance_status: v.compliance?.status ?? (v.is_compliant === false ? "flagged" : v.is_compliant === true ? "clean" : undefined),
        }));

        const structured = {
          seed_smiles: args.smiles,
          seed,
          variants,
          optimization_type: "molmim",
        };

        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: structured,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // Tool: push_to_destination (with proper schema)
  // =========================================================================
  server.registerTool(
    "push_to_destination",
    {
      title: "Push To Destination",
      description:
        "Push data to connected destinations (Google Sheets, BigQuery, Snowflake, Databricks, Salesforce, PostgreSQL, Notion, Benchling, Supabase). " +
        "WORKFLOW: 1) Call with action='list_connections' to see available destinations. " +
        "2) Call with action='discover_schema' + connection_id to see the user's column headers/table schema. " +
        "3) Call with action='export' + connection_id + data + source_tool + target to write data. " +
        "CRITICAL: When exporting, 'data' MUST be a flat object (or array of objects) with ALL prediction values as named keys matching the user's column headers from discover_schema. " +
        "Example: if discover_schema shows columns [Hepatotoxicity, Cardiotoxicity, molecule_name, molecule_smiles], pass data={hepatotoxicity: 1.0, cardiotoxicity: 0.99, molecule_name: 'imatinib', smiles: 'Cc1ccc...'}. " +
        "Include EVERY value from the prediction results — do NOT pass just a SMILES string. The system will auto-fill missing predictions if needed, but always include what you have. " +
        "For Google Sheets: target is the spreadsheet name (e.g. 'Drug Candidates') — use 'Spreadsheet/Worksheet' to target a specific tab.",
      inputSchema: z.object({
        action: z.enum(["list_connections", "discover_schema", "preview_mapping", "export"]).describe("Action to perform"),
        connection_id: z.string().optional().describe("Connection ID (from list_connections). Required for discover_schema, preview_mapping, and export."),
        connector_type: z.enum(["snowflake", "google_sheets", "bigquery", "databricks", "salesforce", "postgresql", "notion", "benchling", "supabase"]).optional().describe("Filter by connector type (for list_connections only)"),
        data: z.union([
          z.record(z.string(), z.unknown()),
          z.array(z.record(z.string(), z.unknown())),
        ]).optional().describe("Data rows to export. Pass a flat object or array of objects with ALL columns as keys (e.g. {molecule_name: 'imatinib', smiles: 'CC...', hepatotoxicity: 1.0, cardiotoxicity: 0.99, ...}). Every key becomes a column."),
        source_tool: z.string().optional().describe("MCP tool that produced the data (e.g., get_molecule_profile, predict_admet). Required for preview_mapping and export."),
        write_mode: z.enum(["append", "replace", "upsert"]).default("append").describe("How to write data: append (add rows), replace (overwrite), upsert (update or insert)"),
        target: z.string().optional().describe("Target spreadsheet/table/database name. For Google Sheets: spreadsheet name or 'Spreadsheet Name/Worksheet'. For BigQuery: 'dataset.table'. For Notion: database name."),
        target_filter: z.string().optional().describe("Filter schemas by name pattern (for discover_schema action)"),
        mapping_id: z.string().optional().describe("Specific mapping configuration to use (optional, auto-resolves if omitted)"),
      }),
    },
    async (args) => {
      try {
        const result = await callEngine("push_to_destination", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // Tool: pull_from_source (bidirectional data pipeline)
  // =========================================================================
  server.registerTool(
    "pull_from_source",
    {
      title: "Pull from Source",
      description:
        "Pull compound data from a connected data warehouse (Snowflake, Databricks), run ADMET/compliance/optimization on each row, and optionally push enriched results back. " +
        "WORKFLOW: 1) action='preview' — inspect table: columns, row count, sample rows, auto-detect SMILES column. " +
        "2) action='pull' — read rows with constrained filters (returned to Claude for small datasets). " +
        "3) action='estimate_pipeline' — get credit cost breakdown + confirmation token for pull→process→push. " +
        "4) action='execute_pipeline' — run full pipeline using confirmation token (server-side batch processing). " +
        "Enterprise only — row limit: 10,000. " +
        "No raw SQL — only parameterized filters. Credit preview serves as 21 CFR Part 11 audit artifact.",
      inputSchema: z.object({
        action: z.enum(["preview", "pull", "estimate_pipeline", "execute_pipeline"]).describe("Action: preview (inspect table), pull (read rows), estimate_pipeline (cost estimate + token), execute_pipeline (run full pipeline)"),
        connection_id: z.string().optional().describe("Connection ID for the source data warehouse (from push_to_destination list_connections)"),
        table: z.string().optional().describe("Table name to read from (e.g., 'compound_library')"),
        columns: z.array(z.string()).optional().describe("Specific columns to select (default: all)"),
        filters: z.array(z.object({
          column: z.string().describe("Column name to filter on"),
          operator: z.enum(["=", "!=", ">", "<", ">=", "<=", "IN", "IS_NULL", "IS_NOT_NULL", "LIKE"]).describe("Filter operator"),
          value: z.unknown().optional().describe("Filter value (not needed for IS_NULL/IS_NOT_NULL)"),
        })).optional().describe("Parameterized filters (no raw SQL)"),
        limit: z.number().optional().describe("Max rows to return (subject to tier limits)"),
        smiles_column: z.string().optional().describe("Column containing SMILES strings (auto-detected if not specified)"),
        processing_tools: z.array(z.enum(["predict_admet", "check_compliance", "optimize_molecule", "calculate_properties"])).optional().describe("Tools to run on each row (for estimate_pipeline/execute_pipeline)"),
        destination_connection_id: z.string().optional().describe("Connection ID for push destination (optional)"),
        destination_table: z.string().optional().describe("Target table name for push destination (optional)"),
        confirmation_token: z.string().optional().describe("Token from estimate_pipeline to authorize execute_pipeline"),
      }),
    },
    async (args) => {
      try {
        const result = await callEngine("pull_from_source", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // Tool: target_discovery (omics-driven target identification)
  // =========================================================================
  server.registerTool(
    "target_discovery",
    {
      title: "Target Discovery",
      description:
        "Identify drug targets for a disease using precomputed omics data (108K target-disease associations). " +
        "Returns ranked targets with composite scores, suggested PDB IDs for docking, key variants, pathways, and tractability. " +
        "Start of the discovery funnel — use results to feed search_literature and lead_optimization.",
      inputSchema: z.object({
        disease: z.string().describe("Disease name or EFO ID (e.g., 'lung cancer' or 'EFO_0001071')"),
        tissue: z.string().optional().describe("Optional tissue filter (e.g., 'lung')"),
        min_evidence: z.number().min(0).max(1).default(0.5).describe("Minimum overall evidence score (0-1)"),
        max_targets: z.number().int().min(1).max(50).default(10).describe("Maximum number of targets to return"),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.targetDiscovery } },
    },
    async (args) => {
      try {
        const result = await callEngine("target_discovery", args) as Record<string, unknown>;
        if (result && !result.disease && args.disease) {
          result.disease = args.disease;
        }
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // Tool: validate_target (adversarial target validation)
  // =========================================================================
  server.registerTool(
    "validate_target",
    {
      title: "Adversarial Target Validation",
      description:
        "Stress-test a drug target hypothesis against clinical, literature, and bioactivity evidence. " +
        "Runs a 'prosecutor' search for contradicting evidence (failed trials, resistance, toxicity) " +
        "alongside supporting evidence. Returns confidence score with tiered weighting: " +
        "clinical trials (3x), ChEMBL bioactivity (2x), literature (1x), omics (1x). " +
        "Use after target_discovery to validate before committing compute credits.",
      inputSchema: z.object({
        target: z.string().describe("Gene symbol (e.g. 'EGFR', 'BRAF') or protein name"),
        disease: z.string().describe("Disease name (e.g. 'glioblastoma') or EFO/MONDO ID"),
        skip_cache: z.boolean().optional().default(false).describe(
          "Bypass the 1-hour result cache for this (target, disease) key. Use when re-running " +
          "after a server-side tuning change so the test exercises the fresh evidence pipeline. " +
          "Responses always carry `_cached: true` when served from cache, so callers can detect " +
          "stale results without this flag."
        ),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.validateTarget } },
    },
    async (args) => {
      try {
        const result = await callEngine("validate_target", args);
        const resultObj = (result && typeof result === "object") ? result as Record<string, unknown> : {};
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: resultObj,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // Tool: lead_optimization (scaffold hopping + enrichment)
  // =========================================================================
  registerAppTool(
    server,
    "lead_optimization",
    {
      title: "Lead Optimization (Scaffold Hopping)",
      description:
        "Structural lead optimization via RDKit scaffold hopping — swaps ring systems (benzene↔pyridine, cyclohexane↔piperidine, etc.) " +
        "to generate novel chemical series with improved metabolic or selectivity profiles. " +
        "This is the funnel step 6 tool for the discovery pipeline. Use when the user asks for scaffold hopping, structural diversification, " +
        "or lead optimization. Enriches variants with chem-props + ADMET and filters via FAVES compliance. " +
        "For AI-based property optimization (staying structurally similar), use optimize_molecule instead.",
      inputSchema: z.object({
        smiles: z.string().describe("SMILES of the lead compound to optimize"),
        target_properties: z.object({
          qed: z.number().optional().describe("Target QED score"),
          logp: z.number().optional().describe("Target LogP"),
          mw: z.number().optional().describe("Target molecular weight"),
        }).optional().describe("Property targets for optimization"),
        num_variants: z.number().int().min(1).max(50).default(5).describe("Number of variants to generate (max 50)"),
        max_variants: z.number().int().min(1).max(50).optional().describe("Legacy alias for num_variants. Both accepted by the executor; num_variants wins if both are passed. Declared on the gateway so clients using the documented alias aren't silently Zod-stripped."),
        similarity_threshold: z.number().min(0).max(1).default(0.3).describe("Minimum Tanimoto similarity to lead"),
        optimization_type: z
          .enum(["scaffold_hop", "property_directed"])
          .default("scaffold_hop")
          .describe(
            "Optimization strategy. scaffold_hop swaps ring systems for diversity; " +
            "property_directed steers variants toward target_properties while staying " +
            "closer to the seed scaffold."
          ),
        similarity_range: z.object({
          min: z.number().min(0).max(1).optional().describe("Lower Tanimoto bound (default 0.3)"),
          max: z.number().min(0).max(1).optional().describe("Upper Tanimoto bound (default 0.85)"),
        }).optional().describe(
          "Optional Tanimoto window (to seed) for filtering variants — variants outside the " +
          "range are dropped before enrichment. Theo's guidance: 0.80-0.85 preserves SAR around " +
          "a lead; 0.75-0.85 for a patent-safe family. Default 0.3-0.85 is broad enough to not " +
          "filter anything by default while still excluding identical matches."
        ),
        patent_risk_thresholds: z.object({
          low: z.number().min(0).max(1).optional().describe("Tc < low → 'novel'. Default 0.4."),
          high: z.number().min(0).max(1).optional().describe("Tc ≥ high → 'high'. Default 0.7."),
        }).optional().describe(
          "Optional override for patent_risk breakpoints. Variants with Tc ≥ high are tagged " +
          "'high' (same patent family), Tc between low and high are 'low' (patentable scaffold " +
          "hop), Tc < low are 'novel' (verify pharmacophore). Defaults: {low: 0.4, high: 0.7}."
        ),
      }),
      outputSchema: z.object({}).passthrough(),
      _meta: { ui: { resourceUri: UI_RESOURCES.leadComparison } },
    },
    async (args) => {
      try {
        const result = await callEngine("lead_optimization", args) as any;

        // Enrich with seed properties and normalize for lead-comparison viewer
        const seed = await enrichSeedProperties(callEngine, args.smiles as string);
        const variants = (result?.variants || []).map((v: any) => ({
          smiles: v.smiles,
          source: "scaffold_hop" as const,
          modification: v.modification,
          mw: v.mw,
          logp: v.logp,
          tpsa: v.tpsa,
          qed: v.qed,
          sa_score: v.sa_score,
          hbd: v.hbd ?? v.h_bond_donors,
          hba: v.hba ?? v.h_bond_acceptors,
          rotatable_bonds: v.rotatable_bonds,
          lipinski_violations: v.lipinski_violations,
          veber_violations: v.veber_violations,
          tanimoto_to_seed: v.tanimoto_to_seed,
          patent_risk: v.patent_risk,
          patent_note: v.patent_note,
          compliance_status: v.compliance_status,
        }));

        const structured = {
          seed_smiles: args.smiles,
          seed,
          variants,
          optimization_type: "scaffold_hop",
        };

        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: structured,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  } // end isCore — compliance, optimize, connectors, target/lead

  // =========================================================================
  // COMPUTE-ONLY TOOLS: docking, MD, QM, NNP, dynamics
  // =========================================================================
  if (isCompute) {

  // Tool: dock_molecules (two-phase: estimate → execute, with docking viewer UI)
  registerAppTool(
    server,
    "dock_molecules",
    {
      title: "Dock Molecules",
      description:
        "Dock molecules against a protein target using AutoDock-GPU. Two-phase workflow: " +
        "Phase 1 (no confirmation_token): returns cost estimate (10 base + 5 per molecule) and a confirmation token. Report the cost to the user in 1 short line, then re-invoke this tool with the confirmation_token in the same turn. " +
        "Phase 2 (with confirmation_token): executes docking, returns binding affinities, poses, and contacts. " +
        "3-10 seconds per molecule, max 100 molecules. Use after lead optimization to score binding affinity. " +
        "Server-side credit enforcement (pre-flight check in execute()) hard-blocks overspend, so the two-phase flow is for cost transparency, not authorization.",
      inputSchema: z.object({
        smiles_list: z.array(z.string()).max(100).describe("SMILES strings to dock (max 100)"),
        protein_pdb_id: z.string().describe("PDB ID of target protein (e.g., '6OIM'). Use suggested_pdb_id from target_discovery."),
        exhaustiveness: z.number().int().min(8).max(32).default(16).describe("Search exhaustiveness (8-32, higher = more accurate but slower)"),
        num_modes: z.number().int().min(1).max(20).default(9).describe("Number of binding modes to generate per molecule"),
        confirmation_token: z.string().optional().describe("Token from Phase 1 to confirm and execute docking. Omit on first call to get cost estimate."),
        protonation_ph: z.number().min(1).max(14).default(7.4).describe("pH for ligand and receptor protonation state (default 7.4 = blood plasma / physiological). Affects ionizable groups: amines (protonated <pKa), carboxylates (deprotonated >pKa), imidazoles, phenols, phosphates. Compartment presets: 1-3 stomach/gastric, 5-7 intestinal, 4.5 lysosomal, 6.5 tumor microenvironment, 7.4 blood/plasma/cytosol."),
        funnel_id: FUNNEL_ID_FIELD,
        reference_ligand_smiles: z.string().optional().describe(
          "SMILES of a co-crystallized native ligand to dock alongside the candidates as a positive control. " +
          "When provided, each docking response carries reference_affinity_kcal + delta_vs_reference_kcal per pose " +
          "and reference_interactions (PLIP contacts) for direct side-by-side comparison. If omitted, the docker " +
          "auto-extracts the largest non-buffer HETATM from the target PDB as the reference. Pass an empty string " +
          "or set enable_reference_docking=false to skip reference docking entirely."
        ),
        enable_reference_docking: z.boolean().optional().default(true).describe(
          "Toggle the reference-ligand co-docking pass. Default true. Set false for fast iteration when the " +
          "delta-vs-reference numbers aren't needed (saves ~one extra dock per request)."
        ),
      }),
      outputSchema: z.object({}).passthrough(),
      _meta: { ui: { resourceUri: UI_RESOURCES.dockingViewer } },
    },
    async (args) => {
      try {
        const result = await callEngine("dock_molecules", args) as Record<string, unknown>;

        // Enrich completed docking results with protein metadata for the viewer
        if (result?.phase === "completed" && args.protein_pdb_id) {
          try {
            const proteinMeta = await callEngine("get_protein_structure", {
              target: args.protein_pdb_id,
              include_ligands: true,
            }) as Record<string, unknown>;
            if (proteinMeta) {
              result.protein_name = proteinMeta.name;
              result.resolution = proteinMeta.resolution;
              result.method = proteinMeta.method;
              result.organism = proteinMeta.organism;
              result.chains = proteinMeta.chains;
              result.ligands = proteinMeta.ligands;
              // Use backend's binding_site_source if available, fall back to ligand-based inference
              if (!result.binding_site_source) {
                result.binding_site_source = (proteinMeta.ligands as unknown[] | undefined)?.length ? "known" : "predicted";
              }
            }
          } catch {
            // Protein metadata enrichment is best-effort
          }
          result.exhaustiveness = args.exhaustiveness ?? 16;
          result.num_modes = args.num_modes ?? 9;
        }

        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result as Record<string, unknown>,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // Tool: parameterize_metal (QM→FF bridge via MCPB.py)
  // =========================================================================
  server.registerTool(
    "parameterize_metal",
    {
      title: "Parameterize Metal Site",
      description:
        "Two-phase metal parameterization via MCPB.py. " +
        "Phase 1 (omit qm_log_content): extracts coordination fragment, generates Gaussian .com files, returns confirmation_token. User runs Gaussian externally. " +
        "Phase 2 (qm_log_content OR qm_file_id + confirmation_token): processes .log → .frcmod, .prep, GROMACS .top/.gro. " +
        "For large QM logs, use generate_upload_url first, then pass qm_file_id instead of inline content. " +
        "Use audit_system first to identify the metal site. CPU-only, 1-2 min per phase.",
      inputSchema: z.object({
        pdb_id: z.string().describe("PDB ID of the metalloprotein (e.g., '1CA2', '1E67')"),
        metal_resid: z.number().int().describe("Residue number of the metal to parameterize"),
        qm_log_content: z.string().optional().describe("Phase 2: inline .log contents (single combined file). For large files use the file_id variants below."),
        qm_file_id: z.string().optional().describe("Phase 2: file ID from generate_upload_url for a single combined log (e.g. a Gaussian .fchk that holds both Hessian and ESP sections). For separate Hessian + ESP runs use hessian_file_id + esp_file_id."),
        hessian_file_id: z.string().optional().describe(
          "Phase 2 (Gaussian two-log path): file_id of the .log from Phase 1's small_fc.com (freq → " +
          "Hessian / force-constants). Pair with esp_file_id below for proper Seminario + RESP " +
          "decomposition. Use this when running Gaussian on the separate .com files Phase 1 produced."
        ),
        esp_file_id: z.string().optional().describe(
          "Phase 2 (Gaussian two-log path): file_id of the .log from Phase 1's large_mk.com " +
          "(Pop(MK,ReadRadii) → ESP charges). Required by MCPB.py's resp_fitting step. Pair with " +
          "hessian_file_id above."
        ),
        confirmation_token: z.string().optional().describe("Phase 2 only: token from Phase 1"),
        qm_software: z.enum(["gaussian", "orca"]).default("gaussian").describe("QM engine"),
        charge: z.number().int().default(0).describe("Total charge of the QM fragment"),
        multiplicity: z.number().int().default(1).describe("Spin multiplicity"),
      }),
    },
    async (args) => {
      try {
        const result = await callEngine("parameterize_metal", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // Tool: run_molecular_dynamics (GROMACS GPU simulation — with MD results UI)
  // =========================================================================
  registerAppTool(
    server,
    "run_molecular_dynamics",
    {
      title: "Run Molecular Dynamics",
      description:
        "Run GPU molecular dynamics simulation using GROMACS. " +
        "Short simulations (≤10ns, ~20 min) poll and return results directly with " +
        "RMSD convergence charts, equilibration analysis, and trajectory data. " +
        "Long simulations (>10ns) return a job ID — use get_job_status to check progress.",
      inputSchema: {
        smiles: z.string().describe("SMILES of the ligand molecule"),
        pdb_id: z.string().optional().describe("PDB ID for protein-ligand simulation (omit for ligand-only)"),
        duration_ns: z.number().min(0.1).max(100).default(1).describe("Simulation duration in nanoseconds"),
        temperature: z.number().min(200).max(400).default(300).describe("Simulation temperature in Kelvin"),
        funnel_id: z.string().optional().describe(
          "Optional conversation-level funnel ID. Stored alongside the async job so " +
          "get_funnel_context can return it and a resuming session can rehydrate the full " +
          "audit trail via get_funnel_audit."
        ),
        intent: z.enum(["smoke_test", "equilibration_only", "pose_stability", "mm_gbsa"]).optional().describe(
          "Scientific intent of the simulation. Drives the use-case-specific scientific_adequacy " +
          "grade in the result's three-layer quality_report. Choose 'smoke_test' for plumbing " +
          "checks, 'equilibration_only' to validate system setup, 'pose_stability' for ligand-pocket " +
          "binding-pose claims (≥10ns standard), 'mm_gbsa' for binding-energy decomposition " +
                    "relative-free-energy window (≥2ns standard). Omit to grade all known intents."
        ),
        adaptive_equilibration: z.boolean().optional().default(false).describe(
          "Opt-in: replace the fixed 100 ps NPT stage with an adaptive loop that extends NPT in " +
          "100 ps blocks (initial 50 ps + extensions up to a 1 ns cap) until water density plateau " +
          "is detected via first-half vs second-half mean comparison. Adds 0–1 ns to total runtime. " +
          "Recommended for protein-ligand complexes where the 100 ps fixed window often leaves " +
          "density still drifting; the adaptive log appears in result.equilibration.npt_adaptive."
        ),
      },
      outputSchema: z.object({
        job_id: z.string().optional(),
        status: z.string().optional(),
        service: z.string().optional(),
        progress: z.number().optional(),
        completed: z.boolean().optional(),
        results: z.record(z.string(), z.unknown()).optional(),
        analysis: z.record(z.string(), z.unknown()).optional(),
        error: z.string().optional(),
      }).passthrough(),
      _meta: { ui: { resourceUri: UI_RESOURCES.mdResults } },
    },
    async (args) => {
      try {
        const result = await callEngine("run_molecular_dynamics", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result as Record<string, unknown>,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );
  } // end isCompute — dock_molecules, parameterize_metal, run_molecular_dynamics

  // =========================================================================
  // BOTH SERVERS: audit_system (free pre-flight target classification)
  //
  // Registered on both Novo and Novo Compute. It's free (0 credits),
  // read-only, and useful for target evaluation even without a Compute
  // subscription — a Core user doing target discovery should know "this
  // is a membrane protein" before deciding whether to upgrade for MD.
  // =========================================================================
  server.registerTool(
    "audit_system",
    {
      title: "Audit System (Free)",
      description:
        "Pre-flight check for molecular dynamics: classify a protein structure without running MD. " +
        "Returns membrane detection, metal sites with coordination and functional role, heme/Fe-S clusters, " +
        "and a routing verdict. Use BEFORE run_molecular_dynamics — if would_route_to='refused', " +
        "MD will refuse with the same reason and the submission is wasted. Free (0 credits), ~5 seconds.",
      inputSchema: z.object({
        pdb_id: z.string().optional().describe("PDB ID (e.g. '1CA2', '2RH1'). Fetched from RCSB."),
        pdb_content: z.string().optional().describe("Raw PDB file contents (for structures not in RCSB)."),
      }).refine((d) => d.pdb_id || d.pdb_content, {
        message: "Provide either pdb_id or pdb_content",
      }),
    },
    async (args) => {
      try {
        const result = await callEngine("audit_system", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // CORE-ONLY: pharmacogenomic stratification
  // =========================================================================
  if (isCore) {

  // =========================================================================
  // Tool: stratify_patients (pharmacogenomic stratification)
  // =========================================================================
  server.registerTool(
    "stratify_patients",
    {
      title: "Stratify Patients",
      description:
        "Pharmacogenomic patient stratification using precomputed omics data. " +
        "Analyzes CYP enzyme metabolism (from ADMET results), population pharmacogenomics (56 pharmacogenes), " +
        "and resistance variants (135K ClinVar pathogenic variants). Returns clinical viability summary. " +
        "Use after docking/ADMET to assess which patient populations will respond to a candidate.",
      inputSchema: z.object({
        smiles: z.string().describe("SMILES of the candidate molecule"),
        gene_symbol: z.string().optional().describe("Target gene symbol — validated against the HGNC registry (44K official symbols + 58K aliases accepted). Examples: EGFR, BRAF, KRAS, CYP2D6, HER2 (auto-resolved to ERBB2). Unknown symbols return a structured error with suggestions (for aliases/previous symbols) or an HGNC search URL (for truly unknown genes). Valid HGNC genes outside the 56-pharmacogene panel return clinical_viability='not_applicable' instead of silent failure. Mutually exclusive alias of target_gene below — pass either, not both."),
        target_gene: z.string().optional().describe(
          "Preferred name in the Novo AG pipeline. Same semantics as gene_symbol — the executor " +
          "accepts either. Pass either, not both. Use this name when chaining from target_discovery " +
          "(which emits target_gene) so the value flows without renaming."
        ),
        admet_results: z.record(z.string(), z.unknown()).optional().describe("ADMET prediction results (for CYP substrate analysis). If omitted, the tool attempts to retrieve from funnel context."),
        indication: z.string().optional().describe(
          "Disease indication for context. Used to bias the pharmacogenomic summary toward " +
          "indication-relevant population data when multiple ethnic frequencies are available."
        ),
        include_pgx: z.boolean().optional().default(true).describe(
          "Include pharmacogenomic analysis (allele frequencies, CYP metabolism phenotype " +
          "distribution across populations). Default true. Set false to skip the omics layer " +
          "for a faster clinical-viability-only response."
        ),
        include_biomarkers: z.boolean().optional().default(true).describe(
          "Include resistance mutation analysis from the 135K ClinVar pathogenic variant set. " +
          "Default true. Set false to skip the resistance layer."
        ),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.stratifyPatients } },
    },
    async (args) => {
      try {
        const result = await callEngine("stratify_patients", args) as Record<string, unknown>;
        if (result) {
          if (!result.smiles && args.smiles) result.smiles = args.smiles;
          if (!result.target_gene && args.gene_symbol) result.target_gene = args.gene_symbol;
        }
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  } // end isCore — stratify_patients

  // =========================================================================
  // COMPUTE-ONLY: async job management (jobs are all Compute workloads —
  // MD, docking, structure prediction, QM, lead optimization)
  // =========================================================================
  if (isCompute) {

  // =========================================================================
  // Tool: list_jobs (with UI)
  // =========================================================================
  registerAppTool(
    server,
    "list_jobs",
    {
      title: "List Pipeline Jobs",
      description:
        "List async pipeline jobs (MD simulations, docking batches, structure predictions, lead optimizations). " +
        "Shows job IDs, status, progress, and timestamps. Use to check what jobs are running or recently completed.",
      inputSchema: {
        status: z.enum(["submitted", "running", "completed", "failed"]).optional().describe("Filter by job status. Omit to return all jobs."),
        service: z.enum(["gromacs-md", "autodock-gpu", "openfold3", "lead-optimization"]).optional().describe("Filter by service. Omit to return all."),
        limit: z.number().int().min(1).max(100).default(15).describe("Max jobs to return (default 15)"),
      },
      // No outputSchema — see get_job_status for rationale.
      _meta: { ui: { resourceUri: UI_RESOURCES.jobs } },
    },
    async (args) => {
      try {
        const result = await callEngine("list_jobs", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result as Record<string, unknown>,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // Tool: get_job_status (with MD results UI)
  // =========================================================================
  // get_job_status intentionally has NO `_meta.ui.resourceUri`. We tried
  // wiring it to md-results through multiple rounds of fixes (schema drops,
  // payload slimming, deep-search dispatchers, content-text upgrade paths)
  // and the viewer's `toolResult` arrived null every time — the host doesn't
  // reliably forward polymorphic async-poll responses to a UI App regardless
  // of what we return. Claude's own text response already renders the
  // completion analysis (RMSF, PCA, ensemble summary) better than we could
  // inline anyway, and the submission-phase viewers (run_conformer_search,
  // generate_dynamics, find_transition_state) still work for the queued card.
  // See release-activity.md 2026-04-21 "Async-poll viewer pullback" for the
  // full diagnostic trail.
  server.registerTool(
    "get_job_status",
    {
      title: "Get Job Status",
      description:
        "Check the status of an async pipeline job (MD simulation, docking batch, structure prediction, lead optimization, conformer search, NEB transition state, dynamics ensemble). Returns status, progress, and — on completion — the service-specific result payload including RMSD/RMSF for MD, binding affinities for docking. Present completed results clearly as a summary table.",
      inputSchema: {
        job_id: z.string().describe("Job ID (e.g., gro_xxx, dock_batch_xxx, of3_xxx, qm_xxx, af_xxx, neb_xxx)"),
        service: z
          .enum(["openfold3", "gromacs", "novo-quantum", "lead-optimization", "autodock-gpu", "auto"])
          .optional()
          .describe(
            "Override prefix-based routing. Default 'auto' detects the service from the job_id prefix. " +
            "Use this only when you need to force a specific service path (e.g., debugging a routing mismatch)."
          ),
        include_ensemble: z.boolean().optional().describe(
          "AlphaFlow (af_*) only: include the full multi-model PDB ensemble inline in the response. " +
          "Default false because the inline payload (~130 KB for a 50-frame run) typically exceeds " +
          "the MCP inline tool-result soft limit and gets spilled to a file by the client. The slim " +
          "default returns frame_count + size_bytes + preview + a hint at the /results/<job_id> " +
          "endpoint. Pass true only if you specifically need the bytes inline (e.g. piping to a " +
          "downstream tool)."
        ),
      },
    },
    async (args) => {
      try {
        const result = await callEngine("get_job_status", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result as Record<string, unknown>,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // Tool: cancel_job — abort a running/queued job execution
  // =========================================================================
  // cancel_job pairs with get_job_status. novomcp's _execute_cancel_job
  // looks up the captured execution_id, issues ARM Jobs.stop, and PATCHes
  // status='cancelling'; the executor's SIGTERM trap finalizes 'cancelled'
  // with whatever partial progress + checkpoints exist.
  // other compute services will return an explicit "not supported" error
  // until their executors implement clean SIGTERM cleanup.
  server.registerTool(
    "cancel_job",
    {
      title: "Cancel Async Job",
      description:
        "Stop a running or queued async job before it completes. Issues a k8s Job `stop` against the captured execution, marks the SQL row 'cancelling', and the executor's SIGTERM handler writes the final 'cancelled' state with whatever partial checkpoints exist. Useful when (a) the user submitted by mistake, (b) wrong inputs, (c) the run is over budget. Cancelling a completed/failed/cancelled job is a no-op.",
      inputSchema: {
        job_id: z.string().describe("Job ID from the original submission (e.g. gro_20260514-...)"),
        reason: z.string().optional().describe("Optional human-readable reason. Persisted in error_message for audit."),
      },
    },
    async (args) => {
      try {
        const result = await callEngine("cancel_job", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result as Record<string, unknown>,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  } // end isCompute — list_jobs + get_job_status + cancel_job

  // =========================================================================
  // Tool: get_pipeline_audit (with UI) — BOTH servers (audit is cross-tier)
  // =========================================================================
  registerAppTool(
    server,
    "get_pipeline_audit",
    {
      title: "Get Pipeline Audit Log",
      description:
        "Retrieve the per-molecule audit trail for a completed pipeline execution. " +
        "Shows what happened to each molecule: SMILES validation, standardization, " +
        "per-tool results (ADMET flags, compliance status, properties), disposition " +
        "(included/excluded), and exclusion reasons. Use for GxP compliance documentation.",
      inputSchema: {
        pipeline_id: z.string().describe("Pipeline ID (e.g., pipe_abc123)"),
      },
      outputSchema: z.object({
        pipeline_id: z.string().optional(),
        source_table: z.string().optional(),
        rows_pulled: z.number().optional(),
        rows_processed: z.number().optional(),
        processing_tools: z.array(z.string()).optional(),
        status: z.string().optional(),
        audit_summary: z.record(z.string(), z.unknown()).optional(),
        molecule_audit_log: z.array(z.record(z.string(), z.unknown())).optional(),
      }).passthrough(),
      _meta: { ui: { resourceUri: UI_RESOURCES.pipelineAudit } },
    },
    async (args) => {
      try {
        const result = await callEngine("get_pipeline_audit", args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result as Record<string, unknown>,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );
  // =========================================================================
  // Pass-through tools (proxy → novomcp) — split by server mode
  // =========================================================================
  // Tools that benefit from an inline viewer set `viewer` to the resource
  // URI. Pass-through proxies that also wire a viewer return both
  // structuredContent (for the viewer) and the text block (for Claude).
  // Tools without `viewer` remain text-only.
  interface PassThruTool {
    name: string;
    description: string;
    viewer?: string;
    // Optional typed input schema. Without this, tools use a permissive
    // `z.object({}).passthrough()` which tells Claude nothing about
    // parameter shapes. For tools that accept arrays or complex objects
    // (smiles_list on batch_profile / screen_library, property-range
    // filters on filter_molecules), Claude will sometimes pass a
    // stringified version instead of a real array — the backend then
    // iterates character-by-character. Supplying a typed schema fixes
    // this at the MCP protocol layer.
    inputSchema?: z.ZodRawShape;
  }

  // Core-only tools: molecule-level profiling and RDKit-side properties.
  // 3D conformer descriptors belong to Compute (structural computation).
  const corePassThruTools: PassThruTool[] = [
    {
      name: "get_molecule_info",
      description: "Get basic molecular properties",
      viewer: UI_RESOURCES.moleculeViewer,
      inputSchema: {
        smiles: z.string().describe("SMILES string of the molecule"),
      },
    },
    { name: "get_platform_info", description: "Get NovoMCP platform information" },
    {
      name: "search_similar",
      description: "Find molecules similar to a query SMILES by DiskANN vector similarity against the 122M compound database. Sub-second results.",
      viewer: UI_RESOURCES.resultsTable,
      inputSchema: {
        smiles: z.string().describe("Query SMILES to search against"),
        top_k: z.number().int().min(1).max(100).default(20).describe("Maximum number of similar molecules to return"),
        threshold: z.number().min(0).max(1).optional().describe("Minimum Tanimoto similarity (0-1). Omit for no floor."),
      },
    },
    {
      name: "filter_molecules",
      description: "Filter the 122M molecule database by property ranges (MW, LogP, TPSA, QED, HBD, HBA, etc.).",
      viewer: UI_RESOURCES.resultsTable,
      inputSchema: {
        mw_min: z.number().optional().describe("Minimum molecular weight"),
        mw_max: z.number().optional().describe("Maximum molecular weight"),
        logp_min: z.number().optional().describe("Minimum LogP"),
        logp_max: z.number().optional().describe("Maximum LogP"),
        tpsa_min: z.number().optional().describe("Minimum TPSA"),
        tpsa_max: z.number().optional().describe("Maximum TPSA"),
        qed_min: z.number().min(0).max(1).optional().describe("Minimum QED drug-likeness score"),
        hbd_max: z.number().int().optional().describe("Maximum hydrogen bond donors"),
        hba_max: z.number().int().optional().describe("Maximum hydrogen bond acceptors"),
        limit: z.number().int().min(1).max(100).default(20).describe("Max results to return"),
      },
    },
    {
      name: "batch_profile",
      description: "Batch version of get_molecule_profile: ADMET (toxicity incl. hepatotoxicity, CYP metabolism, nuclear receptors, stress response), FAVES compliance, and properties for up to 100 molecules in one call. Pre-computed for known molecules; novel ones get on-the-fly properties + ML ADMET. Set include_admet=false for faster, cheaper properties-only screening. smiles_list MUST be a JSON array of strings, not a comma-separated string.",
      viewer: UI_RESOURCES.resultsTable,
      inputSchema: {
        smiles_list: z.array(z.string()).min(1).max(100).describe("Array of SMILES strings (up to 100 molecules)"),
        include_admet: z.boolean().default(true).describe("Include ML ADMET predictions (toxicity, metabolism, etc.) for novel molecules. Default true. Set false for faster, lower-credit properties-only profiling."),
      },
    },
    {
      name: "calculate_properties",
      description: "Calculate RDKit properties",
      viewer: UI_RESOURCES.moleculeViewer,
      inputSchema: {
        smiles: z.string().describe("SMILES string of the molecule"),
      },
    },
    {
      name: "screen_library",
      description: "Screen a library of up to 1,000 molecules for ADMET flags, FAVES compliance, and optional context-dependent compliance. smiles_list MUST be a JSON array of strings, not a comma-separated string.",
      viewer: UI_RESOURCES.resultsTable,
      inputSchema: {
        smiles_list: z.array(z.string()).min(1).max(1000).describe("Array of SMILES strings (up to 1000 molecules)"),
        context: z.object({
          intended_use: z.string().optional(),
          jurisdiction: z.string().optional(),
          therapeutic_area: z.string().optional(),
        }).passthrough().optional().describe("Context for context-dependent compliance screening"),
      },
    },
    { name: "save_funnel_context", description: "Save discovery funnel context for session resumption" },
    { name: "get_funnel_context", description: "Retrieve saved funnel context by job ID" },
    { name: "generate_upload_url", description: "Generate a signed URL for uploading large files (QM logs, PDB, libraries). Returns file_id for downstream tools. Free." },
    { name: "get_file_status", description: "Check upload status, linked tool calls, and processing results for a file." },
    { name: "list_files", description: "List uploaded files with optional type/status filters." },
    {
      name: "explore_chemical_space",
      description: "Explore 122M molecules by chemical region. Returns top Level 1 clusters matching your target profile with rich statistics (MW, QED, toxicity, GI/BBB, PAINS, scaffolds). Start here, then drill_into_cluster to narrow.",
      viewer: UI_RESOURCES.clusterExplorer,
      inputSchema: {
        query: z.string().optional().describe("Natural language description of target profile"),
        smiles: z.string().optional().describe("Reference SMILES for structural similarity"),
        top_k: z.number().int().min(1).max(20).default(5).describe("Number of regions to return"),
      },
    },
    {
      name: "drill_into_cluster",
      description: "Drill into a chemical cluster to see sub-clusters. Narrows from ~1.2M to ~12K to ~100 molecules per level. Use after explore_chemical_space.",
      viewer: UI_RESOURCES.clusterExplorer,
      inputSchema: {
        cluster_id: z.string().describe("Cluster ID from explore_chemical_space or a previous drill"),
        top_k: z.number().int().min(1).max(20).default(5).describe("Number of child clusters to return"),
        sort_by: z.enum(["similarity", "qed_mean", "toxicity_min", "molecule_count", "clean_pct", "gi_high_pct", "bbb_yes_pct", "pains_clean_pct"]).default("similarity").optional().describe("Sort criterion for children"),
      },
    },
    {
      name: "compare_candidates",
      description: "Head-to-head comparison of specific molecules by CID. Returns full ADMET + FAVES profiles ranked by criterion. Use after drilling to leaf clusters.",
      viewer: UI_RESOURCES.resultsTable,
      inputSchema: {
        cids: z.array(z.string()).min(1).max(20).describe("Array of CIDs to compare"),
        rank_by: z.enum(["qed", "toxicity", "drug_likeness", "synthetic_accessibility", "logp"]).default("qed").optional().describe("Ranking criterion"),
        exclude_controlled: z.boolean().default(true).optional().describe("Exclude DEA-controlled substances"),
      },
    },
    {
      name: "vector_search",
      description: "Fast DiskANN vector similarity search over 122M molecules. Finds structural analogs in <100ms using Morgan fingerprint embeddings. Use when you already know the molecule — for broader exploration, use explore_chemical_space.",
      viewer: UI_RESOURCES.resultsTable,
      inputSchema: {
        smiles: z.string().describe("Query SMILES string"),
        top_k: z.number().int().min(1).max(100).default(10).describe("Number of results"),
        min_similarity: z.number().min(0).max(1).default(0.7).optional().describe("Minimum cosine similarity threshold"),
      },
    },
  ];

  // Compute-only tools: 3D structural descriptors + shared infrastructure
  // (platform info, funnel context, file intelligence). Basic RDKit properties
  // (get_molecule_info, calculate_properties) live on Core, not Compute.
  const computePassThruTools: PassThruTool[] = [
    { name: "get_platform_info", description: "Get NovoMCP platform information" },
    { name: "get_3d_properties", description: "Get 3D molecular properties", viewer: UI_RESOURCES.moleculeViewer },
    { name: "save_funnel_context", description: "Save discovery funnel context for session resumption" },
    { name: "get_funnel_context", description: "Retrieve saved funnel context by job ID" },
    { name: "generate_upload_url", description: "Generate a signed URL for uploading large files (QM logs, PDB, libraries). Returns file_id for downstream tools. Free." },
    { name: "get_file_status", description: "Check upload status, linked tool calls, and processing results for a file." },
    { name: "list_files", description: "List uploaded files with optional type/status filters." },
  ];

  const passThruTools = isCompute ? computePassThruTools : corePassThruTools;

  for (const tool of passThruTools) {
    const toolName = tool.name;
    const hasViewer = tool.viewer != null;
    // Prefer the tool's explicit input schema when provided; otherwise fall
    // back to a permissive passthrough. Explicit schemas are important for
    // tools that take arrays or complex objects — without them Claude has
    // been observed passing stringified arrays that the backend iterates
    // character-by-character (fix to batch_profile ~2026-04-21).
    const inputSchema = tool.inputSchema
      ? z.object(tool.inputSchema).passthrough()
      : z.object({}).passthrough();
    server.registerTool(
      toolName,
      {
        title: toolName.split("_").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" "),
        description: tool.description,
        inputSchema,
        ...(hasViewer ? { _meta: { ui: { resourceUri: tool.viewer! } } } : {}),
      },
      async (args: Record<string, unknown>) => {
        try {
          const result = await callEngine(toolName, args);
          // Tools with a viewer publish structuredContent so the viewer has
          // typed data to read. Tools without a viewer keep the text-only
          // response shape. molecule-viewer tolerates partial payloads — a
          // calculate_properties response without ADMET/FAVES simply skips
          // those sections (all fields in MoleculeToolInput are optional).
          if (hasViewer) {
            const resultObj = (result && typeof result === "object")
              ? result as Record<string, unknown>
              : {};
            return {
              content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
              structuredContent: resultObj,
            };
          }
          return {
            content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          };
        } catch (e) {
          return formatToolError(e);
        }
      },
    );
  }

  // =========================================================================
  // COMPUTE-ONLY: Typed schema tools (QM, NNP, property prediction, dynamics)
  // =========================================================================
  if (isCompute) {

  // Property prediction (novomcp-properties)
  server.registerTool(
    "predict_pka",
    {
      title: "Predict pKa",
      description: "Predict acid dissociation constant (pKa) and identify ionizable groups using chemprop. Returns pKa values, group types, and confidence. Critical for charge state at physiological pH.",
      inputSchema: z.object({
        smiles: z.string().describe("SMILES string of the molecule"),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.predictPka } },
    },
    async (args) => {
      try {
        const result = await callEngine("predict_pka", args);
        const resultObj = (result && typeof result === "object") ? result as Record<string, unknown> : {};
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: resultObj,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  server.registerTool(
    "predict_solubility",
    {
      title: "Predict Solubility",
      description: "Predict aqueous solubility (LogS) at 25°C using chemprop. Returns logS value, solubility in mg/mL, category (soluble/sparingly/insoluble), and confidence.",
      inputSchema: z.object({
        smiles: z.string().describe("SMILES string of the molecule"),
        temperature_k: z.number().optional().describe("Temperature in Kelvin (default 298.15)"),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.predictSolubility } },
    },
    async (args) => {
      try {
        const result = await callEngine("predict_solubility", args);
        const resultObj = (result && typeof result === "object") ? result as Record<string, unknown> : {};
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: resultObj,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  server.registerTool(
    "predict_bde",
    {
      title: "Predict Bond Dissociation Energy",
      description: "Predict bond dissociation energies (kcal/mol) using ALFABET. Identifies metabolic soft spots — bonds with BDE < 85 kcal/mol are susceptible to CYP-mediated oxidation. Returns all bonds with energies and the weakest bond.",
      inputSchema: z.object({
        smiles: z.string().describe("SMILES string of the molecule"),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.predictBde } },
    },
    async (args) => {
      try {
        const result = await callEngine("predict_bde", args);
        const resultObj = (result && typeof result === "object") ? result as Record<string, unknown> : {};
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: resultObj,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // QM: xTB energy calculation
  server.registerTool(
    "run_qm_calculation",
    {
      title: "Run Quantum Chemistry Calculation",
      description: "Run xTB semi-empirical quantum chemistry calculation. Returns energy (Hartree/kcal), HOMO-LUMO gap, dipole moment. Types: energy, optimize, solvation. Supports charged species (charge) and open-shell systems (uhf). Pass xyz_input to use a pre-optimized geometry for redox thermodynamic cycles.",
      inputSchema: z.object({
        smiles: z.string().describe("SMILES string of the molecule"),
        calculation_type: z.enum(["energy", "optimize", "solvation"]).default("energy").describe("Calculation type. energy=single-point electronic energy at input geometry, optimize=geometry optimization to nearest minimum, solvation=solvation free energy via ALPB implicit solvent model."),
        charge: z.number().int().default(0).describe("Molecular charge. 0=neutral closed-shell, +1=cation, -1=anion, +2/-2=dicatinon/dianion. Required for charged species, radical ions, protonated amines, deprotonated carboxylates, and redox thermodynamic cycles (oxidation = charge+1 uhf+1, reduction = charge-1 uhf+1)."),
        uhf: z.number().int().default(0).describe("Number of unpaired electrons. 0=singlet closed-shell (default), 1=doublet (radical, radical cation, radical anion, open-shell transition metal d1/d9), 2=triplet (O2, carbene, triplet excited state). Required for correct open-shell energies; neutral radicals and redox-generated radical ions must set uhf=1."),
        solvent: z.string().optional().describe("Solvent for ALPB implicit solvation model. Accepts: water, methanol, ethanol, acetone, acetonitrile, dmso, dmf, chloroform, dichloromethane, thf, toluene, benzene, hexane, ether. Omit for gas-phase calculation."),
        xyz_input: z.string().optional().describe("Pre-optimized XYZ geometry (Cartesian coordinates, Angstroms) — bypasses SMILES-to-3D conversion. Use for thermodynamic cycles (e.g., vertical IP/EA at a fixed geometry for redox potential calculations), transition state follow-up, or reusing a geometry from a prior optimization."),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.qmCalculation } },
    },
    async (args) => {
      try {
        const result = await callEngine("run_qm_calculation", args) as Record<string, unknown>;
        // Echo input fields the viewer needs in its header (smiles, charge,
        // uhf, solvent, calculation_type) in case the backend drops them.
        if (result) {
          if (!result.smiles && args.smiles) result.smiles = args.smiles;
          if (!result.charge && args.charge !== undefined) result.charge = args.charge;
          if (!result.uhf && args.uhf !== undefined) result.uhf = args.uhf;
          if (!result.solvent && args.solvent) result.solvent = args.solvent;
          if (!result.calculation_type && args.calculation_type) {
            result.calculation_type = args.calculation_type;
          }
        }
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // QM: Hessian / vibrational frequencies + thermochemistry
  server.registerTool(
    "run_qm_hessian",
    {
      title: "Vibrational Frequencies & Thermochemistry",
      description: "Compute vibrational frequencies, normal modes, and thermochemistry via xTB Hessian calculation. Returns all vibrational frequencies (cm⁻¹), explicitly flags imaginary frequencies (negative values indicating the structure is not a true minimum — it's a transition state or saddle point), plus zero-point energy (ZPE), enthalpy correction, Gibbs free energy correction, and entropy. Essential for reaction thermodynamics (ΔG, ΔH, TΔS) and verifying optimized geometries are minima. Use optimize_first=true to optimize then run Hessian in one call, or pass xyz_input from a prior optimization.",
      inputSchema: z.object({
        smiles: z.string().describe("SMILES string of the molecule"),
        charge: z.number().int().default(0).describe("Molecular charge"),
        uhf: z.number().int().default(0).describe("Number of unpaired electrons"),
        solvent: z.string().optional().describe("Solvent for ALPB model"),
        temperature: z.number().default(298.15).describe("Temperature in K for thermochemistry"),
        xyz_input: z.string().optional().describe("Pre-optimized XYZ geometry"),
        optimize_first: z.boolean().default(false).describe("Optimize geometry before Hessian (--ohess)"),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.qmHessian } },
    },
    async (args) => {
      try {
        const result = await callEngine("run_qm_hessian", args) as Record<string, unknown>;
        if (result && !result.smiles && args.smiles) {
          result.smiles = args.smiles;
        }
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // Materials Science: Frontier Orbital Analysis (OLED/Optoelectronics)
  server.registerTool(
    "predict_frontier_orbitals",
    {
      title: "Frontier Orbital Analysis (OLED/Optoelectronics)",
      description: "Predict HOMO, LUMO, emission wavelength, emission color, triplet energy, and OLED suitability. Detects OLED functional groups (carbazole, triphenylamine, Ir complexes, etc.). Uses GFN2-xTB + empirical calibration. For OLED dopant screening, charge transport material evaluation, and optoelectronics R&D.",
      inputSchema: z.object({
        smiles: z.string().describe("SMILES string of the molecule or material"),
        solvent: z.string().optional().describe("Solvent environment (e.g., toluene, chloroform)"),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.frontierOrbitals } },
    },
    async (args) => {
      try {
        const result = await callEngine("predict_frontier_orbitals", args) as Record<string, unknown>;
        // Echo the input smiles into the result so the viewer can label the card
        // when structuredContent is the only source (e.g., if the backend dropped it).
        if (result && !result.smiles && args.smiles) {
          result.smiles = args.smiles;
        }
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // Materials Science: Excited States (sTDA-xTB)
  server.registerTool(
    "run_excited_states",
    {
      title: "Excited State Calculation (sTDA-xTB)",
      description: "Compute singlet and triplet excited states using sTDA-xTB. Returns S1/T1 energies, emission/phosphorescence wavelengths, oscillator strengths, singlet-triplet gap. More accurate than HOMO-LUMO gap for emission prediction. Use for OLED design and photochemistry. 10-30 seconds.",
      inputSchema: z.object({
        smiles: z.string().describe("SMILES string of the molecule"),
        charge: z.number().int().default(0).describe("Molecular charge"),
        num_states: z.number().int().min(1).max(50).default(10).describe("Number of excited states"),
        xyz_input: z.string().optional().describe("Pre-optimized XYZ geometry"),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.excitedStates } },
    },
    async (args) => {
      try {
        const result = await callEngine("run_excited_states", args) as Record<string, unknown>;
        if (result && !result.smiles && args.smiles) {
          result.smiles = args.smiles;
        }
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // Materials Science: NEB Transition State Search (async job)
  server.registerTool(
    "find_transition_state",
    {
      title: "Transition State Search (NEB)",
      description: "ASYNC JOB: Find activation barrier between reactant and product via Nudged Elastic Band (NEB) with GFN2-xTB. Returns a job_id immediately — poll with get_job_status every 60s. Takes 1-10 minutes. Returns forward/reverse barriers (kcal/mol), TS geometry, MEP energies. Requires pre-optimized XYZ geometries (use run_qm_hessian with optimize_first=true). Use predict_reaction_thermodynamics first for feasibility, then this for kinetics.",
      inputSchema: z.object({
        reactant_xyz: z.string().describe("Optimized reactant XYZ geometry"),
        product_xyz: z.string().describe("Optimized product XYZ geometry"),
        n_images: z.number().int().min(3).max(20).default(8).describe("NEB intermediate images"),
        charge: z.number().int().default(0).describe("Molecular charge"),
        uhf: z.number().int().default(0).describe("Unpaired electrons"),
        solvent: z.string().optional().describe("ALPB solvent"),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.transitionState } },
    },
    async (args) => {
      try {
        const result = await callEngine("find_transition_state", args) as Record<string, unknown>;
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

        return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

        return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // Materials Science: Electrolyte Redox Potential Screening
  server.registerTool(
    "predict_redox_potential",
    {
      title: "Electrolyte Redox Potential Screening",
      description: "Predict oxidation/reduction potentials for battery electrolyte design. Uses xTB thermodynamic cycle (neutral → cation → anion). Returns IP, EA, electrode potentials (V vs SHE or Li/Li+), and stability against Li-ion/aqueous voltage windows. Takes 30-90 seconds. Screening-grade accuracy.",
      inputSchema: z.object({
        smiles: z.string().describe("SMILES string of the electrolyte molecule"),
        solvent: z.string().default("water").describe("Solvent for ALPB implicit solvation. Must be one of xTB's supported ALPB strings: water (default), acetonitrile, methanol, acetone, dmso, dmf, thf, dioxane, ether, ethylacetate, ch2cl2, chcl3, benzene, toluene, hexane, nitromethane, phenol, aniline. Battery carbonates (ethylene_carbonate, propylene_carbonate, EMC, DMC) are NOT in xTB's ALPB set — passing them crashes xtb with exit 128. For electrolyte redox use 'water' as a polar stand-in; the carbonate SMARTS calibration class (0.318 V MAE) recovers most of the missing solvent shift."),
        reference_electrode: z.enum(["SHE", "Li/Li+", "Ag/AgCl", "SCE", "Fc/Fc+"]).default("SHE").describe("Reference electrode"),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.redoxPotential } },
    },
    async (args) => {
      try {
        const result = await callEngine("predict_redox_potential", args) as Record<string, unknown>;
        if (result && !result.smiles && args.smiles) {
          result.smiles = args.smiles;
        }
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // Materials Science: Reaction Thermodynamics (Catalysis Feasibility)
  server.registerTool(
    "predict_reaction_thermodynamics",
    {
      title: "Reaction Thermodynamics (ΔG, ΔH, K_eq)",
      description: "Predict whether a reaction is thermodynamically feasible. Returns ΔE, ΔH, ΔG, TΔS, K_eq, spontaneity flag, and confidence classification. Takes reactant + product SMILES. Uses xTB with Hessian thermochemistry. Takes 60-180 seconds. High confidence for organic reactions, low for transition metal catalysis.",
      inputSchema: z.object({
        reactant_smiles: z.array(z.string()).min(1).max(10).describe("SMILES strings of reactants"),
        product_smiles: z.array(z.string()).min(1).max(10).describe("SMILES strings of products"),
        solvent: z.string().optional().describe("Solvent (water, thf, dmso, etc.)"),
        temperature: z.number().default(298.15).describe("Temperature in K"),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.reactionThermo } },
    },
    async (args) => {
      try {
        const result = await callEngine("predict_reaction_thermodynamics", args) as Record<string, unknown>;
        // Echo reactant/product lists so the viewer can render the equation
        // even if the backend drops them from the response.
        if (result && !result.reactant_smiles && args.reactant_smiles) {
          result.reactant_smiles = args.reactant_smiles;
        }
        if (result && !result.product_smiles && args.product_smiles) {
          result.product_smiles = args.product_smiles;
        }
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // QM: Conformer search (ASYNC)
  server.registerTool(
    "run_conformer_search",
    {
      title: "Conformer Search (CREST)",
      description: "ASYNC JOB: Generate conformer ensemble using CREST (GFN2-xTB). Returns a job_id immediately — poll with get_job_status every 60s. Takes 5-15 minutes. Returns ranked conformers with Boltzmann populations and relative energies.",
      inputSchema: z.object({
        smiles: z.string().describe("SMILES string of the molecule"),
        max_conformers: z.number().int().min(1).max(100).default(20).describe("Maximum number of conformers to return"),
        energy_window: z.number().default(6.0).describe("Energy window in kcal/mol"),
        quick: z.boolean().default(false).describe("Use quick mode (faster, less thorough)"),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.conformerSearch } },
    },
    async (args) => {
      try {
        const result = await callEngine("run_conformer_search", args);
        const resultObj = (result && typeof result === "object") ? result as Record<string, unknown> : {};
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: resultObj,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // QM: Strain energy
  server.registerTool(
    "dock_with_strain",
    {
      title: "Calculate Strain Energy",
      description: "Calculate strain energy of a ligand using GFN2-xTB. Returns strain in kcal/mol with interpretation (minimal < 2, moderate 2-5, significant 5-10, severe > 10). Use to validate docking poses.",
      inputSchema: z.object({
        smiles: z.string().describe("SMILES string of the ligand"),
        docked_xyz: z.string().optional().describe("XYZ of docked pose (if available)"),
      }),
    },
    async (args) => {
      try {
        const result = await callEngine("dock_with_strain", args);
        return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // Materials Database: Materials Project lookup
  server.registerTool(
    "search_materials_project",
    {
      title: "Search Materials Project Database",
      description: "Search Materials Project for known inorganic materials. Returns band gap, formation energy, stability (energy above hull), crystal system. Search by formula (LiCoO2), chemical system (Li-Co-O), or material ID (mp-22526). Covers inorganic solids — not organic molecules.",
      inputSchema: z.object({
        query: z.string().describe("Chemical formula, chemical system (Li-Fe-O), or material ID (mp-22526)"),
        search_type: z.enum(["formula", "chemsys", "material_id"]).default("formula").describe("Search type"),
        top_k: z.number().int().min(1).max(20).default(5).describe("Max results"),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.materialsProject } },
    },
    async (args) => {
      try {
        const result = await callEngine("search_materials_project", args) as Record<string, unknown>;
        // Echo query/search_type so the viewer header always shows what was searched.
        if (result && !result.query && args.query) {
          result.query = args.query;
        }
        if (result && !result.search_type && args.search_type) {
          result.search_type = args.search_type;
        }
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // NNP: Neural network potential energy
  server.registerTool(
    "compute_energy",
    {
      title: "Compute Energy (Neural Potential)",
      description: "Compute molecular energy using neural network potentials. ~100x faster than xTB. Models: ANI-2x (organic molecules), MACE-MP-0 (universal). Returns energy in eV and kcal/mol plus atomic forces.",
      inputSchema: z.object({
        smiles: z.string().describe("SMILES string of the molecule"),
        method: z.enum(["auto", "ani2x", "mace"]).default("auto").describe("Neural potential model"),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.nnpResults } },
    },
    async (args) => {
      try {
        const result = await callEngine("compute_energy", args);
        const resultObj = (result && typeof result === "object") ? result as Record<string, unknown> : {};
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: resultObj,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // NNP: Geometry optimization (ASE BFGS + MACE/ANI-2x)
  server.registerTool(
    "optimize_geometry_nnp",
    {
      title: "Geometry Optimization (Neural Potential)",
      description: "Atomic geometry refinement via neural network potentials (ANI-2x organic / MACE-MP-0 universal) with ASE BFGS. For structure relaxation of atomic coordinates — not property-directed compound optimization (see lead_optimization or optimize_molecule for that). ~100x faster than xTB. Returns relaxed XYZ, final energy, convergence status. Neutral molecules only — charged species must use run_qm_calculation.",
      inputSchema: z.object({
        smiles: z.string().describe("SMILES string of the molecule"),
        method: z.enum(["auto", "ani2x", "mace"]).default("auto").describe("Neural potential model"),
        fmax: z.number().default(0.05).describe("Force convergence threshold in eV/Å"),
        charge: z.number().int().default(0).describe("Molecular charge. NNPs are neutral-only — must be 0; a non-zero charge is rejected (use run_qm_calculation for charged species)."),
        uhf: z.number().int().default(0).describe("Unpaired electrons (open-shell). NNPs are closed-shell only — must be 0; non-zero is rejected (use run_qm_calculation for radicals)."),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.nnpResults } },
    },
    async (args) => {
      try {
        const result = await callEngine("optimize_geometry_nnp", args);
        const resultObj = (result && typeof result === "object") ? result as Record<string, unknown> : {};
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: resultObj,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // AlphaFlow: Conformational dynamics (ASYNC)
  server.registerTool(
    "generate_dynamics",
    {
      title: "Generate Conformational Dynamics",
      description: "ASYNC JOB: Generate conformational dynamics ensemble using AlphaFlow/ESMFlow. Returns job_id — poll with get_job_status every 60s. Takes 1-5 minutes. Returns multi-model PDB with per-residue RMSF and PCA of conformational variation.",
      inputSchema: z.object({
        pdb_id: z.string().optional().describe("PDB ID to fetch from RCSB (e.g., 1CRN)"),
        pdb_data: z.string().optional().describe("PDB file content as string"),
        sequence: z.string().optional().describe("Amino acid sequence (if no PDB)"),
        n_frames: z.number().int().min(5).max(500).default(50).describe("Number of conformations to generate"),
      }),
      _meta: { ui: { resourceUri: UI_RESOURCES.generateDynamics } },
    },
    async (args) => {
      try {
        const result = await callEngine("generate_dynamics", args);
        const resultObj = (result && typeof result === "object") ? result as Record<string, unknown> : {};
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: resultObj,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );
  } // end isCompute — QM, NNP, property prediction, dynamics tools

  // =========================================================================
  // CORE-ONLY: novo_compute_info upsell tool
  // =========================================================================
  if (isCore) {
    server.registerTool(
      "novo_compute_info",
      {
        title: "Novo Compute Information",
        description:
          "Get information about Novo Compute tools for quantum chemistry, molecular dynamics, " +
          "docking, conformer search, pKa/solubility/BDE prediction, neural network potentials, " +
          "and protein structure prediction. Call this when a user asks about capabilities like " +
          "conformer search, QM calculations, pKa prediction, molecular dynamics, docking, " +
          "strain energy, neural network potentials, or protein structure prediction.",
        inputSchema: z.object({
          capability: z.string().describe("The capability or tool the user asked about (e.g., 'conformer search', 'molecular dynamics', 'pKa prediction')"),
        }),
        outputSchema: mcpResultSchema,
      },
      async (args) => {
        const capability = (args.capability || "").toLowerCase();

        const toolMap: Record<string, string[]> = {
          "conformer": ["run_conformer_search"],
          "qm": ["run_qm_calculation", "run_qm_hessian"],
          "quantum": ["run_qm_calculation", "run_qm_hessian"],
          "energy": ["run_qm_calculation", "compute_energy"],
          "frequency": ["run_qm_hessian"],
          "hessian": ["run_qm_hessian"],
          "thermochemistry": ["run_qm_hessian"],
          "vibrational": ["run_qm_hessian"],
          "oled": ["predict_frontier_orbitals"],
          "frontier orbital": ["predict_frontier_orbitals"],
          "emission": ["predict_frontier_orbitals"],
          "homo": ["predict_frontier_orbitals", "run_qm_calculation"],
          "lumo": ["predict_frontier_orbitals", "run_qm_calculation"],
          "phosphorescent": ["predict_frontier_orbitals", "run_excited_states"],
          "excited state": ["run_excited_states"],
          "singlet": ["run_excited_states"],
          "triplet": ["run_excited_states"],
          "stda": ["run_excited_states"],
          "absorption": ["run_excited_states"],
          "photochemistry": ["run_excited_states"],
          "fluorescent": ["predict_frontier_orbitals"],
          "materials": ["predict_frontier_orbitals", "predict_redox_potential", "search_materials_project"],
          "materials project": ["search_materials_project"],
          "cathode": ["search_materials_project"],
          "anode": ["search_materials_project"],
          "crystal": ["search_materials_project"],
          "band gap": ["search_materials_project", "predict_frontier_orbitals"],
          "formation energy": ["search_materials_project"],
          "stability": ["search_materials_project"],
          "inorganic": ["search_materials_project"],
          "redox": ["predict_redox_potential"],
          "oxidation": ["predict_redox_potential"],
          "reduction": ["predict_redox_potential"],
          "electrolyte": ["predict_redox_potential"],
          "battery": ["predict_redox_potential"],
          "electrochemical": ["predict_redox_potential"],
          "voltage": ["predict_redox_potential"],
          "reaction": ["predict_reaction_thermodynamics"],
          "thermodynamics": ["predict_reaction_thermodynamics", "run_qm_hessian"],
          "gibbs": ["predict_reaction_thermodynamics"],
          "equilibrium": ["predict_reaction_thermodynamics"],
          "catalysis": ["predict_reaction_thermodynamics", "find_transition_state"],
          "catalyst": ["predict_reaction_thermodynamics", "find_transition_state"],
          "feasibility": ["predict_reaction_thermodynamics"],
          "transition state": ["find_transition_state"],
          "activation energy": ["find_transition_state"],
          "activation barrier": ["find_transition_state"],
          "neb": ["find_transition_state"],
          "kinetics": ["find_transition_state"],
          "reaction rate": ["find_transition_state"],
          "diels-alder": ["predict_reaction_thermodynamics"],
          "aldol": ["predict_reaction_thermodynamics"],
          "strain": ["dock_with_strain"],
          "pka": ["predict_pka"],
          "solubility": ["predict_solubility"],
          "bde": ["predict_bde"],
          "bond dissociation": ["predict_bde"],
          "metabolic": ["predict_bde"],
          "docking": ["dock_molecules"],
          "dock": ["dock_molecules"],
          "md": ["run_molecular_dynamics"],
          "molecular dynamics": ["run_molecular_dynamics"],
          "simulation": ["run_molecular_dynamics"],
          "dynamics": ["generate_dynamics"],
          "alphaflow": ["generate_dynamics"],
          "structure prediction": ["predict_structure"],
          "protein structure": ["get_protein_structure", "predict_structure"],
          "neural network": ["compute_energy", "optimize_geometry_nnp"],
          "ani": ["compute_energy", "optimize_geometry_nnp"],
          "nnp optimization": ["optimize_geometry_nnp"],
          "fast optimization": ["optimize_geometry_nnp"],
          "mace": ["compute_energy"],
        };

        const matched = Object.entries(toolMap)
          .filter(([key]) => capability.includes(key))
          .flatMap(([, tools]) => tools);

        const relevantTools = [...new Set(matched.length > 0 ? matched : [
          "run_conformer_search", "run_qm_calculation", "run_qm_hessian",
          "predict_frontier_orbitals", "run_excited_states",
          "predict_redox_potential", "predict_reaction_thermodynamics",
          "dock_with_strain",
          "compute_energy", "predict_pka", "predict_solubility", "predict_bde",
          "dock_molecules", "run_molecular_dynamics", "generate_dynamics",
          "predict_structure", "get_protein_structure",
        ])];

        const result = {
          message: `${capability || "These capabilities"} ${matched.length === 1 ? "is" : "are"} available through Novo Compute — our quantum chemistry, simulation, and structure prediction platform.`,
          capability: capability || "all",
          relevant_tools: relevantTools,
          all_compute_tools: [
            "predict_pka", "predict_solubility", "predict_bde",
            "run_qm_calculation", "run_qm_hessian", "predict_frontier_orbitals",
            "predict_redox_potential", "predict_reaction_thermodynamics",
            "find_transition_state", "run_excited_states", "optimize_geometry_nnp",
            "search_materials_project", "run_conformer_search", "dock_with_strain",
            "compute_energy", "dock_molecules", "run_molecular_dynamics",
            "generate_dynamics", "predict_structure", "get_protein_structure",
          ],
          how_to_access: "Add Novo Compute to your Claude AI connectors. Requires a paid plan (Core, Team, or Enterprise).",
          setup_url: "https://app.novomcp.com/settings/compute",
          upgrade_url: "https://app.novomcp.com/billing",
        };

        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result,
        };
      },
    );
  } // end isCore — novo_compute_info

  // =========================================================================
  // Audit tools with typed schemas (available on both servers)
  // =========================================================================

  server.registerTool(
    "run_novo_ag",
    {
      title: "'Novo AG' / '/agm' → Autonomous Mode Trigger",
      description: "TRIGGER KEYWORDS: 'Novo AG', 'novo ag', 'novoag', '/agm', '/AGM' (case-insensitive). Call ONLY when the user's message contains one of these literal tokens. Returns the 12-stage autonomous discovery instruction set — read it and follow it. For messages without these keywords, operate in normal collaborative mode. Pass mode='interactive' for the human-in-the-loop variant. Cost: 0 credits.",
      inputSchema: z.object({
        disease: z.string().describe("Disease or indication to target (e.g., 'acute myeloid leukemia')"),
        mode: z.enum(["autonomous", "interactive"]).optional().describe("'autonomous' (default) runs end-to-end; 'interactive' pauses at each stage"),
        md_duration_ns: z.number().optional().describe("MD simulation duration in ns (default 1)"),
      }),
    },
    async (args) => {
      try {
        const result = await callEngine("run_novo_ag", args);
        return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  server.registerTool(
    "save_funnel_stage",
    {
      title: "Save Funnel Stage",
      description: "Log a discovery funnel stage with full audit trail for GxP reproducibility. Call after each pipeline step.",
      inputSchema: z.object({
        funnel_id: z.string().describe("Unique funnel identifier (e.g., funnel_glioblastoma_20260330)"),
        stage_index: z.number().int().optional().describe("Monotonic event counter per funnel_id. Server auto-assigns if omitted (recommended)."),
        funnel_stage: z.number().int().min(1).max(12).optional().describe("Canonical funnel stage 1-12 (see docs/AGENTMODE-ARCHITECTURE.md §1). Required for checkpoint events; omit for ad-hoc/exploration outside the funnel."),
        stage_name: z.string().describe("Machine-readable stage name (e.g., target_discovery)"),
        stage_label: z.string().describe("Human-readable stage label (e.g., Target Discovery)"),
        tool_name: z.string().optional().describe("MCP tool used at this stage"),
        tool_arguments: z.record(z.string(), z.unknown()).optional().describe("Arguments passed to the tool"),
        results_summary: z.record(z.string(), z.unknown()).optional().describe("Key findings from this stage"),
        ai_recommendation: z.string().optional().describe("What the AI suggested"),
        human_decision: z.string().optional().describe("What the user chose"),
        human_prompt: z.string().optional().describe("The user's actual message"),
        decision_reasoning: z.string().optional().describe("Why the user made this decision"),
        human_reviewed: z.boolean().optional().describe("Whether a human reviewed this stage (true=interactive, false=autonomous)"),
        molecules_in: z.number().optional().describe("Molecules entering this stage"),
        molecules_out: z.number().optional().describe("Molecules leaving this stage"),
        molecules_filtered: z.record(z.string(), z.unknown()).optional().describe("Breakdown of filtered molecules by reason"),
        system_metadata: z.record(z.string(), z.unknown()).optional().describe("System prep details (force field, water model, box dims)"),
        curation_method: z.record(z.string(), z.unknown()).optional().describe("Library curation filters, order, thresholds"),
        credits_consumed: z.number().optional().describe("Credits used at this stage"),
        execution_time_ms: z.number().optional().describe("Wall clock time in ms"),
        context_forward: z.record(z.string(), z.unknown()).optional().describe("State to carry to next stage"),
        event_type: z
          .enum(["checkpoint", "exploration"])
          .optional()
          .describe(
            "Event classification. 'checkpoint' = formal human-reviewed funnel stage (peer-review view). " +
            "'exploration' = ad-hoc tool call during ideation/backtracking. Defaults to 'checkpoint' " +
            "server-side. The save executor dual-writes this into system_metadata.event_type since " +
            "the top-level column isn't reliably persisted — get_funnel_audit reads via COALESCE."
          ),
        source_file_id: z.string().optional().describe(
          "If this stage was triggered by an uploaded file (e.g. a QM log auto-processed via " +
          "generate_upload_url's auto_process hook), the file_id (f-…) of that source. Lets " +
          "get_funnel_audit trace back from a stage to the file that drove it."
        ),
      }),
    },
    async (args) => {
      try {
        const result = await callEngine("save_funnel_stage", args);
        return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  server.registerTool(
    "get_funnel_audit",
    {
      title: "Get Funnel Audit Log",
      description: "Retrieve funnel audit log for peer review — every decision, tool call, and human input. Use event_type='checkpoint' for the clean peer-review view that excludes ad-hoc exploration tool calls.",
      inputSchema: z.object({
        funnel_id: z.string().describe("Funnel ID to retrieve (e.g., funnel_glioblastoma_20260330)"),
        // Adding this field here is the actual unlock for the event_type filter.
        // novomcp already filters correctly + dashboard-aggregator now honors
        // the query param at the SQL layer.
        // But the gateway's Zod schema strips unknown fields by default, so
        // without declaring event_type here the param was dropped before it
        // reached novomcp and every filter returned the full trail. Same
        // shape of bug as the NNP charge/uhf and the funnel_id-stripping
        // gap captured in feedback_novomcp_apps_zod_strip_layer.md.
        event_type: z
          .enum(["all", "checkpoint", "exploration"])
          .default("all")
          .optional()
          .describe(
            "Filter events. 'checkpoint' = formal funnel stages (peer-review view). " +
              "'exploration' = ad-hoc tool calls (debugging / interactive use). " +
              "'all' = full trail (default).",
          ),
      }),
    },
    async (args) => {
      try {
        const result = await callEngine("get_funnel_audit", args);
        return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  registerAppTool(
    server,
    "list_funnels",
    {
      title: "List Discovery Funnels",
      description:
        "List recent discovery funnel runs with metadata — disease, target gene, outcome, " +
        "stage count, credits consumed, best affinity. Use to find a funnel_id before calling " +
        "get_funnel_audit. Call when the user asks about recent funnels, past runs, or wants " +
        "to resume a previous pipeline.",
      inputSchema: {
        limit: z.number().int().min(1).max(50).default(15).describe("Max funnels to return"),
        target_gene: z.string().optional().describe("Filter by target gene (e.g., 'EGFR')"),
        outcome: z.enum(["SUCCEEDED", "FAILED_NO_LEADS", "FAILED_TOXICITY", "FAILED_POTENCY", "ABANDONED"]).optional().describe("Filter by funnel outcome"),
      },
      outputSchema: z.object({}).passthrough(),
      _meta: { ui: { resourceUri: UI_RESOURCES.funnels } },
    },
    async (args) => {
      try {
        const result = await callEngine("list_funnels", args) as Record<string, unknown>;
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          structuredContent: result,
        };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  server.registerTool(
    "save_funnel_memory",
    {
      title: "Save Funnel Memory (terminal summary)",
      description: "Write terminal summary of a completed funnel to cross-run memory. Call once at Step 8/Stage 8 completion. Captures target, outcome, failure patterns, and a natural-language summary for analogical retrieval. Future funnels on the same target/area will learn from this run.",
      inputSchema: z.object({
        funnel_id: z.string().describe("Funnel run ID this memory belongs to"),
        target_gene: z.string().optional().describe("Target gene symbol (e.g., KRAS, EGFR)"),
        target_pdb_id: z.string().optional().describe("PDB ID used for docking"),
        therapeutic_area: z.string().optional().describe("Therapeutic area / indication"),
        chemotype: z.string().optional().describe("Chemotype or scaffold class explored"),
        outcome: z.enum(["SUCCEEDED", "FAILED_BUDGET", "FAILED_MAX_ITER", "FAILED_REDLINE", "FAILED_CRITICAL", "ABANDONED"]).describe("Terminal outcome"),
        final_lead_count: z.number().int().optional().describe("Lead candidates that survived to the end"),
        best_affinity_kcal: z.number().optional().describe("Best binding affinity (kcal/mol, negative=better)"),
        failure_pattern: z.record(z.string(), z.unknown()).optional().describe("JSON describing what failed and why"),
        decisions: z.record(z.string(), z.unknown()).optional().describe("JSON capturing key decisions made"),
        summary: z.string().describe("Natural-language summary (2-4 sentences) for semantic search"),
      }),
    },
    async (args) => {
      try {
        const result = await callEngine("save_funnel_memory", args);
        return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  server.registerTool(
    "search_prior_runs",
    {
      title: "Search Prior Discovery Runs",
      description: "Query cross-run memory for past funnels targeting the same gene, PDB, or therapeutic area. Returns terminal summaries, outcomes, and lessons. Call at funnel start to learn from precedents. Includes a lazy backstop that auto-generates template summaries for completed funnels missing explicit memory entries.",
      inputSchema: z.object({
        target_gene: z.string().optional().describe("Target gene symbol (e.g., KRAS)"),
        target_pdb_id: z.string().optional().describe("PDB ID"),
        therapeutic_area: z.string().optional().describe("Therapeutic area / indication"),
        outcome: z.enum(["SUCCEEDED", "FAILED_BUDGET", "FAILED_MAX_ITER", "FAILED_REDLINE", "FAILED_CRITICAL", "ABANDONED", "any"]).optional().describe("Filter by outcome"),
        query: z.string().optional().describe("Natural-language query for semantic search over summaries"),
        max_results: z.number().int().optional().describe("Max results (default 10, max 50)"),
      }),
    },
    async (args) => {
      try {
        const result = await callEngine("search_prior_runs", args);
        return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
      } catch (e) {
        return formatToolError(e);
      }
    },
  );

  // =========================================================================
  // MCP Prompts — proxied from novomcp (CORE-ONLY: orchestrate Novo funnel)
  // =========================================================================
  if (isCore) {

  server.prompt(
    "discovery_funnel_interactive",
    "Find a Drug Candidate — interactive pipeline that pauses at each stage for your review and approval. Every decision is logged for reproducibility.",
    { disease: z.string().describe("Disease or indication to target (e.g., 'glioblastoma', 'lung adenocarcinoma')") },
    async (args) => {
      try {
        const resp = await fetch(`${NOVOMCP_ENGINE_URL}/mcp/prompts/discovery_funnel_interactive`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ arguments: args }),
        });
        if (resp.ok) {
          const data = await resp.json() as { messages?: Array<{ role: string; content: { type: string; text: string } }> };
          return { messages: (data.messages || []).map((m: any) => ({ role: m.role as "user" | "assistant", content: m.content })) };
        }
      } catch { /* fall through to inline */ }

      // Inline fallback if novomcp prompt endpoint unavailable
      return {
        messages: [{
          role: "user" as const,
          content: {
            type: "text" as const,
            text: `Run an interactive drug discovery funnel for: ${args.disease}\n\nThis is a human-in-the-loop pipeline. CRITICAL: You MUST stop after presenting each stage's results. Do NOT proceed to the next stage until I explicitly respond. Each stage is a separate assistant turn.\n\nYour funnel_id is: funnel_${args.disease.toLowerCase().replace(/\s+/g, '_').slice(0, 20)}_${new Date().toISOString().replace(/[-:T]/g, '').slice(0, 15)}\n\nRaw tool calls are auto-logged server-side under your funnel_id — never ask me whether to log, and never call save_funnel_stage just to record that a tool ran. After EACH stage completes AND I respond, call save_funnel_stage to capture MY decision (human_reviewed: true, human_decision, human_prompt) — that human context is the one thing the auto-log can't capture.\n\nStages:\n1. Target Discovery (target_discovery)\n2. Literature & Seed Selection (search_literature + search_chembl)\n3. Seed Characterization (predict_admet + check_compliance)\n4. Lead Optimization (lead_optimization)\n5. Molecular Docking (dock_molecules — if available)\n6. MD Simulation (run_molecular_dynamics — if available)\n7. Patient Stratification (stratify_patients)\n\nStart with Stage 1: Target Discovery. Use target_discovery with the disease and min_evidence: 0.4, max_targets: 10. Present results as a ranked table. Then STOP and ask which target I'd like to pursue.`,
          },
        }],
      };
    },
  );

  server.prompt(
    "discovery_funnel",
    "Find a Drug Candidate (Autonomous) — runs the full pipeline without pausing. No human review at each stage.",
    { disease: z.string().describe("Disease or indication to target") },
    async (args) => {
      try {
        const resp = await fetch(`${NOVOMCP_ENGINE_URL}/mcp/prompts/discovery_funnel`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ arguments: args }),
        });
        if (resp.ok) {
          const data = await resp.json() as { messages?: Array<{ role: string; content: { type: string; text: string } }> };
          return { messages: (data.messages || []).map((m: any) => ({ role: m.role as "user" | "assistant", content: m.content })) };
        }
      } catch { /* fall through */ }

      return {
        messages: [{
          role: "user" as const,
          content: {
            type: "text" as const,
            text: `Run a complete autonomous drug discovery funnel for: ${args.disease}\n\nExecute all stages without stopping. After each step, call save_funnel_stage with human_reviewed: false.\n\nYour funnel_id is: funnel_${args.disease.toLowerCase().replace(/\s+/g, '_').slice(0, 20)}_${new Date().toISOString().replace(/[-:T]/g, '').slice(0, 15)}`,
          },
        }],
      };
    },
  );

  server.prompt(
    "deep_characterization",
    "Deep Molecule Characterization — comprehensive analysis including properties, pKa, solubility, conformers, quantum properties, and metabolic soft spots.",
    { smiles: z.string().describe("SMILES string of the molecule to characterize") },
    async (args) => {
      try {
        const resp = await fetch(`${NOVOMCP_ENGINE_URL}/mcp/prompts/deep_characterization`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ arguments: args }),
        });
        if (resp.ok) {
          const data = await resp.json() as { messages?: Array<{ role: string; content: { type: string; text: string } }> };
          return { messages: (data.messages || []).map((m: any) => ({ role: m.role as "user" | "assistant", content: m.content })) };
        }
      } catch { /* fall through */ }

      return {
        messages: [{
          role: "user" as const,
          content: {
            type: "text" as const,
            text: `Perform a deep characterization of this molecule: ${args.smiles}\n\nRun: get_molecule_profile, predict_pka (if available), predict_solubility (if available), run_conformer_search (async), run_qm_calculation, predict_bde (if available). Present a comprehensive report with drug-likeness verdict.`,
          },
        }],
      };
    },
  );

  server.prompt(
    "quick_check",
    "Quick safety and compliance check for a molecule.",
    { smiles: z.string().describe("SMILES string to check") },
    async (args) => ({
      messages: [{
        role: "user" as const,
        content: {
          type: "text" as const,
          text: `Check if this molecule is safe and compliant: ${args.smiles}\n\nCheck for DEA controlled substance status, FDA banned status, structural alerts, and overall compliance.`,
        },
      }],
    }),
  );

  server.prompt(
    "upload_file",
    "Upload a file to NovoMCP — QM logs, PDB structures, compound libraries, or any large file. Generates a secure upload link.",
    {
      filename: z.string().describe("Name of the file to upload (e.g., 'output.log', 'compounds.sdf')"),
      purpose: z.string().optional().describe("What the file will be used for (e.g., 'metal parameterization', 'library screening')"),
    },
    async (args) => ({
      messages: [{
        role: "user" as const,
        content: {
          type: "text" as const,
          text: `I need to upload a file: ${args.filename}${args.purpose ? `\nPurpose: ${args.purpose}` : ''}\n\nGenerate an upload link for me. Detect the file type from the extension:\n- .log, .out → qm_log\n- .pdb, .cif → pdb\n- .sdf, .smi, .csv → library\n- .xtc, .trr → trajectory\n- .frcmod, .prep, .top → frcmod\n\nIf the purpose suggests a specific tool should auto-process the file after upload (e.g., metal parameterization → parameterize_metal, library screening → screen_library), set up auto_process in the generate_upload_url call so processing starts automatically when the upload completes.`,
        },
      }],
    }),
  );

  } // end isCore — MCP prompts

  // =========================================================================
  // COMPUTE PROMPTS: metalloprotein parameterization workflow
  // =========================================================================
  if (isCompute) {

  server.prompt(
    "parameterize_metalloprotein",
    "Parameterize a metalloprotein for MD simulation — audit the target, extract the QM fragment, set up file upload with auto-processing.",
    {
      pdb_id: z.string().describe("PDB ID of the metalloprotein (e.g., '1E67', '1CA2')"),
      metal_resid: z.number().int().optional().describe("Residue number of the metal (if known)"),
    },
    async (args) => ({
      messages: [{
        role: "user" as const,
        content: {
          type: "text" as const,
          text: `I need to parameterize the metal site in PDB ${args.pdb_id}${args.metal_resid ? ` (metal at residue ${args.metal_resid})` : ''} for molecular dynamics simulation.\n\nFollow this workflow:\n\n1. Run audit_system on ${args.pdb_id} to identify the metal site, coordination sphere, and whether the system needs MCPB parameterization.\n\n2. If the audit identifies a metal that needs parameterization, run parameterize_metal Phase 1 to extract the coordination fragment and generate the Gaussian .com input files.\n\n3. Show me the .com files and explain what QM calculations I need to run (which keywords, which basis set, expected runtime).\n\n4. Set up a file upload URL with auto_process enabled — when I upload the Gaussian .log output, parameterize_metal Phase 2 should start automatically and email me when the force field parameters are ready.\n\nPresent each step clearly. If the audit says the system is refused (membrane protein, etc.), explain why and suggest alternatives.`,
        },
      }],
    }),
  );

  } // end isCompute — MCP prompts

  // =========================================================================
  // UI Resources
  // =========================================================================
  const uiApps = [
    { uri: UI_RESOURCES.moleculeViewer, file: "molecule-viewer.html", name: "Molecule Viewer" },
    { uri: UI_RESOURCES.admetDashboard, file: "admet-dashboard.html", name: "ADMET Dashboard" },
    { uri: UI_RESOURCES.researchExplorer, file: "research-explorer.html", name: "Research Explorer" },
    { uri: UI_RESOURCES.structureViewer, file: "structure-viewer.html", name: "Structure Viewer" },
    { uri: UI_RESOURCES.creditUsage, file: "credit-usage.html", name: "Credit Usage" },
    { uri: UI_RESOURCES.favesDashboard, file: "faves-dashboard.html", name: "FAVES Dashboard" },
    { uri: UI_RESOURCES.jobs, file: "jobs.html", name: "Pipeline Jobs" },
    { uri: UI_RESOURCES.funnels, file: "funnels.html", name: "Discovery Funnels" },
    { uri: UI_RESOURCES.mdResults, file: "md-results.html", name: "MD Results" },
    { uri: UI_RESOURCES.pipelineAudit, file: "pipeline-audit.html", name: "Pipeline Audit" },
    { uri: UI_RESOURCES.dockingViewer, file: "docking-viewer.html", name: "Docking Viewer" },
    { uri: UI_RESOURCES.leadComparison, file: "lead-comparison.html", name: "Lead Comparison" },
    { uri: UI_RESOURCES.frontierOrbitals, file: "frontier-orbitals.html", name: "Frontier Orbital Analysis" },
    { uri: UI_RESOURCES.qmHessian, file: "qm-hessian.html", name: "Vibrational Frequencies & Thermochemistry" },
    { uri: UI_RESOURCES.transitionState, file: "transition-state.html", name: "Transition State (NEB)" },
    { uri: UI_RESOURCES.excitedStates, file: "excited-states.html", name: "Excited States (sTDA-xTB)" },
    { uri: UI_RESOURCES.redoxPotential, file: "redox-potential.html", name: "Electrolyte Redox Potential" },
    { uri: UI_RESOURCES.reactionThermo, file: "reaction-thermo.html", name: "Reaction Thermodynamics" },
    { uri: UI_RESOURCES.materialsProject, file: "materials-project.html", name: "Materials Project Search" },
    { uri: UI_RESOURCES.qmCalculation, file: "qm-calculation.html", name: "QM Calculation" },
    { uri: UI_RESOURCES.targetDiscovery, file: "target-discovery.html", name: "Target Discovery" },
    { uri: UI_RESOURCES.clinicalOutcomes, file: "clinical-outcomes.html", name: "Clinical Outcomes" },
    { uri: UI_RESOURCES.stratifyPatients, file: "stratify-patients.html", name: "Patient Stratification" },
    { uri: UI_RESOURCES.conformerSearch, file: "conformer-search.html", name: "Conformer Search" },
    { uri: UI_RESOURCES.generateDynamics, file: "generate-dynamics.html", name: "Conformational Dynamics" },
    { uri: UI_RESOURCES.predictPka, file: "predict-pka.html", name: "pKa Prediction" },
    { uri: UI_RESOURCES.predictSolubility, file: "predict-solubility.html", name: "Solubility Prediction" },
    { uri: UI_RESOURCES.predictBde, file: "predict-bde.html", name: "BDE Prediction" },
    { uri: UI_RESOURCES.nnpResults, file: "nnp-results.html", name: "NNP Results" },
    { uri: UI_RESOURCES.validateTarget, file: "validate-target.html", name: "Target Validation" },
    { uri: UI_RESOURCES.resultsTable, file: "results-table.html", name: "Molecule Results" },
    { uri: UI_RESOURCES.clusterExplorer, file: "cluster-explorer.html", name: "Cluster Explorer" },
  ];

  for (const app of uiApps) {
    registerAppResource(
      server,
      app.uri,
      app.uri,
      { mimeType: RESOURCE_MIME_TYPE, description: `NovoMCP ${app.name} UI` },
      async (): Promise<ReadResourceResult> => {
        const html = await fs.readFile(path.join(DIST_DIR, app.file), "utf-8");
        return {
          contents: [{ uri: app.uri, mimeType: RESOURCE_MIME_TYPE, text: html }],
        };
      },
    );
  }

  return server;
}
