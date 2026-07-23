"""
AWS Secrets Manager integration for NovoMCP
Fetches Azure OpenAI credentials securely from AWS Secrets Manager
"""

import json
import logging
import os
from typing import Dict, Optional, Any
from functools import lru_cache
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

class SecretsManager:
    """AWS Secrets Manager client for secure credential retrieval"""
    
    def __init__(self, region_name: str = None):
        """Initialize Secrets Manager client.

        AWS Secrets Manager lookups are OPT-IN via NOVO_USE_AWS_SECRETS=true.
        When disabled (the default), all get_secret() calls short-circuit
        to None and the caller falls back to environment variables. This
        keeps local runs quiet and fast.
        """
        self.region_name = region_name or os.getenv("AWS_REGION", "us-east-1")
        self.environment = os.getenv("ENVIRONMENT", "development")
        self.secret_prefix = os.getenv(
            "NOVO_SECRET_PREFIX",
            f"novomcp/{self.environment}",
        )

        self.enabled = os.getenv("NOVO_USE_AWS_SECRETS", "false").lower() == "true"
        if not self.enabled:
            self.client = None
            self.available = False
            logger.debug("AWS Secrets Manager disabled (set NOVO_USE_AWS_SECRETS=true to enable)")
            return

        try:
            self.client = boto3.client(
                'secretsmanager',
                region_name=self.region_name
            )
            self.available = True
            logger.info(f"AWS Secrets Manager client initialized for region: {self.region_name}")
        except Exception as e:
            logger.error(f"Failed to initialize AWS Secrets Manager client: {e}")
            self.client = None
            self.available = False
    
    @lru_cache(maxsize=10)
    def get_secret(self, secret_name: str) -> Optional[str]:
        """
        Retrieve a secret value from AWS Secrets Manager
        
        Args:
            secret_name: Name of the secret to retrieve
            
        Returns:
            Secret value as string, or None if not found
        """
        if not self.available:
            return None
        
        try:
            response = self.client.get_secret_value(SecretId=secret_name)
            
            # Secrets can be stored as either string or binary
            if 'SecretString' in response:
                return response['SecretString']
            else:
                # Binary secret (not used for our text-based secrets)
                import base64
                return base64.b64decode(response['SecretBinary']).decode('utf-8')
                
        except ClientError as e:
            error_code = e.response['Error']['Code']
            
            if error_code == 'ResourceNotFoundException':
                logger.warning(f"Secret {secret_name} not found in AWS Secrets Manager")
            elif error_code == 'InvalidRequestException':
                logger.error(f"Invalid request for secret {secret_name}: {e}")
            elif error_code == 'InvalidParameterException':
                logger.error(f"Invalid parameter for secret {secret_name}: {e}")
            elif error_code == 'DecryptionFailure':
                logger.error(f"Cannot decrypt secret {secret_name}: {e}")
            elif error_code == 'InternalServiceError':
                logger.error(f"Internal service error retrieving secret {secret_name}: {e}")
            else:
                logger.error(f"Unexpected error retrieving secret {secret_name}: {e}")
            
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
        """Clear the secrets cache"""
        self.get_secret.cache_clear()
        logger.info("Secrets cache cleared")

# Global instance
_secrets_manager = None

def get_secrets_manager() -> SecretsManager:
    """Get or create global SecretsManager instance"""
    global _secrets_manager
    if _secrets_manager is None:
        _secrets_manager = SecretsManager()
    return _secrets_manager