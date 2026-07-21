"""
NovoMCP Orchestration Service
Universal orchestrator for NovoMCP microservices with direct SQL
"""

# Fail fast on unsupported Python. Python 3.9 hit EOL October 2025 and
# several transitive deps (python-multipart>=0.0.30 for CVE-2024-53981,
# current starlette + fastapi) require 3.10+. Better to tell the user
# what to do than to fail mid-`pip install` with cryptic version errors.
import sys as _sys
if _sys.version_info < (3, 10):
    _v = f"{_sys.version_info.major}.{_sys.version_info.minor}"
    print(
        f"ERROR: NovoMCP requires Python 3.10 or later (detected {_v}).\n"
        "  Python 3.9 hit end-of-life October 2025.\n"
        "  Install a supported Python:\n"
        "    macOS (Homebrew):  brew install python@3.11\n"
        "    Linux (apt):       sudo apt install python3.11 python3.11-venv\n"
        "  Then recreate the venv:\n"
        "    rm -rf .venv\n"
        "    python3.11 -m venv .venv && source .venv/bin/activate\n"
        "    pip install -r requirements.txt\n"
        "    python main_https.py",
        file=_sys.stderr,
    )
    _sys.exit(2)

# Silence third-party import-time warnings that clutter the terminal for
# local users without affecting real error/warn output.
import warnings as _warnings
_warnings.filterwarnings("ignore", message=r".*urllib3 v2 only supports OpenSSL.*")
_warnings.filterwarnings("ignore", message=r".*'schema_extra' has been renamed.*")
_warnings.filterwarnings("ignore", message=r".*on_event is deprecated.*")

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager

# Load .env from the working directory before anything reads os.getenv.
# Never overrides an already-set shell/process env var — that matters for
# docker/k8s where the orchestrator injects the real values. Silent if the
# file doesn't exist.
try:
    from dotenv import load_dotenv
    load_dotenv(override=False)
except ImportError:
    pass

import httpx
import redis
from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import uvicorn

# Import AI modules
from ai.azure_openai_client import AzureOpenAIClient
from ai.intent_recognizer import IntentRecognizer
from ai.orchestration_planner import OrchestrationPlanner
from ai.project_enricher import ProjectEnricher
# Optional extension module. Provide a no-op if not on the import path.
try:
    from internal_routes import add_internal_routes
except ImportError:
    def add_internal_routes(app):  # type: ignore[no-redef]
        return None
from service_config import get_service_config
from config import settings

# Import routers
from routers.proxy import router as proxy_router
from routers.campaigns import router as campaigns_router
from routers.control_center import router as control_center_router
from routers.ai_orchestration import router as ai_orchestration_router
from routers.monitoring import router as monitoring_router
from routers.scheduled_tasks import router as scheduled_tasks_router
from routers.service_health import router as service_health_router
from routers.campaign_chat import router as campaign_chat_router

# MCP (Model Context Protocol) router for NovoMCP
from mcp.router import router as mcp_router, v1_router as mcp_v1_router, setup_mcp
from mcp.agent_endpoint import agent_router as mcp_agent_router
from mcp.events_endpoint import events_router as mcp_events_router, event_stream_manager
from mcp.llm_admin import llm_admin_router as mcp_llm_admin_router
from mcp.oauth import router as oauth_router, setup_oauth
from mcp.mcp_root import router as mcp_root_router, setup_mcp_root

# REST-only flagship route: /v1/developability-report. NOT an MCP tool; lives
# alongside the MCP routers but is a separate top-level operation. See
# docs/NovoMCP/Product/api/v1-developability-report.md.
from routers.developability_report import router as developability_report_router

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True  # Ensure app logging isn't suppressed by uvicorn defaults
)
logger = logging.getLogger(__name__)


# Field-name substrings that mark a value as sensitive. Match is case-insensitive
# against the full key, so this catches "password", "Password", "user_password",
# "api_key", "API-Key", "secret_token", etc. Used by _scrub_sensitive() before
# any request/response body gets logged.
_SENSITIVE_KEY_PARTS = (
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "authorization", "auth", "credential",
)


def _scrub_sensitive(payload):
    """Return a shallow-redacted copy of `payload` safe to log.

    Recurses into nested dicts/lists. Sensitive values become the string
    "<REDACTED>". Non-collection inputs are returned unchanged. Never mutates
    the caller's data.
    """
    if isinstance(payload, dict):
        out = {}
        for k, v in payload.items():
            key_norm = str(k).lower()
            if any(part in key_norm for part in _SENSITIVE_KEY_PARTS):
                out[k] = "<REDACTED>"
            else:
                out[k] = _scrub_sensitive(v)
        return out
    if isinstance(payload, list):
        return [_scrub_sensitive(item) for item in payload]
    return payload


# Configuration
PORT = int(os.getenv("PORT", "8018"))
SERVICE_NAME = "novomcp"
API_KEY = os.getenv("API_KEY", "")
REDIS_URL = os.getenv("REDIS_URL", "")
REDIS_ENABLED = False

# Service Registry - Mixed approach (auth stays hardcoded, others from config)
# Initialize service configuration manager for migrated services
service_config_manager = get_service_config()

# Start with auth (DO NOT MODIFY)


def _resolve_service_url(service_name: str, env_var: str, default_url: str) -> str:
    env_override = os.getenv(env_var)
    if env_override:
        return env_override

    service_info = settings.SERVICES.get(service_name)
    if service_info and service_info.get("url"):
        return service_info["url"]

    return default_url


def _build_service_entry(
    service_name: str,
    env_var: str,
    default_url: str,
    api_key_env: str,
    default_api_key: str,
) -> Dict[str, str]:
    url = _resolve_service_url(service_name, env_var, default_url)
    api_key = os.getenv(api_key_env, default_api_key)
    scheme = "https" if isinstance(url, str) and url.startswith("https://") else "http"
    return {
        "url": url,
        "api_key": api_key,
        "type": scheme,
    }


_SERVICE_DEFAULTS = [
    ("auth", "AUTH_SERVICE_URL", "", "AUTH_SERVICE_API_KEY", ""),
    # CONSOLIDATED: db-manager now routes to dashboard-aggregator (unified service)
    # Azure Container Apps internal URL
    ("db-manager", "DB_MANAGER_URL", "", "DB_MANAGER_API_KEY", ""),
    (
        "dashboard-aggregator",
        "DASHBOARD_AGGREGATOR_URL",
        "",
        "DASHBOARD_AGGREGATOR_API_KEY",
        "",
    ),
    (
        "molecular-worker",
        "MOLECULAR_WORKER_URL",
        "",  # Set via MOLECULAR_WORKER_URL env var at deploy time
        "MOLECULAR_WORKER_API_KEY",
        "",
    ),
    ("red-team", "RED_TEAM_URL", "", "RED_TEAM_API_KEY", ""),  # Not migrated to Azure
    ("zinc-integration", "ZINC_URL", "", "ZINC_API_KEY", ""),  # Not migrated to Azure
    # MolMIM Optimizer - NVIDIA MolMIM API proxy (migrated to Azure)
    (
        "molmim-optimizer",
        "MOLMIM_URL",
        "",
        "MOLMIM_API_KEY",
        "",  # Set via MOLMIM_API_KEY env var
    ),
    (
        "negative-data",
        "NEGATIVE_DATA_URL",
        "",  # Not migrated to Azure
        "NEGATIVE_DATA_API_KEY",
        "",
    ),
    (
        "chem-props",
        "CHEM_PROPS_URL",
        "",
        "CHEM_PROPS_API_KEY",
        "",
    ),
    (
        "drugsynthmc",
        "DRUGSYNTHMC_URL",
        "",  # Not migrated to Azure
        "DRUGSYNTHMC_API_KEY",
        "",
    ),
    (
        "faves-compliance",
        "NOVOMCP_COMPLIANCE_URL",
        "",
        "NOVOMCP_COMPLIANCE_API_KEY",
        "not-required",
    ),
    (
        "knowledge-graph",
        "KNOWLEDGE_GRAPH_URL",
        "",  # Not migrated to Azure
        "KNOWLEDGE_GRAPH_API_KEY",
        "",
    ),
    (
        "molecular-intelligence",
        "MOL_INTEL_URL",
        "",  # Not migrated to Azure
        "MOL_INTEL_API_KEY",
        "",
    ),
    ("openmd", "OPENMD_URL", "", "OPENMD_API_KEY", ""),  # Not migrated to Azure
    (
        "attachment-processor",
        "ATTACHMENT_PROCESSOR_URL",
        "",  # Not migrated to Azure
        "ATTACHMENT_PROCESSOR_API_KEY",
        "",
    ),
    (
        "lead-optimization",
        "LEAD_OPT_URL",
        "",
        "LEAD_OPT_API_KEY",
        "",
    ),
    (
        "autodock-gpu",
        "AUTODOCK_GPU_URL",
        "",
        "AUTODOCK_GPU_API_KEY",
        "",
    ),
    ("gromacs-md", "GROMACS_URL", "", "GROMACS_API_KEY", ""),
    (
        "gromacs-processor",
        "GROMACS_PROCESSOR_URL",
        "",
        "GROMACS_PROCESSOR_API_KEY",
        "",
    ),
    # Quantum computing service using Azure Quantum (migrated from AWS Braket)
    (
        "novo-quantum",
        "NOVO_QUANTUM_URL",
        "",
        "NOVO_QUANTUM_API_KEY",
        "",  # Set via NOVO_QUANTUM_API_KEY env var
    ),
    # OpenFold3 - NVIDIA protein structure prediction API proxy (migrated to Azure)
    (
        "openfold3",
        "OPENFOLD3_URL",
        "",
        "OPENFOLD3_API_KEY",
        "",  # Set via OPENFOLD3_API_KEY env var
    ),
    # NovoMD - Molecular Dynamics Service (Azure Container Apps)
    (
        "novomd",
        "NOVOMD_URL",
        "",
        "NOVOMD_API_KEY",
        "",  # Set via NOVOMD_API_KEY env var
    ),
    # ADDIE Models - ML ADMET prediction service (Azure Container Apps)
    (
        "addie-models",
        "ADDIE_MODELS_URL",
        "",
        "ADDIE_MODELS_API_KEY",
        "",  # Set via ADDIE_MODELS_API_KEY env var
    ),
    # NovoMCP Properties - pKa, solubility, BDE prediction (Azure Container Apps)
    (
        "novomcp-properties",
        "NOVOMCP_PROPERTIES_URL",
        "",
        "NOVOMCP_PROPERTIES_API_KEY",
        "",  # Set via NOVOMCP_PROPERTIES_API_KEY env var
    ),
    # NovoMCP QM Engine - xTB, CREST conformers, strain energy (Azure Container Apps)
    (
        "novomcp-qm",
        "NOVOMCP_QM_URL",
        "",
        "NOVOMCP_QM_API_KEY",
        "",  # Set via NOVOMCP_QM_API_KEY env var
    ),
    # NovoMCP NNP - Neural network potentials: ANI-2x, MACE-MP-0 (Azure Container Apps)
    (
        "novomcp-nnp",
        "NOVOMCP_NNP_URL",
        "",
        "NOVOMCP_NNP_API_KEY",
        "",  # Set via NOVOMCP_NNP_API_KEY env var
    ),
    # NovoMCP NEB - Transition state search via tblite GFN2-xTB + ASE CI-NEB (Azure Container Apps)
    (
        "novomcp-neb",
        "NOVOMCP_NEB_URL",
        "",
        "NOVOMCP_NEB_API_KEY",
        "",  # Set via NOVOMCP_NEB_API_KEY env var
    ),
    # NovoExpert - Phase I clinical clearance prediction (v3) + future clinical models
    # Pure inference service: takes pre-built feature dict, returns calibrated
    # probability + SHAP + domain competence assessment. Feature orchestration
    # happens in novomcp before the call.
    (
        "novoexpert",
        "NOVOEXPERT_URL",
        "",
        "NOVOEXPERT_API_KEY",
        "",  # Set via NOVOEXPERT_API_KEY env var (novoexpert-api-key-2026-...)
    ),
]

SERVICE_REGISTRY = {
    name: _build_service_entry(name, env_var, default_url, api_key_env, default_api_key)
    for name, env_var, default_url, api_key_env, default_api_key in _SERVICE_DEFAULTS
}

# Merge with dynamically loaded services (but keep auth untouched)
try:
    migrated_services = service_config_manager.get_all_services()
    SERVICE_REGISTRY.update(migrated_services)
    logger.info(f"Loaded {len(migrated_services)} service configurations")
except Exception as e:
    logger.warning(f"Failed to load some service configs, using defaults: {e}")

# Global connections
redis_client: Optional[redis.Redis] = None
http_client: Optional[httpx.AsyncClient] = None

# AI modules
ai_client: Optional[AzureOpenAIClient] = None
intent_recognizer: Optional[IntentRecognizer] = None
orchestration_planner: Optional[OrchestrationPlanner] = None
project_enricher: Optional[ProjectEnricher] = None

# Request/Response Models
class ServiceCall(BaseModel):
    """Single service call specification"""
    service: str = Field(..., description="Service name from registry")
    endpoint: str = Field(..., description="API endpoint path")
    method: str = Field(default="GET", description="HTTP method")
    data: Optional[Dict[str, Any]] = Field(default=None, description="Request body")
    params: Optional[Dict[str, Any]] = Field(default=None, description="Query parameters")

class OrchestrationRequest(BaseModel):
    """Orchestration request for multiple services"""
    workflow_type: str = Field(..., description="Type of workflow")
    services: List[ServiceCall] = Field(..., description="Services to call")
    parallel: bool = Field(default=True, description="Execute in parallel if true")
    timeout: int = Field(default=30, description="Timeout in seconds")

class WorkflowRequest(BaseModel):
    """Custom workflow request"""
    steps: List[Dict[str, Any]] = Field(..., description="Workflow steps")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Workflow context")

# Lifespan manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle"""
    global redis_client, http_client, ai_client, intent_recognizer, orchestration_planner, project_enricher

    # Validate critical secrets at startup — only when the deployment is
    # explicitly marked production AND the spine is running against a
    # custom (hosted) backend. Local runs need none of these.
    from core.secrets import require_secret
    _is_production = os.getenv("ENVIRONMENT", "development") == "production"
    _uses_custom_spine = any(
        os.getenv(k, "local").lower() == "custom"
        for k in ("NOVO_AUTH", "NOVO_METER", "NOVO_AUDIT")
    )
    if _is_production and _uses_custom_spine:
        require_secret("JWT_SECRET_KEY", "Required for authentication tokens")
        require_secret("API_KEY", "Required for service authentication")

    logger.debug(
        "Startup: DB_NAME_5=%s DB_NAME_6=%s USE_DIRECT_SQL=%s",
        os.getenv("DB_NAME_5", "unset"),
        os.getenv("DB_NAME_6", "unset"),
        os.getenv("USE_DIRECT_SQL", "true"),
    )

    # JWT is only required when running with a custom spine that uses JWTs.
    # Local mode skips it entirely; production deployments should set it.
    _uses_custom_spine = any(
        os.getenv(k, "local").lower() == "custom"
        for k in ("NOVO_AUTH", "NOVO_METER", "NOVO_AUDIT")
    )
    if _uses_custom_spine and not settings.JWT_SECRET_KEY:
        logger.warning(
            "JWT_SECRET_KEY is not set — required by custom spine implementations. "
            "Set it to a strong random secret."
        )

    logger.info("Starting NovoMCP Orchestration Service")

    # Non-blocking update check against GitHub Releases. Logs at most two
    # info lines (upgrade-since-last-boot + newer-available). Silent on
    # any failure. Opt-out via NOVOMCP_NO_UPDATE_CHECK=1.
    try:
        from core.updater import log_update_status_on_boot
        await log_update_status_on_boot()
    except Exception:
        pass

    # Initialize AI modules
    try:
        ai_client = AzureOpenAIClient()
        intent_recognizer = IntentRecognizer(ai_client)
        orchestration_planner = OrchestrationPlanner(ai_client)
        project_enricher = ProjectEnricher(ai_client)
        logger.info("AI modules initialized with GPT-5")
    except Exception as e:
        logger.warning(f"AI modules initialization failed: {e}")
        ai_client = None
        intent_recognizer = None
        orchestration_planner = None
        project_enricher = None

    # Initialize Redis if available
    if REDIS_URL:
        try:
            redis_client = redis.from_url(REDIS_URL, decode_responses=True)
            redis_client.ping()
            logger.info("Connected to Redis for caching")
            global REDIS_ENABLED
            REDIS_ENABLED = True
        except Exception as e:
            logger.warning(f"Redis connection failed (non-critical): {e}")
            redis_client = None

    # Initialize Redis pub/sub for cross-task WebSocket broadcasting
    redis_pubsub = None
    if REDIS_URL and settings.REDIS_ENABLE_PUBSUB:
        try:
            from core.redis_pubsub import RedisPubSubManager
            import core.redis_pubsub as redis_pubsub_module
            from routers.ai_orchestration import global_ws_manager

            redis_pubsub = RedisPubSubManager(REDIS_URL, settings.REDIS_KEY_PREFIX)
            connected = await redis_pubsub.connect()

            if connected:
                # Start subscriber with handler that rebroadcasts to local WebSockets
                # AND to Studio /v1/events SSE streams. Both surfaces consume the
                # same Redis pubsub channel; the events stream filters per-org so
                # cross-tenant leakage is impossible regardless of who published.
                async def rebroadcast_handler(message: Dict[str, Any]):
                    """Rebroadcast Redis messages to local WebSocket + SSE consumers."""
                    await global_ws_manager.broadcast(message)
                    await event_stream_manager.dispatch(message)

                await redis_pubsub.start_subscriber(rebroadcast_handler)
                redis_pubsub_module.redis_pubsub_manager = redis_pubsub
                logger.info("Redis pub/sub initialized for cross-task WebSocket broadcasting")
            else:
                logger.warning("Redis pub/sub connection failed")
        except Exception as e:
            logger.error(f"Failed to initialize Redis pub/sub: {e}")
    else:
        logger.info("Redis pub/sub disabled (WebSockets will be task-local only)")

    # Initialize HTTP client
    http_client = httpx.AsyncClient(timeout=60.0, verify=settings.httpx_verify)
    logger.info("HTTP client initialized")

    # Initialize NovoMCP (Model Context Protocol for Claude)
    try:
        mcp_service_urls = {
            "faves-compliance": SERVICE_REGISTRY.get("faves-compliance", {}).get("url", ""),
            "molmim-optimizer": SERVICE_REGISTRY.get("molmim-optimizer", {}).get("url", ""),
            "openfold3": SERVICE_REGISTRY.get("openfold3", {}).get("url", ""),
            # V2 tools - added for get_3d_properties, calculate_properties, predict_admet
            "novomd": SERVICE_REGISTRY.get("novomd", {}).get("url", ""),
            "chem-props": SERVICE_REGISTRY.get("chem-props", {}).get("url", ""),
            "addie-models": SERVICE_REGISTRY.get("addie-models", {}).get("url", ""),
            # Pipeline tools - lead optimization, docking, MD simulation
            "lead-optimization": SERVICE_REGISTRY.get("lead-optimization", {}).get("url", ""),
            "autodock-gpu": SERVICE_REGISTRY.get("autodock-gpu", {}).get("url", ""),
            "gromacs-md": SERVICE_REGISTRY.get("gromacs-md", {}).get("url", ""),
            # Property prediction - pKa, solubility, BDE
            "novomcp-properties": SERVICE_REGISTRY.get("novomcp-properties", {}).get("url", ""),
            # QM Engine - xTB, CREST, strain energy
            "novomcp-qm": SERVICE_REGISTRY.get("novomcp-qm", {}).get("url", ""),
            # Neural network potentials - ANI-2x, MACE
            "novomcp-nnp": SERVICE_REGISTRY.get("novomcp-nnp", {}).get("url", ""),
            # NEB transition state search - tblite GFN2-xTB + ASE CI-NEB
            "novomcp-neb": SERVICE_REGISTRY.get("novomcp-neb", {}).get("url", ""),
            # AlphaFlow - conformational dynamics
            "alphaflow": os.getenv("ALPHAFLOW_URL", ""),
            # NovoExpert - Phase I clinical clearance prediction
            "novoexpert": SERVICE_REGISTRY.get("novoexpert", {}).get("url", ""),
        }
        mcp_internal_key = os.getenv("MCP_INTERNAL_API_KEY", API_KEY)
        setup_mcp(mcp_service_urls, mcp_internal_key, redis_client)
        logger.info("NovoMCP initialized - Model Context Protocol ready for Claude integration")

        # Initialize OAuth for MCP clients (Redis-backed for persistence across restarts)
        from mcp.router import _auth_manager, _tool_executor
        if _auth_manager:
            setup_oauth(_auth_manager, redis_client=redis_client)
            logger.info("NovoMCP OAuth initialized - MCP connector ready")

            # Initialize MCP root handler for Claude custom connectors
            if _tool_executor:
                setup_mcp_root(_tool_executor, _auth_manager)
                logger.info("NovoMCP Root handler initialized - MCP at root path ready")
        else:
            logger.warning("NovoMCP OAuth skipped - auth manager not available")
    except Exception as e:
        logger.error(f"Failed to initialize NovoMCP: {e}")

    # Initialize Campaign Loop Manager for autonomous campaigns
    try:
        from ai.campaign_loop import initialize_campaign_loop_manager
        from ai.campaign_decision_engine import CampaignDecisionEngine
        from routers.ai_orchestration import (
            get_campaign_status,
            store_campaign_learning,
            orchestrate_decision
        )

        # Create decision engine
        campaign_decision_engine = CampaignDecisionEngine(ai_client)

        # Initialize campaign loop manager with internal function references
        campaign_loop_mgr = initialize_campaign_loop_manager(
            decision_engine=campaign_decision_engine,
            get_status_func=get_campaign_status,
            learn_func=store_campaign_learning,
            orchestrate_func=orchestrate_decision
        )
        logger.info("Campaign Loop Manager initialized for autonomous execution")

        # AUTO-RESTART LOOPS FOR EXISTING ACTIVE CAMPAIGNS
        # Runs in the background. Skipped entirely when no database is
        # configured (typical for local runs) — the campaign store lives
        # in the DB, so there's nothing to restart without one.
        async def auto_restart_campaigns():
            """Restart active campaign loops after startup."""
            if not os.getenv("AURORA_HOST"):
                logger.debug("Auto-restart: no AURORA_HOST configured, skipping campaign restoration")
                return
            try:
                startup_wait = int(os.getenv("CAMPAIGN_AUTORESTART_STARTUP_WAIT_SECONDS", "45"))
                logger.info(f"Auto-restart: Waiting {startup_wait}s for application startup to complete...")
                await asyncio.sleep(startup_wait)
                logger.info("Auto-restart: Starting campaign loop restoration...")

                active_campaigns = []
                restart_method = "none"

                # Query database directly (NO FALLBACK - autonomous operation requires direct SQL)
                try:
                    logger.info("Auto-restart: Querying database directly for active campaigns...")

                    from core.db_helper import query_sql

                    # Query directly using SQL helper (NO HTTP overhead)
                    active_campaigns = await query_sql("""
                        SELECT id, name, status
                        FROM campaigns
                        WHERE status = 'active'
                        ORDER BY created_at DESC
                    """)

                    restart_method = "direct_sql"
                    logger.info(f"Auto-restart: Direct SQL query found {len(active_campaigns)} active campaigns")

                except Exception as db_error:
                    logger.error(
                        f"Auto-restart: Direct SQL query failed - NO FALLBACK AVAILABLE: {type(db_error).__name__}: {db_error}"
                    )
                    active_campaigns = []

                # Restart campaign loops
                if active_campaigns:
                    logger.info(f"Auto-restart: Restarting {len(active_campaigns)} campaign loops (method: {restart_method})...")
                    restarted_count = 0
                    failed_count = 0

                    for campaign in active_campaigns:
                        try:
                            campaign_id = campaign.get('id')
                            campaign_name = campaign.get('name', 'Unknown')

                            if not campaign_id:
                                logger.warning(f"Auto-restart: Skipping campaign with missing ID: {campaign}")
                                failed_count += 1
                                continue

                            if not campaign_loop_mgr:
                                logger.error("Auto-restart: Campaign loop manager not initialized, cannot restart loops")
                                failed_count += 1
                                break

                            started = campaign_loop_mgr.start_campaign(campaign_id)
                            if started:
                                logger.info(f"Auto-restart: ✓ Restarted loop for {campaign_id} ({campaign_name})")
                                restarted_count += 1
                            else:
                                logger.info(f"Auto-restart: ⊙ Loop already running for {campaign_id} ({campaign_name})")

                        except Exception as loop_error:
                            logger.error(f"Auto-restart: Failed to restart loop for campaign {campaign.get('id', 'unknown')}: {loop_error}")
                            failed_count += 1

                    logger.info(f"Auto-restart: COMPLETE - Restarted: {restarted_count}, Failed: {failed_count}, Total: {len(active_campaigns)}")
                else:
                    logger.info("Auto-restart: No active campaigns found to restart (this is normal for fresh deployments)")

            except Exception as e:
                # Catch-all for any unexpected errors - this should NEVER crash the application
                logger.error(f"Auto-restart: CRITICAL ERROR in background task (non-fatal to application): {type(e).__name__}: {e}", exc_info=True)
                logger.error("Auto-restart: Campaign loops NOT restarted - manual intervention may be required")

        # Start auto-restart in background (non-blocking, failure-safe)
        try:
            asyncio.create_task(auto_restart_campaigns())
            logger.info("Campaign auto-restart task scheduled successfully (running in background)")
        except Exception as task_error:
            logger.error(f"Failed to schedule auto-restart task (non-critical): {task_error}")

    except Exception as e:
        logger.error(f"Failed to initialize Campaign Loop Manager: {e}")

    # Start background job poller for async MD/compute jobs
    try:
        from mcp.router import _tool_executor
        if _tool_executor:
            asyncio.create_task(_tool_executor.start_job_poller())
            logger.info("Job poller background task scheduled successfully")
            # GPU idle reaper: scales warmed GPU HTTP services back to replicas=0
            # after idle, completing the scale-from-zero cost cycle.
            asyncio.create_task(_tool_executor.start_gpu_idle_reaper())
            logger.info("GPU idle reaper background task scheduled successfully")
    except Exception as poller_error:
        logger.error(f"Failed to schedule job poller task (non-critical): {poller_error}")

    # Queue one-off chat history migration into metadata on startup (idempotent)
    async def auto_migrate_chat_history():
        try:
            # Chat history migration is opt-in and depends on the scripts/
            # package + a configured database. Default off so local runs
            # don't fire it.
            migrate_flag = os.getenv("CHAT_MIGRATE_ON_STARTUP", "false").lower() == "true"
            if not migrate_flag:
                return
            if not os.getenv("AURORA_HOST"):
                logger.debug("Chat migration: no AURORA_HOST configured, skipping")
                return

            wait_seconds = int(os.getenv("CHAT_MIGRATE_STARTUP_WAIT_SECONDS", "30"))
            logger.info(f"Chat migration: Waiting {wait_seconds}s before starting...")
            await asyncio.sleep(wait_seconds)

            try:
                from scripts import migrate_chat_history as mch
            except Exception as e:
                logger.debug(f"Chat migration: migration script not available: {e}")
                return

            # Determine scan limit (env configurable); None scans all campaigns
            limit_env = os.getenv("CHAT_MIGRATE_LIMIT", "")
            limit = int(limit_env) if limit_env.isdigit() else None

            # Get campaigns to scan
            try:
                ids = await mch._get_all_campaign_ids(limit)
            except Exception as e:
                logger.error(f"Chat migration: Failed to list campaigns: {e}")
                return

            logger.info(f"Chat migration: Scanning {len(ids)} campaign(s){' (limited)' if limit else ''}")
            updated = 0
            for cid in ids:
                try:
                    res = await mch.migrate_campaign(cid, dry_run=False, verbose=False)
                    if res.get("updated"):
                        updated += 1
                except Exception as e:
                    logger.warning(f"Chat migration: Error migrating {cid}: {e}")

            logger.info(f"Chat migration: Complete. Updated {updated} of {len(ids)} campaign(s)")
        except Exception as e:
            logger.error(f"Chat migration: Unexpected error (non-fatal): {e}")

    try:
        asyncio.create_task(auto_migrate_chat_history())
        logger.info("Chat history migration task scheduled (background)")
    except Exception as e:
        logger.error(f"Failed to schedule chat migration task (non-critical): {e}")

    # Build the tool-search in-memory index (WS10). Scheduled as a background
    # task so startup isn't blocked on Azure OpenAI embedding latency. The
    # endpoint returns empty results until the index is ready, and the status
    # endpoint reports `ready: false` so callers can detect the warmup state.
    # See docs/NovoMCP/AGENT-SDK-TOOL-SEARCH.md for architecture.
    try:
        from mcp.tool_search import build_index as _build_tool_search_index

        async def _build_tool_search_background():
            try:
                await _build_tool_search_index()
            except Exception as e:
                logger.warning(
                    f"tool_search index build failed (endpoint runs in keyword-match "
                    f"fallback): {e}"
                )

        asyncio.create_task(_build_tool_search_background())
        logger.info("Tool-search index build scheduled (background)")
    except Exception as e:
        logger.error(f"Failed to schedule tool-search index build (non-critical): {e}")

    yield

    # Cleanup
    logger.info("Shutting down NovoMCP")

    # Shutdown Redis pub/sub
    if redis_pubsub:
        try:
            await redis_pubsub.stop()
            logger.info("Redis pub/sub shut down")
        except Exception as e:
            logger.error(f"Error shutting down Redis pub/sub: {e}")

    if http_client:
        await http_client.aclose()
    if redis_client:
        redis_client.close()

# Create FastAPI app
app = FastAPI(
    title="NovoMCP Orchestration Service",
    version="1.0.0",
    description="Universal orchestrator for NovoMCP microservices",
    lifespan=lifespan,
    # Public-surface hardening: api.novomcp.com is internet-facing. The
    # auto-generated Swagger/OpenAPI documented the internal orchestration
    # surface (/internal/*, /api/<svc>/* proxies, db-manager) to the open
    # internet. Disable them — the customer-facing API gets a curated spec,
    # not this auto one.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Add CORS middleware — restrict to known frontends and the apps gateway
CORS_ORIGINS = [
    origin for origin in [
        os.getenv("CORS_ORIGIN_APPS", "https://ai.novomcp.com"),
        os.getenv("CORS_ORIGIN_FRONTEND", "https://app.novomcp.com"),
        "https://novomcp.com",
        "https://claude.ai",
        # AI assistant hosts that embed MCP connectors in browser iframes.
        # Each host's connector UI runs from its own origin; the OAuth
        # /authorize redirect + iframe asset fetches need CORS to clear.
        # OAuth /authorize itself is already permissive on redirect_uri
        # (see mcp/oauth.py:283), so the only gap was the browser preflight.
        "https://grok.com",
        "https://x.ai",
        "https://chatgpt.com",
        "https://chat.openai.com",
        "https://gemini.google.com",
        "https://chat.mistral.ai",
        "https://coral.cohere.com",
        # NVIDIA NIM build console — speculative coverage if anyone uses
        # build.nvidia.com as an MCP-client browser surface. NeMo Agent
        # Toolkit, OpenCode, Continue.dev, and Cody are CLI/IDE clients
        # (no browser origin) so they don't need CORS entries.
        "https://build.nvidia.com",
        # Ollama runs locally; its default port is 11434. Open WebUI
        # (self-hosted) defaults to :8080. The existing localhost:3000/3002
        # entries below are dev-frontend; these are the daemon/UI origins.
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        # Chrome extension (Manifest V3, deterministic ID via the `key` field
        # in the novomcp-chrome-extension repo's manifest.json). Pinned to the
        # production extension ID — NOT chrome-extension://* (wildcard would
        # let any installed extension hit the API).
        os.getenv(
            "CORS_ORIGIN_CHROME_EXT",
            "chrome-extension://ehdclhkckafhenkjmglklibfghehanpj",
        ),
        # Word add-in (Office.js taskpane). The iframe origin is the
        # SourceLocation host — production manifest will eventually live at
        # addin.novomcp.com (Azure Blob + Front Door per Novo_Dist_Play.md §4),
        # but the CORS pin is added now so AppSource hosting can flip on
        # without a server deploy when we get there.
        os.getenv(
            "CORS_ORIGIN_WORD_ADDIN",
            "https://addin.novomcp.com",
        ),
        # Localhost dev origins — unconditional. CORS is browser-level
        # only and doesn't gate authentication; every API call still
        # requires a valid Bearer token. Allowing localhost just lets
        # the browser complete the round-trip when the request comes
        # from a developer running the Word add-in or Chrome extension
        # locally. The previous env-var gate (CORS_ALLOW_LOCALHOST) had
        # opaque rollout behavior on Azure Container Apps; hardcoding
        # is more predictable.
        "https://localhost:3000",
        "https://localhost:3002",
        "http://localhost:3000",
        "http://localhost:3002",
    ] if origin
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization", "Content-Type", "X-API-Key", "X-User-ID",
        "X-Tenant-ID", "X-Correlation-ID", "X-Admin-Key",
        "x-service-key", "x-forwarded-host",
        # Studio SPA (app.novomcp.com/studio) calls /v1 cross-origin with the
        # dashboard JWT. It also tags org/user/roles for the BFF path; allow them
        # through preflight here. On the direct JWT path quanta derives identity
        # from the validated token, not these headers (so they can't spoof org).
        "X-Org-ID", "X-User-Roles",
        # Chrome extension / Word add-in surface tagging. X-Novo-Surface
        # namespaces funnel_id slots and persists into the audit row;
        # X-Novo-Client is a secondary diagnostic identifier (Chrome strips
        # User-Agent for extension fetch in some contexts).
        "X-Novo-Surface", "X-Novo-Client",
    ],
)

# --- Public-surface hardening ------------------------------------------------
# api.novomcp.com is the internet-facing host. The /internal/* service-to-service
# endpoints are auth-gated, but they should not be reachable from the public
# internet at all. Internal callers reach novomcp via cluster DNS
# (NOVOMCP_INTERNAL_URL → novomcp.default.svc.cluster.local), a
# different Host, so they are unaffected. Return 404 for these prefixes when the
# request arrives on a public host. Internal callers reach these via cluster DNS
# (a different Host), so they are unaffected.
#
# ⚠️ /proxy/* and /novomcp/* are deliberately NOT blocked: the BROWSER (on
# app.novomcp.com) calls api.novomcp.com/proxy/* directly for the auth flows
# (login / signup / verify-email / profile / logout / token-refresh) because
# cluster DNS isn't browser-routable. /files/* stays public too — it's the
# unauthenticated hosted-upload resolver (GET /files/{id}/upload-url).
_PUBLIC_API_HOSTS = {
    h.strip().lower()
    for h in os.getenv("PUBLIC_API_HOSTS", "api.novomcp.com").split(",")
    if h.strip()
}
_PUBLIC_BLOCKED_PREFIXES = (
    "/internal/",     # service-to-service SQL query/write
    "/api/",          # internal service proxies (proxy_router)
    "/orchestrate",   # agentic orchestration
    "/workflow",      # workflow execution
    "/metrics",       # ops metrics
    "/services",      # service registry
)


@app.middleware("http")
async def _block_internal_paths_on_public_host(request: Request, call_next):
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if host in _PUBLIC_API_HOSTS and request.url.path.startswith(_PUBLIC_BLOCKED_PREFIXES):
        from starlette.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return await call_next(request)


# Add internal service routes for stateless auth architecture
add_internal_routes(app)


# Structured 422 handler scoped to /v1/developability-report. Mirrors the
# error envelope pattern in mcp/router.py:316-321 — callers see a stable
# `detail.error_code` plus message instead of FastAPI's raw Pydantic dump.
# Other routes keep FastAPI's default 422 shape (proxy router etc. depend on
# it). The discriminated-union mismatch on `screen_format` becomes
# `error_code="unsupported_screen_format"` per brief section 7.2.
from fastapi.exceptions import RequestValidationError  # noqa: E402
from starlette.responses import JSONResponse as _StarletteJSONResponse  # noqa: E402


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError):
    """Structured 422 for /v1/developability-report; default shape elsewhere.

    For the developability-report path, detect a discriminated-union mismatch
    on `screen_format` and emit `error_code="unsupported_screen_format"`.
    Other validation errors on that path get `error_code="validation_error"`
    with the raw Pydantic error list under `detail.errors`. All other paths
    fall back to FastAPI's default 422 envelope.
    """
    path = request.url.path
    if not path.startswith("/v1/developability-report"):
        # Default FastAPI behavior for every other route.
        return _StarletteJSONResponse(
            status_code=422,
            content={"detail": exc.errors()},
        )

    errors = exc.errors()
    # Pydantic v2 surfaces discriminated-union mismatches as type
    # "union_tag_invalid" with loc including the discriminator field.
    is_screen_format = any(
        err.get("type") in ("union_tag_invalid", "union_tag_not_found")
        or ("screen_format" in (err.get("loc") or ()))
        for err in errors
    )
    if is_screen_format:
        return _StarletteJSONResponse(
            status_code=422,
            content={
                "detail": {
                    "error_code": "unsupported_screen_format",
                    "message": (
                        "Unsupported screen_format. Mode A v1 accepts "
                        "'generic' or 'lincs'. Mode B/C are out of scope."
                    ),
                    "supported_formats": ["generic", "lincs"],
                    "errors": errors,
                },
            },
        )
    return _StarletteJSONResponse(
        status_code=422,
        content={
            "detail": {
                "error_code": "validation_error",
                "message": "Request body failed schema validation.",
                "errors": errors,
            },
        },
    )


# OpenAPI spec for the REST API. Two sources, in priority order:
#   1. A curated `openapi.json` next to main_https.py (production deploys ship
#      a hand-tuned spec generated by scripts/gen_openapi.py).
#   2. FastAPI's auto-generated spec built from the live route table (OSS
#      installs get this out of the box — no build step needed).
# Both are served at /openapi.json and /v1/openapi.json.
_OPENAPI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "openapi.json")
_OPENAPI_CACHE = None


@app.get("/openapi.json", include_in_schema=False)
@app.get("/v1/openapi.json", include_in_schema=False)
async def serve_curated_openapi():
    global _OPENAPI_CACHE
    if _OPENAPI_CACHE is None:
        try:
            with open(_OPENAPI_PATH) as f:
                _OPENAPI_CACHE = json.load(f)
        except FileNotFoundError:
            # OSS fallback: generate the spec from the live route table. Cache
            # so we don't rebuild on every request.
            _OPENAPI_CACHE = app.openapi()
    return JSONResponse(_OPENAPI_CACHE)


# Include routers
# Proxy router handles service routing including /db paths
# Trusts Dashboard-Aggregator and DB-Manager to work with DBSchema-Manager
app.include_router(proxy_router, prefix="/api")

# Add new routers
app.include_router(campaigns_router, prefix="/novomcp", tags=["campaigns"])
app.include_router(control_center_router, prefix="/control-center", tags=["control-center"])
app.include_router(control_center_router, prefix="/novomcp/control-center", tags=["control-center"])
app.include_router(ai_orchestration_router, prefix="/api", tags=["ai-orchestration"])
app.include_router(ai_orchestration_router, prefix="/novomcp/api", tags=["ai-orchestration"])
app.include_router(monitoring_router, prefix="/novomcp", tags=["monitoring"])
app.include_router(scheduled_tasks_router, prefix="/novomcp", tags=["scheduled-tasks"])
app.include_router(scheduled_tasks_router, prefix="/scheduled", tags=["scheduled-tasks"])  # Direct access for EventBridge
app.include_router(service_health_router, prefix="/novomcp", tags=["service-health"])
app.include_router(campaign_chat_router, prefix="/novomcp", tags=["campaign-chat"])

# NovoMCP - Model Context Protocol for Claude integration
# Exposed at /mcp for SSE connections and tool calls (legacy)
app.include_router(mcp_router, tags=["NovoMCP"])

# Developability report endpoint(s). Two routes mounted from the same module:
#   /v1/tools/developability_report  — canonical catalog-shape route per the
#                                       2026-06-15 design unification: 69 MCP
#                                       tools + 2 API-only tools share one
#                                       call pattern (`{"arguments": ...}` in,
#                                       `{"result": ..., "usage": ...}` out).
#                                       Marked `x-mcp-exposed: false` so the
#                                       OpenAPI consumers distinguish it from
#                                       the MCP-served tools.
#   /v1/developability-report        — deprecated alias retained for the
#                                       T2-D evaluation harness + demo script
#                                       that ship against the legacy URL.
#
# MUST be registered BEFORE mcp_v1_router because FastAPI route matching is
# registration-order; the parametric /v1/tools/{tool_name} in mcp_v1_router
# would otherwise shadow this explicit /v1/tools/developability_report path
# and return "Tool not found: developability_report" (the MCP catalog
# handler doesn't know about API-only tools).
app.include_router(developability_report_router, tags=["NovoMCP v1 — Developability Report"])

# Versioned customer REST API alias (/v1/tools, /v1/tools/{name}, ...)
app.include_router(mcp_v1_router, tags=["NovoMCP v1"])
# Studio server-side agent (SSE) — POST /v1/agent/chat
app.include_router(mcp_agent_router, tags=["NovoMCP Agent"])
# Studio Streamable HTTP notification stream — GET /v1/events
app.include_router(mcp_events_router, tags=["NovoMCP Events"])
# Org LLM config admin (Studio settings) — GET/PUT/DELETE /v1/org/llm-config
app.include_router(mcp_llm_admin_router, tags=["NovoMCP Agent Admin"])

# MCP Root Handler - Streamable HTTP at root (/) for Claude custom connectors
# This is the primary MCP endpoint - Claude expects MCP at root, not /mcp
app.include_router(mcp_root_router, tags=["MCP-Root"])

# OAuth 2.0 for MCP clients (Claude, ChatGPT, Cursor, etc.)
# Endpoints at /.well-known/oauth-authorization-server and /oauth/*
app.include_router(oauth_router, tags=["OAuth"])

# Dependency for API key validation
async def validate_api_key(x_api_key: Optional[str] = Header(None)):
    """Validate API key"""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key

# Health check endpoint
@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": SERVICE_NAME,
        "timestamp": datetime.utcnow().isoformat(),
        "redis": "connected" if REDIS_ENABLED else "disabled",
        "services_available": len(SERVICE_REGISTRY)
    }

# Root endpoint - handles both browser visitors and API clients
@app.get("/")
async def root(request: Request):
    """
    Root endpoint with smart routing:
    - Browsers (Accept: text/html) → Redirect to marketing page
    - API clients (Accept: application/json) → Return service info
    - MCP clients use HEAD/POST (handled by mcp_root_router)
    """
    from fastapi.responses import RedirectResponse

    # Check if this is a browser request
    accept = request.headers.get("accept", "")
    if "text/html" in accept and "application/json" not in accept:
        # Browser request - redirect to marketing page
        return RedirectResponse(
            url="https://novomcp.com",
            status_code=302
        )

    # API request - return service info. Identity reflects the surface the
    # request hit — Novo at ai.novomcp.com, Novo Compute at compute.novomcp.com —
    # derived from the Host header so it's correct whether one deployment or two
    # serves the two hostnames.
    host = (request.headers.get("host") or "").lower()
    is_compute = host.startswith("compute.") or ".compute." in host
    surface_name = "Novo Compute" if is_compute else "Novo"
    description = (
        "Novo Compute — the NovoMCP computational chemistry engine, GPU/quantum "
        "compute surface: docking, molecular dynamics, QM/NNP, conformer + "
        "protein-structure prediction, and metal-site parameterization."
        if is_compute else
        "Novo — the NovoMCP computational chemistry engine: 122M pre-computed "
        "molecules and 69 in-silico tools spanning ADMET, FAVES compliance, "
        "literature, and autonomous discovery funnels."
    )
    return {
        "name": surface_name,
        "service": f"{surface_name} — NovoMCP computational chemistry engine",
        "version": "2.0.0",
        "protocol": "MCP 2025-06-18",
        "transport": "streamable-http",
        "description": description,
        "endpoints": {
            "mcp": {
                "root": "/",
                "method": "POST",
                "description": "MCP JSON-RPC endpoint (Streamable HTTP - primary)"
            },
            "streamable_http": {
                "tools": "/mcp/tools",
                "execute": "/mcp/tools/{tool_name}",
                "health": "/mcp/health",
                "usage": "/mcp/usage",
                "info": "/mcp/info"
            },
            "oauth": {
                "discovery": "/.well-known/oauth-authorization-server",
                "authorize": "/oauth/authorize",
                "token": "/oauth/token",
                "register": "/oauth/register"
            },
            "deprecated": {
                "sse": "/mcp/sse",
                "sse_call": "/mcp/sse/call",
                "note": "SSE transport is deprecated. Use Streamable HTTP."
            }
        },
        "authentication": "OAuth 2.0 with PKCE",
        "documentation": "https://novomcp.com/docs"
    }

# List available services
@app.get("/services", dependencies=[Depends(validate_api_key)])
async def list_services():
    """List all available services"""
    return {
        "services": [
            {
                "name": name,
                "url": config["url"],
                "type": config["type"],
                "available": True
            }
            for name, config in SERVICE_REGISTRY.items()
        ]
    }

# Call a single service
async def call_service(
    service_name: str,
    endpoint: str,
    method: str = "GET",
    data: Optional[Dict] = None,
    params: Optional[Dict] = None,
    user_context: Optional[Dict] = None,
) -> Dict:
    """Call a single service with DNS-aware fallbacks"""
    if service_name not in SERVICE_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Service {service_name} not found")

    service = SERVICE_REGISTRY[service_name]
    endpoint_path = endpoint if endpoint.startswith("/") else f"/{endpoint}"

    candidates: List[str] = []

    primary_url = service.get("url")
    if primary_url:
        candidates.append(primary_url.rstrip("/"))

    service_connect = settings.SERVICES.get(service_name, {}).get("url")
    if service_connect:
        service_connect = service_connect.rstrip("/")
        if service_connect not in candidates:
            candidates.append(service_connect)

    for name, _, default_url, _, _ in _SERVICE_DEFAULTS:
        if name == service_name and default_url:
            default_url = default_url.rstrip("/")
            if default_url not in candidates:
                candidates.append(default_url)
            break

    # Some services expect "API-Key" header instead of "X-API-Key"
    api_key_header = "API-Key" if service_name in ("novo-quantum", "gromacs-md") else "X-API-Key"
    headers = {
        api_key_header: service["api_key"],
        "Content-Type": "application/json",
    }

    if user_context:
        headers.update(user_context)

    last_error: Optional[Exception] = None

    for base_url in candidates:
        url = f"{base_url}{endpoint_path}"
        logger.info(f"Calling {service_name} at {url} with method {method}")
        if data:
            logger.debug(f"Request body: {data}")

        try:
            response = await http_client.request(
                method=method,
                url=url,
                json=data,
                params=params,
                headers=headers,
            )

            if response.status_code >= 400:
                return {
                    "service": service_name,
                    "error": f"HTTP {response.status_code}",
                    "detail": response.text,
                }

            return {
                "service": service_name,
                "status": "success",
                "data": response.json() if response.text else {},
            }
        except httpx.RequestError as exc:
            last_error = exc
            logger.warning(
                f"Transport error calling {service_name} at {url}: {exc}. Trying fallback if available."
            )
            continue
        except Exception as exc:
            logger.error(f"Error calling {service_name}: {exc}")
            return {
                "service": service_name,
                "error": str(exc),
            }

    error_message = str(last_error) if last_error else "Unknown transport error"
    logger.error(f"All endpoints failed for {service_name}: {error_message}")
    return {
        "service": service_name,
        "error": error_message,
    }

# Orchestrate multiple service calls
@app.post("/orchestrate", dependencies=[Depends(validate_api_key)])
async def orchestrate(request: OrchestrationRequest):
    """Orchestrate multiple service calls"""
    results = {}
    
    if request.parallel:
        # Execute all calls in parallel
        tasks = []
        for call in request.services:
            task = call_service(
                call.service,
                call.endpoint,
                call.method,
                call.data,
                call.params
            )
            tasks.append(task)
        
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, response in enumerate(responses):
            service_name = request.services[i].service
            if isinstance(response, Exception):
                results[service_name] = {"error": str(response)}
            else:
                results[service_name] = response
    else:
        # Execute calls sequentially
        for call in request.services:
            result = await call_service(
                call.service,
                call.endpoint,
                call.method,
                call.data,
                call.params
            )
            results[call.service] = result
    
    return {
        "workflow_type": request.workflow_type,
        "execution": "parallel" if request.parallel else "sequential",
        "results": results,
        "timestamp": datetime.utcnow().isoformat()
    }

# Execute custom workflow
@app.post("/workflow", dependencies=[Depends(validate_api_key)])
async def execute_workflow(request: WorkflowRequest):
    """Execute a custom workflow with steps"""
    results = []
    context = request.context or {}
    
    for step in request.steps:
        step_result = {}
        
        if step.get("type") == "service_call":
            result = await call_service(
                step["service"],
                step["endpoint"],
                step.get("method", "GET"),
                step.get("data"),
                step.get("params")
            )
            step_result = {
                "step": step.get("name", "unnamed"),
                "result": result
            }
        elif step.get("type") == "parallel_calls":
            tasks = []
            for call in step["calls"]:
                task = call_service(
                    call["service"],
                    call["endpoint"],
                    call.get("method", "GET"),
                    call.get("data"),
                    call.get("params")
                )
                tasks.append(task)
            
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            step_result = {
                "step": step.get("name", "parallel_execution"),
                "results": responses
            }
        else:
            step_result = {
                "step": step.get("name", "unknown"),
                "error": "Unknown step type"
            }
        
        results.append(step_result)
        
        # Update context if specified
        if step.get("save_to_context"):
            context[step["save_to_context"]] = step_result
    
    return {
        "workflow": "custom",
        "steps_executed": len(results),
        "results": results,
        "context": context,
        "timestamp": datetime.utcnow().isoformat()
    }

# Proxy endpoint for direct service access
# Note: Removed API key validation - proxy endpoints use JWT auth from frontend
@app.api_route("/proxy/{service}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_service(service: str, path: str, request: Request):
    """Proxy requests to specific services"""
    if service not in SERVICE_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Service {service} not found")
    
    # Get request body for POST/PUT/PATCH
    body = None
    if request.method in ["POST", "PUT", "PATCH"]:
        try:
            body = await request.json()
        except:
            # Try to read raw body if JSON parsing fails
            body_bytes = await request.body()
            if body_bytes:
                try:
                    body = json.loads(body_bytes)
                except:
                    logger.warning(f"Could not parse request body for {service}/{path}")
    
    # Log the proxy request for debugging. Scrub sensitive fields first —
    # this used to log raw bodies including plaintext passwords for every
    # /proxy/auth/email-login call. Anyone with kubectl logs access to
    # default could read every login attempt's password (P1 security
    # bug surfaced 2026-06-03).
    logger.info(
        f"Proxy request: {request.method} {service}/{path} with body: {_scrub_sensitive(body)}"
    )
    
    # Extract user context from Authorization header if present
    user_context = {}
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        import jwt
        token = auth_header.split(" ")[1]
        try:
            decoded = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
            )
            user_context = {
                "X-User-Id": decoded.get("sub", ""),
                "X-User-Email": decoded.get("email", ""),
                "X-User-Name": decoded.get("name", ""),
                "X-Org-Id": decoded.get("org_id", "")
            }
            logger.info(f"Proxy auth: user_id={decoded.get('sub', '')}")
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token has expired")
        except (jwt.InvalidSignatureError, jwt.DecodeError):
            raise HTTPException(status_code=401, detail="Invalid token")

    # Forward X-Admin-Key for admin endpoints (e.g. onboard, upgrade)
    admin_key = request.headers.get("X-Admin-Key")
    if admin_key:
        user_context["X-Admin-Key"] = admin_key

    # Forward the request with user context
    result = await call_service(
        service,
        f"/{path}",
        request.method,
        body,
        dict(request.query_params),
        user_context
    )
    
    return result

# Metrics endpoint
@app.get("/metrics", dependencies=[Depends(validate_api_key)])
async def metrics():
    """Service metrics"""
    return {
        "service": SERVICE_NAME,
        "uptime": "healthy",
        "redis": "connected" if REDIS_ENABLED else "disabled",
        "services_registered": len(SERVICE_REGISTRY),
        "timestamp": datetime.utcnow().isoformat()
    }

# =============================================================================
# NOTE: Legacy service proxy routes (OpenMD, auth, red-team, knowledge-graph,
# molecular-worker, zinc-integration, molmim-optimizer, molecular-intelligence,
# negative-data, autodock-gpu direct) removed April 2026.
# All service access now routes through MCP tools or the /proxy/{service}/{path} endpoint.
# =============================================================================


# =============================================================================
# PDB Cache Management Endpoints
# =============================================================================

@app.get("/novomcp/pdb-cache/stats")
async def get_pdb_cache_stats():
    """Get PDB cache statistics - no auth required for monitoring"""
    from utils.pdb_cache import get_cache_stats
    return get_cache_stats()


@app.post("/novomcp/pdb-cache/clear", dependencies=[Depends(validate_api_key)])
async def clear_pdb_cache_endpoint():
    """Clear all PDB cache entries"""
    from utils.pdb_cache import clear_pdb_cache
    result = clear_pdb_cache()
    return {
        "status": "success",
        "message": f"PDB cache cleared: {result['cleared']} entries removed",
        **result
    }


@app.delete("/novomcp/pdb-cache/{pdb_id}", dependencies=[Depends(validate_api_key)])
async def invalidate_pdb_endpoint(pdb_id: str):
    """Invalidate a specific PDB cache entry"""
    from utils.pdb_cache import invalidate_pdb
    result = invalidate_pdb(pdb_id)

    if result["removed"]:
        return {
            "status": "success",
            "message": f"PDB cache entry '{pdb_id}' invalidated",
            **result
        }
    else:
        return {
            "status": "not_found",
            "message": f"PDB cache entry '{pdb_id}' not found",
            **result
        }


@app.get("/novomcp/pdb-cache/{pdb_id}/metadata")
async def get_pdb_metadata_endpoint(pdb_id: str):
    """Get metadata for a cached PDB entry - no auth for monitoring"""
    from utils.pdb_cache import get_structure_metadata
    metadata = get_structure_metadata(pdb_id)

    if metadata:
        return {
            "status": "found",
            "pdb_id": pdb_id.upper(),
            "metadata": metadata
        }
    else:
        raise HTTPException(
            status_code=404,
            detail=f"PDB entry '{pdb_id}' not found in cache"
        )


# Lazy singleton for the hosted upload page's URL-resolution endpoint.
_FILE_INTEL_CLIENT = None


def _get_file_intel_client():
    global _FILE_INTEL_CLIENT
    if _FILE_INTEL_CLIENT is None:
        from core.file_intelligence import FileIntelligenceClient
        _FILE_INTEL_CLIENT = FileIntelligenceClient()
    return _FILE_INTEL_CLIENT


@app.get("/files/{file_id}/upload-url", tags=["Files"])
async def get_file_upload_url(file_id: str):
    """Public: re-sign a fresh presigned PUT URL for a still-pending upload,
    keyed on the (unguessable) file_id.

    Powers the hosted upload page (app.novomcp.com/upload/{file_id}). The page
    fetches this on load instead of carrying the presigned URL in the link
    fragment — a ~700-char SigV4 URL that LLMs truncate when surfacing the link,
    which broke uploads after the Azure→AWS move. Same exposure as the old
    self-contained link (an unguessable id grants upload to one pending key);
    only returns a URL while status == pending_upload.
    """
    try:
        result = await _get_file_intel_client().regenerate_upload_url(file_id)
    except Exception as e:
        logger.error("upload-url regen failed for %s: %s", file_id, e)
        raise HTTPException(status_code=500, detail="Failed to generate upload URL")
    if not result:
        raise HTTPException(status_code=404, detail="Upload link not found or already used")
    return result


async def proxy_to_service(service_name: str, method: str, path: str, data: Optional[Dict[str, Any]] = None):
    """Generic service proxy function with enhanced logging"""
    # Log the incoming proxy request
    logger.info(f"Proxying request to {service_name}: {method} {path}")
    
    service_config = service_config_manager.get_service_config(service_name)
    
    if not service_config:
        logger.error(f"Service {service_name} config not found")
        raise HTTPException(status_code=503, detail=f"Service {service_name} not configured")
    
    if not service_config.get("url"):
        logger.error(f"Service {service_name} URL not configured: {service_config}")
        raise HTTPException(status_code=503, detail=f"Service {service_name} URL not available")
    
    service_url = service_config["url"]
    api_key = service_config.get("api_key")
    
    # Log the target URL for debugging
    full_url = f"{service_url}{path}"
    logger.info(f"Proxying to: {full_url}")
    
    headers = {
        "Content-Type": "application/json"
    }
    
    if api_key:
        headers["X-API-Key"] = api_key
        logger.debug(f"Added API key for {service_name}")
    
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=settings.httpx_verify) as client:
            if method == "GET":
                response = await client.get(full_url, headers=headers)
            elif method == "POST":
                response = await client.post(full_url, headers=headers, json=data)
            elif method == "PUT":
                response = await client.put(full_url, headers=headers, json=data)
            elif method == "DELETE":
                response = await client.delete(full_url, headers=headers)
            else:
                raise HTTPException(status_code=405, detail="Method not allowed")
            
            logger.info(f"Service {service_name} responded with status: {response.status_code}")
            
            if response.status_code >= 400:
                logger.error(f"Service {service_name} error response: {response.text}")
                raise HTTPException(status_code=response.status_code, detail=response.text)
            
            return response.json()
            
    except httpx.TimeoutException:
        logger.error(f"Service {service_name} timeout after 30s")
        raise HTTPException(status_code=504, detail=f"Service {service_name} timeout")
    except httpx.RequestError as e:
        logger.error(f"Service {service_name} request error: {e}")
        raise HTTPException(status_code=503, detail=f"Service {service_name} unavailable")
    except HTTPException:
        raise  # Re-raise HTTP exceptions as-is
    except Exception as e:
        logger.error(f"Service {service_name} proxy error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal proxy error")

# Log registered routes at startup
@app.on_event("startup")
async def log_routes():
    """Log all registered routes for debugging"""
    routes = []
    for route in app.routes:
        if hasattr(route, 'path'):
            routes.append(route.path)
    
    logger.info("=" * 60)
    logger.info("NovoMCP Service Started")
    logger.info(f"Registered {len(routes)} routes:")

    # Log NovoMCP routes
    mcp_routes = [r for r in routes if '/mcp' in r]
    if mcp_routes:
        logger.info(f"NovoMCP routes registered ({len(mcp_routes)} routes):")
        for route in sorted(mcp_routes):
            logger.info(f"  - {route}")

    # Log service configurations
    logger.info("Service configurations loaded:")
    for service_name in ["addie-models", "chem-props", "faves-compliance", "lead-optimization", "autodock-gpu", "gromacs-md"]:
        config = service_config_manager.get_service_config(service_name)
        if config and config.get("url"):
            logger.info(f"  {service_name}: {config['url']} (API Key: {'Present' if config.get('api_key') else 'Missing'})")
        else:
            logger.warning(f"  {service_name}: NOT CONFIGURED")
    
    logger.info("=" * 60)

# Main entry point
if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
        log_level=os.getenv("LOG_LEVEL", "info").lower()
    )
# Deployment triggered at Tue Aug 27 13:50:56 PDT 2025
# Fixed ALB association Tue Aug 27 13:52:49 PDT 2025
