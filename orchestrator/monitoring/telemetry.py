"""
OpenTelemetry Distributed Tracing for NovoMCP

PHASE 4 FIX: Comprehensive distributed tracing across microservices.
Enables end-to-end visibility of requests flowing through the discovery engine.

Traces exported to AWS X-Ray, Jaeger, or other OTLP-compatible backends.
"""

import os
from typing import Optional, Dict, Any
from functools import wraps
from contextlib import contextmanager

# OpenTelemetry imports
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.trace import Status, StatusCode
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    print("OpenTelemetry not available - install with: pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp")

import logging

logger = logging.getLogger(__name__)


class TelemetryConfig:
    """Configuration for telemetry"""

    # Service name
    SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "novomcp")

    # OTLP exporter endpoint (Jaeger, AWS X-Ray, etc.)
    OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

    # Enable/disable tracing
    TRACING_ENABLED = os.getenv("OTEL_TRACING_ENABLED", "true").lower() == "true"

    # Sampling rate (0.0 to 1.0)
    SAMPLING_RATE = float(os.getenv("OTEL_SAMPLING_RATE", "1.0"))

    # Export to console (for debugging)
    CONSOLE_EXPORT = os.getenv("OTEL_CONSOLE_EXPORT", "false").lower() == "true"


def setup_telemetry():
    """
    Initialize OpenTelemetry tracing.

    Call this once at application startup.

    Returns:
        TracerProvider instance or None if telemetry not available
    """
    if not OTEL_AVAILABLE:
        logger.warning("OpenTelemetry not available - distributed tracing disabled")
        return None

    if not TelemetryConfig.TRACING_ENABLED:
        logger.info("OpenTelemetry tracing disabled via config")
        return None

    try:
        # Create resource with service information
        resource = Resource(attributes={
            SERVICE_NAME: TelemetryConfig.SERVICE_NAME,
            "service.version": "2.0",
            "deployment.environment": os.getenv("ENVIRONMENT", "production"),
        })

        # Create tracer provider
        provider = TracerProvider(resource=resource)

        # Add OTLP exporter (for production - sends to collector)
        if TelemetryConfig.OTLP_ENDPOINT:
            otlp_exporter = OTLPSpanExporter(
                endpoint=TelemetryConfig.OTLP_ENDPOINT,
                insecure=True  # Use TLS in production
            )
            provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            logger.info(f"OpenTelemetry: Exporting traces to {TelemetryConfig.OTLP_ENDPOINT}")

        # Add console exporter (for debugging)
        if TelemetryConfig.CONSOLE_EXPORT:
            console_exporter = ConsoleSpanExporter()
            provider.add_span_processor(BatchSpanProcessor(console_exporter))
            logger.info("OpenTelemetry: Console export enabled")

        # Set global tracer provider
        trace.set_tracer_provider(provider)

        # Auto-instrument common libraries
        try:
            # Instrument FastAPI (automatic trace creation for HTTP endpoints)
            # Note: Call FastAPIInstrumentor().instrument_app(app) in main.py
            logger.info("OpenTelemetry: FastAPI instrumentation available")
        except Exception as e:
            logger.warning(f"Failed to instrument FastAPI: {e}")

        try:
            # Instrument httpx (traces outgoing HTTP calls to services)
            HTTPXClientInstrumentor().instrument()
            logger.info("OpenTelemetry: HTTPX instrumented")
        except Exception as e:
            logger.warning(f"Failed to instrument HTTPX: {e}")

        try:
            # Instrument Redis (traces cache operations)
            RedisInstrumentor().instrument()
            logger.info("OpenTelemetry: Redis instrumented")
        except Exception as e:
            logger.warning(f"Failed to instrument Redis: {e}")

        logger.info(f"OpenTelemetry initialized for service: {TelemetryConfig.SERVICE_NAME}")
        return provider

    except Exception as e:
        logger.error(f"Failed to setup OpenTelemetry: {e}", exc_info=True)
        return None


def get_tracer(name: str = __name__):
    """
    Get tracer instance for creating spans.

    Args:
        name: Tracer name (usually __name__)

    Returns:
        Tracer instance
    """
    if not OTEL_AVAILABLE:
        return None

    return trace.get_tracer(name)


@contextmanager
def create_span(
    name: str,
    attributes: Optional[Dict[str, Any]] = None,
    tracer_name: Optional[str] = None
):
    """
    Context manager for creating a span.

    Usage:
        with create_span("execute_retrieval_phase", attributes={"molecules_count": 1000}):
            # Your code here
            ...

    Args:
        name: Span name
        attributes: Span attributes (key-value pairs)
        tracer_name: Tracer name (defaults to caller module)

    Yields:
        Span instance (or None if tracing disabled)
    """
    if not OTEL_AVAILABLE or not TelemetryConfig.TRACING_ENABLED:
        yield None
        return

    tracer = get_tracer(tracer_name or __name__)
    if not tracer:
        yield None
        return

    with tracer.start_as_current_span(name) as span:
        # Add attributes
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, value)

        try:
            yield span
        except Exception as e:
            # Mark span as error
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise


def trace_function(name: Optional[str] = None, attributes: Optional[Dict[str, Any]] = None):
    """
    Decorator to automatically trace a function.

    Usage:
        @trace_function("generate_molecules")
        async def generate_molecules():
            ...

    Args:
        name: Span name (defaults to function name)
        attributes: Span attributes

    Returns:
        Decorated function
    """
    def decorator(func):
        span_name = name or func.__name__

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            with create_span(span_name, attributes=attributes) as span:
                # Add function arguments as attributes
                if span:
                    span.set_attribute("function.name", func.__name__)
                    span.set_attribute("function.module", func.__module__)

                result = await func(*args, **kwargs)
                return result

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            with create_span(span_name, attributes=attributes) as span:
                if span:
                    span.set_attribute("function.name", func.__name__)
                    span.set_attribute("function.module", func.__module__)

                result = func(*args, **kwargs)
                return result

        # Return appropriate wrapper
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


class TracedOperation:
    """
    Helper class for tracing multi-step operations.

    Usage:
        op = TracedOperation("campaign_execution")
        op.add_event("phase_started", {"phase": "RETRIEVAL"})
        # ... do work ...
        op.add_event("phase_completed", {"molecules": 1000})
        op.finish(success=True)
    """

    def __init__(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        self.name = name
        self.span = None
        self.tracer = get_tracer()

        if OTEL_AVAILABLE and TelemetryConfig.TRACING_ENABLED and self.tracer:
            self.span = self.tracer.start_span(name)

            if attributes and self.span:
                for key, value in attributes.items():
                    self.span.set_attribute(key, value)

    def add_event(self, event_name: str, attributes: Optional[Dict[str, Any]] = None):
        """Add an event to the span"""
        if self.span:
            self.span.add_event(event_name, attributes=attributes or {})

    def set_attribute(self, key: str, value: Any):
        """Set span attribute"""
        if self.span:
            self.span.set_attribute(key, value)

    def record_exception(self, exception: Exception):
        """Record exception in span"""
        if self.span:
            self.span.record_exception(exception)
            self.span.set_status(Status(StatusCode.ERROR, str(exception)))

    def finish(self, success: bool = True):
        """Finish the span"""
        if self.span:
            if success:
                self.span.set_status(Status(StatusCode.OK))
            else:
                self.span.set_status(Status(StatusCode.ERROR))

            self.span.end()


def inject_trace_context(headers: Dict[str, str]) -> Dict[str, str]:
    """
    Inject trace context into HTTP headers for propagation.

    Usage:
        headers = {"X-API-Key": "..."}
        headers = inject_trace_context(headers)
        response = httpx.post(url, headers=headers)  # Trace propagates!

    Args:
        headers: HTTP headers dict

    Returns:
        Headers with trace context injected
    """
    if not OTEL_AVAILABLE or not TelemetryConfig.TRACING_ENABLED:
        return headers

    propagator = TraceContextTextMapPropagator()
    propagator.inject(headers)
    return headers


def extract_trace_context(headers: Dict[str, str]):
    """
    Extract trace context from HTTP headers.

    Usage:
        # In HTTP endpoint
        context = extract_trace_context(request.headers)
        with tracer.start_as_current_span("handle_request", context=context):
            ...

    Args:
        headers: HTTP headers dict

    Returns:
        Trace context
    """
    if not OTEL_AVAILABLE or not TelemetryConfig.TRACING_ENABLED:
        return None

    propagator = TraceContextTextMapPropagator()
    return propagator.extract(headers)


# Example usage and integration guide
if __name__ == "__main__":
    # 1. Setup telemetry at application startup
    setup_telemetry()

    # 2. Use context manager for manual tracing
    with create_span("example_operation", attributes={"user_id": "123"}):
        print("Doing work...")

    # 3. Use decorator for automatic tracing
    @trace_function("calculate_score")
    def calculate_score(value: int) -> float:
        return value * 0.5

    result = calculate_score(100)

    # 4. Use TracedOperation for complex operations
    op = TracedOperation("campaign_iteration", {"campaign_id": "abc-123"})
    op.add_event("phase_started", {"phase": "RETRIEVAL"})
    op.set_attribute("molecules_count", 1000)
    op.finish(success=True)

    print(f"Tracing enabled: {OTEL_AVAILABLE and TelemetryConfig.TRACING_ENABLED}")
