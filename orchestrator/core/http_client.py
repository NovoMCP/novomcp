"""
Centralized httpx client factory with TLS verification support.
"""

import httpx
from config import settings


def create_async_client(**kwargs) -> httpx.AsyncClient:
    """Create an httpx.AsyncClient with TLS verification from settings."""
    kwargs.setdefault("verify", settings.httpx_verify)
    return httpx.AsyncClient(**kwargs)
