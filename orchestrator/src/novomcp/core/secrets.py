"""
Fail-fast secret loading for production deployments.
Prevents startup with missing required secrets.
"""

import os
import logging

logger = logging.getLogger(__name__)


def require_secret(env_var: str, description: str = "") -> str:
    """Fail-fast if required secret is missing."""
    value = os.getenv(env_var)
    if not value:
        raise RuntimeError(f"Required secret {env_var} not set. {description}")
    return value
