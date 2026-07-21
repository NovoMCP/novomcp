/**
 * NovoMCP Apps - Entry Point
 *
 * Run with: npm start
 * Or: npx tsx main.ts [--stdio]
 */

import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { createMcpExpressApp } from "@modelcontextprotocol/sdk/server/express.js";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import cors from "cors";
import rateLimit from "express-rate-limit";
import express from "express";
import type { Request, Response } from "express";
import { createServer } from "./server.js";

// Structured audit logger for security-relevant events
function auditLog(event: string, data: Record<string, unknown>) {
  const entry = {
    timestamp: new Date().toISOString(),
    service: "novomcp-apps",
    event,
    ...data,
  };
  process.stdout.write(JSON.stringify(entry) + "\n");
}

/**
 * Resolve the X-Novo-Client tag the backend will persist into
 * funnel_audit_log.system_metadata.client. Two sources, priority order:
 *   1. JSON-RPC `initialize.params.clientInfo.name` when present in body.
 *   2. User-Agent header — Claude Code, Cursor, Windsurf, ChatGPT all set one.
 * Falls back to "" when neither is available (audit row shows surface chip only).
 *
 * Versions are stripped server-side so audit slices are stable across upgrades:
 *   "claude-code/1.2.3" → "claude-code"
 *   "Cursor/0.45.0"     → "cursor"
 *   "ChatGPT/123 (...)"  → "chatgpt"
 * Unknown tokens pass through lowercased; the dashboard's CLIENT_TABLE
 * renders nice names for known values and the raw token otherwise.
 */
function deriveClientTag(req: Request): string {
  const body = req.body as unknown;
  if (body && typeof body === "object") {
    const b = body as { method?: unknown; params?: unknown };
    if (b.method === "initialize" && b.params && typeof b.params === "object") {
      const p = b.params as { clientInfo?: { name?: unknown } };
      const name = p.clientInfo?.name;
      if (typeof name === "string" && name.length > 0) {
        return normalizeClientTag(name);
      }
    }
  }
  const ua = req.headers["user-agent"];
  if (typeof ua === "string" && ua.length > 0) {
    return normalizeClientTag(ua);
  }
  return "";
}

function normalizeClientTag(raw: string): string {
  // Take the first slash-separated token (`Cursor/0.45.0` → `Cursor`),
  // then the first space-separated token (some UAs are `name (extra)`),
  // lowercase, and truncate. Strips version drift so analytics aggregate
  // across upgrades; the dashboard CLIENT_TABLE renders nice display names.
  const head = raw.split("/")[0].split(" ")[0].toLowerCase();
  return head.slice(0, 64);
}

/**
 * Starts an MCP server with Streamable HTTP transport.
 */
export async function startStreamableHTTPServer(
  createServer: (apiKey?: string, clientTag?: string) => McpServer,
): Promise<void> {
  const port = parseInt(process.env.PORT ?? "3002", 10);

  // Host validation to prevent Host header injection (CWE-644).
  // Set CANONICAL_HOST to your deployed host in production; local dev
  // defaults to localhost so an OSS clone works without any config.
  const CANONICAL_HOST = process.env.CANONICAL_HOST || "localhost";
  const ALLOWED_HOSTS = new Set(
    (process.env.ALLOWED_HOSTS || CANONICAL_HOST).split(",").map((h) => h.trim().toLowerCase())
  );

  function getValidatedHost(req: Request): string {
    const host = req.headers.host?.toLowerCase();
    if (host && ALLOWED_HOSTS.has(host)) return host;
    return CANONICAL_HOST;
  }

  // Redirect URL validation to prevent open redirects (CWE-601)
  // Include all known MCP client callback domains
  const ALLOWED_REDIRECT_DOMAINS = new Set([
    CANONICAL_HOST,
    ...ALLOWED_HOSTS,
    // Claude
    "claude.ai",
    "www.claude.ai",
    // ChatGPT
    "chatgpt.com",
    "www.chatgpt.com",
    // Gemini
    "gemini.google.com",
    // Cursor
    "cursor.sh",
    "www.cursor.sh",
    // Windsurf
    "codeium.com",
    "windsurf.com",
    // Allow additional domains via env var
    ...(process.env.ALLOWED_OAUTH_REDIRECT_DOMAINS || "").split(",").map(d => d.trim()).filter(Boolean),
  ]);

  function isAllowedRedirect(url: string): boolean {
    try {
      const parsed = new URL(url);
      if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return false;
      return ALLOWED_REDIRECT_DOMAINS.has(parsed.host.toLowerCase());
    } catch {
      // Relative URLs are safe (same-origin)
      return url.startsWith("/");
    }
  }

  const app = createMcpExpressApp({ host: "0.0.0.0" });

  // Trust Azure Container Apps load balancer / reverse proxy
  app.set("trust proxy", 1);

  // CORS: allow known MCP client origins and our own domains
  const ALLOWED_ORIGINS = [
    "https://claude.ai",
    "https://www.claude.ai",
    "https://chatgpt.com",
    "https://www.chatgpt.com",
    "https://gemini.google.com",
    /^https:\/\/.*\.anthropic\.com$/,
    /^https:\/\/.*\.openai\.com$/,
    /^https:\/\/.*\.novomcp\.com$/,
    /^https:\/\/.*\.novomcp\.com$/,
  ];
  app.use(cors({
    origin: (origin, callback) => {
      // Allow requests with no origin (server-to-server, curl, MCP clients)
      if (!origin) return callback(null, true);
      const allowed = ALLOWED_ORIGINS.some(o =>
        typeof o === "string" ? o === origin : o.test(origin)
      );
      callback(null, allowed);
    },
    credentials: true,
  }));

  // Rate limiting: 100 requests per minute per IP
  app.use(rateLimit({
    windowMs: 60_000,
    max: 100,
    standardHeaders: true,
    legacyHeaders: false,
    message: { error: "Too many requests, please try again later" },
  }));

  // Security headers
  app.use((_req: Request, res: Response, next: () => void) => {
    res.setHeader("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload");
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("X-Frame-Options", "DENY");
    res.setHeader("Referrer-Policy", "strict-origin-when-cross-origin");
    res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
    res.setHeader(
      "Content-Security-Policy",
      "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self' https://*.novomcp.com https://*.novomcp.com"
    );
    next();
  });

  app.use(express.json({ limit: "1mb" }));
  app.use(express.urlencoded({ extended: true, limit: "1mb" }));

  // Backend URL for proxying OAuth requests
  const NOVOMCP_ENGINE_URL = process.env.NOVOMCP_ENGINE_URL;
  if (!NOVOMCP_ENGINE_URL) {
    throw new Error("NOVOMCP_ENGINE_URL environment variable is required");
  }

  // Health check endpoint for Azure Container Apps monitoring
  app.get("/health", (_req: Request, res: Response) => {
    res.json({ status: "healthy", service: "novomcp-apps", timestamp: new Date().toISOString() });
  });

  // Block search engine crawlers - this is an MCP server, not a website
  app.get("/robots.txt", (_req: Request, res: Response) => {
    res.type("text/plain").send("User-agent: *\nDisallow: /\n");
  });

  // Root endpoint - redirect browsers to marketing page, return API info for others
  app.get("/", (req: Request, res: Response) => {
    const accept = req.headers.accept || "";
    if (accept.includes("text/html") && !accept.includes("application/json")) {
      // Browser request - redirect to marketing page
      return res.redirect(302, "https://novomcp.com");
    }
    // API request - return service info
    res.json({
      service: "NovoMCP - Molecular Intelligence MCP Server",
      version: "2.0.0",
      protocol: "MCP 2025-06-18",
      transport: "streamable-http",
      description: "Connect AI assistants to 100M+ compounds with molecular intelligence tools",
      mcp_endpoint: "/mcp",
      oauth: "/.well-known/oauth-authorization-server"
    });
  });

  // OAuth Discovery helper function
  const handleOAuthDiscovery = async (req: Request, res: Response) => {
    try {
      const response = await fetch(`${NOVOMCP_ENGINE_URL}/.well-known/oauth-authorization-server`, {
        headers: {
          host: getValidatedHost(req),
          "x-forwarded-host": getValidatedHost(req)
        }
      });
      const data = await response.json();
      // Replace backend URLs with our public URLs
      const host = getValidatedHost(req);
      const issuer = `https://${host}`;
      res.json({
        ...data,
        issuer,
        authorization_endpoint: `${issuer}/oauth/authorize`,
        token_endpoint: `${issuer}/oauth/token`,
        registration_endpoint: `${issuer}/oauth/register`,
        revocation_endpoint: `${issuer}/oauth/revoke`,
        service_documentation: `${issuer}/docs`
      });
    } catch (error) {
      console.error("OAuth discovery error:", error);
      res.status(500).json({ error: "Failed to fetch OAuth metadata" });
    }
  };

  // OAuth Discovery - at root and /mcp paths (Claude checks relative to connector URL)
  app.get("/.well-known/oauth-authorization-server", handleOAuthDiscovery);
  app.get("/mcp/.well-known/oauth-authorization-server", handleOAuthDiscovery);

  // OAuth Protected Resource (RFC 9728) - tells Claude auth is required
  const handleProtectedResource = (req: Request, res: Response) => {
    const host = getValidatedHost(req);
    const issuer = `https://${host}`;
    res.json({
      resource: issuer,
      authorization_servers: [issuer],
      scopes_supported: ["mcp:tools", "mcp:read", "mcp:write"],
      bearer_methods_supported: ["header"],
      resource_documentation: `${issuer}/docs`
    });
  };
  app.get("/.well-known/oauth-protected-resource", handleProtectedResource);
  app.get("/mcp/.well-known/oauth-protected-resource", handleProtectedResource);

  // OAuth Authorize GET - proxy to novomcp
  app.get("/oauth/authorize", async (req: Request, res: Response) => {
    try {
      const queryString = new URLSearchParams(req.query as Record<string, string>).toString();
      const response = await fetch(`${NOVOMCP_ENGINE_URL}/oauth/authorize?${queryString}`, {
        headers: {
          host: getValidatedHost(req),
          "x-forwarded-host": getValidatedHost(req)
        }
      });
      const html = await response.text();
      // Fix form action to point to our endpoint
      const fixedHtml = html.replace('action="/oauth/authorize"', `action="/oauth/authorize"`);
      res.setHeader("Content-Type", "text/html");
      res.send(fixedHtml);
    } catch (error) {
      console.error("OAuth authorize error:", error);
      res.status(500).send("Authorization error");
    }
  });

  // OAuth Authorize POST - proxy to novomcp
  app.post("/oauth/authorize", async (req: Request, res: Response) => {
    try {
      const formData = new URLSearchParams(req.body as Record<string, string>);
      const response = await fetch(`${NOVOMCP_ENGINE_URL}/oauth/authorize`, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          host: getValidatedHost(req),
          "x-forwarded-host": getValidatedHost(req)
        },
        body: formData.toString(),
        redirect: "manual"
      });

      // Handle redirect response — validate destination to prevent open redirects
      if (response.status === 302 || response.status === 301) {
        const location = response.headers.get("location");
        if (location && isAllowedRedirect(location)) {
          return res.redirect(response.status, location);
        }
        if (location) {
          auditLog("oauth.blocked_redirect", { ip: req.ip, url: location });
        }
      }

      // Return HTML response (error page)
      const html = await response.text();
      res.setHeader("Content-Type", "text/html");
      res.status(response.status).send(html);
    } catch (error) {
      console.error("OAuth authorize POST error:", error);
      res.status(500).send("Authorization error");
    }
  });

  // OAuth Token - proxy to novomcp
  app.post("/oauth/token", async (req: Request, res: Response) => {
    try {
      const contentType = req.headers["content-type"] || "";
      let body: string;
      let headers: Record<string, string> = {
        host: getValidatedHost(req),
        "x-forwarded-host": getValidatedHost(req)
      };

      if (contentType.includes("application/json")) {
        body = JSON.stringify(req.body);
        headers["Content-Type"] = "application/json";
      } else {
        body = new URLSearchParams(req.body as Record<string, string>).toString();
        headers["Content-Type"] = "application/x-www-form-urlencoded";
      }

      const response = await fetch(`${NOVOMCP_ENGINE_URL}/oauth/token`, {
        method: "POST",
        headers,
        body
      });

      const data = await response.json();
      auditLog("oauth.token_exchange", { ip: req.ip, status: response.status });
      res.status(response.status).json(data);
    } catch (error) {
      auditLog("oauth.token_error", { ip: req.ip, error: error instanceof Error ? error.message : "unknown" });
      res.status(500).json({ error: "token_error", error_description: "Failed to exchange token" });
    }
  });

  // OAuth Register - proxy to novomcp
  app.post("/oauth/register", async (req: Request, res: Response) => {
    try {
      const response = await fetch(`${NOVOMCP_ENGINE_URL}/oauth/register`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          host: getValidatedHost(req),
          "x-forwarded-host": getValidatedHost(req)
        },
        body: JSON.stringify(req.body)
      });

      const data = await response.json();
      auditLog("oauth.client_registration", { ip: req.ip, status: response.status });
      res.status(response.status).json(data);
    } catch (error) {
      auditLog("oauth.register_error", { ip: req.ip, error: error instanceof Error ? error.message : "unknown" });
      res.status(500).json({ error: "registration_error" });
    }
  });

  // OAuth Revoke - proxy to novomcp
  app.post("/oauth/revoke", async (req: Request, res: Response) => {
    try {
      const contentType = req.headers["content-type"] || "";
      let body: string;
      let headers: Record<string, string> = {
        host: getValidatedHost(req),
        "x-forwarded-host": getValidatedHost(req)
      };

      if (contentType.includes("application/json")) {
        body = JSON.stringify(req.body);
        headers["Content-Type"] = "application/json";
      } else {
        body = new URLSearchParams(req.body as Record<string, string>).toString();
        headers["Content-Type"] = "application/x-www-form-urlencoded";
      }

      const response = await fetch(`${NOVOMCP_ENGINE_URL}/oauth/revoke`, {
        method: "POST",
        headers,
        body
      });

      const data = await response.json();
      res.status(response.status).json(data);
    } catch (error) {
      res.status(200).json({});  // RFC 7009: always return 200
    }
  });

  // PDB Proxy - fetch PDB files from RCSB (bypasses CORS for UI)
  app.get("/api/pdb/:pdbId", async (req: Request, res: Response) => {
    const pdbId = req.params.pdbId as string;

    // Validate PDB ID format (4 characters, alphanumeric)
    if (!pdbId || !/^[a-zA-Z0-9]{4}$/.test(pdbId)) {
      return res.status(400).json({ error: "Invalid PDB ID format" });
    }

    try {
      const pdbIdUpper = pdbId.toUpperCase();
      const response = await fetch(`https://files.rcsb.org/download/${pdbIdUpper}.pdb`);

      if (!response.ok) {
        return res.status(response.status).json({
          error: `RCSB returned ${response.status}`,
          pdbId: pdbIdUpper
        });
      }

      const pdbContent = await response.text();

      // Return as plain text with CORS headers
      res.setHeader("Content-Type", "text/plain");
      res.setHeader("Access-Control-Allow-Origin", "*");
      res.setHeader("Cache-Control", "public, max-age=86400"); // Cache for 24 hours
      res.send(pdbContent);
    } catch (error) {
      console.error("PDB proxy error:", error);
      res.status(500).json({ error: "Failed to fetch PDB file" });
    }
  });

  // =========================================================================
  // Tool Search passthrough (WS10)
  //
  // /mcp/tool-search* endpoints live on novomcp. The main /mcp JSON-RPC
  // route below doesn't match sub-paths, so without these passthroughs the
  // tool-search endpoints are only reachable at api.novomcp.com. The
  // Claude.ai MCP clients (and NovoWorkbench v1.1) use the ai.novomcp.com /
  // compute.novomcp.com hostnames with nmcp_* / ncmcp_* keys — this block
  // forwards those calls to novomcp with the caller's auth preserved.
  //
  // Not mounted through the MCP JSON-RPC transport because tool-search is a
  // plain HTTP GET/POST with a JSON body, not an MCP protocol method. This
  // is intentional — tool-search exists to prep the tool catalog shown IN the
  // MCP session, so it cannot itself be part of the session.
  //
  // See docs/NovoMCP/AGENT-SDK-TOOL-SEARCH.md and docs/MCP-EXECUTION-PLAN.md
  // §WS10.
  // =========================================================================

  const forwardAuthHeader = (req: Request): Record<string, string> => {
    const out: Record<string, string> = { "Content-Type": "application/json" };
    if (req.headers.authorization) {
      out["Authorization"] = req.headers.authorization;
    }
    const xApiKey = req.headers["x-api-key"];
    if (typeof xApiKey === "string" && xApiKey) {
      out["X-API-Key"] = xApiKey;
    }
    return out;
  };

  app.get("/mcp/tool-search/status", async (req: Request, res: Response) => {
    try {
      const upstream = await fetch(`${NOVOMCP_ENGINE_URL}/mcp/tool-search/status`, {
        method: "GET",
        headers: forwardAuthHeader(req),
      });
      const body = await upstream.text();
      res.status(upstream.status).type("application/json").send(body);
    } catch (err) {
      console.error("tool-search/status passthrough error:", err);
      res.status(502).json({ error: "tool-search upstream unavailable" });
    }
  });

  app.post("/mcp/tool-search", async (req: Request, res: Response) => {
    try {
      const upstream = await fetch(`${NOVOMCP_ENGINE_URL}/mcp/tool-search`, {
        method: "POST",
        headers: forwardAuthHeader(req),
        body: JSON.stringify(req.body ?? {}),
      });
      const body = await upstream.text();
      res.status(upstream.status).type("application/json").send(body);
    } catch (err) {
      console.error("tool-search passthrough error:", err);
      res.status(502).json({ error: "tool-search upstream unavailable" });
    }
  });

  app.post("/mcp/tool-search/rebuild", async (req: Request, res: Response) => {
    try {
      const upstream = await fetch(`${NOVOMCP_ENGINE_URL}/mcp/tool-search/rebuild`, {
        method: "POST",
        headers: forwardAuthHeader(req),
      });
      const body = await upstream.text();
      res.status(upstream.status).type("application/json").send(body);
    } catch (err) {
      console.error("tool-search/rebuild passthrough error:", err);
      res.status(502).json({ error: "tool-search upstream unavailable" });
    }
  });

  // Per-API-key rate limiting for /mcp (stricter than global IP-based limit)
  const mcpKeyLimiter = rateLimit({
    windowMs: 60_000,
    max: 60,
    standardHeaders: true,
    legacyHeaders: false,
    keyGenerator: (req: Request) => {
      const auth = req.headers.authorization;
      if (auth?.startsWith("Bearer ")) return auth.slice(7);
      return (req.headers["x-api-key"] as string) || req.ip || "unknown";
    },
    message: { error: "Rate limit exceeded for this API key" },
  });

  app.all("/mcp", mcpKeyLimiter, async (req: Request, res: Response) => {
    // Extract API key from request headers (Authorization: Bearer <key> or X-API-Key)
    const authHeader = req.headers.authorization;
    const xApiKey = req.headers["x-api-key"] as string | undefined;
    let apiKey = xApiKey || "";
    if (authHeader?.startsWith("Bearer ")) {
      apiKey = authHeader.slice(7);
    }

    // RFC 9728: Return 401 with WWW-Authenticate to trigger OAuth flow
    // This is what makes Claude show "Connect" (OAuth) instead of "Configure"
    if (!apiKey) {
      auditLog("auth.missing_key", { ip: req.ip, method: req.method });
      const host = getValidatedHost(req);
      res.status(401)
        .setHeader(
          "WWW-Authenticate",
          `Bearer resource_metadata="https://${host}/.well-known/oauth-protected-resource"`
        );
      return res.json({
        jsonrpc: "2.0",
        error: {
          code: -32000,
          message: "Authentication required. Please connect with your API key."
        },
        id: null,
      });
    }

    auditLog("mcp.request", { ip: req.ip, method: req.method, hasKey: true });

    // Derive the X-Novo-Client tag the backend will persist into the audit
    // row. Two sources, in priority order:
    //   1. Initialize JSON-RPC `params.clientInfo.name` if this is the first
    //      request of a session — the MCP-protocol-canonical identifier.
    //   2. User-Agent header (Claude Code, Cursor, etc. all set one).
    // Returns "" if neither is present — the audit row falls back to the
    // surface chip alone.
    const clientTag = deriveClientTag(req);

    const server = createServer(apiKey, clientTag);
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
    });

    res.on("close", () => {
      transport.close().catch(() => {});
      server.close().catch(() => {});
    });

    try {
      await server.connect(transport);
      await transport.handleRequest(req, res, req.body);
    } catch (error) {
      auditLog("mcp.error", { ip: req.ip, error: error instanceof Error ? error.message : "unknown" });
      if (!res.headersSent) {
        res.status(500).json({
          jsonrpc: "2.0",
          error: { code: -32603, message: "Internal server error" },
          id: null,
        });
      }
    }
  });

  const httpServer = app.listen(port, () => {
    console.log(`NovoMCP Apps server listening on http://localhost:${port}/mcp`);
    console.log(`\nTo connect in Claude, add as custom connector:`);
    console.log(`  URL: http://localhost:${port}/mcp`);
    console.log(`\nFor production, use cloudflared tunnel:`);
    console.log(`  npx cloudflared tunnel --url http://localhost:${port}`);
  });

  const shutdown = () => {
    console.log("\nShutting down...");
    httpServer.close(() => process.exit(0));
  };

  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

/**
 * Starts an MCP server with stdio transport.
 */
export async function startStdioServer(
  createServer: (apiKey?: string) => McpServer,
): Promise<void> {
  await createServer().connect(new StdioServerTransport());
}

async function main() {
  console.log("Starting NovoMCP Apps server...\n");

  if (process.argv.includes("--stdio")) {
    await startStdioServer(createServer);
  } else {
    await startStreamableHTTPServer(createServer);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
