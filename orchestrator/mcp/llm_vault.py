"""Org BYO-LLM key vault (Phase-2a).

Owned by novomcp (dashboard-aggregator is out-of-repo). Non-secret metadata
lives in Aurora (`research.mcp_llm_config`); the API key lives in AWS Secrets
Manager (`novomcp/llm-key/{org_id}`), mirroring the bridge-connector pattern in
`mcp/connectors/vault_client.py`. The agent runtime reads via
`get_org_llm_config()`; the admin endpoints (`mcp/llm_admin.py`) write via
`set_org_llm_config()` / `delete_org_llm_config()`.

Everything degrades to None on error so the agent endpoint can reply
"llm_not_configured" rather than 500.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

import boto3

from core.db_helper import execute_sql, query_sql

logger = logging.getLogger(__name__)

LLM_SECRET_PREFIX = os.getenv("LLM_SECRET_PREFIX", "novomcp/llm-key/")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
SUPPORTED_PROVIDERS = {"anthropic", "openai", "gemini", "mistral", "cohere"}


def _secret_name(org_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_/-]", "-", org_id)
    return f"{LLM_SECRET_PREFIX}{safe}"


def _secrets_client():
    return boto3.client("secretsmanager", region_name=AWS_REGION)


async def get_org_llm_config(org_id: Optional[str]) -> Optional[dict]:
    """Full config including the decrypted api_key — for the agent runtime.
    None if unset / unreachable / malformed."""
    if not org_id:
        return None
    try:
        rows = await query_sql(
            "SELECT provider, model, base_url, secret_name FROM research.mcp_llm_config WHERE org_id = %s",
            (org_id,),
        )
    except Exception as exc:
        logger.warning("llm-config db read failed for org %s: %s", org_id, exc)
        return None
    if not rows:
        return None
    row = rows[0]
    try:
        secret = _secrets_client().get_secret_value(SecretId=row["secret_name"])
        api_key = json.loads(secret["SecretString"]).get("api_key")
    except Exception as exc:
        logger.warning("llm-config secret fetch failed for org %s: %s", org_id, exc)
        return None
    if not api_key:
        return None
    return {"provider": row["provider"], "model": row["model"], "api_key": api_key, "base_url": row.get("base_url")}


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
    payload = json.dumps({"api_key": api_key})
    sm = _secrets_client()
    try:
        sm.create_secret(
            Name=name,
            SecretString=payload,
            Tags=[{"Key": "Project", "Value": "novomcp"}, {"Key": "org_id", "Value": org_id}, {"Key": "kind", "Value": "llm-key"}],
        )
    except sm.exceptions.ResourceExistsException:
        sm.put_secret_value(SecretId=name, SecretString=payload)

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
    rows = await query_sql("SELECT secret_name FROM research.mcp_llm_config WHERE org_id = %s", (org_id,))
    if rows:
        try:
            _secrets_client().delete_secret(SecretId=rows[0]["secret_name"], ForceDeleteWithoutRecovery=True)
        except Exception as exc:
            logger.warning("llm-config secret delete failed for org %s: %s", org_id, exc)
    await execute_sql("DELETE FROM research.mcp_llm_config WHERE org_id = %s", (org_id,))
    return True
