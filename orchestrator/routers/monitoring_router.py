"""
Monitoring Router
Phase 1, Week 3-4, Task 3.4: Extracted from ai_orchestration.py

Provides monitoring and health check endpoints:
- GET /status: Service status and capabilities
- GET /health: Health check endpoint
- GET /metrics: Prometheus metrics endpoint (proxy)
- GET /circuit-breakers: Circuit breaker states
"""

import os
import sys
import logging
from typing import Dict, Any
from fastapi import APIRouter

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.azure_openai_client import AzureOpenAIClient
from monitoring.circuit_breaker import get_circuit_manager
from monitoring.metrics import get_metrics_collector

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/monitoring", tags=["Monitoring"])

# Initialize monitoring components
azure_client = AzureOpenAIClient()


@router.get("/status")
async def get_status() -> Dict[str, Any]:
    """Get comprehensive service status and capabilities"""
    try:
        circuit_manager = get_circuit_manager()
        metrics_collector = get_metrics_collector()

        # Get circuit breaker summary
        circuit_breaker_status = {}
        if circuit_manager:
            for service_name in ['molecular-intelligence', 'admet-screening', 'autodock-gpu', 'quantum-validation', 'gromacs']:
                try:
                    breaker = circuit_manager.get_breaker(service_name)
                    circuit_breaker_status[service_name] = {
                        "state": breaker.state.name if hasattr(breaker, 'state') else "UNKNOWN",
                        "failure_count": breaker.failure_count if hasattr(breaker, 'failure_count') else 0,
                        "last_failure_time": breaker.last_failure_time.isoformat() if hasattr(breaker, 'last_failure_time') and breaker.last_failure_time else None
                    }
                except Exception as e:
                    circuit_breaker_status[service_name] = {"error": str(e)}

        return {
            "service": "novomcp",
            "version": "2.0.0",
            "status": "operational" if azure_client.available else "degraded",
            "components": {
                "azure_openai": {
                    "status": "operational" if azure_client.available else "degraded",
                    "details": azure_client.get_status()
                },
                "circuit_breakers": {
                    "status": "operational",
                    "services": circuit_breaker_status
                },
                "metrics_collector": {
                    "status": "operational" if metrics_collector else "unavailable"
                }
            },
            "capabilities": {
                "orchestration": azure_client.available,
                "intent_recognition": azure_client.available,
                "project_enrichment": azure_client.available,
                "workflow_suggestions": azure_client.available,
                "campaign_management": True,
                "websocket_streaming": True
            },
            "ai_model": {
                "deployment": azure_client.deployment_name,
                "context_window": "400K tokens" if "gpt-5" in azure_client.deployment_name else "128K tokens"
            }
        }

    except Exception as e:
        logger.error(f"Failed to get status: {str(e)}")
        return {
            "service": "novomcp",
            "status": "error",
            "error": str(e)
        }


@router.get("/health")
async def health_check() -> Dict[str, Any]:
    """
    Simple health check endpoint for load balancers.
    Returns 200 OK if service is running.
    """
    return {
        "status": "healthy",
        "service": "novomcp"
    }


@router.get("/ready")
async def readiness_check() -> Dict[str, Any]:
    """
    Readiness check for Kubernetes.
    Returns 200 if service is ready to accept traffic.
    """
    # Check if critical components are initialized
    ready = True
    components = {}

    try:
        # Check Azure OpenAI
        components["azure_openai"] = azure_client.available
        if not azure_client.available:
            ready = False

        # Check circuit breaker manager
        circuit_manager = get_circuit_manager()
        components["circuit_manager"] = circuit_manager is not None
        if not circuit_manager:
            ready = False

        # Check metrics collector
        metrics_collector = get_metrics_collector()
        components["metrics_collector"] = metrics_collector is not None
        if not metrics_collector:
            ready = False

    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        ready = False
        components["error"] = str(e)

    return {
        "ready": ready,
        "components": components
    }


@router.get("/circuit-breakers")
async def get_circuit_breaker_states() -> Dict[str, Any]:
    """
    Get current state of all circuit breakers.
    Useful for monitoring service health.
    """
    try:
        circuit_manager = get_circuit_manager()
        if not circuit_manager:
            return {
                "success": False,
                "error": "Circuit breaker manager not available"
            }

        services = [
            'molecular-intelligence',
            'admet-screening',
            'autodock-gpu',
            'quantum-validation',
            'gromacs',
            'faves-compliance',
            'optimization'
        ]

        circuit_states = {}
        for service_name in services:
            try:
                breaker = circuit_manager.get_breaker(service_name)
                circuit_states[service_name] = {
                    "state": breaker.state.name if hasattr(breaker, 'state') else "UNKNOWN",
                    "failure_count": breaker.failure_count if hasattr(breaker, 'failure_count') else 0,
                    "success_count": breaker.success_count if hasattr(breaker, 'success_count') else 0,
                    "last_failure_time": breaker.last_failure_time.isoformat() if hasattr(breaker, 'last_failure_time') and breaker.last_failure_time else None,
                    "half_open_attempts": breaker.half_open_attempts if hasattr(breaker, 'half_open_attempts') else 0,
                    "failure_threshold": breaker.failure_threshold if hasattr(breaker, 'failure_threshold') else None,
                    "recovery_timeout": breaker.recovery_timeout if hasattr(breaker, 'recovery_timeout') else None
                }
            except Exception as e:
                circuit_states[service_name] = {
                    "state": "ERROR",
                    "error": str(e)
                }

        # Calculate overall health
        total_services = len(services)
        healthy_services = sum(1 for state in circuit_states.values()
                              if state.get("state") == "CLOSED")
        degraded_services = sum(1 for state in circuit_states.values()
                               if state.get("state") == "HALF_OPEN")
        failed_services = sum(1 for state in circuit_states.values()
                             if state.get("state") == "OPEN")

        return {
            "success": True,
            "circuit_breakers": circuit_states,
            "summary": {
                "total_services": total_services,
                "healthy": healthy_services,
                "degraded": degraded_services,
                "failed": failed_services,
                "health_percentage": (healthy_services / total_services * 100) if total_services > 0 else 0
            }
        }

    except Exception as e:
        logger.error(f"Failed to get circuit breaker states: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


@router.post("/circuit-breakers/{service_name}/reset")
async def reset_circuit_breaker(service_name: str) -> Dict[str, Any]:
    """
    Manually reset a circuit breaker to CLOSED state.
    Use when service is confirmed healthy.
    """
    try:
        circuit_manager = get_circuit_manager()
        if not circuit_manager:
            return {
                "success": False,
                "error": "Circuit breaker manager not available"
            }

        breaker = circuit_manager.get_breaker(service_name)
        if hasattr(breaker, 'reset'):
            breaker.reset()
            logger.info(f"Circuit breaker for {service_name} manually reset")
            return {
                "success": True,
                "service": service_name,
                "message": "Circuit breaker reset to CLOSED state"
            }
        else:
            return {
                "success": False,
                "error": "Circuit breaker does not support reset"
            }

    except Exception as e:
        logger.error(f"Failed to reset circuit breaker for {service_name}: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/metrics/summary")
async def get_metrics_summary() -> Dict[str, Any]:
    """
    Get summary of key metrics from Prometheus.
    Provides quick overview of system performance.
    """
    try:
        metrics_collector = get_metrics_collector()
        if not metrics_collector:
            return {
                "success": False,
                "error": "Metrics collector not available"
            }

        # This would typically query Prometheus
        # For now, return a structure showing what's available
        return {
            "success": True,
            "message": "Metrics summary endpoint - integrate with Prometheus for real data",
            "available_metrics": [
                "novomcp_campaigns_active",
                "novomcp_iterations_total",
                "novomcp_molecules_processed_total",
                "novomcp_service_requests_total",
                "novomcp_circuit_breaker_state",
                "novomcp_quality_gate_evaluations_total"
            ],
            "note": "Use Grafana dashboards for full metrics visualization"
        }

    except Exception as e:
        logger.error(f"Failed to get metrics summary: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }
