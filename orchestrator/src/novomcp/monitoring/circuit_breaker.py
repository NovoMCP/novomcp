"""
Circuit Breaker for Service Resilience

PHASE 4 FIX: Enhanced with per-service failure tracking, Prometheus metrics,
and detailed failure analysis.

Prevents cascading failures by breaking connections to failing services.
"""

import asyncio
import time
from typing import Dict, Any, Callable, Optional, List
from enum import Enum
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque

logger = logging.getLogger(__name__)

# PHASE 4 FIX: Import Prometheus metrics
try:
    from .prometheus_metrics import circuit_breaker_state, circuit_breaker_failures_total, circuit_breaker_trips_total
    METRICS_AVAILABLE = True
except ImportError:
    METRICS_AVAILABLE = False
    logger.debug("Prometheus metrics not available for circuit breaker")


class CircuitState(Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Service is down, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class FailureRecord:
    """Record of a single failure"""
    timestamp: datetime
    exception_type: str
    exception_message: str
    phase: Optional[str] = None


@dataclass
class CircuitStats:
    """
    Statistics for circuit breaker

    PHASE 4 FIX: Added failure history tracking and failure rate calculation
    """
    total_calls: int = 0
    success_calls: int = 0
    failed_calls: int = 0
    last_failure_time: Optional[datetime] = None
    consecutive_failures: int = 0
    state_changes: List[Dict[str, Any]] = field(default_factory=list)
    failure_history: deque = field(default_factory=lambda: deque(maxlen=50))  # Last 50 failures

    def get_failure_rate(self, window_seconds: int = 60) -> float:
        """Calculate failure rate in the last N seconds"""
        if not self.failure_history:
            return 0.0

        cutoff_time = datetime.utcnow() - timedelta(seconds=window_seconds)
        recent_failures = sum(
            1 for f in self.failure_history
            if f.timestamp > cutoff_time
        )

        # Estimate total calls in window (approximate)
        if self.total_calls == 0:
            return 0.0

        # Rough estimate: assume uniform distribution of calls
        estimated_calls_in_window = max(recent_failures, 1)
        return recent_failures / estimated_calls_in_window

    def get_failure_breakdown(self) -> Dict[str, int]:
        """Get breakdown of failures by exception type"""
        breakdown = {}
        for failure in self.failure_history:
            exc_type = failure.exception_type
            breakdown[exc_type] = breakdown.get(exc_type, 0) + 1
        return breakdown


class CircuitBreaker:
    """
    Circuit breaker implementation for service calls.
    Monitors failure rates and breaks circuit when threshold exceeded.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type = Exception
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self._state = CircuitState.CLOSED
        self._stats = CircuitStats()
        self._last_attempt_time = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Get current circuit state"""
        return self._state

    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)"""
        return self._state == CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (rejecting requests)"""
        return self._state == CircuitState.OPEN

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset"""
        if self._stats.last_failure_time is None:
            return False

        time_since_failure = datetime.utcnow() - self._stats.last_failure_time
        return time_since_failure > timedelta(seconds=self.recovery_timeout)

    async def _record_success(self):
        """Record successful call"""
        async with self._lock:
            self._stats.total_calls += 1
            self._stats.success_calls += 1
            self._stats.consecutive_failures = 0

            if self._state == CircuitState.HALF_OPEN:
                logger.info(f"Circuit breaker {self.name}: Service recovered, closing circuit")
                self._transition_to(CircuitState.CLOSED)

    async def _record_failure(self, exception: Exception, phase: Optional[str] = None):
        """
        Record failed call

        PHASE 4 FIX: Enhanced with failure history tracking and metrics
        """
        async with self._lock:
            self._stats.total_calls += 1
            self._stats.failed_calls += 1
            self._stats.consecutive_failures += 1
            self._stats.last_failure_time = datetime.utcnow()

            # PHASE 4 FIX: Add to failure history
            failure_record = FailureRecord(
                timestamp=datetime.utcnow(),
                exception_type=type(exception).__name__,
                exception_message=str(exception),
                phase=phase
            )
            self._stats.failure_history.append(failure_record)

            # PHASE 4 FIX: Update Prometheus metrics
            if METRICS_AVAILABLE:
                circuit_breaker_failures_total.labels(
                    service=self.name,
                    phase=phase or "unknown"
                ).inc()

            logger.warning(
                f"Circuit breaker {self.name}: Failure #{self._stats.consecutive_failures} - {exception}"
            )

            if self._stats.consecutive_failures >= self.failure_threshold:
                if self._state != CircuitState.OPEN:
                    logger.error(
                        f"Circuit breaker {self.name}: Threshold exceeded, opening circuit"
                    )
                    self._transition_to(CircuitState.OPEN)

    def _transition_to(self, new_state: CircuitState):
        """Transition to new state"""
        old_state = self._state
        self._state = new_state
        self._stats.state_changes.append({
            'from': old_state.value,
            'to': new_state.value,
            'timestamp': datetime.utcnow().isoformat()
        })

        logger.info(f"Circuit breaker {self.name}: {old_state.value} -> {new_state.value}")

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Call function with circuit breaker protection

        Args:
            func: Async function to call
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func

        Returns:
            Result from func

        Raises:
            CircuitOpenException: If circuit is open
            Original exception: If func fails
        """
        async with self._lock:
            # Check if circuit should transition from OPEN to HALF_OPEN
            if self._state == CircuitState.OPEN and self._should_attempt_reset():
                logger.info(f"Circuit breaker {self.name}: Attempting reset")
                self._transition_to(CircuitState.HALF_OPEN)

        # Reject if circuit is open
        if self._state == CircuitState.OPEN:
            error_msg = f"Circuit breaker {self.name} is OPEN - service unavailable"
            logger.error(error_msg)
            raise CircuitOpenException(error_msg)

        # Attempt the call
        try:
            result = await func(*args, **kwargs)
            await self._record_success()
            return result

        except self.expected_exception as e:
            await self._record_failure(e)
            raise

    def get_stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics"""
        return {
            'name': self.name,
            'state': self._state.value,
            'total_calls': self._stats.total_calls,
            'success_calls': self._stats.success_calls,
            'failed_calls': self._stats.failed_calls,
            'success_rate': (
                self._stats.success_calls / self._stats.total_calls
                if self._stats.total_calls > 0 else 0
            ),
            'consecutive_failures': self._stats.consecutive_failures,
            'last_failure': (
                self._stats.last_failure_time.isoformat()
                if self._stats.last_failure_time else None
            ),
            'state_changes': self._stats.state_changes[-10:]  # Last 10 changes
        }

    def reset(self):
        """Reset circuit breaker to closed state"""
        self._state = CircuitState.CLOSED
        self._stats = CircuitStats()
        logger.info(f"Circuit breaker {self.name} reset")


class CircuitOpenException(Exception):
    """Exception raised when circuit is open"""
    pass


class ServiceCircuitManager:
    """Manage circuit breakers for multiple services"""

    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}

    def get_breaker(
        self,
        service_name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60
    ) -> CircuitBreaker:
        """Get or create circuit breaker for service"""
        if service_name not in self._breakers:
            self._breakers[service_name] = CircuitBreaker(
                name=service_name,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout
            )
        return self._breakers[service_name]

    def get_all_stats(self) -> Dict[str, Any]:
        """Get statistics for all circuit breakers"""
        return {
            name: breaker.get_stats()
            for name, breaker in self._breakers.items()
        }

    def reset_all(self):
        """Reset all circuit breakers"""
        for breaker in self._breakers.values():
            breaker.reset()


# Global instance
_circuit_manager = ServiceCircuitManager()


def get_circuit_manager() -> ServiceCircuitManager:
    """Get global circuit manager instance"""
    return _circuit_manager