"""
NovoMCP Authentication

Manages API keys for users and organizations.
Supports tiered access (Free, Pro, Team, Enterprise).

Authentication is backed by dashboard-aggregator SQL database.
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

# Dashboard-aggregator URL for key validation
DASHBOARD_AGGREGATOR_URL = os.getenv(
    "DASHBOARD_AGGREGATOR_URL",
    ""
)

# Dashboard JWT — verified locally here (we hold the secret; dashboard-aggregator
# does not). Used by validate_jwt for the browser Studio auth path.
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
# Internal service key for the trusted call to dashboard-aggregator /mcp/validate-jwt.
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


class MCPAuthManager:
    """
    Manages MCP authentication and API keys.

    Authentication is backed by dashboard-aggregator SQL database.
    Redis is used for caching validated users and usage tracking.
    """

    USER_CACHE_PREFIX = "novomcp:user:"
    USER_CACHE_STALE_PREFIX = "novomcp:user:stale:"
    CACHE_TTL = 300          # 5 minutes — fresh window
    STALE_CACHE_TTL = 3600   # 1 hour — serve-stale-on-error window
    VALIDATE_TIMEOUT_SECONDS = 15.0  # bumped from 5s; serve-stale kicks in on timeout

    def __init__(self, redis_client=None, db_client=None):
        """
        Initialize auth manager.

        Args:
            redis_client: Redis client for caching validated users
            db_client: Database client (unused - using HTTP to dashboard-aggregator)
        """
        self.redis = redis_client
        self.db = db_client
        self._http_client = None

        # In-memory cache fallback
        self._user_cache: Dict[str, tuple[MCPUser, datetime]] = {}

    def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        return self._http_client

    async def validate_api_key(self, api_key: str, server_mode: str = "core") -> Optional[MCPUser]:
        """
        Validate an API key via dashboard-aggregator.

        Flow:
        1. Check Redis cache for recently validated key
        2. If not cached, call dashboard-aggregator /mcp/validate-key (or /mcp/validate-compute-key for compute mode)
        3. Cache successful validation for 5 minutes

        Args:
            api_key: The API key (nmcp_ for core, ncmcp_ for compute)
            server_mode: "core" or "compute" — determines which validation endpoint to call

        Returns:
            MCPUser if valid, None otherwise
        """
        if not api_key:
            return None

        # Accept both key prefixes
        if not api_key.startswith("nmcp_") and not api_key.startswith("ncmcp_"):
            return None

        # Check cache first
        cache_key = hashlib.sha256(api_key.encode()).hexdigest()[:16]
        cached_user = await self._get_cached_user(cache_key)
        if cached_user:
            logger.debug(f"Using cached user for key ...{api_key[-8:]}")
            return cached_user

        # Validate via dashboard-aggregator
        # Use compute endpoint for ncmcp_ keys or when server is in compute mode
        is_compute = api_key.startswith("ncmcp_") or server_mode == "compute"
        validate_endpoint = "/mcp/validate-compute-key" if is_compute else "/mcp/validate-key"

        try:
            client = self._get_http_client()
            response = await client.post(
                f"{DASHBOARD_AGGREGATOR_URL}{validate_endpoint}",
                json={"api_key": api_key},
                timeout=self.VALIDATE_TIMEOUT_SECONDS,
            )

            if response.status_code >= 500:
                # Upstream transient failure — try serve-stale before giving up
                stale = await self._get_stale_cached_user(cache_key)
                if stale:
                    logger.warning(
                        f"dashboard-aggregator {response.status_code}; serving stale auth for "
                        f"...{api_key[-8:]} (last-validated within {self.STALE_CACHE_TTL}s)"
                    )
                    return stale
                logger.warning(f"Key validation failed: HTTP {response.status_code}")
                return None

            if response.status_code != 200:
                # 4xx = client-side / key problem; do NOT serve stale
                logger.warning(f"Key validation failed: HTTP {response.status_code}")
                return None

            data = response.json()

            if not data.get("valid"):
                logger.debug(f"Invalid key: ...{api_key[-8:]}")
                return None

            # Create MCPUser from response
            user = MCPUser(
                user_id=data["key_id"],
                email=data["email"],
                tier=UserTier(data["tier"]),
                org_id=data["org_id"],
                org_name=data.get("org_name"),
                name=data.get("name"),
                role=data.get("role", "member"),
                _daily_limit=data.get("daily_limit", 100),
                credits_available=data.get("credits_available"),
                trial_expires_at=datetime.fromisoformat(data["trial_expires_at"]) if data.get("trial_expires_at") else None
            )

            # Cache the validated user (fresh + stale)
            await self._cache_user(cache_key, user)

            logger.info(f"Validated key for {user.email} (org={user.org_name}, tier={user.tier.value})")
            return user

        except httpx.TimeoutException:
            # Transient upstream slowness — serve stale if we have a recent entry.
            # Security note: stale entries require a prior successful validation within
            # STALE_CACHE_TTL (1h). Keys never seen before still return None (401).
            stale = await self._get_stale_cached_user(cache_key)
            if stale:
                logger.warning(
                    f"dashboard-aggregator timeout after {self.VALIDATE_TIMEOUT_SECONDS}s; "
                    f"serving stale auth for {stale.email} (org={stale.org_name})"
                )
                return stale
            logger.error("Timeout validating API key via dashboard-aggregator (no stale cache available)")
            return None
        except Exception as e:
            # Any other network error — same serve-stale fallback
            stale = await self._get_stale_cached_user(cache_key)
            if stale:
                logger.warning(
                    f"dashboard-aggregator error ({type(e).__name__}); serving stale auth for "
                    f"...{api_key[-8:]}"
                )
                return stale
            logger.error(f"Error validating API key: {e}")
            return None

    async def validate_jwt(self, token: str) -> Optional[MCPUser]:
        """
        Validate a dashboard JWT (the access token the Next dashboard issues) and
        resolve it to an MCPUser. Auth path for the browser **Studio** SPA, which
        is same-origin with the dashboard and carries only that JWT — no API key.

        We verify the JWT signature/expiry **locally** (we hold JWT_SECRET_KEY;
        dashboard-aggregator does not), then resolve org/user entitlements from
        dashboard-aggregator `POST /mcp/validate-jwt` with the verified claims. It
        returns the same payload as `/mcp/validate-key` (tier + period-aware
        credits + role), so the browser path bills the user's org with per-user
        attribution — never the platform key. Caching + serve-stale mirror
        validate_api_key.

        Returns MCPUser if valid, None otherwise.
        """
        if not token:
            return None

        cache_key = hashlib.sha256(token.encode()).hexdigest()[:16]
        cached_user = await self._get_cached_user(cache_key)
        if cached_user:
            logger.debug("Using cached user for JWT")
            return cached_user

        # 1) Verify + decode locally. Bad signature / expiry → reject fast, no upstream call.
        import jwt as _jwt
        try:
            claims = _jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        except Exception as exc:
            logger.debug(f"JWT decode rejected: {type(exc).__name__}")
            return None

        user_id = claims.get("sub") or claims.get("user_id")
        if not user_id:
            return None
        org_id = claims.get("org_id")

        # 2) Resolve entitlements (tier + period-aware credits + role) by claims.
        try:
            client = self._get_http_client()
            response = await client.post(
                f"{DASHBOARD_AGGREGATOR_URL}/mcp/validate-jwt",
                json={
                    "user_id": user_id,
                    "org_id": org_id,
                    "email": claims.get("email"),
                    "name": claims.get("name"),
                },
                headers={"X-API-Key": DASHBOARD_AGGREGATOR_API_KEY},
                timeout=self.VALIDATE_TIMEOUT_SECONDS,
            )

            if response.status_code >= 500:
                stale = await self._get_stale_cached_user(cache_key)
                if stale:
                    logger.warning(
                        f"dashboard-aggregator {response.status_code} on validate-jwt; "
                        f"serving stale auth for {stale.email}"
                    )
                    return stale
                logger.warning(f"JWT entitlement lookup failed: HTTP {response.status_code}")
                return None

            if response.status_code != 200:
                logger.warning(f"JWT entitlement lookup failed: HTTP {response.status_code}")
                return None

            data = response.json()
            if not data.get("valid"):
                logger.debug("JWT identity has no active org/profile")
                return None

            user = MCPUser(
                user_id=data.get("user_id") or data.get("key_id") or user_id,
                email=data.get("email") or claims.get("email") or "",
                tier=UserTier(data["tier"]),
                org_id=data["org_id"],
                org_name=data.get("org_name"),
                name=data.get("name") or claims.get("name"),
                role=data.get("role", "member"),
                _daily_limit=data.get("daily_limit", 100),
                credits_available=data.get("credits_available"),
                trial_expires_at=datetime.fromisoformat(data["trial_expires_at"]) if data.get("trial_expires_at") else None,
            )

            await self._cache_user(cache_key, user)
            logger.info(f"Validated JWT for {user.email} (org={user.org_name}, tier={user.tier.value})")
            return user

        except httpx.TimeoutException:
            stale = await self._get_stale_cached_user(cache_key)
            if stale:
                logger.warning(f"validate-jwt timeout; serving stale auth for {stale.email}")
                return stale
            logger.error("Timeout resolving JWT entitlements (no stale cache)")
            return None
        except Exception as e:
            stale = await self._get_stale_cached_user(cache_key)
            if stale:
                logger.warning(f"validate-jwt error ({type(e).__name__}); serving stale auth")
                return stale
            logger.error(f"Error resolving JWT entitlements: {e}")
            return None

    async def _get_cached_user(self, cache_key: str) -> Optional[MCPUser]:
        """Get cached user from Redis or memory."""
        if self.redis:
            try:
                data = self.redis.get(f"{self.USER_CACHE_PREFIX}{cache_key}")
                if data:
                    return MCPUser.from_dict(json.loads(data))
            except Exception as e:
                logger.warning(f"Redis cache read error: {e}")

        # Memory fallback
        if cache_key in self._user_cache:
            user, cached_at = self._user_cache[cache_key]
            if datetime.utcnow() - cached_at < timedelta(seconds=self.CACHE_TTL):
                return user
            else:
                del self._user_cache[cache_key]

        return None

    async def _cache_user(self, cache_key: str, user: MCPUser):
        """Cache validated user in Redis or memory.

        Writes two keys: the fresh-window entry (5-min TTL, standard cache) and
        the stale-fallback entry (1-hour TTL, used only when dashboard-aggregator
        is unreachable). The stale key prevents transient upstream failures from
        triggering false 401s for users whose keys are known-good.
        """
        if self.redis:
            payload = json.dumps(user.to_dict())
            try:
                self.redis.set(f"{self.USER_CACHE_PREFIX}{cache_key}", payload, ex=self.CACHE_TTL)
            except Exception as e:
                logger.warning(f"Redis fresh cache write error: {e}")
            try:
                self.redis.set(
                    f"{self.USER_CACHE_STALE_PREFIX}{cache_key}", payload, ex=self.STALE_CACHE_TTL
                )
            except Exception as e:
                logger.warning(f"Redis stale cache write error: {e}")

        # Always keep in memory too
        self._user_cache[cache_key] = (user, datetime.utcnow())

    async def _get_stale_cached_user(self, cache_key: str) -> Optional[MCPUser]:
        """Get a cached user for serve-stale-on-error fallback.

        Unlike _get_cached_user, this ignores the 5-min freshness window and
        returns entries up to STALE_CACHE_TTL (1 hour) old. Only called when
        dashboard-aggregator is genuinely unreachable. Returns None if no prior
        successful validation exists within the stale window.
        """
        if self.redis:
            try:
                data = self.redis.get(f"{self.USER_CACHE_STALE_PREFIX}{cache_key}")
                if data:
                    return MCPUser.from_dict(json.loads(data))
            except Exception as e:
                logger.warning(f"Redis stale cache read error: {e}")

        # Memory fallback — allow up to STALE_CACHE_TTL age
        if cache_key in self._user_cache:
            user, cached_at = self._user_cache[cache_key]
            if datetime.utcnow() - cached_at < timedelta(seconds=self.STALE_CACHE_TTL):
                return user

        return None

    async def record_usage(self, user: MCPUser, queries: int = 1) -> bool:
        """
        Record usage for a user in Redis cache.

        Usage tracking is kept in Redis for performance.
        Daily counters reset automatically.

        Returns:
            True if within limits, False if quota exceeded
        """
        if not user.can_query(queries):
            return False

        user.record_query(queries)

        # Update cached user with new usage
        cache_key = user.user_id[:16]  # Use first 16 chars of user_id as cache key
        await self._cache_user(cache_key, user)

        return True

    async def get_usage_stats(self, user: MCPUser) -> Dict[str, Any]:
        """Get usage statistics for a user."""
        return {
            "user_id": user.user_id,
            "email": user.email,
            "org_id": user.org_id,
            "org_name": user.org_name,
            "tier": user.tier.value,
            "daily_queries": user.daily_queries,
            "daily_limit": user.daily_limit,
            "daily_remaining": max(0, user.daily_limit - user.daily_queries),
            "monthly_queries": user.monthly_queries,
            "batch_limit": user.batch_limit
        }

    async def close(self):
        """Close HTTP client on shutdown."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
