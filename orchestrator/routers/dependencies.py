"""
Shared FastAPI Dependencies
Phase 1, Week 3-4, Task 3.6: Created for router refactoring

Provides reusable FastAPI dependencies for:
- Authentication and API key validation
- Request validation
- Common extractors
- Shared utilities
"""

import os
import logging
from typing import Optional, Dict, Any
from fastapi import Header, HTTPException, Depends, Request
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)


# =============================================================================
# Authentication Dependencies
# =============================================================================

async def get_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key")
) -> str:
    """
    Validate API key from request header.
    Used for service-to-service authentication.

    Usage:
        @router.post("/endpoint")
        async def endpoint(api_key: str = Depends(get_api_key)):
            ...
    """
    require_api_key = os.getenv("REQUIRE_API_KEY", "true").lower() != "false"

    if not x_api_key:
        if not require_api_key:
            logger.warning("API key not provided - bypassed via REQUIRE_API_KEY=false")
            return "dev-key"
        raise HTTPException(
            status_code=401,
            detail="API key required"
        )

    # Validate API key against configured keys
    valid_keys = {
        os.getenv("NOVOMCP_API_KEY"),
        os.getenv("INTERNAL_SERVICE_KEY"),
    }
    valid_keys.discard(None)

    if x_api_key not in valid_keys:
        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )

    return x_api_key


async def get_optional_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key")
) -> Optional[str]:
    """
    Get API key if provided, but don't require it.
    Used for public endpoints that have optional authentication.
    """
    return x_api_key


async def get_service_key(
    x_service_key: Optional[str] = Header(None, alias="x-service-key")
) -> str:
    """
    Validate internal service key for service-to-service calls.
    More permissive than API key for internal microservice communication.
    """
    if not x_service_key:
        return "internal"

    # Accept internal service key from env var
    internal_key = os.getenv("INTERNAL_SERVICE_KEY")
    if internal_key and x_service_key == internal_key:
        return x_service_key

    # Log suspicious attempts
    logger.warning(f"Invalid service key attempted: {x_service_key[:10]}...")

    return "invalid"


# =============================================================================
# Request Validation Dependencies
# =============================================================================

async def validate_campaign_id(campaign_id: str) -> str:
    """
    Validate campaign ID format.
    Ensures campaign_id is a valid UUID or string ID.

    Usage:
        @router.get("/campaign/{campaign_id}")
        async def get_campaign(
            campaign_id: str = Depends(validate_campaign_id)
        ):
            ...
    """
    if not campaign_id:
        raise HTTPException(status_code=400, detail="Campaign ID is required")

    # Allow UUID format or string format
    try:
        # Try parsing as UUID
        uuid.UUID(campaign_id)
    except ValueError:
        # Not a UUID, check if it's a valid string ID
        if not campaign_id.replace("-", "").replace("_", "").isalnum():
            raise HTTPException(
                status_code=400,
                detail="Invalid campaign ID format"
            )

    return campaign_id


async def validate_request_body(request: Request) -> Dict[str, Any]:
    """
    Validate and parse JSON request body.
    Ensures content-type is application/json and body is valid JSON.

    Usage:
        @router.post("/endpoint")
        async def endpoint(
            body: Dict[str, Any] = Depends(validate_request_body)
        ):
            ...
    """
    content_type = request.headers.get("content-type", "")

    if "application/json" not in content_type:
        raise HTTPException(
            status_code=415,
            detail="Content-Type must be application/json"
        )

    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid JSON body: {str(e)}"
        )

    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail="Request body must be a JSON object"
        )

    return body


# =============================================================================
# Pagination Dependencies
# =============================================================================

async def get_pagination(
    offset: int = 0,
    limit: int = 20
) -> Dict[str, int]:
    """
    Extract pagination parameters with validation.

    Usage:
        @router.get("/items")
        async def list_items(
            pagination: Dict[str, int] = Depends(get_pagination)
        ):
            offset = pagination["offset"]
            limit = pagination["limit"]
    """
    if offset < 0:
        raise HTTPException(
            status_code=400,
            detail="Offset must be >= 0"
        )

    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=400,
            detail="Limit must be between 1 and 100"
        )

    return {
        "offset": offset,
        "limit": limit
    }


# =============================================================================
# Common Extractors
# =============================================================================

async def get_user_id(
    x_user_id: Optional[str] = Header(None, alias="X-User-ID")
) -> Optional[str]:
    """
    Extract user ID from request header.
    Returns None if not provided.
    """
    return x_user_id


async def get_tenant_id(
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID")
) -> Optional[str]:
    """
    Extract tenant ID from request header.
    Important for multi-tenancy isolation.
    """
    return x_tenant_id


async def get_correlation_id(
    x_correlation_id: Optional[str] = Header(None, alias="X-Correlation-ID")
) -> str:
    """
    Extract or generate correlation ID for request tracing.
    Auto-generates if not provided.
    """
    if x_correlation_id:
        return x_correlation_id

    # Generate new correlation ID
    correlation_id = f"req_{uuid.uuid4()}"
    logger.debug(f"Generated correlation ID: {correlation_id}")

    return correlation_id


# =============================================================================
# Request Context
# =============================================================================

class RequestContext:
    """
    Container for common request context.
    Combines multiple dependencies into a single object.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        correlation_id: Optional[str] = None
    ):
        self.api_key = api_key
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.correlation_id = correlation_id or f"req_{uuid.uuid4()}"
        self.timestamp = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        """Convert context to dictionary for logging/serialization"""
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp.isoformat()
        }


async def get_request_context(
    api_key: Optional[str] = Depends(get_optional_api_key),
    user_id: Optional[str] = Depends(get_user_id),
    tenant_id: Optional[str] = Depends(get_tenant_id),
    correlation_id: str = Depends(get_correlation_id)
) -> RequestContext:
    """
    Get comprehensive request context.
    Combines multiple headers into a single context object.

    Usage:
        @router.post("/endpoint")
        async def endpoint(
            context: RequestContext = Depends(get_request_context)
        ):
            logger.info(f"Request from user: {context.user_id}")
            logger.info(f"Correlation ID: {context.correlation_id}")
    """
    return RequestContext(
        api_key=api_key,
        user_id=user_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id
    )


# =============================================================================
# Validators
# =============================================================================

def validate_uuid(value: str) -> bool:
    """
    Validate if string is a valid UUID.

    Usage:
        if not validate_uuid(campaign_id):
            raise HTTPException(400, "Invalid UUID")
    """
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def validate_email(email: str) -> bool:
    """
    Basic email validation.

    Usage:
        if not validate_email(user_email):
            raise HTTPException(400, "Invalid email")
    """
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


# =============================================================================
# Rate Limiting
# =============================================================================

from core.rate_limiter import rate_limit  # noqa: E402 — re-export for router use


# =============================================================================
# Example Usage in Router
# =============================================================================

"""
Example of using these dependencies in a router:

```python
from fastapi import APIRouter, Depends
from routers.dependencies import (
    get_api_key,
    validate_campaign_id,
    get_request_context,
    RequestContext
)

router = APIRouter(prefix="/campaigns")

@router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: str = Depends(validate_campaign_id),
    api_key: str = Depends(get_api_key),
    context: RequestContext = Depends(get_request_context)
):
    logger.info(f"Fetching campaign {campaign_id}", extra=context.to_dict())
    # Your endpoint logic here
    return {"campaign_id": campaign_id}

@router.post("/create")
async def create_campaign(
    body: Dict[str, Any] = Depends(validate_request_body),
    context: RequestContext = Depends(get_request_context)
):
    logger.info("Creating campaign", extra=context.to_dict())
    # Your endpoint logic here
    return {"success": True}
```
"""
