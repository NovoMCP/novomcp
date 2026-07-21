"""
NovoMCP OAuth 2.0 Implementation

Implements OAuth 2.0 for MCP integration (Claude, ChatGPT, Cursor, Windsurf,
VS Code, Gemini, and any MCP-compatible client).
Supports:
- Authorization Code flow with PKCE (RFC 7636)
- Dynamic Client Registration (RFC 7591)
- Token exchange and refresh
- Token revocation (RFC 7009)

Reference: https://modelcontextprotocol.io/docs/concepts/authentication
"""

import os
import json
import secrets
import hashlib
import base64
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse, urljoin

from fastapi import APIRouter, Request, HTTPException, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel

from .auth import MCPAuthManager

logger = logging.getLogger(__name__)

# OAuth configuration
OAUTH_ISSUER = os.environ.get("OAUTH_ISSUER", "https://novomcp.com")
AUTH_CODE_EXPIRY_SECONDS = 600  # 10 minutes
ACCESS_TOKEN_EXPIRY_SECONDS = 86400 * 30  # 30 days
REFRESH_TOKEN_EXPIRY_SECONDS = 86400 * 90  # 90 days

# Redis key prefixes for OAuth stores
REDIS_AUTH_CODE_PREFIX = "novomcp:oauth:code:"
REDIS_TOKEN_PREFIX = "novomcp:oauth:token:"
REDIS_REFRESH_PREFIX = "novomcp:oauth:refresh:"
REDIS_CLIENT_PREFIX = "novomcp:oauth:client:"

# In-memory fallback caps (used only when Redis is unavailable)
MAX_AUTH_CODES = 10_000
MAX_REGISTERED_CLIENTS = 1_000
MAX_OAUTH_TOKENS = 50_000

# In-memory fallback stores
_auth_codes: Dict[str, Dict[str, Any]] = {}
_registered_clients: Dict[str, Dict[str, Any]] = {}
_oauth_tokens: Dict[str, Dict[str, Any]] = {}
_refresh_tokens: Dict[str, Dict[str, Any]] = {}

# Pre-register Claude as a client
_CLAUDE_CLIENT = {
    "client_secret": None,
    "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
    "client_name": "Claude"
}
_registered_clients["claude"] = _CLAUDE_CLIENT

router = APIRouter(tags=["OAuth"])

# Global references
_auth_manager: Optional[MCPAuthManager] = None
_redis = None  # Redis client for persistent OAuth storage


def setup_oauth(auth_manager: MCPAuthManager, redis_client=None):
    """Initialize OAuth with auth manager and optional Redis for persistence."""
    global _auth_manager, _redis
    _auth_manager = auth_manager
    _redis = redis_client

    if _redis:
        # Ensure Claude is registered in Redis
        _store_client("claude", _CLAUDE_CLIENT)
        logger.info("OAuth initialized with Redis-backed persistent storage")
    else:
        logger.info(
            "OAuth using in-memory stores. "
            "Set REDIS_URL for persistence across restarts."
        )


# =============================================================================
# Redis-backed storage helpers
# =============================================================================

def _store_auth_code(code: str, data: Dict[str, Any]):
    """Store auth code in Redis (with TTL) or in-memory fallback."""
    if _redis:
        # Serialize datetime to ISO string for JSON storage
        store_data = {**data, "expires_at": data["expires_at"].isoformat()}
        _redis.set(
            f"{REDIS_AUTH_CODE_PREFIX}{code}",
            json.dumps(store_data),
            ex=AUTH_CODE_EXPIRY_SECONDS
        )
    else:
        if len(_auth_codes) >= MAX_AUTH_CODES:
            _cleanup_expired_codes()
        _auth_codes[code] = data


def _get_auth_code(code: str) -> Optional[Dict[str, Any]]:
    """Retrieve auth code from Redis or in-memory fallback."""
    if _redis:
        raw = _redis.get(f"{REDIS_AUTH_CODE_PREFIX}{code}")
        if not raw:
            return None
        data = json.loads(raw)
        data["expires_at"] = datetime.fromisoformat(data["expires_at"])
        return data
    return _auth_codes.get(code)


def _delete_auth_code(code: str):
    """Delete auth code (one-time use)."""
    if _redis:
        _redis.delete(f"{REDIS_AUTH_CODE_PREFIX}{code}")
    else:
        _auth_codes.pop(code, None)


def _store_token(token: str, data: Dict[str, Any]):
    """Store OAuth token in Redis (with TTL) or in-memory fallback."""
    if _redis:
        store_data = {**data, "expires_at": data["expires_at"].isoformat()}
        ttl = int((data["expires_at"] - datetime.utcnow()).total_seconds())
        if ttl > 0:
            _redis.set(
                f"{REDIS_TOKEN_PREFIX}{token}",
                json.dumps(store_data),
                ex=ttl
            )
    else:
        if len(_oauth_tokens) >= MAX_OAUTH_TOKENS:
            _cleanup_expired_tokens()
        _oauth_tokens[token] = data


def _get_token(token: str) -> Optional[Dict[str, Any]]:
    """Retrieve OAuth token from Redis or in-memory fallback."""
    if _redis:
        raw = _redis.get(f"{REDIS_TOKEN_PREFIX}{token}")
        if not raw:
            return None
        data = json.loads(raw)
        data["expires_at"] = datetime.fromisoformat(data["expires_at"])
        return data
    return _oauth_tokens.get(token)


def _delete_token(token: str):
    """Delete/revoke an OAuth token."""
    if _redis:
        _redis.delete(f"{REDIS_TOKEN_PREFIX}{token}")
    else:
        _oauth_tokens.pop(token, None)


def _store_refresh_token(token: str, data: Dict[str, Any]):
    """Store refresh token in Redis (with TTL) or in-memory fallback."""
    if _redis:
        store_data = {**data, "expires_at": data["expires_at"].isoformat()}
        ttl = int((data["expires_at"] - datetime.utcnow()).total_seconds())
        if ttl > 0:
            _redis.set(
                f"{REDIS_REFRESH_PREFIX}{token}",
                json.dumps(store_data),
                ex=ttl
            )
    else:
        _refresh_tokens[token] = data


def _get_refresh_token(token: str) -> Optional[Dict[str, Any]]:
    """Retrieve refresh token from Redis or in-memory fallback."""
    if _redis:
        raw = _redis.get(f"{REDIS_REFRESH_PREFIX}{token}")
        if not raw:
            return None
        data = json.loads(raw)
        data["expires_at"] = datetime.fromisoformat(data["expires_at"])
        return data
    return _refresh_tokens.get(token)


def _delete_refresh_token(token: str):
    """Delete a refresh token."""
    if _redis:
        _redis.delete(f"{REDIS_REFRESH_PREFIX}{token}")
    else:
        _refresh_tokens.pop(token, None)


def _store_client(client_id: str, data: Dict[str, Any]):
    """Store registered client in Redis or in-memory fallback."""
    if _redis:
        _redis.set(f"{REDIS_CLIENT_PREFIX}{client_id}", json.dumps(data))
    else:
        if len(_registered_clients) >= MAX_REGISTERED_CLIENTS:
            raise HTTPException(status_code=503, detail="Client registration limit reached")
        _registered_clients[client_id] = data


def _get_client(client_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve registered client from Redis or in-memory fallback."""
    if _redis:
        raw = _redis.get(f"{REDIS_CLIENT_PREFIX}{client_id}")
        if raw:
            return json.loads(raw)
        return None
    return _registered_clients.get(client_id)


def _normalize_empty(value: Optional[str]) -> Optional[str]:
    """Convert empty strings to None for consistent handling across clients."""
    if value is None or value.strip() == "":
        return None
    return value


# =============================================================================
# OAuth Discovery Endpoint
# =============================================================================

def _get_issuer_from_request(request: Request) -> str:
    """
    Derive the OAuth issuer URL from the request host.
    This ensures the issuer matches the URL used to connect.
    """
    host = request.headers.get("host", "novomcp.com")
    # Use X-Forwarded-Host if behind a proxy (Azure Front Door)
    forwarded_host = request.headers.get("x-forwarded-host")
    if forwarded_host:
        host = forwarded_host
    # Strip port if present
    if ":" in host and not host.startswith("["):
        host = host.split(":")[0]
    return f"https://{host}"


@router.get("/.well-known/oauth-authorization-server")
async def oauth_metadata(request: Request):
    """
    OAuth 2.0 Authorization Server Metadata (RFC 8414)

    MCP clients use this to discover OAuth endpoints.
    Issuer is dynamically set based on request host to avoid mismatches.
    """
    issuer = _get_issuer_from_request(request)
    logger.info(f"[OAuth:discovery] issuer={issuer} host={request.headers.get('host')} "
                f"user-agent={request.headers.get('user-agent', 'unknown')}")
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oauth/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "registration_endpoint": f"{issuer}/oauth/register",
        "revocation_endpoint": f"{issuer}/oauth/revoke",
        "scopes_supported": ["mcp:tools", "mcp:read", "mcp:write"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256", "plain"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "none"],
        "revocation_endpoint_auth_methods_supported": ["client_secret_post", "none"],
        "service_documentation": f"{issuer}/docs"
    }


# =============================================================================
# Authorization Endpoint
# =============================================================================

@router.get("/oauth/authorize")
async def authorize_get(
    request: Request,
    response_type: str = Query("code"),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    state: str = Query(None),
    scope: str = Query("mcp:tools"),
    code_challenge: str = Query(None),
    code_challenge_method: str = Query("S256"),
    resource: str = Query(None),
):
    """
    OAuth Authorization Endpoint - GET

    Shows login page for user to enter their API key.
    """
    # Normalize empty params
    state = _normalize_empty(state)
    code_challenge = _normalize_empty(code_challenge)
    resource = _normalize_empty(resource)

    # Capture any extra query params clients send (future-proofing)
    known_params = {"response_type", "client_id", "redirect_uri", "state",
                    "scope", "code_challenge", "code_challenge_method", "resource"}
    extra_params = {k: v for k, v in request.query_params.items() if k not in known_params}

    # Log the full request for debugging client-specific issues
    logger.info(
        f"[OAuth:authorize:GET] client_id={client_id} "
        f"redirect_uri={redirect_uri} state={'yes' if state else 'no'} "
        f"scope={scope} pkce={'yes' if code_challenge else 'no'} "
        f"method={code_challenge_method} resource={resource} "
        f"extra_params={list(extra_params.keys()) if extra_params else 'none'} "
        f"user-agent={request.headers.get('user-agent', 'unknown')}"
    )

    if response_type != "code":
        logger.warning(f"[OAuth:authorize:GET] rejected response_type={response_type}")
        return HTMLResponse(
            content=_error_page("Invalid response_type. Only 'code' is supported."),
            status_code=400
        )

    # Look up client — unknown clients are allowed (dynamic registration may happen later)
    client = _get_client(client_id)
    if client:
        logger.info(f"[OAuth:authorize:GET] known client: {client.get('client_name', client_id)}")
    else:
        logger.info(f"[OAuth:authorize:GET] unknown client_id={client_id} (accepting anyway)")

    # Render login page
    return HTMLResponse(content=_login_page(
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        scope=scope,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        resource=resource,
        extra_params=extra_params,
    ))


@router.post("/oauth/authorize")
async def authorize_post(
    request: Request,
    api_key: str = Form(...),
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(None),
    scope: str = Form("mcp:tools"),
    code_challenge: str = Form(None),
    code_challenge_method: str = Form("S256"),
    resource: str = Form(None),
    extra_params_json: str = Form("{}"),
):
    """
    OAuth Authorization Endpoint - POST

    Validates API key and redirects with auth code.
    """
    # Normalize empty form values (hidden fields submit "" not null)
    state = _normalize_empty(state)
    code_challenge = _normalize_empty(code_challenge)
    code_challenge_method = _normalize_empty(code_challenge_method) or "S256"
    resource = _normalize_empty(resource)

    # Parse extra params forwarded from the GET
    try:
        extra_params = json.loads(extra_params_json) if extra_params_json else {}
    except json.JSONDecodeError:
        extra_params = {}

    logger.info(
        f"[OAuth:authorize:POST] client_id={client_id} "
        f"redirect_uri={redirect_uri} state={'yes' if state else 'no'} "
        f"pkce={'yes' if code_challenge else 'no'} resource={resource} "
        f"extra_params={list(extra_params.keys()) if extra_params else 'none'}"
    )

    if not _auth_manager:
        logger.error("[OAuth:authorize:POST] auth manager not initialized")
        raise HTTPException(status_code=503, detail="OAuth not initialized")

    # Validate the API key
    user = await _auth_manager.validate_api_key(api_key)
    if not user:
        logger.warning(f"[OAuth:authorize:POST] invalid API key for client={client_id}")
        return HTMLResponse(content=_login_page(
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            scope=scope,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            resource=resource,
            extra_params=extra_params,
            error="Invalid API key. Please check and try again."
        ))

    # Generate authorization code
    auth_code = secrets.token_urlsafe(32)

    # Store code with metadata (Redis handles TTL, in-memory has cap cleanup)
    _store_auth_code(auth_code, {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "api_key": api_key,
        "user_id": user.user_id,
        "scope": scope,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "resource": resource,
        "expires_at": datetime.utcnow() + timedelta(seconds=AUTH_CODE_EXPIRY_SECONDS)
    })

    # Build redirect URL — handle redirect_uris that already have query params
    redirect_params = {"code": auth_code}
    if state:
        redirect_params["state"] = state

    parsed = urlparse(redirect_uri)
    existing_params = parse_qs(parsed.query, keep_blank_values=True)
    # Merge: existing params + new oauth params
    for k, v in redirect_params.items():
        existing_params[k] = [v]
    new_query = urlencode(existing_params, doseq=True)
    redirect_url = urlunparse(parsed._replace(query=new_query))

    logger.info(
        f"[OAuth:authorize:POST] success user={user.user_id} client={client_id} "
        f"redirect_url={redirect_url}"
    )

    return RedirectResponse(url=redirect_url, status_code=302)


# =============================================================================
# Token Endpoint
# =============================================================================

class TokenRequest(BaseModel):
    grant_type: str
    code: Optional[str] = None
    redirect_uri: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    code_verifier: Optional[str] = None
    refresh_token: Optional[str] = None


@router.post("/oauth/token")
async def token_exchange(request: Request):
    """
    OAuth Token Endpoint

    Exchanges authorization code for access token, or refreshes a token.
    """
    # Parse form data or JSON (different clients use different content types)
    content_type = request.headers.get("content-type", "")

    if "application/x-www-form-urlencoded" in content_type:
        form_data = await request.form()
        data = dict(form_data)
    elif "application/json" in content_type:
        data = await request.json()
    else:
        # Try form data as default
        try:
            form_data = await request.form()
            data = dict(form_data)
        except Exception:
            data = await request.json()

    grant_type = data.get("grant_type")

    logger.info(
        f"[OAuth:token] grant_type={grant_type} "
        f"client_id={data.get('client_id', 'none')} "
        f"has_code={'yes' if data.get('code') else 'no'} "
        f"has_verifier={'yes' if data.get('code_verifier') else 'no'} "
        f"has_refresh={'yes' if data.get('refresh_token') else 'no'} "
        f"content-type={content_type} "
        f"user-agent={request.headers.get('user-agent', 'unknown')}"
    )

    if grant_type == "refresh_token":
        return await _handle_refresh_token(data)

    if grant_type != "authorization_code":
        logger.warning(f"[OAuth:token] rejected grant_type={grant_type}")
        return JSONResponse(
            status_code=400,
            content={"error": "unsupported_grant_type",
                      "error_description": "Supported: authorization_code, refresh_token"}
        )

    code = data.get("code")
    redirect_uri = data.get("redirect_uri")
    client_id = data.get("client_id")
    code_verifier = data.get("code_verifier")

    if not code:
        logger.warning("[OAuth:token] missing code parameter")
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_request", "error_description": "Missing code parameter"}
        )

    # Look up auth code
    code_data = _get_auth_code(code)
    if not code_data:
        logger.warning(f"[OAuth:token] invalid/expired auth code from client={client_id}")
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_grant", "error_description": "Invalid or expired authorization code"}
        )

    # Check expiration (Redis TTL handles this too, but belt-and-suspenders)
    if datetime.utcnow() > code_data["expires_at"]:
        _delete_auth_code(code)
        logger.warning(f"[OAuth:token] expired auth code for client={client_id}")
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_grant", "error_description": "Authorization code expired"}
        )

    # Validate redirect_uri matches (if provided — some clients omit on token exchange)
    if redirect_uri and redirect_uri != code_data["redirect_uri"]:
        logger.warning(
            f"[OAuth:token] redirect_uri mismatch: "
            f"expected={code_data['redirect_uri']} got={redirect_uri}"
        )
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_grant", "error_description": "redirect_uri mismatch"}
        )

    # Validate PKCE if code_challenge was provided during authorization
    if code_data.get("code_challenge"):
        if not code_verifier:
            logger.warning(f"[OAuth:token] PKCE: code_verifier required but missing, client={client_id}")
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_request", "error_description": "code_verifier required"}
            )

        # Verify code_verifier
        if code_data["code_challenge_method"] == "S256":
            computed = base64.urlsafe_b64encode(
                hashlib.sha256(code_verifier.encode()).digest()
            ).decode().rstrip("=")
        else:  # plain
            computed = code_verifier

        if computed != code_data["code_challenge"].rstrip("="):
            logger.warning(f"[OAuth:token] PKCE: code_verifier mismatch, client={client_id}")
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_grant", "error_description": "Invalid code_verifier"}
            )
    else:
        if code_verifier:
            logger.info(f"[OAuth:token] client sent code_verifier but no code_challenge was stored "
                        f"(skipping PKCE check), client={client_id}")

    # Delete the code (one-time use)
    _delete_auth_code(code)

    # Generate tokens
    access_token = f"nmcp_oauth_{secrets.token_urlsafe(32)}"
    refresh_token = f"nmcp_refresh_{secrets.token_urlsafe(32)}"
    access_expires = datetime.utcnow() + timedelta(seconds=ACCESS_TOKEN_EXPIRY_SECONDS)
    refresh_expires = datetime.utcnow() + timedelta(seconds=REFRESH_TOKEN_EXPIRY_SECONDS)

    _store_token(access_token, {
        "api_key": code_data["api_key"],
        "user_id": code_data["user_id"],
        "client_id": code_data["client_id"],
        "scope": code_data["scope"],
        "expires_at": access_expires,
    })

    _store_refresh_token(refresh_token, {
        "api_key": code_data["api_key"],
        "user_id": code_data["user_id"],
        "client_id": code_data["client_id"],
        "scope": code_data["scope"],
        "expires_at": refresh_expires,
    })

    logger.info(f"[OAuth:token] issued access+refresh tokens for user={code_data['user_id']} "
                f"client={code_data['client_id']}")

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_EXPIRY_SECONDS,
        "refresh_token": refresh_token,
        "scope": code_data["scope"]
    }


async def _handle_refresh_token(data: Dict[str, Any]) -> JSONResponse:
    """Handle refresh_token grant type."""
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        logger.warning("[OAuth:refresh] missing refresh_token")
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_request", "error_description": "Missing refresh_token"}
        )

    token_data = _get_refresh_token(refresh_token)
    if not token_data:
        logger.warning("[OAuth:refresh] invalid/expired refresh_token")
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_grant", "error_description": "Invalid or expired refresh token"}
        )

    if datetime.utcnow() > token_data["expires_at"]:
        _delete_refresh_token(refresh_token)
        logger.warning(f"[OAuth:refresh] expired refresh_token for user={token_data.get('user_id')}")
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_grant", "error_description": "Refresh token expired"}
        )

    # Rotate: delete old refresh token, issue new access + refresh
    _delete_refresh_token(refresh_token)

    new_access = f"nmcp_oauth_{secrets.token_urlsafe(32)}"
    new_refresh = f"nmcp_refresh_{secrets.token_urlsafe(32)}"
    access_expires = datetime.utcnow() + timedelta(seconds=ACCESS_TOKEN_EXPIRY_SECONDS)
    refresh_expires = datetime.utcnow() + timedelta(seconds=REFRESH_TOKEN_EXPIRY_SECONDS)

    _store_token(new_access, {
        "api_key": token_data["api_key"],
        "user_id": token_data["user_id"],
        "client_id": token_data.get("client_id"),
        "scope": token_data["scope"],
        "expires_at": access_expires,
    })

    _store_refresh_token(new_refresh, {
        "api_key": token_data["api_key"],
        "user_id": token_data["user_id"],
        "client_id": token_data.get("client_id"),
        "scope": token_data["scope"],
        "expires_at": refresh_expires,
    })

    logger.info(f"[OAuth:refresh] rotated tokens for user={token_data['user_id']}")

    return JSONResponse(content={
        "access_token": new_access,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_EXPIRY_SECONDS,
        "refresh_token": new_refresh,
        "scope": token_data["scope"]
    })


# =============================================================================
# Token Revocation (RFC 7009)
# =============================================================================

@router.post("/oauth/revoke")
async def revoke_token(request: Request):
    """
    OAuth Token Revocation Endpoint (RFC 7009)

    Revokes an access or refresh token.
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    token = data.get("token")
    token_type_hint = data.get("token_type_hint")

    logger.info(f"[OAuth:revoke] hint={token_type_hint} "
                f"user-agent={request.headers.get('user-agent', 'unknown')}")

    if not token:
        return JSONResponse(status_code=400, content={"error": "invalid_request"})

    # Try to revoke as access token
    if token.startswith("nmcp_oauth_"):
        _delete_token(token)
        logger.info("[OAuth:revoke] revoked access token")
    elif token.startswith("nmcp_refresh_"):
        _delete_refresh_token(token)
        logger.info("[OAuth:revoke] revoked refresh token")
    else:
        # Try both
        _delete_token(token)
        _delete_refresh_token(token)

    # RFC 7009: always return 200 even if token was already invalid
    return JSONResponse(status_code=200, content={})


# =============================================================================
# Dynamic Client Registration
# =============================================================================

class ClientRegistrationRequest(BaseModel):
    client_name: str
    redirect_uris: list[str]
    grant_types: Optional[list[str]] = ["authorization_code"]
    response_types: Optional[list[str]] = ["code"]
    token_endpoint_auth_method: Optional[str] = "none"
    scope: Optional[str] = None


@router.post("/oauth/register")
async def register_client(request: Request):
    """
    OAuth Dynamic Client Registration (RFC 7591)

    Allows MCP clients (Claude, ChatGPT, Cursor, Windsurf, VS Code, Gemini,
    etc.) to register dynamically.
    """
    # Accept both JSON body and Pydantic — some clients send extra fields
    body = await request.json()

    client_name = body.get("client_name", "Unknown Client")
    redirect_uris = body.get("redirect_uris", [])
    grant_types = body.get("grant_types", ["authorization_code"])
    response_types = body.get("response_types", ["code"])
    token_endpoint_auth_method = body.get("token_endpoint_auth_method", "none")

    logger.info(
        f"[OAuth:register] client_name={client_name} "
        f"redirect_uris={redirect_uris} "
        f"grant_types={grant_types} "
        f"auth_method={token_endpoint_auth_method} "
        f"user-agent={request.headers.get('user-agent', 'unknown')}"
    )

    # Generate client credentials
    client_id = secrets.token_urlsafe(16)
    client_secret = secrets.token_urlsafe(32) if token_endpoint_auth_method != "none" else None

    # Store client (Redis or in-memory with cap enforcement)
    client_data = {
        "client_secret": client_secret,
        "redirect_uris": redirect_uris,
        "client_name": client_name,
        "grant_types": grant_types,
        "response_types": response_types,
        "token_endpoint_auth_method": token_endpoint_auth_method,
        "created_at": datetime.utcnow().isoformat()
    }
    _store_client(client_id, client_data)

    logger.info(f"[OAuth:register] registered client_id={client_id} name={client_name}")

    response = {
        "client_id": client_id,
        "client_name": client_name,
        "redirect_uris": redirect_uris,
        "grant_types": grant_types,
        "response_types": response_types,
        "token_endpoint_auth_method": token_endpoint_auth_method
    }

    if client_secret:
        response["client_secret"] = client_secret

    return response


# =============================================================================
# Helper Functions
# =============================================================================

def resolve_oauth_token(token: str) -> Optional[str]:
    """
    Resolve an opaque OAuth token to the underlying API key.
    Returns None if the token is invalid or expired.
    """
    if not token or not token.startswith("nmcp_oauth_"):
        return None

    token_data = _get_token(token)
    if not token_data:
        return None

    # Redis TTL handles expiration, but check explicitly for in-memory fallback
    if datetime.utcnow() > token_data["expires_at"]:
        _delete_token(token)
        return None

    return token_data["api_key"]


def revoke_oauth_token(token: str) -> bool:
    """Revoke an OAuth token without affecting the underlying API key."""
    token_data = _get_token(token)
    if token_data:
        _delete_token(token)
        return True
    return False


def _cleanup_expired_codes():
    """Remove expired authorization codes."""
    now = datetime.utcnow()
    expired = [code for code, data in _auth_codes.items() if data["expires_at"] < now]
    for code in expired:
        del _auth_codes[code]


def _cleanup_expired_tokens():
    """Remove expired OAuth tokens."""
    now = datetime.utcnow()
    expired = [t for t, data in _oauth_tokens.items() if data["expires_at"] < now]
    for t in expired:
        del _oauth_tokens[t]


def _html_escape(s: str) -> str:
    """Escape HTML special characters to prevent XSS."""
    if not s:
        return ""
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#x27;"))


def _login_page(
    client_id: str,
    redirect_uri: str,
    state: str = None,
    scope: str = "mcp:tools",
    code_challenge: str = None,
    code_challenge_method: str = "S256",
    resource: str = None,
    extra_params: Dict[str, str] = None,
    error: str = None
) -> str:
    """Generate the OAuth login HTML page."""
    # Look up client name from registered clients
    client_data = _get_client(client_id)
    client_display_name = _html_escape(
        client_data.get("client_name", client_id) if client_data else client_id
    )

    error_html = f'<div class="error">{_html_escape(error)}</div>' if error else ''
    client_id = _html_escape(client_id)
    redirect_uri = _html_escape(redirect_uri)
    state_val = _html_escape(state) if state else ''
    scope = _html_escape(scope)
    code_challenge_val = _html_escape(code_challenge) if code_challenge else ''
    code_challenge_method = _html_escape(code_challenge_method) if code_challenge_method else 'S256'
    resource_val = _html_escape(resource) if resource else ''
    extra_params_json = _html_escape(json.dumps(extra_params)) if extra_params else '{}'

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Connect to NovoMCP</title>
    <style>
        :root {{
            --bg: #F8F6F3;
            --text: #2D2A26;
            --text-soft: #6B6560;
            --accent: #B8704B;
            --border: #E8E4DE;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .container {{
            background: white;
            padding: 48px;
            border-radius: 8px;
            box-shadow: 0 4px 24px rgba(0,0,0,0.08);
            max-width: 420px;
            width: 100%;
        }}
        .logo {{
            font-size: 24px;
            font-weight: 600;
            margin-bottom: 8px;
            color: var(--text);
        }}
        .logo span {{ color: var(--accent); }}
        .subtitle {{
            color: var(--text-soft);
            font-size: 14px;
            margin-bottom: 32px;
        }}
        .client-info {{
            background: var(--bg);
            padding: 16px;
            border-radius: 6px;
            margin-bottom: 24px;
            font-size: 14px;
        }}
        .client-info strong {{ color: var(--text); }}
        .error {{
            background: #FEE2E2;
            color: #991B1B;
            padding: 12px 16px;
            border-radius: 6px;
            margin-bottom: 20px;
            font-size: 14px;
        }}
        label {{
            display: block;
            font-size: 14px;
            font-weight: 500;
            margin-bottom: 8px;
            color: var(--text);
        }}
        input[type="text"], input[type="password"] {{
            width: 100%;
            padding: 12px 16px;
            border: 1px solid var(--border);
            border-radius: 6px;
            font-size: 15px;
            margin-bottom: 20px;
            font-family: monospace;
        }}
        input:focus {{
            outline: none;
            border-color: var(--accent);
        }}
        button {{
            width: 100%;
            padding: 14px;
            background: var(--text);
            color: white;
            border: none;
            border-radius: 6px;
            font-size: 15px;
            font-weight: 500;
            cursor: pointer;
            transition: background 0.2s;
        }}
        button:hover {{ background: var(--accent); }}
        .help {{
            margin-top: 20px;
            font-size: 13px;
            color: var(--text-soft);
            text-align: center;
        }}
        .help a {{ color: var(--accent); }}
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">Novo<span>MCP</span></div>
        <p class="subtitle">Connect to molecular intelligence</p>

        <div class="client-info">
            <strong>{client_display_name}</strong> wants to access your NovoMCP account
        </div>

        {error_html}

        <form method="POST" action="/oauth/authorize">
            <input type="hidden" name="client_id" value="{client_id}">
            <input type="hidden" name="redirect_uri" value="{redirect_uri}">
            <input type="hidden" name="state" value="{state_val}">
            <input type="hidden" name="scope" value="{scope}">
            <input type="hidden" name="code_challenge" value="{code_challenge_val}">
            <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
            <input type="hidden" name="resource" value="{resource_val}">
            <input type="hidden" name="extra_params_json" value="{extra_params_json}">

            <label for="api_key">Your NovoMCP API Key</label>
            <input type="password" id="api_key" name="api_key" placeholder="nmcp_... or ncmcp_..." required>

            <button type="submit">Authorize Access</button>
        </form>

        <p class="help">
            Don't have an API key? <a href="mailto:ari@novomcp.com">Request access</a>
        </p>
    </div>
</body>
</html>'''


def _error_page(message: str) -> str:
    """Generate an error HTML page."""
    message = _html_escape(message)
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Error - NovoMCP</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, sans-serif;
            background: #F8F6F3;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
        }}
        .error-box {{
            background: white;
            padding: 48px;
            border-radius: 8px;
            box-shadow: 0 4px 24px rgba(0,0,0,0.08);
            text-align: center;
            max-width: 400px;
        }}
        h1 {{ color: #991B1B; font-size: 24px; margin-bottom: 16px; }}
        p {{ color: #6B6560; }}
    </style>
</head>
<body>
    <div class="error-box">
        <h1>Authorization Error</h1>
        <p>{message}</p>
    </div>
</body>
</html>'''
