"""
Proxy Router for NovoMCP
Routes requests to internal services (auth, dashboard, db-manager)
"""
import os
import sys
# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi import APIRouter, Request, HTTPException, Response
from fastapi.responses import JSONResponse
import httpx
import logging
from typing import Optional, Dict, Any

from config import settings
from service_config import get_service_config

logger = logging.getLogger(__name__)

router = APIRouter()

service_config_manager = get_service_config()


def _resolve_service_url(service_name: str, env_var: str, default_url: str) -> str:
    """Resolve proxy target using env override, Service Connect, then default.
    Adds alias support for service URLs.
    """
    env_override = os.getenv(env_var)
    # Alias support for molecular-intelligence
    if not env_override and service_name == "molecular-intelligence":
        env_override = (
            os.getenv("MOLECULAR_INTELLIGENCE_URL")
            or os.getenv("MOLECULAR_INTEL_URL")
            or os.getenv("MOL_INTEL_URL")
        )
    if not env_override and service_name == "faves-compliance":
        env_override = (
            os.getenv("NOVOMCP_COMPLIANCE_URL")
            or os.getenv("NOVOMCP_MOLECULE_INDEX_URL")
        )
    if env_override:
        return env_override

    service_info = settings.SERVICES.get(service_name)
    if service_info and service_info.get("url"):
        return service_info["url"]

    return default_url


# Internal service URLs - Azure Container Apps internal URLs
# All services require X-API-Key header for authentication
SERVICE_URLS = {
    # Core services - Azure Container Apps internal URLs
    # CONSOLIDATED: db-manager now routes to managed backend (unified service)
    "db-manager": _resolve_service_url(
        "db-manager",
        "DB_MANAGER_URL",
        "",
    ),

    # Auth service (Azure Container Apps internal URL by default)
    "auth": _resolve_service_url(
        "auth",
        "AUTH_URL",
        "",
    ),

    # Services with internal ALBs
    "attachment-processor": _resolve_service_url(
        "attachment-processor",
        "ATTACHMENT_PROCESSOR_URL",
        "",
    ),
    "chem-props": _resolve_service_url(
        "chem-props",
        "CHEM_PROPS_URL",
        "",
    ),
    "drugsynthmc": _resolve_service_url(
        "drugsynthmc",
        "DRUGSYNTHMC_URL",
        "",
    ),
    "faves-compliance": _resolve_service_url(
        "faves-compliance",
        "NOVOMCP_COMPLIANCE_URL",
        "",
    ),
    "knowledge-graph": _resolve_service_url(
        "knowledge-graph",
        "KNOWLEDGE_GRAPH_URL",
        "",
    ),
    "molmim-optimizer": _resolve_service_url(
        "molmim-optimizer",
        "MOLMIM_OPTIMIZER_URL",
        "",
    ),
    "negative-data": _resolve_service_url(
        "negative-data",
        "NEGATIVE_DATA_URL",
        "",
    ),
    "openmd": _resolve_service_url(
        "openmd",
        "OPENMD_URL",
        "",
    ),
    "prompt-library": _resolve_service_url(
        "prompt-library",
        "PROMPT_LIBRARY_URL",
        "",
    ),
    "red-team": _resolve_service_url(
        "red-team",
        "RED_TEAM_URL",
        "",
    ),
    "molecular-intelligence": _resolve_service_url(
        "molecular-intelligence",
        "MOL_INTEL_URL",
        "",
    ),
    "tdc-integration": _resolve_service_url(
        "tdc-integration",
        "TDC_INTEGRATION_URL",
        "",
    ),
    "zinc-integration": _resolve_service_url(
        "zinc-integration",
        "ZINC_INTEGRATION_URL",
        "",
    ),
    "molecular-worker": _resolve_service_url(
        "molecular-worker",
        "MOLECULAR_WORKER_URL",
        "",
    ),
    # CONSOLIDATED: dbschema-manager now routes to managed backend (unified service)
    "dbschema-manager": _resolve_service_url(
        "dbschema-manager",
        "DBSCHEMA_MANAGER_URL",
        "",
    ),

    # GPU services on Azure Container Apps
    "autodock-gpu": _resolve_service_url(
        "autodock-gpu",
        "AUTODOCK_GPU_URL",
        "",
    ),
    "lead-optimization": _resolve_service_url(
        "lead-optimization",
        "LEAD_OPT_URL",
        "",
    ),
    "gromacs-md": _resolve_service_url(
        "gromacs-md",
        "GROMACS_URL",
        "",
    ),
    "novo-quantum": _resolve_service_url(
        "novo-quantum",
        "NOVO_QUANTUM_URL",
        "",
    ),
    # OpenFold3 - NVIDIA protein structure prediction (migrated to Azure)
    "openfold3": _resolve_service_url(
        "openfold3",
        "OPENFOLD3_URL",
        "",
    ),
}

# Optional service-specific API key defaults (env overrides)
SERVICE_API_KEYS = {
    "db-manager": os.getenv("DB_MANAGER_API_KEY"),
    "auth": os.getenv("AUTH_SERVICE_API_KEY"),
    "novo-quantum": os.getenv("NOVO_QUANTUM_API_KEY"),
    "lead-optimization": os.getenv("LEAD_OPT_API_KEY"),
    "faves-compliance": os.getenv("NOVOMCP_COMPLIANCE_API_KEY"),
    "molmim-optimizer": os.getenv("MOLMIM_OPTIMIZER_API_KEY"),
    "openfold3": os.getenv("OPENFOLD3_API_KEY"),
    "autodock-gpu": os.getenv("AUTODOCK_GPU_API_KEY") or os.getenv("AUTODOCK_API_KEY"),
    "gromacs-md": os.getenv("GROMACS_API_KEY"),
}

# HTTP client with proper timeout and SSL handling
# Increased timeout to 120s for AI-powered services (GPT-5 scoring takes ~20-30s per molecule)
from config import settings as _settings
client = httpx.AsyncClient(
    timeout=120.0,
    verify=_settings.httpx_verify,
    follow_redirects=True,
    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
)

async def proxy_request(
    service: str,
    path: str,
    request: Request,
    method: str = "GET",
    body: Optional[bytes] = None
) -> Response:
    """
    Proxy a request to an internal service
    """
    try:
        # Get the service URL
        service_config = service_config_manager.get_service_config(service)

        base_url = service_config.get("url") if service_config else None
        if not base_url:
            base_url = SERVICE_URLS.get(service)

        if not base_url:
            raise HTTPException(status_code=404, detail=f"Service {service} not found")

        # Normalise slashes to avoid accidental double separators
        normalised_path = path.lstrip("/")
        if normalised_path:
            full_url = f"{base_url.rstrip('/')}/{normalised_path}"
        else:
            full_url = base_url.rstrip('/')
        
        # Copy headers from original request, filtering out None values
        headers = {k: v for k, v in request.headers.items() if v is not None}
        # Remove host header as it will be set by httpx
        headers.pop("host", None)
        # Remove original API key so the injected service-specific key takes precedence
        headers.pop("x-api-key", None)

        # Inject downstream API key when available to satisfy service auth requirements
        api_key = service_config.get("api_key") if service_config else None
        if not api_key:
            api_key = SERVICE_API_KEYS.get(service)

        if api_key:
            api_key = api_key.strip()
            # Some services expect "API-Key" header instead of "X-API-Key"
            header_name = "API-Key" if service in ("novo-quantum", "gromacs-md") else "X-API-Key"
            headers[header_name] = api_key
            try:
                masked = f"...{api_key[-4:]}" if len(api_key) >= 4 else "set"
            except Exception:
                masked = "set"
            logger.info(f"Injected {header_name} for {service}: present, suffix={masked}")
        
        # Make the proxied request
        logger.info(f"Proxying {method} request to {service}: {full_url}")
        
        response = None
        request_params = {
            "method": method,
            "url": full_url,
            "headers": headers,
            "content": body,
            "params": dict(request.query_params)
        }

        try:
            response = await client.request(**request_params)
        except httpx.RequestError as primary_error:
            logger.warning(f"Primary proxy attempt to {service} failed: {primary_error}")
            # If we were using a secrets-provided URL, attempt fallback to static map
            fallback_url = SERVICE_URLS.get(service)
            if service_config and fallback_url and fallback_url != base_url:
                logger.info(f"Retrying {service} proxy using fallback URL: {fallback_url}")
                fallback_path = path.lstrip("/")
                retry_url = f"{fallback_url.rstrip('/')}/{fallback_path}" if fallback_path else fallback_url.rstrip('/')
                request_params["url"] = retry_url
                response = await client.request(**request_params)
            else:
                raise
        
        # Return the response
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.headers.get("content-type", "application/json")
        )
        
    except httpx.TimeoutException:
        logger.error(f"Timeout proxying request to {service}")
        raise HTTPException(status_code=504, detail="Gateway timeout")
    except Exception as e:
        logger.error(f"Error proxying request to {service}: {e}")
        raise HTTPException(status_code=502, detail=f"Bad gateway: {str(e)}")

# Auth service routes
@router.api_route("/auth/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_auth(path: str, request: Request):
    """Proxy requests to auth service"""
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    # Don't add 'auth/' prefix if path already starts with it
    final_path = path if path.startswith("auth/") else f"auth/{path}"
    return await proxy_request("auth", final_path, request, request.method, body)

# DB Manager routes
@router.api_route("/db-manager/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_db_manager(path: str, request: Request):
    """Proxy requests to db-manager service"""
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    return await proxy_request("db-manager", f"db-manager/{path}", request, request.method, body)

# DrugSynthMC routes
@router.api_route("/drugsynthmc/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_drugsynthmc(path: str, request: Request):
    """Proxy requests to drugsynthmc service"""
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    return await proxy_request("drugsynthmc", path, request, request.method, body)

# Chem Props routes
@router.api_route("/chem-props/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_chem_props(path: str, request: Request):
    """Proxy requests to chem-props service"""
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    return await proxy_request("chem-props", path, request, request.method, body)

# FAVES Compliance routes
@router.api_route("/faves-compliance/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_faves(path: str, request: Request):
    """Proxy requests to faves-compliance service"""
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    return await proxy_request("faves-compliance", path, request, request.method, body)

# Molecular Worker routes
@router.api_route("/molecular-worker/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_molecular_worker(path: str, request: Request):
    """Proxy requests to molecular-worker service"""
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    return await proxy_request("molecular-worker", path, request, request.method, body)

# ADDIE routes
@router.api_route("/addie/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_addie(path: str, request: Request):
    """Proxy requests to addie service"""
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    return await proxy_request("addie", path, request, request.method, body)

# MolMIM Optimizer routes
@router.api_route("/molmim-optimizer/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_molmim(path: str, request: Request):
    """Proxy requests to molmim-optimizer service"""
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    return await proxy_request("molmim-optimizer", path, request, request.method, body)

# ZINC Integration routes
@router.api_route("/zinc-integration/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_zinc(path: str, request: Request):
    """Proxy requests to zinc-integration service"""
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    return await proxy_request("zinc-integration", path, request, request.method, body)

# TDC Integration routes
@router.api_route("/tdc-integration/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_tdc(path: str, request: Request):
    """Proxy requests to tdc-integration service"""
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    return await proxy_request("tdc-integration", path, request, request.method, body)

# Molecular Intelligence routes
@router.api_route("/molecular-intelligence/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_molecular_intelligence(path: str, request: Request):
    """Proxy requests to molecular-intelligence service"""
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    return await proxy_request("molecular-intelligence", path, request, request.method, body)

# DB Manager routes (for campaign persistence)
@router.api_route("/db-manager/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_db_manager(path: str, request: Request):
    """Proxy requests to db-manager service"""
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    return await proxy_request("db-manager", path, request, request.method, body)

# Novo-Quantum routes (Azure - replaces AWS Braket)
@router.api_route("/novo-quantum/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_novo_quantum(path: str, request: Request):
    """Proxy requests to Novo-Quantum service (Azure)"""
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    return await proxy_request("novo-quantum", path, request, request.method, body)

# Lead Optimization routes
@router.api_route("/lead-optimization/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_lead_optimization(path: str, request: Request):
    """Proxy requests to Lead Optimization service"""
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    return await proxy_request("lead-optimization", path, request, request.method, body)

# Negative Data routes
@router.api_route("/negative-data/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_negative_data(path: str, request: Request):
    """Proxy requests to Negative Data service"""
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    return await proxy_request("negative-data", path, request, request.method, body)

# OpenFold3 routes (Azure - NVIDIA protein structure prediction)
@router.api_route("/openfold3/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_openfold3(path: str, request: Request):
    """Proxy requests to OpenFold3 protein structure prediction service (Azure)"""
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    return await proxy_request("openfold3", path, request, request.method, body)

# AutoDock-GPU routes (Azure Container Apps - A100 GPU molecular docking)
@router.api_route("/autodock-gpu/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_autodock_gpu(path: str, request: Request):
    """Proxy requests to AutoDock-GPU molecular docking service (Azure)"""
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    return await proxy_request("autodock-gpu", path, request, request.method, body)

# GROMACS-MD routes (Azure Container Apps - A100 GPU molecular dynamics)
@router.api_route("/gromacs-md/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_gromacs_md(path: str, request: Request):
    """Proxy requests to GROMACS-MD molecular dynamics service (Azure)"""
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    return await proxy_request("gromacs-md", path, request, request.method, body)

# PDB proxy - fetch from RCSB with CORS headers for MCP Apps
@router.api_route("/pdb/{pdb_id}", methods=["GET", "HEAD"])
async def proxy_pdb(pdb_id: str, request: Request):
    """
    Proxy PDB files from RCSB to enable MCP Apps UI to fetch structures.
    RCSB doesn't include CORS headers, so we proxy through here.

    GET returns the .pdb content. HEAD is supported as a cheap header-only
    probe (e.g. `curl -sI`): it validates the id and returns 200 + headers
    without round-tripping to RCSB.
    """
    # Normalize PDB ID (uppercase, 4 chars)
    pdb_id = pdb_id.upper().strip()
    if len(pdb_id) != 4:
        raise HTTPException(status_code=400, detail=f"Invalid PDB ID: {pdb_id}")

    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Cache-Control": "public, max-age=86400",  # Cache for 24h
    }

    # HEAD: header-only liveness probe — don't fetch from RCSB.
    if request.method == "HEAD":
        return Response(status_code=200, media_type="text/plain", headers=cors_headers)

    rcsb_url = f"https://files.rcsb.org/download/{pdb_id}.pdb"

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(rcsb_url)
            if response.status_code == 404:
                raise HTTPException(status_code=404, detail=f"PDB {pdb_id} not found in RCSB")
            response.raise_for_status()

            return Response(
                content=response.content,
                media_type="text/plain",
                headers=cors_headers,
            )
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch from RCSB: {str(e)}")

# Health check for proxy
@router.get("/proxy/health")
async def proxy_health():
    """Health check for proxy functionality"""
    return {"status": "healthy", "services": list(SERVICE_URLS.keys())}
