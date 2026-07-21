"""
Secure Service Configuration for NovoMCP
Loads service URLs and API keys from AWS Secrets Manager or environment variables
"""

import os
import json
import logging
from typing import Dict, Any, Optional
from ai.secrets_manager import get_secrets_manager
from config import settings

logger = logging.getLogger(__name__)


def _resolve_service_url(service_name: str, env_var: str, default_url: str) -> str:
    """Prefer Service Connect discovery URL when available, otherwise fall back.
    Also supports common alias env vars for molecular-intelligence.
    """
    env_override = os.getenv(env_var)
    # Backward/alias support for molecular-intelligence URL env vars
    if not env_override and service_name == "molecular-intelligence":
        env_override = (
            os.getenv("MOLECULAR_INTELLIGENCE_URL")
            or os.getenv("MOLECULAR_INTEL_URL")
            or os.getenv("MOL_INTEL_URL")
        )
    # Compliance / molecule-index URL env vars. FAVES is one valid backend
    # among several (Kaggle-hosted, self-hosted, user's own service).
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

class ServiceConfig:
    """Securely manage service configurations"""
    
    def __init__(self):
        self.secrets_manager = get_secrets_manager()
        self._service_cache = {}
        
    def get_service_config(self, service_name: str) -> Dict[str, Any]:
        """
        Get configuration for a specific service
        
        Priority:
        1. AWS Secrets Manager
        2. Environment variables
        3. Default values (for development only)
        """
        # Check cache first
        if service_name in self._service_cache:
            return self._service_cache[service_name]
        
        config = {}
        
        # Try AWS Secrets Manager first
        if self.secrets_manager.available:
            try:
                # Try to get from combined service configs secret
                all_configs = self.secrets_manager.get_secret("novomcp/service-configs")
                if all_configs:
                    all_configs_data = json.loads(all_configs)
                    if service_name in all_configs_data:
                        config = all_configs_data[service_name]
                        logger.info(f"Loaded {service_name} config from Secrets Manager")
                
                # If not found, try individual service secret
                if not config:
                    service_secret = self.secrets_manager.get_service_credentials(service_name)
                    if service_secret:
                        config = service_secret
                        logger.info(f"Loaded {service_name} config from individual secret")
            except Exception as e:
                logger.warning(f"Failed to load {service_name} from Secrets Manager: {e}")
        
        # Fall back to environment variables
        if not config:
            env_mapping = {
                "chem-props": {
                    "url": _resolve_service_url(
                        "chem-props",
                        "CHEM_PROPS_URL",
                        "",
                    ),
                    "api_key": os.getenv("CHEM_PROPS_API_KEY") or "",
                    "type": "https",
                },
                "drugsynthmc": {
                    "url": _resolve_service_url(
                        "drugsynthmc",
                        "DRUGSYNTHMC_URL",
                        "",
                    ),
                    "api_key": os.getenv("DRUGSYNTHMC_API_KEY"),
                    "type": "https",
                },
                "faves-compliance": {
                    "url": _resolve_service_url(
                        "faves-compliance",
                        "NOVOMCP_COMPLIANCE_URL",
                        "",
                    ),
                    "api_key": os.getenv("NOVOMCP_COMPLIANCE_API_KEY") or "not-required",
                    "type": "https",
                },
                "openmd": {
                    "url": _resolve_service_url(
                        "openmd",
                        "OPENMD_URL",
                        "",
                    ),
                    "api_key": os.getenv("OPENMD_API_KEY"),
                    "type": "https",
                },
                "attachment-processor": {
                    "url": _resolve_service_url(
                        "attachment-processor",
                        "ATTACHMENT_PROCESSOR_URL",
                        "",
                    ),
                    "api_key": os.getenv("ATTACHMENT_PROCESSOR_API_KEY"),
                    "type": "https",
                },
                # AUTH SERVICE - Internal ALB
                "auth": {
                    "url": _resolve_service_url(
                        "auth",
                        "AUTH_SERVICE_URL",
                        "",
                    ),
                    "api_key": os.getenv("AUTH_SERVICE_API_KEY") or os.getenv("AUTH_API_KEY"),
                    "type": "https",
                },
                # CONSOLIDATED: db-manager now routes to dashboard-aggregator (unified service)
                # Azure Container Apps internal URL
                "db-manager": {
                    "url": _resolve_service_url(
                        "db-manager",
                        "DB_MANAGER_URL",
                        "",
                    ),
                    "api_key": os.getenv("DB_MANAGER_API_KEY") or os.getenv("DASHBOARD_AGGREGATOR_API_KEY"),
                    "type": "https",
                },
                "dashboard-aggregator": {
                    "url": _resolve_service_url(
                        "dashboard-aggregator",
                        "DASHBOARD_AGGREGATOR_URL",
                        "",
                    ),
                    "api_key": os.getenv("DASHBOARD_AGGREGATOR_API_KEY"),
                    "type": "https",
                },
                "molecular-worker": {
                    "url": _resolve_service_url(
                        "molecular-worker",
                        "MOLECULAR_WORKER_URL",
                        "",
                    ),
                    "api_key": os.getenv("MOLECULAR_WORKER_API_KEY"),
                    "type": "https",
                },
                # CONSOLIDATED: dbschema-manager now routes to dashboard-aggregator (unified service)
                # Azure Container Apps internal URL
                "dbschema-manager": {
                    "url": _resolve_service_url(
                        "dbschema-manager",
                        "DBSCHEMA_MANAGER_URL",
                        "",
                    ),
                    "api_key": os.getenv("DBSCHEMA_MANAGER_API_KEY") or os.getenv("DASHBOARD_AGGREGATOR_API_KEY"),
                    "type": "https",
                },
                "knowledge-graph": {
                    "url": _resolve_service_url(
                        "knowledge-graph",
                        "KNOWLEDGE_GRAPH_URL",
                        "",
                    ),
                    "api_key": os.getenv("KNOWLEDGE_GRAPH_API_KEY"),
                    "type": "https",
                },
                "molecular-intelligence": {
                    "url": _resolve_service_url(
                        "molecular-intelligence",
                        "MOL_INTEL_URL",
                        "",
                    ),
                    # Accept both short and long env var names for API key
                    "api_key": os.getenv("MOL_INTEL_API_KEY") or os.getenv("MOLECULAR_INTELLIGENCE_API_KEY"),
                    "type": "https",
                },
                "negative-data": {
                    "url": _resolve_service_url(
                        "negative-data",
                        "NEGATIVE_DATA_URL",
                        "",
                    ),
                    "api_key": os.getenv("NEGATIVE_DATA_API_KEY"),
                    "type": "https",
                },
                "zinc-integration": {
                    "url": _resolve_service_url(
                        "zinc-integration",
                        "ZINC_INTEGRATION_URL",
                        "",
                    ),
                    "api_key": os.getenv("ZINC_INTEGRATION_API_KEY"),
                    "type": "https",
                },
                "tdc-integration": {
                    "url": _resolve_service_url(
                        "tdc-integration",
                        "TDC_INTEGRATION_URL",
                        "",
                    ),
                    "api_key": os.getenv("TDC_INTEGRATION_API_KEY"),
                    "type": "https",
                },
                "red-team": {
                    "url": _resolve_service_url(
                        "red-team",
                        "RED_TEAM_URL",
                        "",
                    ),
                    "api_key": os.getenv("RED_TEAM_API_KEY"),
                    "type": "https",
                },
                "prompt-library": {
                    "url": _resolve_service_url(
                        "prompt-library",
                        "PROMPT_LIBRARY_URL",
                        "",
                    ),
                    "api_key": os.getenv("PROMPT_LIBRARY_API_KEY"),
                    "type": "https",
                },
                "molmim-optimizer": {
                    "url": _resolve_service_url(
                        "molmim-optimizer",
                        "MOLMIM_OPTIMIZER_URL",
                        "",
                    ),
                    "api_key": os.getenv("MOLMIM_OPTIMIZER_API_KEY") or "",
                    "type": "https",
                },
                "autodock-gpu": {
                    "url": _resolve_service_url(
                        "autodock-gpu",
                        "AUTODOCK_GPU_URL",
                        "",
                    ),
                    "api_key": os.getenv("AUTODOCK_GPU_API_KEY") or "",
                    "type": "https",
                },
                # Quantum computing service using Azure Quantum (migrated from AWS Braket)
                "novo-quantum": {
                    "url": _resolve_service_url(
                        "novo-quantum",
                        "NOVO_QUANTUM_URL",
                        "",
                    ),
                    "api_key": os.getenv("NOVO_QUANTUM_API_KEY") or "",
                    "type": "https",
                },
                "lead-optimization": {
                    "url": _resolve_service_url(
                        "lead-optimization",
                        "LEAD_OPT_URL",
                        "",
                    ),
                    "api_key": os.getenv("LEAD_OPT_API_KEY"),
                    "type": "https",
                },
                "gromacs-processor": {
                    "url": _resolve_service_url(
                        "gromacs-processor",
                        "GROMACS_PROCESSOR_URL",
                        "",
                    ),
                    "api_key": os.getenv("GROMACS_PROCESSOR_API_KEY") or "",
                    "type": "https",
                },
                # NovoMD - Molecular Dynamics Service (Azure Container Apps)
                "novomd": {
                    "url": _resolve_service_url(
                        "novomd",
                        "NOVOMD_URL",
                        "",
                    ),
                    "api_key": os.getenv("NOVOMD_API_KEY"),
                    "type": "https",
                },
                # OpenFold3 - NVIDIA protein structure prediction (Azure Container Apps)
                "openfold3": {
                    "url": _resolve_service_url(
                        "openfold3",
                        "OPENFOLD3_URL",
                        "",
                    ),
                    "api_key": os.getenv("OPENFOLD3_API_KEY") or "",
                    "type": "https",
                },
            }
            
            if service_name in env_mapping:
                config = env_mapping[service_name]
                # Missing API keys are expected in local mode (no downstream
                # service to talk to). Log at debug so local runs stay quiet;
                # deployments that actually need the service will see the
                # first-call failure with a clear message.
                if not config.get("api_key"):
                    logger.debug(f"No API key configured for {service_name}")

        if config and config.get("url") and config.get("type"):
            url = config["url"]
            if isinstance(url, str):
                if url.startswith("http://"):
                    config["type"] = "http"
                elif url.startswith("https://"):
                    config["type"] = "https"

        # Cache the configuration
        if config:
            self._service_cache[service_name] = config

        return config
    
    def get_all_services(self) -> Dict[str, Dict[str, Any]]:
        """Get configuration for all services"""
        # All services now on internal ALBs
        services = [
            "chem-props", "drugsynthmc", "faves-compliance",
            "openmd", "attachment-processor", "auth", "db-manager",
            "dashboard-aggregator", "molecular-worker", "dbschema-manager",
            "knowledge-graph", "molecular-intelligence", "negative-data",
            "zinc-integration", "tdc-integration", "red-team",
            "prompt-library", "molmim-optimizer", "autodock-gpu",
            "novo-quantum", "lead-optimization", "gromacs-processor", "novomd",
            "openfold3"
        ]
        
        registry = {}
        for service in services:
            config = self.get_service_config(service)
            if config and config.get("url"):
                registry[service] = config
        
        return registry
    
    def clear_cache(self):
        """Clear the service configuration cache"""
        self._service_cache = {}
        if self.secrets_manager:
            self.secrets_manager.clear_cache()
        logger.info("Service configuration cache cleared")

# Global instance
_service_config = None

def get_service_config() -> ServiceConfig:
    """Get or create global ServiceConfig instance"""
    global _service_config
    if _service_config is None:
        _service_config = ServiceConfig()
    return _service_config
