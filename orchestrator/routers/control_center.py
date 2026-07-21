"""Control Center router proxies dashboard data through NovoMCP."""
from __future__ import annotations

import os
import sys
# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, File, Form

from config import settings
from service_config import get_service_config
from core.rate_limiter import rate_limit

router = APIRouter(dependencies=[Depends(rate_limit("control_center"))])
logger = logging.getLogger(__name__)
service_config_manager = get_service_config()

DASHBOARD_DEFAULT_URL = ""


def _resolve_dashboard_base_url() -> str:
    env_override = os.getenv("DASHBOARD_AGGREGATOR_URL")
    if env_override:
        return env_override

    service_info = settings.SERVICES.get("dashboard-aggregator")
    if service_info and service_info.get("url"):
        return service_info["url"]

    return DASHBOARD_DEFAULT_URL


DASHBOARD_BASE_URL = _resolve_dashboard_base_url()
DASHBOARD_API_KEY = os.getenv("DASHBOARD_AGGREGATOR_API_KEY", "")
HTTP_TIMEOUT_SECONDS = float(os.getenv("CONTROL_CENTER_HTTP_TIMEOUT", "20"))

# CONSOLIDATED: db-manager now routes to dashboard-aggregator (unified service)
# Azure Container Apps internal URL
DB_MANAGER_DEFAULT_URL = os.getenv(
    "DB_MANAGER_URL",
    "",
)
DB_MANAGER_DEFAULT_API_KEY = os.getenv("DB_MANAGER_API_KEY") or os.getenv("DASHBOARD_AGGREGATOR_API_KEY", "")

ATTACHMENT_PROCESSOR_DEFAULT_URL = os.getenv(
    "ATTACHMENT_PROCESSOR_URL",
    "",
)
ATTACHMENT_PROCESSOR_DEFAULT_API_KEY = os.getenv("ATTACHMENT_PROCESSOR_API_KEY", "")

DEFAULT_TTL = 30
LONG_TTL = 90

_CACHE: Dict[str, Tuple[float, Any]] = {}
_cache_lock = asyncio.Lock()


def _resolve_service_endpoint(
    service_name: str,
    default_url: str,
    default_api_key: str,
):
    """Resolve service endpoint and API key using config overrides."""
    config = service_config_manager.get_service_config(service_name) if service_config_manager else None
    url = default_url
    api_key = default_api_key

    if config:
        url = config.get("url") or url
        api_key = config.get("api_key") or api_key

    return url, api_key


def _require_identity(
    x_org_id: Optional[str] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
) -> Tuple[str, str]:
    if not x_org_id or not x_user_id:
        raise HTTPException(status_code=400, detail="Missing X-Org-ID or X-User-ID header")
    return x_org_id, x_user_id


async def _cached_fetch(key: str, ttl: int, fetcher: asyncio.Future) -> Any:
    now = time.time()
    async with _cache_lock:
        cached = _CACHE.get(key)
        if cached and cached[0] > now:
            return cached[1]
        stale_data = cached[1] if cached else None

    try:
        data = await fetcher
    except Exception as exc:  # pragma: no cover - defensive guard
        if stale_data is not None:
            logger.warning(
                "Control Center fetch for %s failed (%s); serving stale data",
                key,
                exc,
            )
            async with _cache_lock:
                _CACHE[key] = (now + max(5, ttl // 2 or 5), stale_data)
            return stale_data

        logger.error("Control Center fetch for %s failed with no cache", key, exc_info=exc)
        raise

    async with _cache_lock:
        _CACHE[key] = (now + ttl, data)

    return data


async def _make_request(
    method: str,
    path: str,
    *,
    org_id: str,
    user_id: str,
    params: Optional[Dict[str, Any]] = None,
    json_payload: Optional[Dict[str, Any]] = None,
) -> Any:
    url = f"{DASHBOARD_BASE_URL}{path}"
    headers = {
        "X-API-Key": DASHBOARD_API_KEY,
        "X-Org-ID": org_id,
        "X-User-ID": user_id,
    }

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, verify=settings.httpx_verify) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=json_payload,
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Dashboard aggregator request failed: {exc}")

    if response.status_code >= 400:
        detail = response.text or response.reason_phrase
        raise HTTPException(status_code=response.status_code, detail=detail)

    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"Invalid JSON from dashboard aggregator: {exc}")


def _to_number(value: Any, default: float | int = 0) -> float | int:
    try:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str) and value.strip():
            parsed = float(value)
            return int(parsed) if parsed.is_integer() else parsed
    except (TypeError, ValueError):
        pass
    return default


def _normalise_weekly_activity(entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalised: List[Dict[str, Any]] = []
    for entry in entries or []:
        label = (
            entry.get("label")
            or entry.get("name")
            or entry.get("day")
            or entry.get("day_name")
            or entry.get("dayName")
        )
        normalised.append(
            {
                "label": label,
                "molecules": _to_number(entry.get("molecules") or entry.get("molecule_count"), 0),
                "scores": _to_number(entry.get("scores") or entry.get("score_count"), 0),
            }
        )
    return normalised


def _map_job_console_summary(payload: Any, org_id: str) -> Dict[str, Any]:
    stats = payload if isinstance(payload, dict) else {}
    jobs = stats.get("jobs", {})
    spend = stats.get("spend", {})
    compliance = stats.get("compliance", {})
    projects = stats.get("projects", {})
    molecules = stats.get("molecules", {})
    ai_scores = stats.get("ai_scores", stats.get("aiScores", {}))
    team = stats.get("team", {})
    charts = stats.get("charts", {})

    weekly_activity = charts.get("weekly_activity") or charts.get("weeklyActivity") or []

    return {
        "surface": "job-console",
        "orgId": stats.get("org_id") or stats.get("orgId") or org_id,
        "snapshotAt": stats.get("snapshot_at") or stats.get("snapshotAt"),
        "jobs": {
            "queued": _to_number(jobs.get("queued"), 0),
            "queuedChange": jobs.get("queued_change"),
            "running": _to_number(jobs.get("running"), 0),
            "runningChange": jobs.get("running_change"),
            "completed24h": _to_number(jobs.get("completed_24h"), 0),
            "failed24h": _to_number(jobs.get("failed_24h"), 0),
            "avgDurationMinutes": _to_number(jobs.get("avg_duration_minutes"), 0),
            "gpuUtilization": _to_number(jobs.get("gpu_utilization"), 0),
            "gpuCapacity": _to_number(jobs.get("gpu_capacity"), 0),
        },
        "spend": {
            "creditsUsed": _to_number(spend.get("credits_used"), 0),
            "creditsRemaining": _to_number(spend.get("credits_remaining"), 0),
            "burnRate": _to_number(spend.get("burn_rate"), 0),
            "burn24h": _to_number(spend.get("burn_24h"), 0),
            "currency": spend.get("currency", "USD"),
        },
        "compliance": {
            "openAlerts": _to_number(compliance.get("open_alerts"), 0),
            "critical": _to_number(compliance.get("critical"), 0),
            "pendingSignoff": _to_number(compliance.get("pending_signoff"), 0),
            "resolved24h": _to_number(compliance.get("resolved_24h"), 0),
        },
        "projects": {
            "active": _to_number(projects.get("active"), 0),
            "total": _to_number(projects.get("total"), 0),
            "trend": projects.get("trend"),
        },
        "molecules": {
            "total": _to_number(molecules.get("total"), 0),
            "generated": _to_number(molecules.get("generated"), 0),
            "trend": molecules.get("trend"),
        },
        "aiScores": {
            "processed": _to_number(ai_scores.get("processed"), 0),
            "average": _to_number(ai_scores.get("average"), 0),
            "trend": ai_scores.get("trend"),
        },
        "team": {
            "active": _to_number(team.get("active"), 0),
            "total": _to_number(team.get("total"), 0),
            "trend": team.get("trend"),
        },
        "charts": {
            "weeklyActivity": _normalise_weekly_activity(weekly_activity),
        },
    }


def _extract_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("activities", "items", "data", "results"):
        if key in payload and isinstance(payload[key], list):
            return payload[key]
    return []


def _map_activity_feed(payload: Any, org_id: str) -> Dict[str, Any]:
    activities = _extract_items(payload)
    def _normalise(entry: Dict[str, Any]) -> Dict[str, Any]:
        timestamp = entry.get("timestamp") or entry.get("created_at")
        if hasattr(timestamp, "isoformat"):
            timestamp = timestamp.isoformat()
        return {
            "type": entry.get("type"),
            "description": entry.get("description"),
            "timestamp": timestamp,
            "project": entry.get("project") or entry.get("project_name"),
            "user": entry.get("user") or {"name": entry.get("user_name")},
            "status": entry.get("status"),
        }

    return {
        "surface": "job-console",
        "orgId": org_id,
        "activities": [_normalise(entry) for entry in activities],
        "count": len(activities),
    }


def _map_molecules(payload: Any, org_id: str, project_id: Optional[str]) -> Dict[str, Any]:
    molecules = _extract_items(payload)
    return {
        "surface": "experiment-board",
        "orgId": org_id,
        "projectId": project_id,
        "molecules": molecules,
        "count": len(molecules),
    }


def _map_score_distribution(payload: Any, org_id: str) -> Dict[str, Any]:
    rows = _extract_items(payload)
    buckets = []
    for row in rows:
        buckets.append(
            {
                "bucket": row.get("score_bucket") or row.get("bucket"),
                "count": _to_number(
                    row.get("molecule_count") or row.get("count") or row.get("total"), 0
                ),
            }
        )
    return {
        "surface": "experiment-board",
        "orgId": org_id,
        "buckets": buckets,
        "count": len(buckets),
    }


def _map_projects_summary(payload: Any, org_id: str) -> Dict[str, Any]:
    projects = _extract_items(payload)
    return {
        "surface": "experiment-board",
        "orgId": org_id,
        "projects": projects,
        "count": len(projects),
    }


def _map_watchlist(payload: Any, org_id: str) -> Dict[str, Any]:
    items = _extract_items(payload)
    return {
        "surface": "compliance-hub",
        "orgId": org_id,
        "items": items,
        "count": len(items),
    }


def _map_compliance_items(payload: Any, org_id: str, surface: str) -> Dict[str, Any]:
    items = _extract_items(payload)
    return {
        "surface": surface,
        "orgId": org_id,
        "items": items,
        "count": len(items),
    }


@router.post("/library-intake/upload")
async def library_intake_upload(
    file: UploadFile = File(...),
    project_name: Optional[str] = Form(None),
    project_description: Optional[str] = Form(None),
    workflow_type: Optional[str] = Form("library-intake"),
    identity: Tuple[str, str] = Depends(_require_identity),
):
    """Create a project, upload attachment, and return combined intake response."""
    org_id, user_id = identity

    workflow_labels = {
        "library-intake": "Library Intake & QC",
        "parallel-screening": "Parallel Screening",
        "compliance-audit": "Compliance & Audit",
    }

    workflow_label = workflow_labels.get(workflow_type, "Library Processing")

    inferred_name = Path(file.filename or "library-upload").stem
    fallback_name = f"{workflow_label} - {inferred_name}" if inferred_name else f"{workflow_label} Project"
    name = project_name.strip() if project_name and project_name.strip() else fallback_name
    description = project_description or f"Auto-created from {file.filename} via {workflow_label}"

    db_url, db_api_key = _resolve_service_endpoint(
        "db-manager",
        DB_MANAGER_DEFAULT_URL,
        DB_MANAGER_DEFAULT_API_KEY,
    )

    project_payload = {
        "name": name,
        "description": description,
        "org_id": org_id,
        "created_by": user_id,
        "metadata": {
            "workflow": workflow_type or "library-intake",
            "workflow_type": workflow_type or "library-intake",
            "source_filename": file.filename,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, verify=settings.httpx_verify) as client:
            project_response = await client.post(
                f"{db_url}/projects",
                headers={"X-API-Key": db_api_key},
                json=project_payload,
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Project creation failed: {exc}")

    if project_response.status_code >= 400:
        raise HTTPException(
            status_code=project_response.status_code,
            detail=f"Project creation failed: {project_response.text}")

    project_data = project_response.json()
    project_id = project_data.get("project_id") or project_data.get("id")
    if not project_id:
        raise HTTPException(status_code=502, detail="Project creation did not return an ID")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    attachment_url, attachment_api_key = _resolve_service_endpoint(
        "attachment-processor",
        ATTACHMENT_PROCESSOR_DEFAULT_URL,
        ATTACHMENT_PROCESSOR_DEFAULT_API_KEY,
    )

    files_payload = {
        "file": (
            file.filename,
            file_bytes,
            file.content_type or "application/octet-stream",
        )
    }
    data_payload = {"project_id": str(project_id)}

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, verify=settings.httpx_verify) as client:
            upload_response = await client.post(
                f"{attachment_url}/attachment-processor/upload",
                headers={"X-API-Key": attachment_api_key},
                files=files_payload,
                data=data_payload,
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Attachment upload failed: {exc}")

    if upload_response.status_code >= 400:
        raise HTTPException(
            status_code=upload_response.status_code,
            detail=f"Attachment upload failed: {upload_response.text}")

    attachment_data = upload_response.json()

    return {
        "project": {
            "id": str(project_id),
            "name": name,
            "description": description,
            "org_id": org_id,
            "created_at": datetime.utcnow().isoformat(),
        },
        "attachment": attachment_data,
    }


@router.get("/job-console/summary")
async def job_console_summary(identity: Tuple[str, str] = Depends(_require_identity)) -> Dict[str, Any]:
    org_id, user_id = identity
    cache_key = f"job-console:summary:{org_id}"

    async def fetch() -> Dict[str, Any]:
        payload = await _make_request(
            "GET",
            "/api/v1/aggregated-stats",
            org_id=org_id,
            user_id=user_id,
        )
        return _map_job_console_summary(payload, org_id)

    return await _cached_fetch(cache_key, DEFAULT_TTL, fetch())


@router.get("/job-console/activity")
async def job_console_activity(
    limit: int = 12,
    identity: Tuple[str, str] = Depends(_require_identity),
) -> Dict[str, Any]:
    org_id, user_id = identity
    limit = max(1, min(limit, 50))
    cache_key = f"job-console:activity:{org_id}:{limit}"

    async def fetch() -> Dict[str, Any]:
        payload = await _make_request(
            "POST",
            "/query",
            org_id=org_id,
            user_id=user_id,
            json_payload={
                "query_type": "job_activity_feed",
                "filters": {
                    "org_id": org_id,
                    "limit": limit,
                    "order_by": "occurred_at DESC",
                },
            },
        )
        return _map_activity_feed(payload, org_id)

    return await _cached_fetch(cache_key, DEFAULT_TTL, fetch())


@router.get("/experiment-board/molecules")
async def experiment_board_molecules(
    project_id: Optional[str] = None,
    limit: int = 50,
    identity: Tuple[str, str] = Depends(_require_identity),
) -> Dict[str, Any]:
    org_id, user_id = identity
    limit = max(1, min(limit, 200))
    cache_key = f"experiment-board:molecules:{org_id}:{project_id or 'all'}:{limit}"

    async def fetch() -> Dict[str, Any]:
        payload = await _make_request(
            "GET",
            "/api/v1/molecules",
            org_id=org_id,
            user_id=user_id,
            params={"limit": limit, "project_id": project_id} if project_id else {"limit": limit},
        )
        return _map_molecules(payload, org_id, project_id)

    return await _cached_fetch(cache_key, DEFAULT_TTL, fetch())


@router.get("/experiment-board/score-distribution")
async def experiment_board_distribution(
    identity: Tuple[str, str] = Depends(_require_identity),
) -> Dict[str, Any]:
    org_id, user_id = identity
    cache_key = f"experiment-board:distribution:{org_id}"

    async def fetch() -> Dict[str, Any]:
        payload = await _make_request(
            "GET",
            "/api/v1/molecules/score-distribution",
            org_id=org_id,
            user_id=user_id,
        )
        return _map_score_distribution(payload, org_id)

    return await _cached_fetch(cache_key, DEFAULT_TTL, fetch())


@router.get("/projects/summary")
async def project_summary(
    limit: int = 50,
    identity: Tuple[str, str] = Depends(_require_identity),
) -> Dict[str, Any]:
    org_id, user_id = identity
    limit = max(1, min(limit, 200))
    cache_key = f"projects:summary:{org_id}:{limit}"

    async def fetch() -> Dict[str, Any]:
        payload = await _make_request(
            "GET",
            "/api/v1/projects/summary",
            org_id=org_id,
            user_id=user_id,
            params={"limit": limit},
        )
        return _map_projects_summary(payload, org_id)

    return await _cached_fetch(cache_key, LONG_TTL, fetch())


@router.get("/compliance/watchlist")
async def compliance_watchlist(
    limit: int = 50,
    identity: Tuple[str, str] = Depends(_require_identity),
) -> Dict[str, Any]:
    org_id, user_id = identity
    limit = max(1, min(limit, 200))
    cache_key = f"compliance:watchlist:{org_id}:{limit}"

    async def fetch() -> Dict[str, Any]:
        payload = await _make_request(
            "GET",
            "/api/v1/compliance/watchlist",
            org_id=org_id,
            user_id=user_id,
            params={"limit": limit},
        )
        return _map_watchlist(payload, org_id)

    return await _cached_fetch(cache_key, DEFAULT_TTL, fetch())


@router.get("/compliance/alerts")
async def compliance_alerts(
    limit: int = 20,
    identity: Tuple[str, str] = Depends(_require_identity),
) -> Dict[str, Any]:
    org_id, user_id = identity
    limit = max(1, min(limit, 100))
    cache_key = f"compliance:alerts:{org_id}:{limit}"

    async def fetch() -> Dict[str, Any]:
        payload = await _make_request(
            "POST",
            "/query",
            org_id=org_id,
            user_id=user_id,
            json_payload={
                "query_type": "compliance_alerts",
                "filters": {
                    "org_id": org_id,
                    "limit": limit,
                    "order_by": "created_at DESC",
                },
            },
        )
        return _map_compliance_items(payload, org_id, "compliance-hub")

    return await _cached_fetch(cache_key, DEFAULT_TTL, fetch())


@router.get("/compliance/signoff-queue")
async def compliance_signoff_queue(
    limit: int = 25,
    status: Optional[str] = None,
    identity: Tuple[str, str] = Depends(_require_identity),
) -> Dict[str, Any]:
    org_id, user_id = identity
    limit = max(1, min(limit, 100))
    cache_key = f"compliance:signoff:{org_id}:{limit}:{status or 'all'}"

    async def fetch() -> Dict[str, Any]:
        payload = await _make_request(
            "POST",
            "/query",
            org_id=org_id,
            user_id=user_id,
            json_payload={
                "query_type": "compliance_signoff_queue",
                "filters": {
                    "org_id": org_id,
                    "limit": limit,
                    "status": status,
                },
            },
        )
        return _map_compliance_items(payload, org_id, "compliance-hub")

    return await _cached_fetch(cache_key, DEFAULT_TTL, fetch())


@router.get("/compliance/audit-timeline")
async def compliance_audit_timeline(
    limit: int = 50,
    identity: Tuple[str, str] = Depends(_require_identity),
) -> Dict[str, Any]:
    org_id, user_id = identity
    limit = max(1, min(limit, 200))
    cache_key = f"compliance:audit:{org_id}:{limit}"

    async def fetch() -> Dict[str, Any]:
        payload = await _make_request(
            "POST",
            "/query",
            org_id=org_id,
            user_id=user_id,
            json_payload={
                "query_type": "compliance_audit_timeline",
                "filters": {
                    "org_id": org_id,
                    "limit": limit,
                    "order_by": "created_at DESC",
                },
            },
        )
        return _map_compliance_items(payload, org_id, "compliance-hub")

    return await _cached_fetch(cache_key, DEFAULT_TTL, fetch())
