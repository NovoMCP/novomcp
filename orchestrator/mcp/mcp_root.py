"""
NovoMCP Root Handler - Streamable HTTP Transport

Implements MCP protocol at the root path (/) for Claude custom connectors.
Claude expects MCP at root, not at /mcp.

Protocol Version: 2025-06-18
Reference: https://spec.modelcontextprotocol.io/specification/2025-06-18/
"""

import json
import logging
import uuid
from typing import Optional, Dict, Any
from datetime import datetime

from fastapi import APIRouter, Request, Response, HTTPException, Header
from fastapi.responses import JSONResponse

from .tools import MCP_TOOLS, MCP_PROMPTS, MCP_PROMPT_TEMPLATES, MCP_RESOURCES, MCP_RESOURCE_DATA, MCPToolExecutor, ToolTier, host_is_compute, is_tool_visible
from .auth import MCPAuthManager, MCPUser

logger = logging.getLogger(__name__)

# MCP Protocol Version
MCP_PROTOCOL_VERSION = "2025-06-18"

# Create router without prefix (mounts at root)
router = APIRouter(tags=["MCP-Root"])

# Global instances (set by setup_mcp_root)
_tool_executor: Optional[MCPToolExecutor] = None
_auth_manager: Optional[MCPAuthManager] = None

# Session storage (use Redis in production)
_sessions: Dict[str, Dict[str, Any]] = {}


def setup_mcp_root(tool_executor: MCPToolExecutor, auth_manager: MCPAuthManager):
    """Initialize MCP root handler with shared components."""
    global _tool_executor, _auth_manager
    _tool_executor = tool_executor
    _auth_manager = auth_manager
    logger.info("MCP root handler initialized")


async def _get_user_from_request(request: Request) -> Optional[MCPUser]:
    """Extract and validate user from request headers."""
    if not _auth_manager:
        logger.warning("AUTH MANAGER NOT INITIALIZED - rejecting request")
        return None

    # Check Authorization header
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        # Resolve opaque OAuth tokens to the underlying API key
        if token.startswith("nmcp_oauth_"):
            from .oauth import resolve_oauth_token
            resolved = resolve_oauth_token(token)
            if not resolved:
                logger.warning(f"Invalid/expired OAuth token: {token[:16]}...")
                return None
            token = resolved
        logger.info(f"Validating Bearer token: {token[:10]}...")
        user = await _auth_manager.validate_api_key(token)
        if user:
            logger.info(f"User authenticated: {user.user_id}")
        else:
            logger.warning(f"Invalid token: {token[:10]}...")
        return user

    # Check X-API-Key header
    api_key = request.headers.get("x-api-key")
    if api_key:
        logger.info(f"Validating X-API-Key: {api_key[:10]}...")
        user = await _auth_manager.validate_api_key(api_key)
        if user:
            logger.info(f"User authenticated: {user.user_id}")
        else:
            logger.warning(f"Invalid API key: {api_key[:10]}...")
        return user

    logger.warning("No authentication credentials provided in request")
    return None


def _make_jsonrpc_response(id: Any, result: Any = None, error: Dict = None) -> Dict:
    """Create a JSON-RPC 2.0 response."""
    response = {"jsonrpc": "2.0", "id": id}
    if error:
        response["error"] = error
    else:
        response["result"] = result
    return response


def _make_jsonrpc_error(id: Any, code: int, message: str, data: Any = None) -> Dict:
    """Create a JSON-RPC error response."""
    error = {"code": code, "message": message}
    if data:
        error["data"] = data
    return _make_jsonrpc_response(id, error=error)


# =============================================================================
# HEAD endpoint - Protocol version discovery
# =============================================================================

@router.head("/")
async def mcp_head():
    """
    HEAD request for MCP protocol discovery.
    Returns MCP-Protocol-Version header that Claude checks.
    """
    return Response(
        content="",
        headers={
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            "Content-Type": "application/json"
        }
    )


# =============================================================================
# POST endpoint - JSON-RPC handler
# =============================================================================

@router.post("/")
async def mcp_jsonrpc(request: Request):
    """
    Main MCP JSON-RPC endpoint.

    Handles all MCP protocol messages:
    - initialize: Start a session
    - notifications/initialized: Client ready
    - tools/list: List available tools
    - tools/call: Execute a tool
    - ping: Health check
    """
    # Parse JSON-RPC request
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content=_make_jsonrpc_error(None, -32700, "Parse error")
        )

    # Get session ID from header (if exists)
    session_id = request.headers.get("mcp-session-id")

    # Handle batched requests
    if isinstance(body, list):
        responses = []
        for req in body:
            resp = await _handle_jsonrpc_request(req, request, session_id)
            if resp:  # Notifications don't return responses
                responses.append(resp)
        return JSONResponse(content=responses if responses else None)

    # Handle single request
    response = await _handle_jsonrpc_request(body, request, session_id)

    # Add session header if this is initialize response
    headers = {}
    if body.get("method") == "initialize" and response and "result" in response:
        new_session_id = response.get("result", {}).get("_sessionId")
        if new_session_id:
            headers["Mcp-Session-Id"] = new_session_id

    if response:
        return JSONResponse(content=response, headers=headers)
    else:
        return Response(status_code=204)  # Notification, no response


async def _handle_jsonrpc_request(
    body: Dict[str, Any],
    request: Request,
    session_id: Optional[str]
) -> Optional[Dict]:
    """Handle a single JSON-RPC request."""

    # Validate JSON-RPC structure
    if body.get("jsonrpc") != "2.0":
        return _make_jsonrpc_error(body.get("id"), -32600, "Invalid Request")

    method = body.get("method")
    params = body.get("params", {})
    req_id = body.get("id")  # None for notifications

    if not method:
        return _make_jsonrpc_error(req_id, -32600, "Missing method")

    # Route to handler
    try:
        if method == "initialize":
            result = await _handle_initialize(params, request)
            return _make_jsonrpc_response(req_id, result)

        elif method == "notifications/initialized":
            # Notification - no response
            logger.info(f"Client initialized for session {session_id}")
            return None

        elif method == "ping":
            return _make_jsonrpc_response(req_id, {})

        elif method == "tools/list":
            result = await _handle_tools_list(params, request, session_id)
            return _make_jsonrpc_response(req_id, result)

        elif method == "tools/call":
            result = await _handle_tools_call(params, request, session_id)
            return _make_jsonrpc_response(req_id, result)

        elif method == "resources/list":
            result = await _handle_resources_list(request)
            return _make_jsonrpc_response(req_id, result)

        elif method == "resources/read":
            result = await _handle_resources_read(params, request)
            return _make_jsonrpc_response(req_id, result)

        elif method == "prompts/list":
            result = await _handle_prompts_list(request)
            return _make_jsonrpc_response(req_id, result)

        elif method == "prompts/get":
            result = await _handle_prompts_get(params, request)
            return _make_jsonrpc_response(req_id, result)

        else:
            return _make_jsonrpc_error(req_id, -32601, f"Method not found: {method}")

    except HTTPException as e:
        return _make_jsonrpc_error(req_id, -32000, e.detail)
    except Exception as e:
        logger.exception(f"Error handling {method}")
        return _make_jsonrpc_error(req_id, -32603, str(e))


async def _handle_initialize(params: Dict, request: Request) -> Dict:
    """Handle initialize request - start MCP session."""
    # REQUIRE authentication for initialization
    user = await _get_user_from_request(request)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Please complete OAuth flow first."
        )

    # Create new session with authenticated user
    session_id = str(uuid.uuid4())

    _sessions[session_id] = {
        "created_at": datetime.utcnow().isoformat(),
        "client_info": params.get("clientInfo", {}),
        "user_id": user.user_id,
        "user_tier": user.tier.value,
        "authenticated": True
    }

    logger.info(f"MCP session initialized for user {user.user_id}: {session_id}")

    # SERVER_INSTRUCTIONS is owned by mcp/router.py — single source of truth.
    # Imported lazily to keep the module import graph acyclic at module load.
    from mcp.router import SERVER_INSTRUCTIONS

    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "serverInfo": {
            "name": "NovoMCP",
            "version": "1.0.0"
        },
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"listChanged": False},
            "prompts": {"listChanged": False}
        },
        "instructions": SERVER_INSTRUCTIONS,
        "_sessionId": session_id  # Include for header
    }


async def _handle_tools_list(params: Dict, request: Request, session_id: str) -> Dict:
    """Handle tools/list - return available tools. REQUIRES AUTHENTICATION."""
    user = await _get_user_from_request(request)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required to list tools."
        )

    user_tier = user.tier.value

    # Get tools available for this tier
    tier_order = [ToolTier.FREE, ToolTier.PRO, ToolTier.TEAM, ToolTier.ENTERPRISE]
    try:
        user_tier_index = tier_order.index(ToolTier(user_tier))
    except ValueError:
        user_tier_index = 0  # Default to free

    # Surface gate (Novo vs Novo Compute) layered on top of the tier gate:
    # a tool must be both visible on this host's surface AND within the user's tier.
    is_compute = host_is_compute(request.headers.get("host"))

    tools = []
    for name, tool in MCP_TOOLS.items():
        if not is_tool_visible(name, is_compute):
            continue
        tool_tier_index = tier_order.index(tool["tier"])
        if user_tier_index >= tool_tier_index:
            tools.append({
                "name": tool["name"],
                "description": tool["description"],
                "inputSchema": tool["inputSchema"]
            })

    surface = "compute" if is_compute else "core"
    logger.info(f"Tools listed for user {user.user_id} on {surface} surface: {len(tools)} tools")
    return {"tools": tools}


async def _handle_tools_call(params: Dict, request: Request, session_id: str) -> Dict:
    """Handle tools/call - execute a tool."""
    tool_name = params.get("name")
    arguments = params.get("arguments", {})

    if not tool_name:
        raise HTTPException(status_code=400, detail="Missing tool name")

    if tool_name not in MCP_TOOLS:
        raise HTTPException(status_code=404, detail=f"Tool not found: {tool_name}")

    # Surface gate: reject compute-only tools on the core host (and vice versa),
    # even if the client names the tool directly. Matches tools/list filtering.
    is_compute = host_is_compute(request.headers.get("host"))
    if not is_tool_visible(tool_name, is_compute):
        surface = "Novo Compute" if not is_compute else "Novo"
        raise HTTPException(
            status_code=404,
            detail=f"Tool '{tool_name}' is not available on this surface. It is served by {surface}.",
        )

    # Get user for authorization
    user = await _get_user_from_request(request)

    if not user:
        raise HTTPException(status_code=401, detail="Authentication required for tool execution")

    if not _tool_executor:
        raise HTTPException(status_code=503, detail="Tool executor not initialized")

    # Execute tool with credit tracking
    result = await _tool_executor.execute(
        tool_name=tool_name,
        arguments=arguments,
        user_tier=ToolTier(user.tier.value),
        org_id=user.org_id,
        user_id=user.user_id,
        session_id=session_id,
        credits_available=user.credits_available,
    )

    # Record usage (rate limiting)
    if _auth_manager:
        await _auth_manager.record_usage(user, result.usage.get("queries", 1))

    if result.success:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result.data, indent=2)
                }
            ],
            "isError": False
        }
    else:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: {result.error}"
                }
            ],
            "isError": True
        }


async def _handle_prompts_list(request: Request) -> Dict:
    """Handle prompts/list - return available prompts."""
    user = await _get_user_from_request(request)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required to list prompts."
        )

    prompts = []
    for name, prompt in MCP_PROMPTS.items():
        # Build arguments list from prompt definition
        arguments = []
        for arg in prompt.get("arguments", []):
            arguments.append({
                "name": arg["name"],
                "description": arg.get("description", ""),
                "required": arg.get("required", False)
            })

        prompts.append({
            "name": name,
            "description": prompt.get("description", ""),
            "arguments": arguments
        })

    logger.info(f"Prompts listed for user {user.user_id}: {len(prompts)} prompts")
    return {"prompts": prompts}


async def _handle_prompts_get(params: Dict, request: Request) -> Dict:
    """Handle prompts/get - return a specific prompt with arguments filled in."""
    prompt_name = params.get("name")
    arguments = params.get("arguments", {})

    if not prompt_name:
        raise HTTPException(status_code=400, detail="Missing prompt name")

    if prompt_name not in MCP_PROMPTS:
        raise HTTPException(status_code=404, detail=f"Prompt not found: {prompt_name}")

    user = await _get_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Get prompt template
    if prompt_name not in MCP_PROMPT_TEMPLATES:
        raise HTTPException(status_code=404, detail=f"Prompt template not found: {prompt_name}")

    template = MCP_PROMPT_TEMPLATES[prompt_name]
    messages = template.get("messages", [])

    # Fill in arguments
    filled_messages = []
    for msg in messages:
        content = msg.get("content", {})
        if isinstance(content, dict) and content.get("type") == "text":
            text = content.get("text", "")
            # Replace placeholders with arguments
            for arg_name, arg_value in arguments.items():
                text = text.replace(f"{{{arg_name}}}", str(arg_value))
            filled_messages.append({
                "role": msg.get("role", "user"),
                "content": {"type": "text", "text": text}
            })
        else:
            filled_messages.append(msg)

    logger.info(f"Prompt '{prompt_name}' retrieved for user {user.user_id}")
    return {
        "description": MCP_PROMPTS[prompt_name].get("description", ""),
        "messages": filled_messages
    }


async def _handle_resources_list(request: Request) -> Dict:
    """Handle resources/list - return available resources."""
    user = await _get_user_from_request(request)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required to list resources."
        )

    resources = []
    for name, resource in MCP_RESOURCES.items():
        resources.append({
            "uri": resource.get("uri", f"novomcp://resources/{name}"),
            "name": resource.get("name", name),
            "description": resource.get("description", ""),
            "mimeType": resource.get("mimeType", "application/json"),
        })

    logger.info(f"Resources listed for user {user.user_id}: {len(resources)} resources")
    return {"resources": resources}


async def _handle_resources_read(params: Dict, request: Request) -> Dict:
    """Handle resources/read - return resource content."""
    uri = params.get("uri")

    if not uri:
        raise HTTPException(status_code=400, detail="Missing resource URI")

    user = await _get_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Extract resource name from URI (e.g., "novomcp://resources/compliance_schedules" -> "compliance_schedules")
    resource_name = None
    if uri.startswith("novomcp://resources/"):
        resource_name = uri.replace("novomcp://resources/", "")

    if not resource_name or resource_name not in MCP_RESOURCES:
        raise HTTPException(status_code=404, detail=f"Resource not found: {uri}")

    if resource_name not in MCP_RESOURCE_DATA:
        raise HTTPException(status_code=404, detail=f"Resource data not found: {resource_name}")

    resource_data = MCP_RESOURCE_DATA[resource_name]

    logger.info(f"Resource '{resource_name}' read by user {user.user_id}")
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(resource_data, indent=2)
            }
        ]
    }
