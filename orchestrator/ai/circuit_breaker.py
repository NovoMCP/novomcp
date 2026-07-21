"""
Circuit Breaker Pattern for Campaign Failure Protection
Prevents infinite error loops by halting campaigns after repeated failures
"""

import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Circuit tripped, campaign halted
    HALF_OPEN = "half_open"  # Testing if system recovered


class FailureCategory(Enum):
    """Categories of failures for tracking"""
    SERVICE_ERROR = "service_error"  # Service call failed
    QUALITY_GATE = "quality_gate"  # Quality gate failed
    TIMEOUT = "timeout"  # Operation timed out
    VALIDATION = "validation"  # Data validation failed


class CampaignCircuitBreaker:
    """
    Circuit breaker for campaign failure protection
    Tracks failures per phase and halts campaign after threshold
    """

    def __init__(self, campaign_id: str, config: Optional[Dict[str, Any]] = None):
        self.campaign_id = campaign_id
        self.config = config or {}

        # Circuit breaker thresholds
        self.failure_threshold = self.config.get("circuit_breaker_threshold", 5)
        self.reset_timeout_minutes = self.config.get("circuit_reset_timeout", 30)
        self.half_open_attempts = self.config.get("half_open_attempts", 3)

        # State tracking
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.consecutive_failures = 0
        self.last_failure_time: Optional[datetime] = None
        self.last_success_time: Optional[datetime] = None
        self.trip_time: Optional[datetime] = None

        # Phase-specific failure tracking
        self.phase_failures: Dict[str, int] = {}
        self.failure_history: list = []

        # Half-open state tracking
        self.half_open_success_count = 0

    def record_success(self, phase: str = ""):
        """Record successful operation"""
        self.last_success_time = datetime.utcnow()
        self.consecutive_failures = 0

        if self.state == CircuitState.HALF_OPEN:
            self.half_open_success_count += 1
            if self.half_open_success_count >= self.half_open_attempts:
                # Reset circuit breaker
                logger.info(f"Circuit breaker for campaign {self.campaign_id} recovered")
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.half_open_success_count = 0
                self.trip_time = None

        logger.debug(f"Campaign {self.campaign_id} recorded success (phase: {phase})")

    def record_failure(self, phase: str, category: FailureCategory, error: str) -> Dict[str, Any]:
        """
        Record operation failure

        Returns:
            {
                "circuit_state": str,
                "should_halt": bool,
                "failure_count": int,
                "threshold": int,
                "message": str
            }
        """
        self.last_failure_time = datetime.utcnow()
        self.failure_count += 1
        self.consecutive_failures += 1

        # Track phase-specific failures
        if phase not in self.phase_failures:
            self.phase_failures[phase] = 0
        self.phase_failures[phase] += 1

        # Add to failure history
        self.failure_history.append({
            "phase": phase,
            "category": category.value,
            "error": error,
            "timestamp": self.last_failure_time.isoformat(),
            "consecutive_count": self.consecutive_failures
        })

        # Keep only last 50 failures
        self.failure_history = self.failure_history[-50:]

        logger.warning(
            f"Campaign {self.campaign_id} failure {self.consecutive_failures}/{self.failure_threshold} "
            f"(phase: {phase}, category: {category.value})"
        )

        # Check if should trip circuit
        if self.consecutive_failures >= self.failure_threshold:
            return self._trip_circuit(phase, error)

        return {
            "circuit_state": self.state.value,
            "should_halt": False,
            "failure_count": self.consecutive_failures,
            "threshold": self.failure_threshold,
            "message": f"Failure {self.consecutive_failures}/{self.failure_threshold} recorded"
        }

    def _trip_circuit(self, phase: str, error: str) -> Dict[str, Any]:
        """Trip the circuit breaker"""
        if self.state != CircuitState.OPEN:
            self.state = CircuitState.OPEN
            self.trip_time = datetime.utcnow()

            logger.error(
                f"Circuit breaker TRIPPED for campaign {self.campaign_id} "
                f"after {self.consecutive_failures} consecutive failures (phase: {phase})"
            )

        return {
            "circuit_state": self.state.value,
            "should_halt": True,
            "failure_count": self.consecutive_failures,
            "threshold": self.failure_threshold,
            "phase": phase,
            "error": error,
            "message": f"Circuit breaker tripped after {self.consecutive_failures} consecutive failures",
            "trip_time": self.trip_time.isoformat() if self.trip_time else None
        }

    def attempt_reset(self) -> bool:
        """
        Attempt to reset circuit breaker (transition to half-open)

        Returns:
            True if reset successful, False if still in timeout period
        """
        if self.state != CircuitState.OPEN:
            return False

        if not self.trip_time:
            return False

        # Check if timeout period has elapsed
        elapsed = datetime.utcnow() - self.trip_time
        timeout_duration = timedelta(minutes=self.reset_timeout_minutes)

        if elapsed >= timeout_duration:
            logger.info(f"Circuit breaker for campaign {self.campaign_id} entering HALF-OPEN state")
            self.state = CircuitState.HALF_OPEN
            self.half_open_success_count = 0
            return True

        return False

    def can_proceed(self) -> Tuple[bool, str]:
        """
        Check if operation can proceed

        Returns:
            (can_proceed: bool, reason: str)
        """
        if self.state == CircuitState.OPEN:
            # Check if can attempt reset
            if self.attempt_reset():
                return True, "Circuit in half-open state, attempting recovery"
            else:
                remaining = self._get_remaining_timeout()
                return False, f"Circuit breaker open. Retry in {remaining} minutes"

        return True, "Circuit breaker closed, operation allowed"

    def _get_remaining_timeout(self) -> int:
        """Get remaining timeout minutes"""
        if not self.trip_time:
            return 0

        elapsed = datetime.utcnow() - self.trip_time
        timeout_duration = timedelta(minutes=self.reset_timeout_minutes)
        remaining = timeout_duration - elapsed

        return max(0, int(remaining.total_seconds() / 60))

    def get_state(self) -> Dict[str, Any]:
        """Get current circuit breaker state"""
        can_proceed, reason = self.can_proceed()

        return {
            "circuit_state": self.state.value,
            "can_proceed": can_proceed,
            "reason": reason,
            "failure_count": self.failure_count,
            "consecutive_failures": self.consecutive_failures,
            "threshold": self.failure_threshold,
            "trip_time": self.trip_time.isoformat() if self.trip_time else None,
            "last_failure": self.last_failure_time.isoformat() if self.last_failure_time else None,
            "last_success": self.last_success_time.isoformat() if self.last_success_time else None,
            "phase_failures": self.phase_failures,
            "recent_failures": self.failure_history[-10:]  # Last 10 failures
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialize circuit breaker state for persistence"""
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "consecutive_failures": self.consecutive_failures,
            "trip_time": self.trip_time.isoformat() if self.trip_time else None,
            "last_failure_time": self.last_failure_time.isoformat() if self.last_failure_time else None,
            "last_success_time": self.last_success_time.isoformat() if self.last_success_time else None,
            "phase_failures": self.phase_failures,
            "failure_history": self.failure_history,
            "half_open_success_count": self.half_open_success_count
        }

    @classmethod
    def from_dict(cls, campaign_id: str, data: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> 'CampaignCircuitBreaker':
        """Deserialize circuit breaker state"""
        breaker = cls(campaign_id, config)

        breaker.state = CircuitState(data.get("state", "closed"))
        breaker.failure_count = data.get("failure_count", 0)
        breaker.consecutive_failures = data.get("consecutive_failures", 0)
        breaker.phase_failures = data.get("phase_failures", {})
        breaker.failure_history = data.get("failure_history", [])
        breaker.half_open_success_count = data.get("half_open_success_count", 0)

        # Parse datetime fields
        if data.get("trip_time"):
            breaker.trip_time = datetime.fromisoformat(data["trip_time"])
        if data.get("last_failure_time"):
            breaker.last_failure_time = datetime.fromisoformat(data["last_failure_time"])
        if data.get("last_success_time"):
            breaker.last_success_time = datetime.fromisoformat(data["last_success_time"])

        return breaker

    def get_intervention_request(self) -> Dict[str, Any]:
        """Build intervention request when circuit breaker trips"""
        top_failing_phase = max(self.phase_failures.items(), key=lambda x: x[1])[0] if self.phase_failures else "unknown"

        failure_summary = {}
        for failure in self.failure_history[-5:]:
            category = failure["category"]
            if category not in failure_summary:
                failure_summary[category] = []
            failure_summary[category].append(failure["error"])

        return {
            "type": "circuit_breaker_trip",
            "severity": "critical",
            "campaign_id": self.campaign_id,
            "reason": f"Circuit breaker tripped after {self.consecutive_failures} consecutive failures",
            "details": {
                "failing_phase": top_failing_phase,
                "failure_count": self.failure_count,
                "consecutive_failures": self.consecutive_failures,
                "threshold": self.failure_threshold,
                "failure_summary": failure_summary,
                "recent_failures": self.failure_history[-5:]
            },
            "recommended_actions": [
                f"Review recent failures in {top_failing_phase} phase",
                "Check service health and connectivity",
                "Verify campaign constraints are achievable",
                "Consider adjusting quality gate thresholds",
                "Reset circuit breaker after investigation"
            ],
            "timestamp": datetime.utcnow().isoformat()
        }

    def reset_manual(self):
        """Manually reset circuit breaker (e.g., after human intervention)"""
        logger.info(f"Manually resetting circuit breaker for campaign {self.campaign_id}")
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.consecutive_failures = 0
        self.trip_time = None
        self.half_open_success_count = 0
        # Keep phase_failures for learning, but reset to allow retry
