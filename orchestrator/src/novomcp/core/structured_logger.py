"""
Structured Logging Utilities for NovoMCP

PHASE 4 FIX: JSON-formatted logging with correlation IDs, request tracking, and structured fields.
Enables easy parsing by CloudWatch Insights, Datadog, or other log aggregation systems.
"""

import logging
import json
import uuid
import time
from datetime import datetime
from typing import Dict, Any, Optional
from contextvars import ContextVar
from functools import wraps

# Context variables for correlation tracking (thread-safe across async contexts)
correlation_id_var: ContextVar[Optional[str]] = ContextVar('correlation_id', default=None)
campaign_id_var: ContextVar[Optional[str]] = ContextVar('campaign_id', default=None)
iteration_number_var: ContextVar[Optional[int]] = ContextVar('iteration_number', default=None)
phase_var: ContextVar[Optional[str]] = ContextVar('phase', default=None)


class StructuredLogger:
    """
    Structured logger with JSON formatting and correlation IDs.

    Usage:
        logger = StructuredLogger(__name__)
        logger.info("Campaign started", campaign_id="abc-123", iteration=1)

    Output (JSON):
        {
            "timestamp": "2025-11-15T10:30:45.123Z",
            "level": "INFO",
            "logger": "workflow_engine",
            "message": "Campaign started",
            "correlation_id": "req-456def",
            "campaign_id": "abc-123",
            "iteration": 1,
            "service": "novomcp",
            "environment": "production"
        }
    """

    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(name)

    def _build_log_entry(
        self,
        level: str,
        message: str,
        extra: Optional[Dict[str, Any]] = None,
        exc_info: Optional[Exception] = None
    ) -> Dict[str, Any]:
        """Build structured log entry with all context"""
        import os

        entry = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': level,
            'logger': self.name,
            'message': message,
            'service': os.getenv('SERVICE_NAME', 'novomcp'),
            'environment': os.getenv('ENVIRONMENT', 'production'),
        }

        # Add correlation tracking from context
        correlation_id = correlation_id_var.get()
        if correlation_id:
            entry['correlation_id'] = correlation_id

        campaign_id = campaign_id_var.get()
        if campaign_id:
            entry['campaign_id'] = campaign_id

        iteration_number = iteration_number_var.get()
        if iteration_number is not None:
            entry['iteration_number'] = iteration_number

        phase = phase_var.get()
        if phase:
            entry['phase'] = phase

        # Add custom fields
        if extra:
            entry.update(extra)

        # Add exception info if present
        if exc_info:
            entry['exception'] = {
                'type': exc_info.__class__.__name__,
                'message': str(exc_info),
                'traceback': self._format_exception(exc_info)
            }

        return entry

    def _format_exception(self, exc: Exception) -> str:
        """Format exception with traceback"""
        import traceback
        return ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    def _log(self, level: str, message: str, **kwargs):
        """Internal logging method"""
        exc_info = kwargs.pop('exc_info', None)
        entry = self._build_log_entry(level, message, kwargs, exc_info)

        # Log as JSON string
        json_log = json.dumps(entry)

        # Map to standard logging levels
        log_method = {
            'DEBUG': self.logger.debug,
            'INFO': self.logger.info,
            'WARNING': self.logger.warning,
            'ERROR': self.logger.error,
            'CRITICAL': self.logger.critical
        }.get(level, self.logger.info)

        log_method(json_log)

    def debug(self, message: str, **kwargs):
        """Log debug message"""
        self._log('DEBUG', message, **kwargs)

    def info(self, message: str, **kwargs):
        """Log info message"""
        self._log('INFO', message, **kwargs)

    def warning(self, message: str, **kwargs):
        """Log warning message"""
        self._log('WARNING', message, **kwargs)

    def error(self, message: str, **kwargs):
        """Log error message"""
        self._log('ERROR', message, **kwargs)

    def critical(self, message: str, **kwargs):
        """Log critical message"""
        self._log('CRITICAL', message, **kwargs)


class CorrelationContext:
    """
    Context manager for correlation ID tracking.

    Usage:
        with CorrelationContext(correlation_id="req-123", campaign_id="abc-456"):
            logger.info("Processing request")  # Automatically includes correlation_id
    """

    def __init__(
        self,
        correlation_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        iteration_number: Optional[int] = None,
        phase: Optional[str] = None
    ):
        self.correlation_id = correlation_id or f"req-{uuid.uuid4().hex[:12]}"
        self.campaign_id = campaign_id
        self.iteration_number = iteration_number
        self.phase = phase

        # Store tokens for cleanup
        self.tokens = []

    def __enter__(self):
        """Set context variables"""
        self.tokens.append(correlation_id_var.set(self.correlation_id))

        if self.campaign_id:
            self.tokens.append(campaign_id_var.set(self.campaign_id))

        if self.iteration_number is not None:
            self.tokens.append(iteration_number_var.set(self.iteration_number))

        if self.phase:
            self.tokens.append(phase_var.set(self.phase))

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Reset context variables"""
        for token in self.tokens:
            if token:
                try:
                    # Reset to previous value
                    if hasattr(token, 'var'):
                        token.var.reset(token)
                except:
                    pass


def log_execution_time(logger: StructuredLogger, operation: str):
    """
    Decorator to log execution time of functions.

    Usage:
        @log_execution_time(logger, "molecule_generation")
        async def generate_molecules():
            ...

    Logs:
        {
            "message": "molecule_generation completed",
            "operation": "molecule_generation",
            "duration_ms": 1234.56,
            "status": "success"
        }
    """
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            status = "success"
            error = None

            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                status = "error"
                error = e
                raise
            finally:
                duration_ms = (time.time() - start_time) * 1000
                logger.info(
                    f"{operation} completed",
                    operation=operation,
                    duration_ms=round(duration_ms, 2),
                    status=status,
                    error=str(error) if error else None
                )

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            status = "success"
            error = None

            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                status = "error"
                error = e
                raise
            finally:
                duration_ms = (time.time() - start_time) * 1000
                logger.info(
                    f"{operation} completed",
                    operation=operation,
                    duration_ms=round(duration_ms, 2),
                    status=status,
                    error=str(error) if error else None
                )

        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


class MetricsLogger:
    """
    Logger for emitting metrics in structured format.

    Usage:
        metrics = MetricsLogger()
        metrics.counter("molecules_generated", 100, campaign_id="abc-123")
        metrics.gauge("queue_depth", 42)
        metrics.histogram("phase_duration_seconds", 123.45, phase="RETRIEVAL")
    """

    def __init__(self):
        self.logger = StructuredLogger("metrics")

    def counter(self, name: str, value: float, **labels):
        """Emit counter metric"""
        self.logger.info(
            f"METRIC: {name}",
            metric_type="counter",
            metric_name=name,
            metric_value=value,
            **labels
        )

    def gauge(self, name: str, value: float, **labels):
        """Emit gauge metric"""
        self.logger.info(
            f"METRIC: {name}",
            metric_type="gauge",
            metric_name=name,
            metric_value=value,
            **labels
        )

    def histogram(self, name: str, value: float, **labels):
        """Emit histogram metric"""
        self.logger.info(
            f"METRIC: {name}",
            metric_type="histogram",
            metric_name=name,
            metric_value=value,
            **labels
        )


# Convenience function for getting structured logger
def get_logger(name: str) -> StructuredLogger:
    """Get structured logger instance"""
    return StructuredLogger(name)


# Example usage
if __name__ == "__main__":
    # Configure root logger to output JSON
    logging.basicConfig(
        level=logging.INFO,
        format='%(message)s'  # Just the message (which is JSON)
    )

    logger = get_logger(__name__)

    # Simple log
    logger.info("Application started")

    # Log with context
    with CorrelationContext(campaign_id="test-campaign-123", iteration_number=5):
        logger.info("Processing iteration", molecules_count=100, phase="RETRIEVAL")
        logger.warning("Low pass rate", pass_rate=0.08, threshold=0.10)

        try:
            raise ValueError("Test error")
        except Exception as e:
            logger.error("Operation failed", exc_info=e, operation="test")

    # Metrics
    metrics = MetricsLogger()
    metrics.counter("requests_total", 1, endpoint="/orchestrate", status="200")
    metrics.histogram("request_duration_seconds", 0.123, endpoint="/orchestrate")
