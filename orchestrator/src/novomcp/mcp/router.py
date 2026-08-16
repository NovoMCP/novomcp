"""
NovoMCP Router

Implements the Model Context Protocol (MCP) for Claude integration.
Exposes molecular intelligence tools via Streamable HTTP transport.

MCP Protocol Reference (Streamable HTTP - recommended):
- GET  /mcp/tools           → List available tools
- POST /mcp/tools/{name}    → Execute a specific tool
- Authentication via API key in header or query param

Legacy SSE transport (deprecated):
- GET  /mcp/sse             → Persistent SSE connection
- POST /mcp/sse/call        → Tool execution via SSE
"""

import logging
import json
import asyncio
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
from fastapi import APIRouter, Request, Response, HTTPException, Depends, Query, Header
from fastapi.responses import JSONResponse, FileResponse
import uuid
import httpx

from .tools import MCP_TOOLS, MCP_RESOURCES, MCP_RESOURCE_DATA, MCP_PROMPTS, MCP_PROMPT_TEMPLATES, MCPToolExecutor, visible_tools, visible_prompts, host_is_compute, is_tool_visible, host_is_rest_api, rest_tool_visible, is_tool_locally_available
from .auth import MCPUser, UserTier, validate_via_spine
from .rate_limiter import MCPRateLimiter, RateLimitResult
from .spine import Spine, build_spine
from . import tool_search as tool_search_module

logger = logging.getLogger(__name__)

# Server-level instructions returned in the MCP `initialize` response.
# Per the 2024-11-05 spec, `InitializeResult.instructions` is "a hint to the
# model" that every client sees on connect — before any tool call. We use it
# to deliver the per-conversation funnel_id rule so the audit trail isolates
# parallel conversations from the same user. Without this, the only places
# the LLM learns the rule are the run_novo_ag tool result and the
# discovery_funnel prompt template, both of which require explicit
# invocation. See _resolve_funnel_id in tools.py (4-tier resolver) for the
# fallback behavior when the LLM does not mint one.
SERVER_INSTRUCTIONS = """\
NovoMCP is a drug discovery + materials science engine. Every conversation must mint and carry a unique funnel_id for audit isolation and cross-run learning.

FUNNEL_ID PROTOCOL — apply before any tool call:
1. At conversation start, mint funnel_id = `funnel_{topic_short}_{YYYYMMDD}_{HHMMSS}` using the current UTC time. topic_short is a 2-4 char abbreviation of the focus (e.g. "aml" for acute myeloid leukemia, "gbm" for glioblastoma, "alz" for Alzheimer's, "mat" for materials work).
2. NEVER reuse a funnel_id across conversations or topics. New conversation = new id. Topic pivot mid-conversation = new id.
3. Pass funnel_id as an argument on every funnel-eligible tool call (target_discovery, validate_target, search_chembl, predict_admet, dock_molecules, run_molecular_dynamics, lead_optimization, stratify_patients, generate_dynamics, …). The server keys its audit log on it.
4. You do NOT need to call save_funnel_stage for ordinary tool calls — every call is auto-logged server-side under the funnel_id you carry. Only call save_funnel_stage to record an explicit human-reviewed checkpoint.
5. For autonomous full-funnel runs, when the user says "Novo AG" or "agm" (some MCP clients like Claude Desktop treat `/agm` as an unknown slash command — use `agm` without the slash), invoke run_novo_ag. It returns the canonical 11-stage protocol that supersedes these notes.

Why this matters: a user may run parallel conversations (e.g. cancer in one chat, Alzheimer's in another, materials in a third). Each is a distinct discovery track and must have its own audit trail. Without your explicit minting, the server falls back to a user-keyed slot that cannot distinguish parallel conversations from the same account.
"""

# Create router
router = APIRouter(prefix="/mcp", tags=["MCP"])

# Global instances (initialized in setup_mcp)
_tool_executor: Optional[MCPToolExecutor] = None
_rate_limiter: Optional[MCPRateLimiter] = None

# Assembled at setup_mcp() from environment. Defaults to LocalAuthGate +
# NoopMeter + FileAuditSink so the engine runs without external services.
_spine: Optional[Spine] = None


def _user_tier_value(user) -> str:
    """String form of the user's tier ("free" | "core" | "team" | "enterprise").

    The local user is always highest-tier; this value is only load-bearing for
    the REST-host compute paywall (rest_tool_visible) and the rate limiter.
    """
    t = getattr(user, "tier", "enterprise")
    return t.value if hasattr(t, "value") else str(t)


def setup_mcp(
    service_urls: Dict[str, str],
    internal_api_key: str,
    redis_client=None
):
    """
    Initialize MCP components.

    Called during application startup.
    """
    global _tool_executor, _rate_limiter, _spine

    _tool_executor = MCPToolExecutor(service_urls, internal_api_key)
    _rate_limiter = MCPRateLimiter(redis_client=redis_client)
    _spine = build_spine()

    _tool_executor.spine = _spine

    logger.info("NovoMCP initialized with %d tools", len(MCP_TOOLS))


async def get_mcp_user(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    api_key: Optional[str] = Query(None, alias="api_key")
) -> MCPUser:
    """
    Dependency to authenticate MCP requests.

    Accepts API key via:
    - Authorization: Bearer <key> header
    - X-API-Key header
    - api_key query parameter
    """
    # Extract API key from various sources
    key = None

    if authorization and authorization.startswith("Bearer "):
        key = authorization[7:]
    elif x_api_key:
        key = x_api_key
    elif api_key:
        key = api_key

    if not key:
        raise HTTPException(
            status_code=401,
            detail="Missing API key. Provide via Authorization header, X-API-Key header, or api_key query param."
        )

    # Resolve opaque OAuth tokens to the underlying API key
    if key.startswith("nmcp_oauth_"):
        from .oauth import resolve_oauth_token
        resolved = resolve_oauth_token(key)
        if not resolved:
            raise HTTPException(status_code=401, detail="Invalid or expired OAuth token")
        key = resolved

    # Route auth through the spine (LocalAuthGate by default).
    if _spine is None:
        raise HTTPException(status_code=503, detail="MCP not initialized")
    user = await validate_via_spine(_spine, key, mode="core")
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return user


# =============================================================================
# MCP Protocol Endpoints
# =============================================================================


async def _json_body(request: Request) -> Dict[str, Any]:
    """Parse a JSON request body robustly for the public REST surface.

    Empty body → {} (many tools take no arguments). Malformed JSON → HTTP 400
    with an actionable message, instead of an unhandled JSONDecodeError bubbling
    up to a 500. api.novomcp.com/v1 is customer-facing, so a bad payload must
    return a clear 400, not an opaque Internal Server Error.
    """
    raw = await request.body()
    if not raw or not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_json",
                "message": f"Request body is not valid JSON: {exc}",
            },
        )


@router.get("/tools")
async def list_tools(request: Request, user: MCPUser = Depends(get_mcp_user)):
    """
    List available MCP tools.

    Returns tool definitions in MCP format.
    Filters by surface (Novo vs Novo Compute, from Host header).
    """
    host = request.headers.get("host")
    is_rest = host_is_rest_api(host)
    is_compute = host_is_compute(host)

    available_tools = []
    for name, tool in MCP_TOOLS.items():
        # REST API (api.novomcp.com) = one host, all tools, compute gated by paid
        # tier. The two MCP connectors keep host-based visibility.
        if is_rest:
            if not rest_tool_visible(name, _user_tier_value(user)):
                continue
        elif not is_tool_visible(name, is_compute):
            continue
        # Local-availability filter — hide tools whose service/data deps aren't
        # wired locally. Applies to REST and MCP surfaces alike; the JSON-RPC
        # handler (line ~825) already uses visible_tools() which applies this
        # same filter. Override with NOVOMCP_SHOW_HIDDEN_TOOLS=1 for debugging.
        if not is_tool_locally_available(name):
            continue
        tool_def = {
            "name": tool["name"],
            "description": tool["description"],
            "inputSchema": tool["inputSchema"]
        }
        # Include _meta.ui for MCP Apps support (v2.7)
        if "_meta" in tool:
            tool_def["_meta"] = tool["_meta"]
        available_tools.append(tool_def)

    return {
        "tools": available_tools,
        "user": {
            "tier": _user_tier_value(user),
            "daily_remaining": user.daily_limit - user.daily_queries
        }
    }


@router.post("/tools/{tool_name}")
async def execute_tool(
    tool_name: str,
    request: Request,
    user: MCPUser = Depends(get_mcp_user)
):
    """
    Execute an MCP tool directly (non-SSE).

    For simple integrations that don't need streaming.
    """
    if tool_name not in MCP_TOOLS:
        raise HTTPException(status_code=404, detail=f"Tool not found: {tool_name}")

    # Refuse tool calls for tools whose local requirements aren't met. The
    # tools/list endpoint already hides these, but nothing stopped a client
    # from calling them by name — which produced DNS errors and 500s when
    # the tool tried to reach an unwired service URL. Now returns a clean
    # structured 503 with the missing env var so the caller can act on it.
    # Override with NOVOMCP_SHOW_HIDDEN_TOOLS=1 to let calls through
    # (useful for developing against half-configured installs).
    if not is_tool_locally_available(tool_name):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "service_not_configured",
                "message": f"Tool '{tool_name}' requires a backing service that isn't configured on this install. See tool visibility docs.",
                "tool": tool_name,
                "docs": "https://github.com/NovoMCP/novomcp/blob/main/docs/tool-availability.md",
            },
        )

    # Unified REST surface (api.novomcp.com): all tools are reachable, but
    # compute tools require a paid tier (the REST API is one host, so the
    # ncmcp_/compute paywall is enforced by tier here instead of by host).
    # Mirrors validate-compute-key's COMPUTE_TIERS. The MCP connectors
    # (ai./compute.) use the JSON-RPC path and keep host-based gating.
    if host_is_rest_api(request.headers.get("host")) and not rest_tool_visible(tool_name, _user_tier_value(user)):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "paid_plan_required",
                "message": f"'{tool_name}' is a Novo Compute tool and requires a paid plan (Core, Team, or Enterprise).",
                "upgrade_url": os.getenv("NOVOMCP_PRICING_URL", ""),
            },
        )

    body = await _json_body(request)
    arguments = body.get("arguments", {})

    # Check rate limit
    batch_size = len(arguments.get("smiles_list", [1]))
    rate_result = await _rate_limiter.check_rate_limit(
        user.user_id,
        _user_tier_value(user),
        tool_name,
        batch_size
    )

    if not rate_result.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": rate_result.result.value,
                "retry_after": rate_result.retry_after,
                "reset_at": rate_result.reset_at.isoformat()
            }
        )

    # Execute tool
    # Allow X-Org-ID header to override the API key's org_id.
    # The BFF forwards the real user's org_id from their session;
    # without this, all BFF requests run with the admin account's
    # org_id and Cosmos queries return zero results.
    org_id = request.headers.get("X-Org-ID") or user.org_id

    # X-Novo-Surface namespaces funnel_id slots per client (chrome-ext-v1,
    # word-addin-v1, ...). Absent header on /mcp/* = Claude default, preserves
    # legacy slot. Absent header on /v1/* = `api-v1` — REST callers are direct
    # API users by definition; defaulting them gives the audit row a sensible
    # surface chip without requiring every curl / Python-SDK / Hex notebook
    # caller to set the header explicitly. Clients that want finer granularity
    # (e.g. Hex sending `api-v1` + `X-Novo-Client: hex.tech-mcp`) override.
    surface = (request.headers.get("X-Novo-Surface") or "").strip()[:32]
    if not surface and request.url.path.startswith("/v1/"):
        surface = "api-v1"

    # X-Novo-Client is the finer-grained identifier within a surface — the
    # MCP `clientInfo.name` for mcp-v1 (claude-ai, claude-code, cursor, ...),
    # the tool name for api-v1 (curl, python-sdk, hex.tech-mcp), or the
    # versioned build string for first-party surfaces
    # (NovoMCP-WordAddin/1.2.3). Persisted into funnel_audit_log.system_metadata.client
    # so the dashboard renders "MCP via Claude Code" instead of just "MCP".
    client_tag = (request.headers.get("X-Novo-Client") or "").strip()[:64]

    result = await _tool_executor.execute(
        tool_name,
        arguments,
        org_id=org_id,
        user_id=request.headers.get("X-User-ID") or user.user_id,
        user_email=user.email,
        surface=surface,
        client_tag=client_tag,
    )

    # Usage recording happens inside MCPToolExecutor.execute() via the spine
    # meter (NoopMeter locally, un-metered); no separate accounting needed here.

    if result.success:
        return {
            "result": result.data,
            "usage": result.usage
        }
    else:
        # Preserve structured error data — many tools return rich payloads
        # (error_code, suggested_symbol, hgnc_search_url, retry_with, etc.)
        # in result.data alongside the human-readable result.error message.
        # Without this merge, callers see only the plain string in `detail`
        # and lose actionable fields like alias suggestions and retry hints.
        detail_obj: Dict[str, Any] = {"error": result.error}
        if isinstance(result.data, dict):
            detail_obj.update(result.data)
        elif result.data is not None:
            detail_obj["data"] = result.data
        raise HTTPException(status_code=400, detail=detail_obj)


@router.get("/resources")
async def list_resources(user: MCPUser = Depends(get_mcp_user)):
    """
    List available MCP resources.

    Returns resource definitions in MCP format.
    """
    available_resources = []
    for name, resource in MCP_RESOURCES.items():
        available_resources.append({
            "uri": resource["uri"],
            "name": resource["name"],
            "description": resource["description"],
            "mimeType": resource.get("mimeType", "application/json"),
            "annotations": resource.get("annotations", {})
        })

    return {
        "resources": available_resources,
        "user": {
            "tier": _user_tier_value(user)
        }
    }


@router.get("/resources/{resource_name}")
async def get_resource(
    resource_name: str,
    user: MCPUser = Depends(get_mcp_user)
):
    """
    Get content of an MCP resource.

    Returns the actual resource data.
    """
    if resource_name not in MCP_RESOURCES:
        raise HTTPException(status_code=404, detail=f"Resource not found: {resource_name}")

    resource = MCP_RESOURCES[resource_name]

    # Return resource content
    # First check for inline content, then look up in MCP_RESOURCE_DATA
    content = resource.get("content")
    if content is None:
        content = MCP_RESOURCE_DATA.get(resource_name)

    if callable(content):
        # Dynamic resource - call the function to get content
        content = await content() if asyncio.iscoroutinefunction(content) else content()

    return {
        "uri": resource["uri"],
        "name": resource["name"],
        "mimeType": resource.get("mimeType", "application/json"),
        "content": content
    }


@router.get("/prompts")
async def list_prompts(user: MCPUser = Depends(get_mcp_user)):
    """
    List available MCP prompts.

    Returns prompt definitions in MCP format.
    Prompts are pre-defined interaction templates.
    """
    available_prompts = []
    for name, prompt in visible_prompts().items():
        available_prompts.append({
            "name": prompt["name"],
            "description": prompt["description"],
            "arguments": prompt.get("arguments", [])
        })

    return {
        "prompts": available_prompts,
        "user": {
            "tier": _user_tier_value(user)
        }
    }


@router.get("/prompts/{prompt_name}")
async def get_prompt(
    prompt_name: str,
    user: MCPUser = Depends(get_mcp_user)
):
    """
    Get an MCP prompt by name.

    Returns the full prompt definition including message templates.
    """
    if prompt_name not in MCP_PROMPTS:
        raise HTTPException(status_code=404, detail=f"Prompt not found: {prompt_name}")

    prompt = MCP_PROMPTS[prompt_name]

    # Get messages from prompt definition or from MCP_PROMPT_TEMPLATES
    messages = prompt.get("messages")
    if messages is None:
        template = MCP_PROMPT_TEMPLATES.get(prompt_name, {})
        messages = template.get("messages", [])

    return {
        "name": prompt["name"],
        "description": prompt["description"],
        "arguments": prompt.get("arguments", []),
        "messages": messages
    }


# =============================================================================
# Tool Search (WS10) — in-memory semantic retrieval over MCP_TOOLS
# See docs/NovoMCP/AGENT-SDK-TOOL-SEARCH.md for architecture.
# =============================================================================


@router.get("/tool-search/status")
async def tool_search_status(user: MCPUser = Depends(get_mcp_user)):
    """Diagnostic snapshot of the tool-search index.

    Returns index readiness, size, embedding dimension, build duration, last
    error (if any), and whether we're running in the keyword-match fallback
    mode (embedding API unreachable at startup).
    """
    return tool_search_module.status()


@router.post("/tool-search/rebuild")
async def tool_search_rebuild(user: MCPUser = Depends(get_mcp_user)):
    """Force a synchronous rebuild of the tool-search index.

    Useful when the startup build failed (e.g., Azure OpenAI was transiently
    unavailable, env vars were misconfigured then fixed) and we need to
    retry without restarting the container. Returns the post-rebuild status.
    """
    await tool_search_module.build_index()
    return tool_search_module.status()


@router.post("/tool-search")
async def tool_search_query(
    request: Request,
    user: MCPUser = Depends(get_mcp_user),
):
    """Retrieve the top-K tools relevant to a query.

    Body:
        {
          "query": "dock this compound against EGFR",  // required
          "top_k": 5,                                  // optional, default 5
          "template": "discovery_funnel_interactive",  // optional — skip
                                                       //   retrieval, load
                                                       //   manifest
          "include_core_whitelist": true               // optional, default true
        }

    Returns:
        {
          "query": ...,
          "template": ...,
          "tools": [ {name, description, inputSchema, similarity?} ],
          "_meta": { mode, index_size, retrieved, whitelist, manifest }
        }

    Tools the caller's tier can't access are filtered out (server-driven
    entitlement — single API key, visibility gated by plan).
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be JSON.")

    query = body.get("query")
    if not isinstance(query, str) or not query.strip():
        raise HTTPException(
            status_code=400,
            detail="Field 'query' is required and must be a non-empty string.",
        )

    top_k = body.get("top_k", 5)
    if not isinstance(top_k, int) or top_k < 1 or top_k > 50:
        raise HTTPException(
            status_code=400,
            detail="Field 'top_k' must be an integer between 1 and 50.",
        )

    template = body.get("template")
    if template is not None and not isinstance(template, str):
        raise HTTPException(
            status_code=400,
            detail="Field 'template', if provided, must be a string.",
        )

    include_core_whitelist = body.get("include_core_whitelist", True)
    if not isinstance(include_core_whitelist, bool):
        raise HTTPException(
            status_code=400,
            detail="Field 'include_core_whitelist', if provided, must be a bool.",
        )

    result = await tool_search_module.search(
        query=query.strip(),
        top_k=top_k,
        template=template,
        include_core_whitelist=include_core_whitelist,
    )
    return result


# =============================================================================
# MCP JSON-RPC Endpoint (for Claude Custom Connectors & MCP Apps)
# =============================================================================

# MCP Apps resource MIME type (must match SDK exactly for Claude to recognize it)
MCP_APP_MIME_TYPE = "text/html;profile=mcp-app"

# UI Apps directory
UI_APPS_DIR = Path(__file__).parent.parent / "ui-apps"

# UI Resources for MCP Apps (registered automatically from ui-apps directory)
def _get_ui_resources() -> Dict[str, Any]:
    """Get UI resources from ui-apps directory."""
    resources = {}
    if UI_APPS_DIR.exists():
        for app_dir in UI_APPS_DIR.iterdir():
            if app_dir.is_dir() and (app_dir / "index.html").exists():
                resource_uri = f"ui://novomcp/{app_dir.name}"
                resources[resource_uri] = {
                    "uri": resource_uri,
                    "name": f"NovoMCP {app_dir.name.replace('-', ' ').title()}",
                    "mimeType": MCP_APP_MIME_TYPE,
                    "description": _get_app_description(app_dir.name),
                    "path": app_dir / "index.html"
                }
    return resources


@router.post("")
@router.post("/")
async def mcp_jsonrpc_endpoint(request: Request):
    """
    MCP JSON-RPC endpoint for Claude Custom Connectors.

    Implements the Model Context Protocol over Streamable HTTP transport.
    This is the main endpoint for MCP Apps and Claude integration.

    Supported methods:
    - initialize: Server capabilities
    - tools/list: List available tools
    - tools/call: Execute a tool
    - resources/list: List resources (including UI apps)
    - resources/read: Read resource content (including UI HTML)
    """
    try:
        body = await request.json()
    except Exception:
        return _jsonrpc_error(None, -32700, "Parse error")

    # Handle JSON-RPC request
    jsonrpc = body.get("jsonrpc")
    method = body.get("method")
    params = body.get("params", {})
    request_id = body.get("id")

    if jsonrpc != "2.0":
        return _jsonrpc_error(request_id, -32600, "Invalid Request: jsonrpc must be '2.0'")

    if not method:
        return _jsonrpc_error(request_id, -32600, "Invalid Request: method is required")

    # Route to appropriate handler
    try:
        if method == "initialize":
            result = await _handle_initialize(params)
        elif method == "tools/list":
            result = await _handle_tools_list(params, request)
        elif method == "tools/call":
            result = await _handle_tools_call(params, request)
        elif method == "resources/list":
            result = await _handle_resources_list(params)
        elif method == "resources/read":
            result = await _handle_resources_read(params)
        elif method == "prompts/list":
            result = await _handle_prompts_list(params)
        elif method == "prompts/get":
            result = await _handle_prompts_get(params)
        elif method == "ping":
            result = {}
        else:
            return _jsonrpc_error(request_id, -32601, f"Method not found: {method}")

        return _jsonrpc_success(request_id, result)

    except HTTPException as e:
        return _jsonrpc_error(request_id, -32000, e.detail)
    except Exception as e:
        logger.exception(f"Error handling MCP request: {method}")
        return _jsonrpc_error(request_id, -32603, str(e))


def _jsonrpc_success(request_id: Any, result: Any) -> Response:
    """Create a JSON-RPC success response."""
    return JSONResponse(
        content={
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result
        },
        headers={"Content-Type": "application/json"}
    )


def _jsonrpc_error(request_id: Any, code: int, message: str) -> Response:
    """Create a JSON-RPC error response."""
    return JSONResponse(
        content={
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message}
        },
        status_code=200,  # JSON-RPC errors still return 200
        headers={"Content-Type": "application/json"}
    )


async def _handle_initialize(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle MCP initialize request."""
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {"listChanged": True},
            "resources": {"listChanged": True},
            "prompts": {"listChanged": True}
        },
        "serverInfo": {
            "name": "NovoMCP",
            "version": "2.7.0"
        },
        "instructions": SERVER_INSTRUCTIONS,
    }


async def _handle_tools_list(params: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """Handle tools/list request.

    Tools are filtered by surface (Novo vs Novo Compute), derived from the
    request Host header. Compute-only tools (docking, MD, QM/NNP,
    materials, structure) are hidden from the core surface and vice versa;
    shared infra tools appear on both.
    """
    is_compute = host_is_compute(request.headers.get("host"))
    tools = []
    for name, tool in visible_tools(is_compute).items():
        tool_def = {
            "name": tool["name"],
            "description": tool["description"],
            "inputSchema": tool["inputSchema"]
        }
        # Include title if available
        if "title" in tool:
            tool_def["title"] = tool["title"]
        # Include _meta.ui for MCP Apps (matching ext-apps SDK format)
        if "_meta" in tool:
            meta = dict(tool["_meta"])
            # SDK adds flat "ui/resourceUri" key alongside nested structure
            if "ui" in meta and "resourceUri" in meta["ui"]:
                meta["ui/resourceUri"] = meta["ui"]["resourceUri"]
            tool_def["_meta"] = meta
            # SDK also adds execution hints
            tool_def["execution"] = {"taskSupport": "forbidden"}
        tools.append(tool_def)

    return {"tools": tools}


async def _handle_tools_call(params: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """Handle tools/call request."""
    tool_name = params.get("name")
    arguments = params.get("arguments", {})

    if not tool_name:
        raise HTTPException(status_code=400, detail="Missing tool name")

    if tool_name not in MCP_TOOLS:
        raise HTTPException(status_code=404, detail=f"Tool not found: {tool_name}")

    # Surface gate: a tool listed only on the other server must not be callable
    # here even if the client guessed its name (e.g. a Core key hitting a
    # Compute-only tool). Mirrors the tools/list filtering above.
    is_compute = host_is_compute(request.headers.get("host"))
    if not is_tool_visible(tool_name, is_compute):
        surface = "Novo Compute" if not is_compute else "Novo"
        raise HTTPException(
            status_code=404,
            detail=f"Tool '{tool_name}' is not available on this surface. It is served by {surface}.",
        )

    if not _tool_executor:
        raise HTTPException(status_code=503, detail="MCP not initialized")

    # Execute tool
    result = await _tool_executor.execute(
        tool_name,
        arguments,
        org_id="public",
        user_id="anonymous"
    )

    if result.success:
        # MCP Apps require structuredContent for UI rendering
        return {
            "content": [
                {"type": "text", "text": json.dumps(result.data, default=str)}
            ],
            "structuredContent": result.data if isinstance(result.data, dict) else {"data": result.data}
        }
    else:
        return {
            "content": [
                {"type": "text", "text": f"Error: {result.error}"}
            ],
            "isError": True
        }


async def _handle_resources_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle resources/list request - includes UI resources for MCP Apps."""
    resources = []

    # Add standard MCP resources
    for name, resource in MCP_RESOURCES.items():
        resources.append({
            "uri": resource["uri"],
            "name": resource["name"],
            "description": resource.get("description", ""),
            "mimeType": resource.get("mimeType", "application/json")
        })

    # Add UI resources for MCP Apps
    ui_resources = _get_ui_resources()
    for uri, resource in ui_resources.items():
        resources.append({
            "uri": resource["uri"],
            "name": resource["name"],
            "description": resource["description"],
            "mimeType": resource["mimeType"]
        })

    return {"resources": resources}


async def _handle_resources_read(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle resources/read request - serves UI app HTML for MCP Apps."""
    uri = params.get("uri")

    if not uri:
        raise HTTPException(status_code=400, detail="Missing resource URI")

    # Check if it's a UI resource (ui:// scheme)
    if uri.startswith("ui://novomcp/"):
        app_name = uri.replace("ui://novomcp/", "")
        ui_resources = _get_ui_resources()

        if uri not in ui_resources:
            raise HTTPException(status_code=404, detail=f"UI resource not found: {uri}")

        resource = ui_resources[uri]
        html_path = resource["path"]

        if not html_path.exists():
            raise HTTPException(status_code=404, detail=f"UI app file not found: {app_name}")

        html_content = html_path.read_text(encoding="utf-8")

        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": MCP_APP_MIME_TYPE,
                    "text": html_content
                }
            ]
        }

    # Check standard resources
    resource_name = uri.split("/")[-1] if "/" in uri else uri
    if resource_name in MCP_RESOURCES:
        resource = MCP_RESOURCES[resource_name]
        content = resource.get("content")
        if content is None:
            content = MCP_RESOURCE_DATA.get(resource_name)
        if callable(content):
            content = await content() if asyncio.iscoroutinefunction(content) else content()

        return {
            "contents": [
                {
                    "uri": resource["uri"],
                    "mimeType": resource.get("mimeType", "application/json"),
                    "text": json.dumps(content) if isinstance(content, (dict, list)) else str(content)
                }
            ]
        }

    raise HTTPException(status_code=404, detail=f"Resource not found: {uri}")


async def _handle_prompts_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle prompts/list request. Hides prompts whose orchestrated tools
    are not all locally available. Override with NOVOMCP_SHOW_HIDDEN_PROMPTS=1.
    """
    prompts = []
    for name, prompt in visible_prompts().items():
        prompts.append({
            "name": prompt["name"],
            "description": prompt.get("description", ""),
            "arguments": prompt.get("arguments", [])
        })
    return {"prompts": prompts}


async def _handle_prompts_get(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle prompts/get request."""
    prompt_name = params.get("name")
    if not prompt_name or prompt_name not in MCP_PROMPTS:
        raise HTTPException(status_code=404, detail=f"Prompt not found: {prompt_name}")

    prompt = MCP_PROMPTS[prompt_name]
    messages = prompt.get("messages")
    if messages is None:
        template = MCP_PROMPT_TEMPLATES.get(prompt_name, {})
        messages = template.get("messages", [])

    return {
        "description": prompt.get("description", ""),
        "messages": messages
    }


# =============================================================================
# MCP Apps - UI Resources (v2.7)
# =============================================================================

# MIME types for UI resources
UI_MIME_TYPES = {
    ".html": "text/html",
    ".js": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


@router.get("/ui/{app_name}")
async def get_ui_app(app_name: str):
    """
    Serve MCP App UI resources.

    MCP Apps are interactive UI components that render in Claude conversations.
    This endpoint serves the bundled HTML/JS/CSS for each app.

    Available apps:
    - molecule-viewer: Interactive 3D molecule visualization
    - admet-dashboard: ADMET prediction visualization (coming soon)
    - research-explorer: Research results explorer (coming soon)
    """
    # Resolve app directory
    app_dir = UI_APPS_DIR / app_name

    if not app_dir.exists() or not app_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"UI app not found: {app_name}")

    # Serve index.html
    index_file = app_dir / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail=f"UI app {app_name} has no index.html")

    return FileResponse(
        index_file,
        media_type="text/html",
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Frame-Options": "ALLOWALL"  # Allow embedding in iframes (required for MCP Apps)
        }
    )


@router.get("/ui/{app_name}/{file_path:path}")
async def get_ui_app_resource(app_name: str, file_path: str):
    """
    Serve static resources for MCP App UI (JS, CSS, images, etc.).
    """
    # Resolve file path
    app_dir = UI_APPS_DIR / app_name
    resource_file = app_dir / file_path

    # Security: ensure path doesn't escape app directory
    try:
        resource_file.resolve().relative_to(app_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    if not resource_file.exists() or not resource_file.is_file():
        raise HTTPException(status_code=404, detail=f"Resource not found: {file_path}")

    # Determine MIME type
    suffix = resource_file.suffix.lower()
    mime_type = UI_MIME_TYPES.get(suffix, "application/octet-stream")

    return FileResponse(
        resource_file,
        media_type=mime_type,
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Frame-Options": "ALLOWALL"
        }
    )


@router.get("/ui-apps")
async def list_ui_apps():
    """
    List available MCP App UIs.

    Returns metadata about available UI apps for tool integration.
    """
    apps = []

    if UI_APPS_DIR.exists():
        for app_dir in UI_APPS_DIR.iterdir():
            if app_dir.is_dir() and (app_dir / "index.html").exists():
                apps.append({
                    "name": app_dir.name,
                    "resourceUri": f"ui://novomcp/{app_dir.name}",
                    "url": f"/mcp/ui/{app_dir.name}",
                    "description": _get_app_description(app_dir.name)
                })

    return {
        "apps": apps,
        "total": len(apps),
        "note": "MCP Apps render interactive UI components directly in Claude conversations"
    }


def _get_app_description(app_name: str) -> str:
    """Get description for a UI app."""
    descriptions = {
        "molecule-viewer": "Interactive 3D molecule visualization with NGL Viewer. Supports rotation, zoom, multiple color schemes, and ADMET overlay.",
        "admet-dashboard": "ADMET prediction visualization with radar charts and traffic-light indicators.",
        "research-explorer": "Interactive research results explorer with timeline and filtering.",
        "structure-viewer": "Protein structure visualization with confidence coloring.",
        "credit-usage": "Credit usage dashboard showing account tier, balance, and research value realized.",
        "jobs": "Pipeline jobs tracker showing MD simulations, docking, and structure predictions with status and results.",
        "md-results": "MD simulation results with RMSD convergence, equilibration analysis (temperature, pressure, density, energy), and stability metrics.",
        "pipeline-audit": "Per-molecule audit trail for pipeline executions showing disposition, tool results, structural-alert flags, and exclusion reasons.",
        "docking-viewer": "Docking results with protein method card (resolution, method, organism), binding affinity rankings, interaction contacts, and strain validation.",
        "lead-comparison": "Side-by-side property comparison table for lead optimization variants with color-coded drug-likeness ranges and delta-vs-seed analysis.",
    }
    return descriptions.get(app_name, "MCP App UI component")


# =============================================================================
# Utility Endpoints
# =============================================================================

@router.get("/health")
async def mcp_health():
    """Health check for MCP endpoint."""
    return {
        "status": "ok",
        "server": "NovoMCP",
        "version": "2.0.0",
        "transport": "streamable-http",
        "tools_available": len(MCP_TOOLS)
    }


@router.get("/usage")
async def get_usage(user: MCPUser = Depends(get_mcp_user)):
    """Get current usage statistics for the authenticated user."""
    stats = await _rate_limiter.get_usage_stats(user.user_id, _user_tier_value(user))
    return {
        "user_id": user.user_id,
        "email": user.email,
        "org": user.org_name,
        "tier": _user_tier_value(user),
        **stats
    }


@router.get("/info")
async def mcp_info():
    """
    Public endpoint with MCP server information.

    Can be used by Claude to discover the server capabilities.
    """
    return {
        "name": "NovoMCP",
        "description": "Molecular Intelligence MCP Server - 15 tools, 4 resources, 5 prompts for ADMET predictions, regulatory compliance, literature search, molecular analysis, and autonomous discovery funnels",
        "version": "2.0.0",
        "protocol": "mcp",
        "transport": "streamable-http",
        "capabilities": {
            "tools": len(MCP_TOOLS),
            "resources": len(MCP_RESOURCES),
            "prompts": len(MCP_PROMPTS)
        },
        "endpoints": {
            "tools": "/mcp/tools",
            "execute": "/mcp/tools/{tool_name}",
            "resources": "/mcp/resources",
            "resource": "/mcp/resources/{resource_name}",
            "prompts": "/mcp/prompts",
            "prompt": "/mcp/prompts/{prompt_name}",
            "health": "/mcp/health",
            "usage": "/mcp/usage"
        },
        "deprecated_endpoints": {
            "sse": "/mcp/sse",
            "sse_call": "/mcp/sse/call",
            "note": "SSE transport is deprecated. Use Streamable HTTP endpoints instead."
        },
        "authentication": {
            "type": "api_key",
            "methods": [
                {"header": "Authorization", "format": "Bearer <key>"},
                {"header": "X-API-Key"},
                {"query": "api_key"}
            ]
        },
        "tiers": {
            "free": {"daily_limit": 100, "tools": 2},
            "pro": {"daily_limit": 1000, "tools": 8},
            "team": {"daily_limit": 10000, "tools": 13},
            "enterprise": {"daily_limit": "unlimited", "tools": 15}
        },
        "contact": "ari@novomcp.com",
        "documentation": "https://novomcp.com/docs"
    }


# =============================================================================
# /v1 — versioned customer REST API alias
# =============================================================================
# The customer-facing REST API. Same handlers as /mcp/tools* (auth, rate limit,
# billing, and the unified-surface tier gating all live in the handlers), just a
# stable, versioned path. Declared as explicit routes (not a re-mount of the
# /mcp-prefixed router, which would yield /v1/mcp/tools). Back-compat: the
# original /mcp/tools* paths keep working.
v1_router = APIRouter(prefix="/v1", tags=["NovoMCP v1"])
v1_router.add_api_route("/tools", list_tools, methods=["GET"])
v1_router.add_api_route("/tools/{tool_name}", execute_tool, methods=["POST"])
v1_router.add_api_route("/usage", get_usage, methods=["GET"])
v1_router.add_api_route("/info", mcp_info, methods=["GET"])
v1_router.add_api_route("/health", mcp_health, methods=["GET"])
