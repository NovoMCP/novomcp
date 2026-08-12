"""Org BYO-LLM key vault (OSS engine).

In the public engine there is no external secret store, so per-org LLM keys
are supplied through environment variables and read by the agent runtime's
own `_env_llm_config()` fallback. This module keeps the interface
(`get_org_llm_config()`, `get_org_llm_status()`, `set_org_llm_config()`,
`delete_org_llm_config()`) so callers import and run unchanged; the org-key
lookups degrade to None so the agent endpoint replies "llm_not_configured"
rather than 500. A managed backend supplies a real vault out-of-repo.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

from novomcp.core.db_helper import execute_sql, query_sql

logger = logging.getLogger(__name__)

LLM_SECRET_PREFIX = os.getenv("LLM_SECRET_PREFIX", "novomcp/llm-key/")
SUPPORTED_PROVIDERS = {"anthropic", "openai", "gemini", "mistral", "cohere"}


def _secret_name(org_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_/-]", "-", org_id)
    return f"{LLM_SECRET_PREFIX}{safe}"


async def get_org_llm_config(org_id: Optional[str]) -> Optional[dict]:
    """Per-org LLM keys are not stored in the OSS engine; always None so the
    agent runtime falls back to its environment-variable LLM config."""
    return None


async def get_org_llm_status(org_id: Optional[str]) -> Optional[dict]:
    """Non-secret status for the dashboard (no api_key). None if unset."""
    if not org_id:
        return None
    rows = await query_sql(
        "SELECT provider, model, base_url, updated_by, updated_at FROM research.mcp_llm_config WHERE org_id = %s",
        (org_id,),
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "provider": row["provider"],
        "model": row["model"],
        "base_url": row.get("base_url"),
        "updated_by": row.get("updated_by"),
        "updated_at": str(row.get("updated_at")) if row.get("updated_at") else None,
    }


async def set_org_llm_config(
    org_id: str,
    *,
    provider: str,
    model: str,
    api_key: str,
    base_url: Optional[str] = None,
    updated_by: Optional[str] = None,
) -> dict:
    """Store/replace an org's LLM config. Key → Secrets Manager, metadata → Aurora.
    Raises ValueError on bad input."""
    if not org_id:
        raise ValueError("org_id is required")
    provider = (provider or "").lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider '{provider}' (supported: {', '.join(sorted(SUPPORTED_PROVIDERS))})")
    if not model or not api_key:
        raise ValueError("model and api_key are required")

    name = _secret_name(org_id)
    # OSS engine: no external secret store. The API key is not persisted here;
    # supply it via the agent runtime's environment-variable LLM config. Only
    # the non-secret metadata is recorded in Aurora.
    logger.warning(
        "LLM key vault not configured in the OSS engine; storing metadata only "
        "for org %s (set the LLM key via environment variables)", org_id
    )

    await execute_sql(
        """
        INSERT INTO research.mcp_llm_config (org_id, provider, model, base_url, secret_name, updated_by, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (org_id) DO UPDATE SET
            provider = EXCLUDED.provider,
            model = EXCLUDED.model,
            base_url = EXCLUDED.base_url,
            secret_name = EXCLUDED.secret_name,
            updated_by = EXCLUDED.updated_by,
            updated_at = now()
        """,
        (org_id, provider, model, base_url, name, updated_by),
    )
    return {"org_id": org_id, "provider": provider, "model": model, "base_url": base_url}


async def delete_org_llm_config(org_id: Optional[str]) -> bool:
    """Remove an org's LLM config (secret + metadata)."""
    if not org_id:
        return False
    # OSS engine: no external secret store to purge; drop the metadata row only.
    await execute_sql("DELETE FROM research.mcp_llm_config WHERE org_id = %s", (org_id,))
    return True
