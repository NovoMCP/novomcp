"""
Secret retrieval shim for NovoMCP (OSS engine).

The public engine loads all credentials from environment variables. This
module keeps the historical SecretStore interface (`get_secrets_manager()`,
`.available`, `.get_secret()`) so existing callers import and run unchanged,
but every secret lookup short-circuits to None and the caller falls back to
its environment-variable path.
"""

import json
import logging
import os
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)

class SecretStore:
    """No-op secret retriever. Always unavailable; callers fall back to env vars."""

    def __init__(self, region_name: str = None):
        """Initialize the no-op secret retriever.

        The OSS engine has no external secret store. `available` is always
        False and `get_secret()` always returns None, so callers use their
        environment-variable fallbacks.
        """
        self.region_name = region_name or os.getenv("AWS_REGION", "us-east-1")
        self.environment = os.getenv("ENVIRONMENT", "development")
        self.secret_prefix = os.getenv(
            "NOVO_SECRET_PREFIX",
            f"novomcp/{self.environment}",
        )
        self.client = None
        self.available = False
        logger.debug("Secret store disabled; credentials load from environment variables")

    def get_secret(self, secret_name: str) -> Optional[str]:
        """Always None in the OSS engine; callers fall back to environment variables."""
        return None

    def get_azure_openai_config(self) -> Dict[str, Any]:
        """
        Get Azure OpenAI configuration from AWS Secrets Manager
        
        Returns:
            Dictionary with Azure OpenAI configuration
        """
        config = {}
        
        # Try to get combined secret first
        combined_secret_name = f"{self.secret_prefix}/novomcp/azure-openai"
        combined_secret = self.get_secret(combined_secret_name)
        
        if combined_secret:
            try:
                # Parse JSON secret
                secret_data = json.loads(combined_secret)
                config = {
                    "api_key": secret_data.get("AZURE_OPENAI_API_KEY"),
                    "endpoint": secret_data.get("AZURE_OPENAI_ENDPOINT"),
                    "deployment": secret_data.get("AZURE_OPENAI_DEPLOYMENT"),
                    "api_version": secret_data.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
                    "enable_ai_orchestration": secret_data.get("ENABLE_AI_ORCHESTRATION", "true").lower() == "true",
                    "enable_project_enrichment": secret_data.get("ENABLE_PROJECT_ENRICHMENT", "true").lower() == "true",
                    "enable_intent_recognition": secret_data.get("ENABLE_INTENT_RECOGNITION", "true").lower() == "true"
                }
                logger.info("Successfully loaded Azure OpenAI config from AWS Secrets Manager")
                return config
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse combined secret JSON: {e}")
        
        # Fall back to individual secrets
        api_key_secret = self.get_secret(f"{self.secret_prefix}/azure-openai/api-key")
        endpoint_secret = self.get_secret(f"{self.secret_prefix}/azure-openai/endpoint")
        deployment_secret = self.get_secret(f"{self.secret_prefix}/azure-openai/deployment")
        
        if api_key_secret:
            config["api_key"] = api_key_secret
        if endpoint_secret:
            config["endpoint"] = endpoint_secret
        if deployment_secret:
            config["deployment"] = deployment_secret
        
        # Get additional config if available
        config_secret = self.get_secret(f"{self.secret_prefix}/azure-openai/config")
        if config_secret:
            try:
                additional_config = json.loads(config_secret)
                config.update({
                    "api_version": additional_config.get("api_version", "2024-02-01"),
                    "temperature": additional_config.get("temperature", 0.3),
                    "max_tokens": additional_config.get("max_tokens", 1500),
                    "enable_ai_orchestration": additional_config.get("enable_ai_orchestration", True),
                    "enable_project_enrichment": additional_config.get("enable_project_enrichment", True),
                    "enable_intent_recognition": additional_config.get("enable_intent_recognition", True)
                })
            except json.JSONDecodeError:
                logger.warning("Failed to parse additional config JSON")
        
        # Fall back to environment variables if secrets not found
        if not config.get("api_key"):
            config["api_key"] = os.getenv("AZURE_OPENAI_API_KEY")
        if not config.get("endpoint"):
            config["endpoint"] = os.getenv("AZURE_OPENAI_ENDPOINT", "https://eastus2.api.cognitive.microsoft.com/")
        if not config.get("deployment"):
            config["deployment"] = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5-chat")
        
        if config.get("api_key"):
            logger.info("Azure OpenAI configuration loaded")
        else:
            logger.debug("Azure OpenAI API key not configured (AI features disabled)")
        
        return config
    
    def get_service_credentials(self, service_name: str) -> Dict[str, Any]:
        """
        Get credentials for a specific service
        
        Args:
            service_name: Name of the service
            
        Returns:
            Dictionary with service credentials
        """
        secret_name = f"{self.secret_prefix}/services/{service_name}"
        secret_value = self.get_secret(secret_name)
        
        if secret_value:
            try:
                return json.loads(secret_value)
            except json.JSONDecodeError:
                # If not JSON, return as simple string value
                return {"value": secret_value}
        
        return {}
    
    def clear_cache(self):
        """No-op; the OSS engine caches no secrets."""
        return None

# Global instance
_secrets_manager = None

def get_secrets_manager() -> SecretStore:
    """Get or create global SecretStore instance"""
    global _secrets_manager
    if _secrets_manager is None:
        _secrets_manager = SecretStore()
    return _secrets_manager