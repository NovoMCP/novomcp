"""
Configuration management for NovoMCP v2.0
Centralized settings with environment variable support
"""
import os
from typing import List, Optional
from pydantic_settings import BaseSettings
from urllib.parse import urlparse

class Settings(BaseSettings):
    """Application settings with environment variable support"""
    
    # Service Configuration
    SERVICE_NAME: str = "novomcp"
    PORT: int = 8018
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    # Environment
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "production")
    AWS_REGION: str = os.getenv("AWS_REGION", "us-east-1")
    
    # CORS Configuration
    ALLOWED_ORIGINS: List[str] = [
        "https://app.novomcp.com",
        "https://novomcp.com",
        "https://app.novomcp.com",
        "http://localhost:3000",
        "http://localhost:3001"
    ]
    
    # Service Discovery and Routing
    SERVICE_BASE_URL: str = os.getenv("SERVICE_BASE_URL", "http://localhost")
    ALB_BASE_URL: str = os.getenv("ALB_BASE_URL", "https://api.novomcp.com")
    
    # Routing Configuration (Dual-Mode)
    USE_ALB_ROUTING: bool = os.getenv("USE_ALB_ROUTING", "false").lower() == "true"
    SERVICE_CONNECT_ENABLED: bool = os.getenv("SERVICE_CONNECT_ENABLED", "true").lower() == "true"
    # Cloud Map (AWS Service Connect) DNS routing is disabled by default on Azure
    CLOUD_MAP_ENABLED: bool = os.getenv("CLOUD_MAP_ENABLED", "false").lower() == "true"
    CALLER_TYPE: str = os.getenv("CALLER_TYPE", "hybrid")  # NovoMCP is hybrid (external + internal)
    SERVICE_DISCOVERY_NAMESPACE: str = os.getenv("SERVICE_DISCOVERY_NAMESPACE", "novomcp.local")
    
    # Feature Flags
    USE_MOLECULAR_INTELLIGENCE: bool = os.getenv("USE_MOLECULAR_INTELLIGENCE", "true").lower() == "true"
    ENABLE_ASYNC_JOBS: bool = os.getenv("ENABLE_ASYNC_JOBS", "true").lower() == "true"
    ENABLE_CACHING: bool = os.getenv("ENABLE_CACHING", "true").lower() == "true"
    
    # Azure OpenAI Configuration - MUST use GitHub Secrets or AWS Secrets Manager
    AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")  # Required - set via GitHub Secrets
    AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")  # Required - set via GitHub Secrets
    AZURE_OPENAI_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
    
    # AI Feature Flags
    ENABLE_AI_ORCHESTRATION: bool = os.getenv("ENABLE_AI_ORCHESTRATION", "true").lower() == "true"
    ENABLE_PROJECT_ENRICHMENT: bool = os.getenv("ENABLE_PROJECT_ENRICHMENT", "true").lower() == "true"
    ENABLE_INTENT_RECOGNITION: bool = os.getenv("ENABLE_INTENT_RECOGNITION", "true").lower() == "true"
    
    # Circuit Breaker Settings
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = int(os.getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "10"))
    CIRCUIT_BREAKER_RECOVERY_TIMEOUT: int = int(os.getenv("CIRCUIT_BREAKER_RECOVERY_TIMEOUT", "60"))
    CIRCUIT_BREAKER_EXPECTED_EXCEPTION: str = "TimeoutError"
    
    # Timeout Settings now use centralized configuration
    # These are kept for backward compatibility but will be overridden by timeout_config
    DEFAULT_TIMEOUT: int = int(os.getenv("DEFAULT_TIMEOUT", "30"))
    GENERATION_TIMEOUT: int = int(os.getenv("GENERATION_TIMEOUT", "300"))
    ORCHESTRATION_TIMEOUT: int = int(os.getenv("ORCHESTRATION_TIMEOUT", "360"))
    
    # Redis Configuration
    REDIS_URL: Optional[str] = os.getenv("REDIS_URL")
    
    @property
    def REDIS_HOST(self) -> str:
        if self.REDIS_URL:
            parsed = urlparse(self.REDIS_URL)
            return parsed.hostname or "localhost"
        return os.getenv("REDIS_HOST", "localhost")
    
    @property
    def REDIS_PORT(self) -> int:
        if self.REDIS_URL:
            parsed = urlparse(self.REDIS_URL)
            return parsed.port or 6379
        return int(os.getenv("REDIS_PORT", "6379"))
    
    @property
    def REDIS_DB(self) -> int:
        if self.REDIS_URL:
            parsed = urlparse(self.REDIS_URL)
            if parsed.path and parsed.path != "/":
                return int(parsed.path.lstrip("/"))
            return 0
        return int(os.getenv("REDIS_DB", "0"))
    
    @property
    def REDIS_PASSWORD(self) -> Optional[str]:
        if self.REDIS_URL:
            parsed = urlparse(self.REDIS_URL)
            return parsed.password
        return os.getenv("REDIS_PASSWORD")
    
    CACHE_TTL: int = int(os.getenv("CACHE_TTL", "3600"))  # 1 hour
    
    # AWS Configuration
    AWS_ACCESS_KEY_ID: Optional[str] = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY: Optional[str] = os.getenv("AWS_SECRET_ACCESS_KEY")
    
    # AWS SQS Configuration
    SQS_QUEUE_PREFIX: str = os.getenv("SQS_QUEUE_PREFIX", "novomcp")
    SQS_MOLECULAR_QUEUE: str = os.getenv("SQS_MOLECULAR_QUEUE", "molecular-jobs")
    SQS_ANALYSIS_QUEUE: str = os.getenv("SQS_ANALYSIS_QUEUE", "analysis-jobs")
    SQS_OPTIMIZATION_QUEUE: str = os.getenv("SQS_OPTIMIZATION_QUEUE", "optimization-jobs")
    SQS_DLQ_SUFFIX: str = "-dlq"
    SQS_MAX_RETRIES: int = int(os.getenv("SQS_MAX_RETRIES", "3"))
    SQS_VISIBILITY_TIMEOUT: int = int(os.getenv("SQS_VISIBILITY_TIMEOUT", "300"))  # 5 minutes
    SQS_WAIT_TIME_SECONDS: int = int(os.getenv("SQS_WAIT_TIME_SECONDS", "20"))  # Long polling
    
    # AWS SNS Configuration
    SNS_TOPIC_PREFIX: str = os.getenv("SNS_TOPIC_PREFIX", "novomcp")
    SNS_JOB_COMPLETION_TOPIC: str = os.getenv("SNS_JOB_COMPLETION_TOPIC", "job-completion")
    SNS_JOB_FAILURE_TOPIC: str = os.getenv("SNS_JOB_FAILURE_TOPIC", "job-failure")
    SNS_PROGRESS_UPDATE_TOPIC: str = os.getenv("SNS_PROGRESS_UPDATE_TOPIC", "job-progress")
    
    # Async Job Configuration
    MAX_CONCURRENT_JOBS: int = int(os.getenv("MAX_CONCURRENT_JOBS", "10"))
    JOB_TIMEOUT: int = int(os.getenv("JOB_TIMEOUT", "300"))  # 5 minutes
    JOB_CLEANUP_INTERVAL: int = int(os.getenv("JOB_CLEANUP_INTERVAL", "3600"))  # 1 hour
    
    # Redis Enhanced Configuration
    REDIS_KEY_PREFIX: str = os.getenv("REDIS_KEY_PREFIX", "novomcp")
    REDIS_STATUS_TTL: int = int(os.getenv("REDIS_STATUS_TTL", "3600"))  # 1 hour
    REDIS_RESULT_TTL: int = int(os.getenv("REDIS_RESULT_TTL", "86400"))  # 24 hours
    REDIS_CACHE_TTL: int = int(os.getenv("REDIS_CACHE_TTL", "300"))  # 5 minutes
    REDIS_ENABLE_PUBSUB: bool = os.getenv("REDIS_ENABLE_PUBSUB", "true").lower() == "true"
    
    # Database Configuration
    DATABASE_URL: Optional[str] = os.getenv("DATABASE_URL")
    
    # Authentication
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # TLS Configuration
    VERIFY_TLS: bool = os.getenv("VERIFY_TLS", "true").lower() != "false"
    TLS_CA_BUNDLE: Optional[str] = os.getenv("TLS_CA_BUNDLE")

    @property
    def httpx_verify(self):
        """Return the appropriate verify parameter for httpx clients."""
        if not self.VERIFY_TLS:
            return False
        return self.TLS_CA_BUNDLE or True
    
    # Downstream service API keys (loaded from env or .env)
    CAMPAIGN_MANAGER_API_KEY: Optional[str] = os.getenv("CAMPAIGN_MANAGER_API_KEY")
    DASHBOARD_AGGREGATOR_API_KEY: Optional[str] = os.getenv("DASHBOARD_AGGREGATOR_API_KEY")
    
    # Per-service env var overrides. Set any of these to point the engine at
    # a specific deployed backend. Used by OSS installs where each optional
    # service ships separately (Modal, Runpod, self-hosted k8s, ...).
    # If set → the URL is honored. If unset in local mode → the service is
    # treated as unwired and _call_service returns a clean 503 so tools
    # degrade gracefully with the "not configured" branch instead of DNS
    # errors on a docker-compose-only hostname.
    _SERVICE_ENV_OVERRIDES = {
        "faves-compliance": "NOVOMCP_COMPLIANCE_URL",
        "chem-props": "CHEM_PROPS_URL",
        "addie-models": "ADDIE_MODELS_URL",
        "molmim-optimizer": "MOLMIM_OPTIMIZER_URL",
        "openfold3": "OPENFOLD3_URL",
        "autodock-gpu": "AUTODOCK_GPU_URL",
        "gromacs-md": "GROMACS_MD_URL",
        "novomcp-qm": "NOVOMCP_QM_URL",
        "novomcp-nnp": "NOVOMCP_NNP_URL",
    }

    # Service URLs (for adapter connections)
    # Dynamic service URL generation based on routing mode
    def _get_service_url(self, service_name: str, port: int) -> str:
        """Generate service URL based on routing configuration.

        Precedence:
          1. Per-service env override (NOVOMCP_COMPLIANCE_URL, etc.) — hosted
             deploys point at their real backend; OSS users point at whatever
             they wired (Modal endpoint, self-hosted k8s ingress, ...).
          2. CLOUD_MAP_ENABLED → AWS Cloud Map DNS.
          3. USE_ALB_ROUTING → ALB routing.
          4. Local/docker-compose default → http://<service>:<port>.

        For services in _SERVICE_ENV_OVERRIDES, the docker-compose default is
        suppressed when the env var is unset — otherwise the engine would try
        to resolve a bare hostname (e.g. faves-compliance) that only exists
        inside a compose network, causing DNS errors in bare-metal local mode.
        """
        # 1) Per-service env override always wins.
        env_key = self._SERVICE_ENV_OVERRIDES.get(service_name)
        if env_key:
            env_url = os.getenv(env_key, "").strip()
            if env_url:
                return env_url

        # NovoMCP uses internal routing for service-to-service communication
        # Use AWS Cloud Map DNS only when explicitly enabled
        if self.CLOUD_MAP_ENABLED:
            # ECS Service Connect creates DNS entries with full namespace domain
            # Format: service-name.namespace:port (as seen in Route 53)

            # Special cases where we need to use -sc suffix for DNS_HTTP support
            if service_name == "auth":
                # auth (HTTP only) vs auth-sc (DNS_HTTP) - use auth-sc for DNS
                return f"http://auth-sc.{self.SERVICE_DISCOVERY_NAMESPACE}:{port}"
            elif service_name == "db-manager":
                # db-manager not in Cloud Map, but db-manager-sc has DNS_HTTP
                return f"http://db-manager-sc.{self.SERVICE_DISCOVERY_NAMESPACE}:{port}"
            elif service_name == "project-data":
                # project-data has DNS_HTTP in Cloud Map (project-data-sc is HTTP only without DNS)
                return f"http://project-data.{self.SERVICE_DISCOVERY_NAMESPACE}:{port}"

            # All other services use standard naming (they have DNS_HTTP support)
            return f"http://{service_name}.{self.SERVICE_DISCOVERY_NAMESPACE}:{port}"
        elif not self.USE_ALB_ROUTING:
            # Local / docker-compose. If the service has an env override key
            # but no value was set, suppress the docker hostname default —
            # otherwise we DNS-error on a name that only resolves inside a
            # compose network. Return "" so _call_service returns a clean 503
            # and the tool degrades to its "not configured" branch.
            if env_key:
                return ""
            return f"http://{service_name}:{port}"
        else:
            # ALB routing (only for external requests)
            return f"{self.ALB_BASE_URL}/{service_name}"
    
    # Service registry - URLs will be generated dynamically
    @property
    def SERVICES(self) -> dict:
        """Service registry with intelligent routing"""
        # All services use DNS+API Cloud Map entries (no -sc suffix)
        # Service Connect should use the same names for discovery
        return {
            "auth": {"url": self._get_service_url("auth", 8006), "port": 8006},
            "project-data": {"url": self._get_service_url("project-data", 8025), "port": 8025},
            "rbac": {"url": self._get_service_url("rbac", 8092), "port": 8092},
            "molecular-intelligence": {"url": self._get_service_url("molecular-intelligence", 8029), "port": 8029},
            "molmim-optimizer": {"url": self._get_service_url("molmim-optimizer", 8014), "port": 8014},
            "chem-props": {"url": self._get_service_url("chem-props", 8003), "port": 8003},
            "openmd": {"url": self._get_service_url("openmd", 8002), "port": 8002},
            "tdc-integration": {"url": self._get_service_url("tdc-integration", 8011), "port": 8011},
            "faves-compliance": {"url": self._get_service_url("faves-compliance", 8005), "port": 8005},
            "red-team": {"url": self._get_service_url("red-team", 8009), "port": 8009},
            "negative-data": {"url": self._get_service_url("negative-data", 8012), "port": 8012},
            "db-manager": {"url": self._get_service_url("db-manager", 8005), "port": 8005},
            "monitoring": {"url": self._get_service_url("monitoring", 8016), "port": 8016},
            "attachment-processor": {"url": self._get_service_url("attachment-processor", 8018), "port": 8018},
            "knowledge-graph": {"url": self._get_service_url("knowledge-graph", 8019), "port": 8019},
            "prompt-library": {"url": self._get_service_url("prompt-library", 8020), "port": 8020},
            "bias-mitigation": {"url": self._get_service_url("bias-mitigation", 8022), "port": 8022},
            "bias-monitoring": {"url": self._get_service_url("bias-monitoring", 8023), "port": 8023},
            "dashboard-aggregator": {"url": self._get_service_url("dashboard-aggregator", 8024), "port": 8024},
            "zinc-integration": {"url": self._get_service_url("zinc-integration", 8026), "port": 8026},
            "molecular-worker": {"url": self._get_service_url("molecular-worker", 8080), "port": 8080}
        }
    
    # Monitoring
    ENABLE_METRICS: bool = os.getenv("ENABLE_METRICS", "true").lower() == "true"
    METRICS_PORT: int = int(os.getenv("METRICS_PORT", "9090"))
    
    class Config:
        env_file = ".env"
        case_sensitive = True

# Create global settings instance
settings = Settings()
