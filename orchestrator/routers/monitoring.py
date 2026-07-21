"""
Monitoring Endpoints for Production Health and Metrics
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import Dict, Any
import logging
import asyncio
from monitoring.circuit_breaker import get_circuit_manager
from monitoring.metrics import get_metrics_collector
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/monitoring", tags=["Monitoring"])

# Track if metrics broadcasting is running
_metrics_broadcast_task = None


@router.get("/health")
async def health_check() -> Dict[str, Any]:
    """Basic health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "novomcp"
    }


@router.get("/metrics")
async def get_metrics() -> Dict[str, Any]:
    """Get comprehensive metrics summary"""
    try:
        metrics = get_metrics_collector()
        return metrics.get_summary()
    except Exception as e:
        logger.error(f"Failed to get metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/metrics/prometheus")
async def get_prometheus_metrics() -> str:
    """Get metrics in Prometheus format"""
    try:
        metrics = get_metrics_collector()
        return metrics.export_metrics()
    except Exception as e:
        logger.error(f"Failed to export metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/circuit-breakers")
async def get_circuit_breaker_status() -> Dict[str, Any]:
    """Get status of all circuit breakers"""
    try:
        manager = get_circuit_manager()
        return manager.get_all_stats()
    except Exception as e:
        logger.error(f"Failed to get circuit breaker status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/circuit-breakers/reset")
async def reset_circuit_breakers(service: str = None) -> Dict[str, Any]:
    """Reset circuit breakers"""
    try:
        manager = get_circuit_manager()

        if service:
            breaker = manager.get_breaker(service)
            breaker.reset()
            message = f"Reset circuit breaker for {service}"
        else:
            manager.reset_all()
            message = "Reset all circuit breakers"

        return {
            "status": "success",
            "message": message,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to reset circuit breakers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/service-stats/{service_name}")
async def get_service_statistics(service_name: str) -> Dict[str, Any]:
    """Get statistics for a specific service"""
    try:
        metrics = get_metrics_collector()
        stats = metrics.get_service_stats(service_name)

        if not stats:
            raise HTTPException(status_code=404, detail=f"No stats for service {service_name}")

        return stats
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get service stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/campaign-stats/{campaign_id}")
async def get_campaign_statistics(campaign_id: str = None) -> Dict[str, Any]:
    """Get campaign statistics"""
    try:
        metrics = get_metrics_collector()
        return metrics.get_campaign_stats(campaign_id)
    except Exception as e:
        logger.error(f"Failed to get campaign stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/metrics/clear")
async def clear_old_metrics(hours: int = 24) -> Dict[str, Any]:
    """Clear metrics older than specified hours"""
    try:
        metrics = get_metrics_collector()
        metrics.clear_old_metrics(hours)

        return {
            "status": "success",
            "message": f"Cleared metrics older than {hours} hours",
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to clear metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/readiness")
async def readiness_check() -> Dict[str, Any]:
    """
    Readiness check for deployment.
    Checks if critical services are available.
    """
    try:
        from service_config import get_service_config
        from ai.azure_openai_client import get_azure_client

        service_config = get_service_config()
        azure_client = get_azure_client()

        # Check critical dependencies
        checks = {
            "azure_openai": azure_client.available if azure_client else False,
            "service_config": bool(service_config.get_all_services()),
        }

        # Check circuit breakers
        manager = get_circuit_manager()
        breaker_stats = manager.get_all_stats()
        open_circuits = [
            name for name, stats in breaker_stats.items()
            if stats.get('state') == 'open'
        ]

        checks["open_circuits"] = len(open_circuits)

        # Determine overall readiness
        ready = (
            checks["azure_openai"] and
            checks["service_config"] and
            len(open_circuits) < 3  # Allow some circuits to be open
        )

        return {
            "ready": ready,
            "checks": checks,
            "open_circuits": open_circuits,
            "timestamp": datetime.utcnow().isoformat()
        }

    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        return {
            "ready": False,
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }


async def periodic_metrics_broadcast():
    """
    Periodically broadcast system metrics to all connected WebSocket clients.
    Runs every 5 seconds to provide real-time metrics updates.
    """
    from routers.ai_orchestration import broadcast_global_update

    logger.info("Starting periodic metrics broadcast")

    while True:
        try:
            await asyncio.sleep(5)  # Broadcast every 5 seconds

            # Collect current metrics
            metrics_collector = get_metrics_collector()

            # Get aggregated metrics
            summary = metrics_collector.get_summary()
            service_stats = metrics_collector.get_service_stats()
            timing_stats = metrics_collector.get_timing_stats()

            # Prepare broadcast payload (without circuit breakers to prevent trade secret exposure)
            metrics_update = {
                "uptime_seconds": summary.get("uptime_seconds", 0),
                "service_stats": {
                    "total_calls": service_stats.get("total_calls", 0),
                    "success_rate": service_stats.get("success_rate", 1.0),
                    "avg_duration_ms": service_stats.get("avg_duration_ms", 0),
                    "by_service": service_stats.get("by_service", {})
                },
                "campaign_stats": summary.get("campaign_stats", {}),
                "timing_percentiles": {
                    service: {
                        "p50_ms": stats.get("p50_ms", 0),
                        "p95_ms": stats.get("p95_ms", 0),
                        "p99_ms": stats.get("p99_ms", 0),
                        "count": stats.get("count", 0)
                    }
                    for service, stats in timing_stats.items()
                },
                "recent_insights": summary.get("recent_insights", []),
                "counters": summary.get("counters", {}),
                "gauges": summary.get("gauges", {})
            }

            # Broadcast to all global WebSocket connections
            await broadcast_global_update("system_metrics", metrics_update)

        except Exception as e:
            logger.error(f"Error in periodic metrics broadcast: {e}")
            await asyncio.sleep(5)  # Continue even on error


@router.post("/metrics/broadcast/start")
async def start_metrics_broadcast(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """
    Start periodic metrics broadcasting via WebSocket.
    Metrics will be broadcast every 5 seconds to all connected clients.
    """
    global _metrics_broadcast_task

    if _metrics_broadcast_task and not _metrics_broadcast_task.done():
        return {
            "status": "already_running",
            "message": "Metrics broadcasting is already active",
            "timestamp": datetime.utcnow().isoformat()
        }

    # Start background task
    _metrics_broadcast_task = asyncio.create_task(periodic_metrics_broadcast())

    logger.info("Started periodic metrics broadcast")

    return {
        "status": "started",
        "message": "Periodic metrics broadcasting started (5s interval)",
        "timestamp": datetime.utcnow().isoformat()
    }


@router.post("/metrics/broadcast/stop")
async def stop_metrics_broadcast() -> Dict[str, Any]:
    """Stop periodic metrics broadcasting"""
    global _metrics_broadcast_task

    if _metrics_broadcast_task and not _metrics_broadcast_task.done():
        _metrics_broadcast_task.cancel()
        _metrics_broadcast_task = None

        logger.info("Stopped periodic metrics broadcast")

        return {
            "status": "stopped",
            "message": "Metrics broadcasting stopped",
            "timestamp": datetime.utcnow().isoformat()
        }

    return {
        "status": "not_running",
        "message": "Metrics broadcasting was not active",
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/metrics/broadcast/status")
async def get_broadcast_status() -> Dict[str, Any]:
    """Check if metrics broadcasting is active"""
    global _metrics_broadcast_task

    is_running = _metrics_broadcast_task and not _metrics_broadcast_task.done()

    return {
        "broadcasting": is_running,
        "interval_seconds": 5 if is_running else None,
        "timestamp": datetime.utcnow().isoformat()
    }