"""Async client for the OPM (Orientations of Proteins in Membranes) API.

OPM is the primary signal for membrane protein detection. A hit means
the PDB is a known membrane protein; a miss means it isn't (or isn't
in OPM yet). We treat a miss as "not a membrane protein" for routing,
with a warning when the API was unreachable vs. a genuine miss.

Response shape (observed): OPM returns a structured JSON object on a
hit, or an empty body / error on a miss. Schema isn't fully documented
so we treat any non-empty structured response as a hit.
"""

from __future__ import annotations

import json
import logging
from typing import Optional, Tuple

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

OPM_URL = "https://opm-back.cc.lehigh.edu/opm-backend/primary_structures/pdbid/{pdb_id}"
OPM_TIMEOUT_S = 10.0
OPM_CACHE_TTL_S = 30 * 24 * 3600  # 30 days

_REDIS_KEY_PREFIX = "intake:opm:"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, max=5),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
    reraise=True,
)
async def _opm_fetch(http_client: httpx.AsyncClient, url: str):
    """HTTP GET with automatic retry on transient network errors."""
    return await http_client.get(url, timeout=OPM_TIMEOUT_S)


async def check_membrane(
    pdb_id: Optional[str],
    *,
    http_client: httpx.AsyncClient,
    redis_client=None,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Check whether a PDB is in OPM.

    Args:
        pdb_id: 4-character PDB ID. Returns (False, None, None) if missing.
        http_client: An httpx.AsyncClient to reuse for the request.
        redis_client: Optional async Redis client for caching. If None,
            every call hits the OPM API.

    Returns:
        (is_membrane, source, warning). `source` is "cache" or "opm_api"
        when we have an answer, None when unreachable. `warning` is set
        only when the API was unreachable and we defaulted to "not a
        membrane" — classification should add it to SystemProfile.warnings.
    """
    if not pdb_id:
        return False, None, None
    pdb_id = pdb_id.strip().lower()

    # 1. Cache check
    if redis_client is not None:
        try:
            cached = await redis_client.get(_REDIS_KEY_PREFIX + pdb_id)
            if cached:
                data = json.loads(cached)
                return bool(data.get("is_membrane")), "cache", None
        except Exception as e:  # pragma: no cover — Redis hiccup
            logger.warning(f"OPM cache read failed for {pdb_id}: {e}")

    # 2. API fetch (with retry on transient network errors)
    try:
        resp = await _opm_fetch(http_client, OPM_URL.format(pdb_id=pdb_id))
    except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError) as e:
        warning = f"OPM API unreachable ({type(e).__name__}); assuming not a membrane"
        logger.warning(f"{warning} for {pdb_id}")
        return False, None, warning

    if resp.status_code != 200:
        # 404 is a legitimate miss — not in OPM.
        if resp.status_code == 404:
            await _cache_set(redis_client, pdb_id, {"is_membrane": False})
            return False, "opm_api", None
        warning = f"OPM API returned HTTP {resp.status_code}; assuming not a membrane"
        logger.warning(f"{warning} for {pdb_id}")
        return False, None, warning

    body = resp.text.strip()
    if not body or body in ("null", "[]", "{}"):
        await _cache_set(redis_client, pdb_id, {"is_membrane": False})
        return False, "opm_api", None

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        await _cache_set(redis_client, pdb_id, {"is_membrane": False})
        return False, "opm_api", None

    # Any non-empty structured response counts as a hit.
    is_hit = bool(parsed) and not (isinstance(parsed, list) and len(parsed) == 0)
    await _cache_set(redis_client, pdb_id, {"is_membrane": is_hit})
    return is_hit, "opm_api", None


async def _cache_set(redis_client, pdb_id: str, data: dict) -> None:
    if redis_client is None:
        return
    try:
        await redis_client.set(
            _REDIS_KEY_PREFIX + pdb_id,
            json.dumps(data),
            ex=OPM_CACHE_TTL_S,
        )
    except Exception as e:  # pragma: no cover
        logger.warning(f"OPM cache write failed for {pdb_id}: {e}")
