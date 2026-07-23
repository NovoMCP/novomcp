"""
Prometheus Metrics for NovoMCP

PHASE 4 FIX: Comprehensive metrics for monitoring campaign performance,
phase execution times, failure rates, and system health.

Metrics exported on /metrics endpoint for Prometheus scraping.
"""

from prometheus_client import Counter, Histogram, Gauge, Info, generate_latest, REGISTRY
from prometheus_client import CollectorRegistry
from typing import Optional
import time
from functools import wraps

# Create custom registry (optional - use default REGISTRY for simplicity)
# custom_registry = CollectorRegistry()

# ============================================================================
# CAMPAIGN METRICS
# ============================================================================

# Campaign lifecycle
campaigns_total = Counter(
    'novomcp_campaigns_total',
    'Total number of campaigns created',
    ['status']  # active, completed, stopped, error
)

campaigns_active = Gauge(
    'novomcp_campaigns_active',
    'Number of currently active campaigns'
)

campaign_iterations_total = Counter(
    'novomcp_campaign_iterations_total',
    'Total number of campaign iterations',
    ['campaign_id', 'status']  # completed, failed
)

campaign_discoveries_total = Counter(
    'novomcp_campaign_discoveries_total',
    'Total number of molecules discovered',
    ['campaign_id', 'discovery_type']  # therapeutic, tool, fragment
)

# ============================================================================
# PHASE EXECUTION METRICS
# ============================================================================

phase_execution_duration_seconds = Histogram(
    'novomcp_phase_execution_duration_seconds',
    'Time spent executing each phase',
    ['phase', 'status'],  # RETRIEVAL, ADMET_SCREENING, etc. | success, failure
    buckets=[1, 5, 10, 30, 60, 120, 300, 600, 1800]  # 1s to 30 min
)

phase_molecules_processed = Histogram(
    'novomcp_phase_molecules_processed',
    'Number of molecules processed in each phase',
    ['phase'],
    buckets=[10, 50, 100, 200, 500, 1000, 2000, 5000]
)

phase_pass_rate = Histogram(
    'novomcp_phase_pass_rate',
    'Pass rate (0.0-1.0) for quality gates',
    ['phase', 'gate_id'],
    buckets=[0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.0]
)

# ============================================================================
# QUALITY GATE METRICS
# ============================================================================

quality_gate_evaluations_total = Counter(
    'novomcp_quality_gate_evaluations_total',
    'Total number of quality gate evaluations',
    ['gate_id', 'passed']  # molecular_constraints, admet_filters, etc. | true, false
)

quality_gate_failures_total = Counter(
    'novomcp_quality_gate_failures_total',
    'Total number of quality gate failures by type',
    ['gate_id', 'failure_type']  # toxicity, binding, compliance, etc.
)

parameter_adjustments_total = Counter(
    'novomcp_parameter_adjustments_total',
    'Total number of parameter adjustments made',
    ['parameter', 'adjustment_type']  # increased, decreased, clamped
)

# ============================================================================
# SERVICE CALL METRICS
# ============================================================================

service_requests_total = Counter(
    'novomcp_service_requests_total',
    'Total number of service requests',
    ['service', 'endpoint', 'status']  # molecular-intelligence, /generate-batch, 200
)

service_request_duration_seconds = Histogram(
    'novomcp_service_request_duration_seconds',
    'Service request duration',
    ['service', 'endpoint'],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300]  # 100ms to 5 min
)

service_retries_total = Counter(
    'novomcp_service_retries_total',
    'Total number of service request retries',
    ['service', 'reason']  # timeout, 503, connection_error
)

service_retry_success_total = Counter(
    'novomcp_service_retry_success_total',
    'Number of successful retries',
    ['service', 'attempt']  # 2, 3
)

# ============================================================================
# EARLY EXIT METRICS
# ============================================================================

early_exit_triggered_total = Counter(
    'novomcp_early_exit_triggered_total',
    'Number of times early exit was triggered',
    ['reason']  # low_pass_rate, error
)

early_exit_queries_saved = Counter(
    'novomcp_early_exit_queries_saved',
    'Number of queries saved by early exit'
)

# ============================================================================
# DOCKING METRICS
# ============================================================================

docking_requests_total = Counter(
    'novomcp_docking_requests_total',
    'Total number of docking requests',
    ['status']  # success, failure, timeout
)

docking_duration_seconds = Histogram(
    'novomcp_docking_duration_seconds',
    'Docking operation duration',
    buckets=[10, 30, 60, 90, 120, 180, 300]  # 10s to 5 min
)

docking_concurrent_operations = Gauge(
    'novomcp_docking_concurrent_operations',
    'Number of concurrent docking operations'
)

docking_queue_depth = Gauge(
    'novomcp_docking_queue_depth',
    'Number of docking requests waiting in queue'
)

# ============================================================================
# DATABASE METRICS
# ============================================================================

db_queries_total = Counter(
    'novomcp_db_queries_total',
    'Total number of database queries',
    ['database', 'operation']  # research, SELECT/INSERT/UPDATE
)

db_query_duration_seconds = Histogram(
    'novomcp_db_query_duration_seconds',
    'Database query duration',
    ['database', 'operation'],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5]  # 1ms to 5s
)

db_connection_pool_size = Gauge(
    'novomcp_db_connection_pool_size',
    'Database connection pool size',
    ['database']
)

db_connection_pool_active = Gauge(
    'novomcp_db_connection_pool_active',
    'Number of active database connections',
    ['database']
)

# ============================================================================
# CACHE METRICS
# ============================================================================

cache_requests_total = Counter(
    'novomcp_cache_requests_total',
    'Total number of cache requests',
    ['cache_type', 'result']  # redis, hit/miss/error
)

cache_hit_rate = Gauge(
    'novomcp_cache_hit_rate',
    'Cache hit rate (0.0-1.0)',
    ['cache_type']
)

# ============================================================================
# CIRCUIT BREAKER METRICS
# ============================================================================

circuit_breaker_state = Gauge(
    'novomcp_circuit_breaker_state',
    'Circuit breaker state (0=closed, 1=open, 2=half_open)',
    ['service', 'phase']
)

circuit_breaker_failures_total = Counter(
    'novomcp_circuit_breaker_failures_total',
    'Total number of circuit breaker failures',
    ['service', 'phase']
)

circuit_breaker_trips_total = Counter(
    'novomcp_circuit_breaker_trips_total',
    'Number of times circuit breaker opened',
    ['service', 'phase']
)

# ============================================================================
# SYSTEM METRICS
# ============================================================================

system_info = Info(
    'novomcp_system',
    'System information'
)

# Set system info once at startup
system_info.info({
    'version': '2.0',
    'service': 'novomcp',
    'phase': 'phase-4-observability'
})

# ============================================================================
# HELPER DECORATORS
# ============================================================================

def track_phase_execution(phase: str):
    """
    Decorator to track phase execution time and status.

    Usage:
        @track_phase_execution("RETRIEVAL")
        async def execute_retrieval_phase():
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            status = "success"

            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                status = "failure"
                raise
            finally:
                duration = time.time() - start_time
                phase_execution_duration_seconds.labels(
                    phase=phase,
                    status=status
                ).observe(duration)

        return wrapper
    return decorator


def track_service_call(service: str, endpoint: str):
    """
    Decorator to track service call metrics.

    Usage:
        @track_service_call("molecular-intelligence", "/generate-batch")
        async def call_molecular_intelligence():
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            status = "200"

            try:
                result = await func(*args, **kwargs)

                # Extract status from result if available
                if isinstance(result, dict):
                    if result.get('status') == 'error':
                        status = result.get('status_code', '500')
                    elif 'status_code' in result:
                        status = str(result['status_code'])

                return result
            except Exception as e:
                status = "error"
                raise
            finally:
                duration = time.time() - start_time

                # Increment request counter
                service_requests_total.labels(
                    service=service,
                    endpoint=endpoint,
                    status=status
                ).inc()

                # Observe duration
                service_request_duration_seconds.labels(
                    service=service,
                    endpoint=endpoint
                ).observe(duration)

        return wrapper
    return decorator


# ============================================================================
# METRICS REPORTER
# ============================================================================

class MetricsReporter:
    """
    Helper class for reporting metrics from workflow operations.

    Usage:
        reporter = MetricsReporter()
        reporter.record_phase_execution("RETRIEVAL", duration=123.45, status="success")
        reporter.record_quality_gate("admet_filters", passed=False, failure_type="toxicity")
    """

    @staticmethod
    def record_phase_execution(phase: str, duration: float, status: str = "success", molecules_processed: int = 0):
        """Record phase execution metrics"""
        phase_execution_duration_seconds.labels(
            phase=phase,
            status=status
        ).observe(duration)

        if molecules_processed > 0:
            phase_molecules_processed.labels(phase=phase).observe(molecules_processed)

    @staticmethod
    def record_quality_gate(gate_id: str, passed: bool, failure_type: str = None, pass_rate: float = None):
        """Record quality gate evaluation"""
        quality_gate_evaluations_total.labels(
            gate_id=gate_id,
            passed=str(passed).lower()
        ).inc()

        if not passed and failure_type:
            quality_gate_failures_total.labels(
                gate_id=gate_id,
                failure_type=failure_type
            ).inc()

        if pass_rate is not None:
            phase_pass_rate.labels(
                phase="unknown",  # Set by caller if known
                gate_id=gate_id
            ).observe(pass_rate)

    @staticmethod
    def record_parameter_adjustment(parameter: str, adjustment_type: str):
        """Record parameter adjustment"""
        parameter_adjustments_total.labels(
            parameter=parameter,
            adjustment_type=adjustment_type
        ).inc()

    @staticmethod
    def record_service_retry(service: str, reason: str, success: bool = False, attempt: int = 2):
        """Record service retry attempt"""
        service_retries_total.labels(
            service=service,
            reason=reason
        ).inc()

        if success:
            service_retry_success_total.labels(
                service=service,
                attempt=str(attempt)
            ).inc()

    @staticmethod
    def record_early_exit(reason: str, queries_saved: int):
        """Record early exit event"""
        early_exit_triggered_total.labels(reason=reason).inc()
        early_exit_queries_saved.inc(queries_saved)

    @staticmethod
    def record_docking(duration: float, status: str = "success"):
        """Record docking operation"""
        docking_requests_total.labels(status=status).inc()
        docking_duration_seconds.observe(duration)

    @staticmethod
    def record_cache_request(cache_type: str, hit: bool):
        """Record cache request"""
        result = "hit" if hit else "miss"
        cache_requests_total.labels(
            cache_type=cache_type,
            result=result
        ).inc()


# Export metrics endpoint
def get_metrics():
    """Get Prometheus metrics in text format"""
    return generate_latest(REGISTRY)
