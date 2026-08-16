"""Connection Registry credential vault (OSS engine).

In the public engine there is no external credential store, so this vault is
a no-op. The interface (`get_vault_client()`, `store_credentials`,
`get_credentials`, `delete_credentials`, `rotate_credentials`) is preserved so
callers import and run unchanged; reads return None and writes degrade to a
logged no-op. A managed backend supplies a real vault out-of-repo.
"""

import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Secret-name prefix for the managed vault (supplied out-of-repo).
SECRET_PREFIX = os.environ.get("CONNECTOR_SECRET_PREFIX", "novomcp/connectors/")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


class ConnectionVaultClient:
    """No-op connector credential vault. Reads return None; writes are ignored."""

    def __init__(self, region: Optional[str] = None):
        self.region = region or AWS_REGION
        self.client = None
        logger.info("ConnectionVaultClient running in no-op mode (no external secret store)")

    @staticmethod
    def _secret_name(org_id: str, connection_id: str) -> str:
        """Generate a stable secret name from org + connection IDs."""
        safe_org = org_id.replace("_", "-").replace(".", "-")
        safe_conn = connection_id.replace("_", "-").replace(".", "-")
        return f"{SECRET_PREFIX}{safe_org}-{safe_conn}"

    async def store_credentials(self, org_id: str, connection_id: str, credentials: Dict) -> str:
        secret_name = self._secret_name(org_id, connection_id)
        logger.warning(
            "Credential vault is not configured in the OSS engine; "
            "store_credentials is a no-op (org=%s conn=%s)", org_id, connection_id
        )
        return secret_name

    async def get_credentials(self, vault_secret_name: str) -> Optional[Dict]:
        logger.debug("Credential vault not configured; get_credentials returns None (%s)", vault_secret_name)
        return None

    async def delete_credentials(self, vault_secret_name: str) -> bool:
        logger.debug("Credential vault not configured; delete_credentials is a no-op (%s)", vault_secret_name)
        return False

    async def rotate_credentials(self, vault_secret_name: str, new_credentials: Dict) -> bool:
        logger.debug("Credential vault not configured; rotate_credentials is a no-op (%s)", vault_secret_name)
        return False

    def clear_cache(self):
        return None


_vault_client: Optional[ConnectionVaultClient] = None


def get_vault_client() -> ConnectionVaultClient:
    global _vault_client
    if _vault_client is None:
        _vault_client = ConnectionVaultClient()
    return _vault_client
