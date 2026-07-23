"""
Service Health Check Router
Tests all microservices in the orchestration chain
"""

from fastapi import APIRouter, HTTPException, Request
from typing import Dict, Any, List
import logging
import asyncio
import httpx
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter()


# Critical services for campaign orchestration
ORCHESTRATION_SERVICES = {
    "drugsynthmc": {
        "name": "DrugSynthMC",
        "description": "Molecule generation engine",
        "critical": True,
        "test_endpoint": "/drugsynthmc/health",
        "test_payload": None,
        "expected_keys": ["status", "service"]
    },
    "chem-props": {
        "name": "Chemical Properties",
        "description": "Property calculations (MW, LogP, TPSA, etc.)",
        "critical": True,
        "test_endpoint": "/chem-props/health",
        "test_payload": None,
        "expected_keys": ["status"]
    },
    "addie": {
        "name": "ADDIE Models",
        "description": "ADMET prediction",
        "critical": True,
        "test_endpoint": "/addie/health",
        "test_payload": None,
        "expected_keys": ["status"]
    },
    "molecular-intelligence": {
        "name": "Molecular Intelligence",
        "description": "Advanced generation and expansion",
        "critical": False,
        "test_endpoint": "/molecular-intelligence/health",
        "test_payload": None,
        "expected_keys": ["status"]
    },
    "molmim-optimizer": {
        "name": "MolMIM Optimizer",
        "description": "Lead optimization",
        "critical": True,
        "test_endpoint": "/molmim/health",
        "test_payload": None,
        "expected_keys": ["status"]
    },
    "autodock-gpu": {
        "name": "AutoDock GPU",
        "description": "Molecular docking",
        "critical": False,
        "test_endpoint": "/autodock/health",
        "test_payload": None,
        "expected_keys": ["status"]
    },
    "gromacs-md": {
        "name": "GROMACS MD",
        "description": "Molecular dynamics simulations",
        "critical": False,
        "test_endpoint": "/gromacs/health",
        "test_payload": None,
        "expected_keys": ["status"]
    },
    "faves-compliance": {
        "name": "FAVES Compliance",
        "description": "Safety and compliance validation",
        "critical": True,
        "test_endpoint": "/faves/health",
        "test_payload": None,
        "expected_keys": ["status"]
    },
    "zinc-integration": {
        "name": "ZINC Integration",
        "description": "ZINC database search",
        "critical": False,
        "test_endpoint": "/zinc/health",
        "test_payload": None,
        "expected_keys": ["status"]
    },
    "tdc-integration": {
        "name": "TDC Integration",
        "description": "TDC benchmarking",
        "critical": False,
        "test_endpoint": "/tdc/health",
        "test_payload": None,
        "expected_keys": ["status"]
    },
    "knowledge-graph": {
        "name": "Knowledge Graph",
        "description": "Molecular relationship mapping",
        "critical": False,
        "test_endpoint": "/knowledge-graph/health",
        "test_payload": None,
        "expected_keys": ["status"]
    },
    "molecular-worker": {
        "name": "Molecular Worker",
        "description": "Async job processing",
        "critical": True,
        "test_endpoint": "/molecular-worker/health",
        "test_payload": None,
        "expected_keys": ["status"]
    },
    "db-manager": {
        "name": "DB Manager",
        "description": "WRITE operations (INSERT/UPDATE/DELETE)",
        "critical": True,
        "test_endpoint": "/db/health",
        "test_payload": None,
        "expected_keys": ["status"]
    }
}


async def _test_service(service_name: str, config: Dict, timeout: int = 10) -> Dict[str, Any]:
    """
    Test a single internal service health endpoint.

    IMPORTANT: This runs INSIDE NovoMCP container, so it can directly
    access internal ALBs within the VPC (vpc-0fff44aeba25f75dc).
    """
    result = {
        "service": service_name,
        "name": config["name"],
        "critical": config["critical"],
        "status": "unknown",
        "response_time_ms": None,
        "error": None,
        "details": {},
        "alb_url": None
    }

    try:
        # Get service URL from service registry
        from service_config import get_service_config
        from config import settings

        service_config_manager = get_service_config()
        service_cfg = service_config_manager.get_service_config(service_name)

        if not service_cfg:
            # Fallback to settings
            service_info = settings.SERVICES.get(service_name)
            if not service_info:
                result["status"] = "not_configured"
                result["error"] = "Service not found in configuration"
                return result
            base_url = service_info.get("url")
            api_key = service_info.get("api_key")
        else:
            base_url = service_cfg.get("url")
            api_key = service_cfg.get("api_key")

        if not base_url:
            result["status"] = "not_configured"
            result["error"] = "No URL configured"
            return result

        result["alb_url"] = base_url

        # Build full URL (internal ALB)
        endpoint = config["test_endpoint"]
        full_url = f"{base_url}{endpoint}"

        # Make request to internal service
        # This works because NovoMCP is in the same VPC
        start_time = datetime.utcnow()
        from config import settings as _settings
        async with httpx.AsyncClient(timeout=timeout, verify=_settings.httpx_verify) as client:
            headers = {}
            if api_key:
                headers["X-API-Key"] = api_key.strip()

            response = await client.get(full_url, headers=headers)

        end_time = datetime.utcnow()
        response_time = (end_time - start_time).total_seconds() * 1000

        result["response_time_ms"] = round(response_time, 2)

        # Check response
        if response.status_code == 200:
            try:
                data = response.json()
                result["details"] = data

                # Verify expected keys
                expected_keys = config.get("expected_keys", [])
                if all(key in data for key in expected_keys):
                    result["status"] = "healthy"
                else:
                    result["status"] = "degraded"
                    result["error"] = f"Missing expected keys: {expected_keys}"

            except Exception as e:
                result["status"] = "degraded"
                result["error"] = f"Invalid JSON response: {str(e)}"
                result["details"] = {"raw_response": response.text[:500]}
        else:
            result["status"] = "unhealthy"
            result["error"] = f"HTTP {response.status_code}: {response.text[:200]}"

    except httpx.TimeoutException:
        result["status"] = "timeout"
        result["error"] = f"Request timed out after {timeout}s"
    except httpx.ConnectError as e:
        result["status"] = "unreachable"
        result["error"] = f"Connection failed: {str(e)}"
        result["note"] = "Check if service is deployed, ALB is configured, and security group allows traffic"
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    return result


@router.get("/service-health/check-all")
async def check_all_services(request: Request = None):
    """
    Check health of all orchestration services.
    Tests each service in parallel and returns comprehensive status.
    """
    try:
        logger.info("Starting comprehensive service health check...")

        # Test all services in parallel
        tasks = [
            _test_service(service_name, config)
            for service_name, config in ORCHESTRATION_SERVICES.items()
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        service_results = []
        for result in results:
            if isinstance(result, Exception):
                service_results.append({
                    "service": "unknown",
                    "status": "error",
                    "error": str(result)
                })
            else:
                service_results.append(result)

        # Calculate summary
        total = len(service_results)
        healthy = sum(1 for r in service_results if r["status"] == "healthy")
        degraded = sum(1 for r in service_results if r["status"] == "degraded")
        unhealthy = sum(1 for r in service_results if r["status"] in ["unhealthy", "timeout", "unreachable", "error"])
        not_configured = sum(1 for r in service_results if r["status"] == "not_configured")

        critical_services = [r for r in service_results if r.get("critical", False)]
        critical_healthy = sum(1 for r in critical_services if r["status"] == "healthy")
        critical_total = len(critical_services)

        overall_status = "healthy"
        if critical_healthy < critical_total:
            overall_status = "critical"
        elif unhealthy > 0:
            overall_status = "degraded"
        elif degraded > 0:
            overall_status = "warning"

        return {
            "status": overall_status,
            "timestamp": datetime.utcnow().isoformat(),
            "summary": {
                "total_services": total,
                "healthy": healthy,
                "degraded": degraded,
                "unhealthy": unhealthy,
                "not_configured": not_configured,
                "critical_services": {
                    "total": critical_total,
                    "healthy": critical_healthy,
                    "unhealthy": critical_total - critical_healthy
                }
            },
            "services": service_results,
            "recommendations": _generate_recommendations(service_results)
        }

    except Exception as e:
        logger.error(f"Service health check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/service-health/test-pipeline")
async def test_molecule_pipeline(request: Request = None):
    """
    Test the complete molecule generation pipeline with a real molecule.
    This simulates what happens during campaign execution.
    """
    try:
        test_smiles = "CC(C)Cc1ccc(cc1)C(C)C(O)=O"  # Ibuprofen - simple test molecule

        pipeline_results = {
            "test_molecule": test_smiles,
            "timestamp": datetime.utcnow().isoformat(),
            "steps": []
        }

        # Step 1: Generate (skip, using test molecule)
        pipeline_results["steps"].append({
            "step": 1,
            "service": "drugsynthmc",
            "action": "generate",
            "status": "skipped",
            "note": "Using test molecule instead"
        })

        # Step 2: Calculate properties
        try:
            from routers.proxy import proxy_request
            props_result = await proxy_request(
                "chem-props",
                "/chem-props/calculate",
                request,
                "POST",
                {"smiles": test_smiles}
            )
            pipeline_results["steps"].append({
                "step": 2,
                "service": "chem-props",
                "action": "calculate_properties",
                "status": "success" if props_result else "failed",
                "result": props_result
            })
        except Exception as e:
            pipeline_results["steps"].append({
                "step": 2,
                "service": "chem-props",
                "action": "calculate_properties",
                "status": "error",
                "error": str(e)
            })

        # Step 3: ADDIE screening (ADMET prediction)
        try:
            addie_result = await proxy_request(
                "addie",
                "/addie/predict",
                request,
                "POST",
                {"smiles": test_smiles}
            )
            pipeline_results["steps"].append({
                "step": 3,
                "service": "addie",
                "action": "admet_prediction",
                "status": "success" if addie_result else "failed",
                "result": addie_result
            })
        except Exception as e:
            pipeline_results["steps"].append({
                "step": 3,
                "service": "addie",
                "action": "admet_prediction",
                "status": "error",
                "error": str(e)
            })

        # Step 4: FAVES compliance
        try:
            faves_result = await proxy_request(
                "faves-compliance",
                "/faves/validate",
                request,
                "POST",
                {"smiles": test_smiles}
            )
            pipeline_results["steps"].append({
                "step": 4,
                "service": "faves-compliance",
                "action": "compliance_check",
                "status": "success" if faves_result else "failed",
                "result": faves_result
            })
        except Exception as e:
            pipeline_results["steps"].append({
                "step": 4,
                "service": "faves-compliance",
                "action": "compliance_check",
                "status": "error",
                "error": str(e)
            })

        # Calculate pipeline health
        successful_steps = sum(1 for s in pipeline_results["steps"] if s["status"] == "success")
        total_steps = len(pipeline_results["steps"])

        pipeline_results["overall_status"] = "healthy" if successful_steps == total_steps else "degraded"
        pipeline_results["success_rate"] = f"{successful_steps}/{total_steps}"

        return pipeline_results

    except Exception as e:
        logger.error(f"Pipeline test failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _generate_recommendations(results: List[Dict]) -> List[str]:
    """Generate actionable recommendations based on health check results"""
    recommendations = []

    # Check critical services
    critical_down = [r for r in results if r.get("critical") and r["status"] not in ["healthy", "degraded"]]
    if critical_down:
        recommendations.append(
            f"🚨 CRITICAL: {len(critical_down)} critical services down: {', '.join(r['name'] for r in critical_down)}"
        )

    # Check for timeouts
    timeouts = [r for r in results if r["status"] == "timeout"]
    if timeouts:
        recommendations.append(
            f"⚠️ {len(timeouts)} services timing out - check resource allocation: {', '.join(r['name'] for r in timeouts)}"
        )

    # Check for not configured
    not_configured = [r for r in results if r["status"] == "not_configured"]
    if not_configured:
        recommendations.append(
            f"⚙️ {len(not_configured)} services not configured: {', '.join(r['name'] for r in not_configured)}"
        )

    # Check for unreachable
    unreachable = [r for r in results if r["status"] == "unreachable"]
    if unreachable:
        recommendations.append(
            f"🔌 {len(unreachable)} services unreachable - check network/ALB: {', '.join(r['name'] for r in unreachable)}"
        )

    # Slow services
    slow = [r for r in results if r.get("response_time_ms") is not None and r.get("response_time_ms") > 1000]
    if slow:
        recommendations.append(
            f"🐌 {len(slow)} services responding slowly (>1s): {', '.join(r['name'] for r in slow)}"
        )

    if not recommendations:
        recommendations.append("✅ All services operational - orchestration pipeline ready")

    return recommendations


@router.get("/service-health/critical-only")
async def check_critical_services(request: Request = None):
    """Check only critical services (faster check for monitoring)"""
    try:
        critical_services = {
            name: config for name, config in ORCHESTRATION_SERVICES.items()
            if config.get("critical", False)
        }

        tasks = [
            _test_service(service_name, config)
            for service_name, config in critical_services.items()
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        healthy = sum(1 for r in results if not isinstance(r, Exception) and r["status"] == "healthy")
        total = len(results)

        return {
            "status": "healthy" if healthy == total else "critical",
            "critical_services": {
                "healthy": healthy,
                "total": total
            },
            "services": [r for r in results if not isinstance(r, Exception)]
        }

    except Exception as e:
        logger.error(f"Critical services check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
