"""Admin endpoints for the org BYO-LLM config (Studio settings UI).

Gated by `X-Admin-Key` — the dashboard BFF attaches it *after* enforcing the
admin role (X-User-Roles must include 'admin'), and forwards the org via
`X-Org-ID`. So these endpoints trust the BFF's role check and key, and act on
the org in the header. The GET never returns the key (status only).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request

from . import router as _router  # _json_body
from .llm_vault import delete_org_llm_config, get_org_llm_status, set_org_llm_config

logger = logging.getLogger(__name__)

llm_admin_router = APIRouter(prefix="/v1/org/llm-config", tags=["NovoMCP Agent Admin"])


def _verify_admin(admin_key: Optional[str]) -> None:
    expected = os.getenv("NOVOMCP_ADMIN_KEY") or os.getenv("MCP_ADMIN_KEY")
    if not expected or admin_key != expected:
        raise HTTPException(status_code=403, detail="Invalid admin key")


def _require_org(org_id: Optional[str]) -> str:
    if not org_id:
        raise HTTPException(status_code=400, detail="X-Org-ID header is required")
    return org_id


@llm_admin_router.get("")
async def get_llm_config(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-ID"),
):
    """Non-secret status: whether an LLM provider is configured for the org."""
    _verify_admin(x_admin_key)
    org_id = _require_org(x_org_id)
    status = await get_org_llm_status(org_id)
    return {"configured": status is not None, "config": status}


@llm_admin_router.put("")
async def put_llm_config(
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-ID"),
    x_user_id: Optional[str] = Header(None, alias="X-User-ID"),
):
    """Set/replace the org's LLM provider config. Body: {provider, model, api_key, base_url?}."""
    _verify_admin(x_admin_key)
    org_id = _require_org(x_org_id)
    body = await _router._json_body(request)
    try:
        result = await set_org_llm_config(
            org_id,
            provider=body.get("provider"),
            model=body.get("model"),
            api_key=body.get("api_key"),
            base_url=body.get("base_url"),
            updated_by=x_user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **result}


@llm_admin_router.delete("")
async def delete_llm_config(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    x_org_id: Optional[str] = Header(None, alias="X-Org-ID"),
):
    """Remove the org's LLM provider config (secret + metadata)."""
    _verify_admin(x_admin_key)
    org_id = _require_org(x_org_id)
    await delete_org_llm_config(org_id)
    return {"ok": True}
