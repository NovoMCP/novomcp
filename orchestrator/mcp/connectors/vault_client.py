"""
AWS Secrets Manager client for Connection Registry credential management.

Pod authenticates via IRSA (or standard boto3 credential chain). Secret
IDs follow the configured prefix so IAM grants can be shared with sibling
services.
"""

import json
import logging
import os
import time
from typing import Dict, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_credential_cache: Dict[str, tuple] = {}  # secret_name -> (data, expiry_timestamp)
CACHE_TTL_SECONDS = 60

# Match the IAM scope of novomcp-bridge / managed backend
SECRET_PREFIX = os.environ.get("BRIDGE_SECRET_PREFIX", "novomcp/bridge-conn/")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


class ConnectionVaultClient:
    """AWS Secrets Manager client for connector credential CRUD operations."""

    def __init__(self, region: Optional[str] = None):
        self.region = region or AWS_REGION
        self.client = boto3.client("secretsmanager", region_name=self.region)
        logger.info(f"ConnectionVaultClient (Secrets Manager) initialized for region {self.region}")

    @staticmethod
    def _secret_name(org_id: str, connection_id: str) -> str:
        """Generate Secrets Manager secret name from org + connection IDs."""
        safe_org = org_id.replace("_", "-").replace(".", "-")
        safe_conn = connection_id.replace("_", "-").replace(".", "-")
        return f"{SECRET_PREFIX}{safe_org}-{safe_conn}"

    async def store_credentials(self, org_id: str, connection_id: str, credentials: Dict) -> str:
        secret_name = self._secret_name(org_id, connection_id)
        secret_value = json.dumps(credentials)

        try:
            try:
                self.client.create_secret(
                    Name=secret_name,
                    SecretString=secret_value,
                    Description=f"novomcp connector credentials org={org_id} conn={connection_id}",
                    Tags=[
                        {"Key": "org_id", "Value": org_id},
                        {"Key": "connection_id", "Value": connection_id},
                        {"Key": "service", "Value": "novomcp"},
                        {"Key": "Project", "Value": "novomcp"},
                    ],
                )
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") == "ResourceExistsException":
                    self.client.put_secret_value(SecretId=secret_name, SecretString=secret_value)
                else:
                    raise
            logger.info(f"Stored credentials for connection {connection_id} (org: {org_id})")
            _credential_cache.pop(secret_name, None)
            return secret_name
        except ClientError as e:
            logger.error(f"Failed to store credentials: {e}")
            raise

    async def get_credentials(self, vault_secret_name: str) -> Optional[Dict]:
        if vault_secret_name in _credential_cache:
            data, expiry = _credential_cache[vault_secret_name]
            if time.time() < expiry:
                return data

        try:
            resp = self.client.get_secret_value(SecretId=vault_secret_name)
            credentials = json.loads(resp["SecretString"])
            _credential_cache[vault_secret_name] = (
                credentials,
                time.time() + CACHE_TTL_SECONDS,
            )
            return credentials
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code == "ResourceNotFoundException":
                logger.warning(f"Secret not found: {vault_secret_name}")
                return None
            logger.error(f"Failed to retrieve credentials: {e}")
            raise

    async def delete_credentials(self, vault_secret_name: str) -> bool:
        try:
            # ForceDeleteWithoutRecovery matches the legacy purge_deleted_secret behavior
            self.client.delete_secret(
                SecretId=vault_secret_name,
                ForceDeleteWithoutRecovery=True,
            )
            _credential_cache.pop(vault_secret_name, None)
            logger.info(f"Deleted credentials: {vault_secret_name}")
            return True
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code == "ResourceNotFoundException":
                logger.warning(f"Secret not found for deletion: {vault_secret_name}")
                return False
            logger.error(f"Failed to delete credentials: {e}")
            raise

    async def rotate_credentials(self, vault_secret_name: str, new_credentials: Dict) -> bool:
        try:
            self.client.put_secret_value(
                SecretId=vault_secret_name,
                SecretString=json.dumps(new_credentials),
            )
            _credential_cache.pop(vault_secret_name, None)
            logger.info(f"Rotated credentials: {vault_secret_name}")
            return True
        except ClientError as e:
            logger.error(f"Failed to rotate credentials: {e}")
            raise

    def clear_cache(self):
        _credential_cache.clear()


_vault_client: Optional[ConnectionVaultClient] = None


def get_vault_client() -> ConnectionVaultClient:
    global _vault_client
    if _vault_client is None:
        _vault_client = ConnectionVaultClient()
    return _vault_client
