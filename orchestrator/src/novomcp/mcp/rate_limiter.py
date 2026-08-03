"""
NovoMCP Rate Limiter

Implements rate limiting and usage tracking for MCP endpoints.
Prevents abuse and enforces tier-based limits.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum
import asyncio

logger = logging.getLogger(__name__)


class RateLimitResult(str, Enum):
    """Result of a rate limit check."""
    ALLOWED = "allowed"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"


@dataclass
class RateLimitInfo:
    """Information about current rate limit status."""
    allowed: bool
    result: RateLimitResult
    remaining: int
    limit: int
    reset_at: datetime
    retry_after: Optional[int] = None  # Seconds until next allowed request


class MCPRateLimiter:
    """
    Rate limiter for MCP endpoints.

    Implements:
    - Per-minute rate limiting (burst protection)
    - Daily query quotas (tier-based)
    - Per-tool limits
    """

    # Rate limits per tier (requests per minute)
    RATE_LIMITS = {
        "free": 10,
        "core": 60,
        "pro": 60,
        "team": 300,
        "enterprise": 1000
    }

    # Daily query limits per tier
    DAILY_LIMITS = {
        "free": 10,
        "core": 1000,
        "pro": 1000,
        "team": 10000,
        "enterprise": 1000000
    }

    # Per-tool limits (some tools cost more)
    TOOL_COSTS = {
        # Free tier
        "get_molecule_profile": 1,
        "get_molecule_info": 1,
        # Pro tier
        "search_similar": 3,
        "filter_molecules": 2,
        "batch_profile": 0,  # Uses batch size as cost
        # Team tier
        "optimize_molecule": 5,
        "predict_structure": 10,
        "get_structure_result": 0,  # Status checks are free
        # Enterprise tier
        "check_compliance": 3,
        "screen_library": 0  # Uses batch size as cost
    }

    def __init__(self, redis_client=None):
        """
        Initialize rate limiter.

        Args:
            redis_client: Redis client for distributed rate limiting
        """
        self.redis = redis_client

        # In-memory fallback for development
        self._rate_windows: Dict[str, list] = {}  # user_id -> list of timestamps
        self._daily_usage: Dict[str, Dict[str, int]] = {}  # user_id -> {date: count}

    async def check_rate_limit(
        self,
        user_id: str,
        tier: str,
        tool_name: Optional[str] = None,
        batch_size: int = 1
    ) -> RateLimitInfo:
        """
        Check if a request should be allowed.

        Args:
            user_id: User making the request
            tier: User's subscription tier
            tool_name: Name of the tool being called
            batch_size: For batch operations, the number of items

        Returns:
            RateLimitInfo with allowed status and limits
        """
        now = datetime.utcnow()

        # Calculate cost
        cost = self._calculate_cost(tool_name, batch_size)

        # Check per-minute rate limit
        rate_result = await self._check_minute_rate(user_id, tier)
        if not rate_result.allowed:
            return rate_result

        # Check daily quota
        quota_result = await self._check_daily_quota(user_id, tier, cost)
        if not quota_result.allowed:
            return quota_result

        # Record the request
        await self._record_request(user_id, cost)

        return RateLimitInfo(
            allowed=True,
            result=RateLimitResult.ALLOWED,
            remaining=quota_result.remaining - cost,
            limit=quota_result.limit,
            reset_at=self._get_next_reset()
        )

    def _calculate_cost(self, tool_name: Optional[str], batch_size: int) -> int:
        """Calculate the query cost for a request."""
        if tool_name == "batch_classify":
            return batch_size
        return self.TOOL_COSTS.get(tool_name, 1)

    async def _check_minute_rate(self, user_id: str, tier: str) -> RateLimitInfo:
        """Check per-minute rate limit."""
        limit = self.RATE_LIMITS.get(tier, 10)
        window_key = f"rate:{user_id}"
        now = time.time()
        window_start = now - 60  # 1 minute window

        if self.redis:
            # Use Redis sorted set for sliding window (sync client)
            pipe = self.redis.pipeline()
            pipe.zremrangebyscore(window_key, 0, window_start)
            pipe.zcard(window_key)
            results = pipe.execute()
            current_count = results[1]
        else:
            # In-memory implementation
            if window_key not in self._rate_windows:
                self._rate_windows[window_key] = []

            # Clean old entries
            self._rate_windows[window_key] = [
                ts for ts in self._rate_windows[window_key]
                if ts > window_start
            ]
            current_count = len(self._rate_windows[window_key])

        if current_count >= limit:
            return RateLimitInfo(
                allowed=False,
                result=RateLimitResult.RATE_LIMITED,
                remaining=0,
                limit=limit,
                reset_at=datetime.utcnow() + timedelta(seconds=60),
                retry_after=60
            )

        return RateLimitInfo(
            allowed=True,
            result=RateLimitResult.ALLOWED,
            remaining=limit - current_count,
            limit=limit,
            reset_at=datetime.utcnow() + timedelta(seconds=60)
        )

    async def _check_daily_quota(self, user_id: str, tier: str, cost: int) -> RateLimitInfo:
        """Check daily query quota."""
        limit = self.DAILY_LIMITS.get(tier, 10)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        quota_key = f"quota:{user_id}:{today}"

        if self.redis:
            # Sync redis client
            current = self.redis.get(quota_key)
            current_count = int(current) if current else 0
        else:
            if user_id not in self._daily_usage:
                self._daily_usage[user_id] = {}
            current_count = self._daily_usage[user_id].get(today, 0)

        if current_count + cost > limit:
            # Calculate reset time (midnight UTC)
            tomorrow = datetime.utcnow().replace(
                hour=0, minute=0, second=0, microsecond=0
            ) + timedelta(days=1)

            return RateLimitInfo(
                allowed=False,
                result=RateLimitResult.QUOTA_EXCEEDED,
                remaining=0,
                limit=limit,
                reset_at=tomorrow,
                retry_after=int((tomorrow - datetime.utcnow()).total_seconds())
            )

        return RateLimitInfo(
            allowed=True,
            result=RateLimitResult.ALLOWED,
            remaining=limit - current_count,
            limit=limit,
            reset_at=self._get_next_reset()
        )

    async def _record_request(self, user_id: str, cost: int):
        """Record a request for rate limiting."""
        now = time.time()
        today = datetime.utcnow().strftime("%Y-%m-%d")

        if self.redis:
            # Sync redis client
            pipe = self.redis.pipeline()

            # Add to rate window
            rate_key = f"rate:{user_id}"
            pipe.zadd(rate_key, {str(now): now})
            pipe.expire(rate_key, 120)  # Expire after 2 minutes

            # Increment daily quota
            quota_key = f"quota:{user_id}:{today}"
            pipe.incrby(quota_key, cost)
            pipe.expire(quota_key, 86400 * 2)  # Expire after 2 days

            pipe.execute()
        else:
            # In-memory
            rate_key = f"rate:{user_id}"
            if rate_key not in self._rate_windows:
                self._rate_windows[rate_key] = []
            self._rate_windows[rate_key].append(now)

            if user_id not in self._daily_usage:
                self._daily_usage[user_id] = {}
            self._daily_usage[user_id][today] = self._daily_usage[user_id].get(today, 0) + cost

    def _get_next_reset(self) -> datetime:
        """Get the next quota reset time (midnight UTC)."""
        tomorrow = datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)
        return tomorrow

    async def get_usage_stats(self, user_id: str, tier: str) -> Dict[str, Any]:
        """Get current usage statistics for a user."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        daily_limit = self.DAILY_LIMITS.get(tier, 10)
        rate_limit = self.RATE_LIMITS.get(tier, 10)

        if self.redis:
            # Sync redis client
            quota_key = f"quota:{user_id}:{today}"
            daily_used = int(self.redis.get(quota_key) or 0)

            rate_key = f"rate:{user_id}"
            window_start = time.time() - 60
            self.redis.zremrangebyscore(rate_key, 0, window_start)
            minute_used = self.redis.zcard(rate_key)
        else:
            daily_used = self._daily_usage.get(user_id, {}).get(today, 0)
            rate_key = f"rate:{user_id}"
            minute_used = len(self._rate_windows.get(rate_key, []))

        return {
            "tier": tier,
            "daily": {
                "used": daily_used,
                "limit": daily_limit,
                "remaining": max(0, daily_limit - daily_used),
                "reset_at": self._get_next_reset().isoformat()
            },
            "rate": {
                "used": minute_used,
                "limit": rate_limit,
                "remaining": max(0, rate_limit - minute_used),
                "window": "1 minute"
            }
        }


# =============================================================================
# Anomaly Detection
# =============================================================================

class UsageAnomalyDetector:
    """
    Detects suspicious usage patterns that might indicate abuse.
    """

    def __init__(self, redis_client=None):
        self.redis = redis_client
        self._patterns: Dict[str, list] = {}

    async def check_for_anomalies(
        self,
        user_id: str,
        tool_name: str,
        batch_size: int = 1
    ) -> Tuple[bool, Optional[str]]:
        """
        Check for suspicious patterns.

        Returns:
            Tuple of (is_suspicious, reason)
        """
        # Check for rapid-fire identical requests (potential scraping)
        if await self._check_repetitive_requests(user_id, tool_name):
            return True, "Repetitive requests detected"

        # Check for systematic batch queries (potential bulk export)
        if tool_name == "batch_classify" and batch_size > 500:
            if await self._check_bulk_export_pattern(user_id):
                return True, "Bulk export pattern detected"

        return False, None

    async def _check_repetitive_requests(self, user_id: str, tool_name: str) -> bool:
        """Check for repetitive identical requests."""
        key = f"pattern:{user_id}:{tool_name}"
        now = time.time()

        if self.redis:
            # Sync redis client
            count = self.redis.incr(key)
            if count == 1:
                self.redis.expire(key, 10)  # 10 second window
            return count > 20  # More than 20 identical requests in 10 seconds
        else:
            if key not in self._patterns:
                self._patterns[key] = []
            self._patterns[key].append(now)
            self._patterns[key] = [t for t in self._patterns[key] if t > now - 10]
            return len(self._patterns[key]) > 20

    async def _check_bulk_export_pattern(self, user_id: str) -> bool:
        """Check for patterns suggesting bulk data export."""
        key = f"bulk:{user_id}"
        now = time.time()

        if self.redis:
            # Sync redis client
            count = self.redis.incr(key)
            if count == 1:
                self.redis.expire(key, 3600)  # 1 hour window
            return count > 10  # More than 10 large batch requests per hour
        else:
            if key not in self._patterns:
                self._patterns[key] = []
            self._patterns[key].append(now)
            self._patterns[key] = [t for t in self._patterns[key] if t > now - 3600]
            return len(self._patterns[key]) > 10
