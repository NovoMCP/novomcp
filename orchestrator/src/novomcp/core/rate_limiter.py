"""
Sliding-window rate limiter for FastAPI routes.
Uses Redis sorted sets when available, in-memory fallback for dev/test.
"""

import time
import logging
from typing import Dict, Optional
from collections import defaultdict
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# Rate limits per category (requests per minute)
RATE_LIMITS: Dict[str, int] = {
    "default": 60,
    "campaign_chat": 20,
    "ai_orchestration": 10,
    "control_center": 30,
    "health": 120,
}

# In-memory sliding window storage (fallback when Redis unavailable)
_memory_store: Dict[str, list] = defaultdict(list)


class RateLimiter:
    """Sliding-window rate limiter with Redis or in-memory backend."""

    def __init__(self):
        self._redis = None
        self._use_redis = False

    async def _get_redis(self):
        """Lazy-init Redis connection."""
        if self._redis is not None:
            return self._redis if self._use_redis else None

        try:
            import redis.asyncio as aioredis
            import os
            redis_url = os.getenv("REDIS_URL")
            if redis_url:
                self._redis = aioredis.from_url(redis_url, decode_responses=True)
                await self._redis.ping()
                self._use_redis = True
                logger.info("Rate limiter using Redis backend")
                return self._redis
        except Exception as e:
            logger.warning(f"Rate limiter Redis unavailable, using in-memory: {e}")

        self._use_redis = False
        self._redis = False  # Sentinel to avoid re-trying
        return None

    async def check(self, key: str, category: str, window_seconds: int = 60) -> Dict[str, int]:
        """
        Check rate limit. Returns dict with limit/remaining/reset info.
        Raises HTTPException(429) if exceeded.
        """
        limit = RATE_LIMITS.get(category, RATE_LIMITS["default"])
        now = time.time()
        window_start = now - window_seconds

        redis_client = await self._get_redis()

        if redis_client:
            return await self._check_redis(redis_client, key, limit, now, window_start, window_seconds)
        else:
            return self._check_memory(key, limit, now, window_start, window_seconds)

    async def _check_redis(self, redis_client, key: str, limit: int, now: float, window_start: float, window_seconds: int) -> Dict[str, int]:
        redis_key = f"ratelimit:{key}"
        pipe = redis_client.pipeline()
        pipe.zremrangebyscore(redis_key, 0, window_start)
        pipe.zadd(redis_key, {str(now): now})
        pipe.zcard(redis_key)
        pipe.expire(redis_key, window_seconds + 1)
        results = await pipe.execute()

        count = results[2]
        remaining = max(0, limit - count)
        reset = int(now + window_seconds)

        info = {"limit": limit, "remaining": remaining, "reset": reset}

        if count > limit:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={
                    "Retry-After": str(window_seconds),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset),
                },
            )

        return info

    def _check_memory(self, key: str, limit: int, now: float, window_start: float, window_seconds: int) -> Dict[str, int]:
        # Clean old entries
        _memory_store[key] = [t for t in _memory_store[key] if t > window_start]
        _memory_store[key].append(now)

        count = len(_memory_store[key])
        remaining = max(0, limit - count)
        reset = int(now + window_seconds)

        info = {"limit": limit, "remaining": remaining, "reset": reset}

        if count > limit:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={
                    "Retry-After": str(window_seconds),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset),
                },
            )

        return info


# Singleton
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def rate_limit(category: str = "default"):
    """
    FastAPI dependency factory for rate limiting.

    Usage:
        router = APIRouter(dependencies=[Depends(rate_limit("campaign_chat"))])
    """
    async def _check_rate(request: Request):
        limiter = get_rate_limiter()
        # Use client IP + path as the rate limit key
        client_ip = request.client.host if request.client else "unknown"
        api_key = request.headers.get("X-API-Key", "")
        # Prefer API key for keying (more stable than IP behind proxies)
        identity = api_key[:16] if api_key else client_ip
        key = f"{category}:{identity}"
        await limiter.check(key, category)

    return _check_rate
