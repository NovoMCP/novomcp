"""
NovoMCP Authentication

Manages API keys for users and organizations.
Supports tiered access (Free, Pro, Team, Enterprise).

Authentication is backed by managed backend SQL database.
"""

import logging
import hashlib
import secrets
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from enum import Enum
import json
import httpx

logger = logging.getLogger(__name__)

# managed backend URL for key validation
DASHBOARD_AGGREGATOR_URL = os.getenv(
    "DASHBOARD_AGGREGATOR_URL",
    ""
)

# Dashboard JWT — verified locally here (we hold the secret; managed backend
# does not). Used by validate_jwt for the browser Studio auth path.
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
# Internal service key for the trusted call to managed backend /mcp/validate-jwt.
DASHBOARD_AGGREGATOR_API_KEY = os.getenv("DASHBOARD_AGGREGATOR_API_KEY", "")


class UserTier(str, Enum):
    """User subscription tiers."""
    FREE = "free"
    CORE = "core"
    PRO = "pro"
    TEAM = "team"
    ENTERPRISE = "enterprise"


@dataclass
class MCPUser:
    """Represents an authenticated MCP user."""
    user_id: str  # This is key_id from mcp_api_keys
    email: str
    tier: UserTier
    org_id: Optional[str] = None
    org_name: Optional[str] = None
    name: Optional[str] = None
    role: str = "member"
    _daily_limit: int = 100  # From org
    created_at: datetime = field(default_factory=datetime.utcnow)

    # Trial enforcement
    credits_available: Optional[float] = None
    trial_expires_at: Optional[datetime] = None

    # Usage tracking (kept in Redis for performance)
    daily_queries: int = 0
    monthly_queries: int = 0
    last_query_date: Optional[datetime] = None

    # Limits - use org's daily_limit if set, otherwise tier-based
    @property
    def daily_limit(self) -> int:
        if self._daily_limit > 0:
            return self._daily_limit
        limits = {
            UserTier.FREE: 10,
            UserTier.CORE: 1000,
            UserTier.PRO: 1000,
            UserTier.TEAM: 10000,
            UserTier.ENTERPRISE: 1000000
        }
        return limits.get(self.tier, 10)

    @property
    def batch_limit(self) -> int:
        limits = {
            UserTier.FREE: 0,  # No batch access
            UserTier.CORE: 1000,
            UserTier.PRO: 1000,
            UserTier.TEAM: 10000,
            UserTier.ENTERPRISE: 100000
        }
        return limits.get(self.tier, 0)

    @property
    def is_trial_blocked(self) -> bool:
        """True if org cannot execute tools due to depleted credits or expired trial."""
        # Paid tiers with overage are never blocked
        if self.tier in (UserTier.ENTERPRISE, UserTier.TEAM):
            return False
        # Core: blocked only when credits exhausted (no trial expiry, no overage)
        if self.tier == UserTier.CORE:
            return self.credits_available is not None and self.credits_available <= 0
        # Free: blocked when credits exhausted OR trial expired
        if self.credits_available is not None and self.credits_available <= 0:
            return True
        if self.trial_expires_at is not None and datetime.utcnow() > self.trial_expires_at:
            return True
        return False

    @property
    def trial_block_reason(self) -> Optional[str]:
        """Reason for block: credits_exhausted, trial_expired, or None."""
        if self.tier in (UserTier.ENTERPRISE, UserTier.TEAM):
            return None
        if self.tier == UserTier.CORE:
            if self.credits_available is not None and self.credits_available <= 0:
                return "credits_exhausted"
            return None
        if self.credits_available is not None and self.credits_available <= 0:
            return "credits_exhausted"
        if self.trial_expires_at is not None and datetime.utcnow() > self.trial_expires_at:
            return "trial_expired"
        return None

    def can_query(self, count: int = 1) -> bool:
        """Check if user can make more queries."""
        return self.daily_queries + count <= self.daily_limit

    def record_query(self, count: int = 1):
        """Record a query for usage tracking."""
        today = datetime.utcnow().date()
        if self.last_query_date is None or self.last_query_date.date() != today:
            # Reset daily counter
            self.daily_queries = 0

        self.daily_queries += count
        self.monthly_queries += count
        self.last_query_date = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "user_id": self.user_id,
            "email": self.email,
            "tier": self.tier.value,
            "org_id": self.org_id,
            "org_name": self.org_name,
            "name": self.name,
            "role": self.role,
            "daily_limit": self._daily_limit,
            "created_at": self.created_at.isoformat(),
            "daily_queries": self.daily_queries,
            "monthly_queries": self.monthly_queries,
            "last_query_date": self.last_query_date.isoformat() if self.last_query_date else None,
            "credits_available": self.credits_available,
            "trial_expires_at": self.trial_expires_at.isoformat() if self.trial_expires_at else None
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MCPUser":
        """Create from dictionary."""
        return cls(
            user_id=data["user_id"],
            email=data["email"],
            tier=UserTier(data["tier"]),
            org_id=data.get("org_id"),
            org_name=data.get("org_name"),
            name=data.get("name"),
            role=data.get("role", "member"),
            _daily_limit=data.get("daily_limit", 100),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.utcnow(),
            daily_queries=data.get("daily_queries", 0),
            monthly_queries=data.get("monthly_queries", 0),
            last_query_date=datetime.fromisoformat(data["last_query_date"]) if data.get("last_query_date") else None,
            credits_available=data.get("credits_available"),
            trial_expires_at=datetime.fromisoformat(data["trial_expires_at"]) if data.get("trial_expires_at") else None
        )


async def validate_via_spine(
    spine, credential: Optional[str], mode: str = "core"
) -> Optional["MCPUser"]:
    """Validate a credential through the pluggable spine, returning an MCPUser.

    Local runs resolve every credential to the unlimited local user via
    LocalAuthGate; a custom (hosted) AuthGate may wrap a richer MCPUser in the
    spine User's ``extra['mcp_user']`` slot. Otherwise we synthesize an
    enterprise-tier user so downstream code that expects tier/credit/limit
    fields keeps working. Shared by the MCP router, the root handler, and the
    OAuth flow so all three authenticate through the same seam.
    """
    if spine is None:
        return None
    spine_user = await spine.auth.validate(credential, mode=mode)
    if not spine_user:
        return None
    wrapped = spine_user.extra.get("mcp_user") if spine_user.extra else None
    if wrapped is not None:
        return wrapped
    return MCPUser(
        user_id=spine_user.user_id,
        email=spine_user.email or "local@localhost",
        tier=UserTier.ENTERPRISE,
        org_id=spine_user.org_id or "local",
        org_name=spine_user.org_name or "local",
        _daily_limit=1_000_000,
    )
