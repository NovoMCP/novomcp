"""
ServiceProxy - Direct HTTP client for calling microservices

This bypasses the orchestrate_decision recursion by making DIRECT HTTP calls to services.
Used by workflow_engine.py to fix the recursion bug.

PHASE 1 FIX: Added retry logic with exponential backoff and service-specific timeouts
"""

import os
import httpx
import logging
import asyncio
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# PHASE 1 FIX: Service-specific timeout configuration
# Critical for preventing timeout failures on long-running operations
SERVICE_TIMEOUTS = {
    "molecular-intelligence": 180,  # PyArrow queries can take 2-3 min
    "faves-compliance": 120,  # Compliance checks are fast
    "autodock-gpu": 300,  # Docking with exhaustiveness=32 needs 5 min
    "gromacs-md": 600,  # MD simulations need 10 min
    "novo-quantum": 1800,  # Quantum jobs need 30 min (Azure)
    "lead-optimization": 180,  # Optimization is moderately fast
    "molmim-optimizer": 240,  # MolMIM can be slow
    "knowledge-graph": 120,  # Knowledge queries are fast
    "tdc-integration": 120,  # TDC queries are fast
    "openfold3": 900,  # Protein structure prediction can take 15 min
    "default": 120  # Fallback for unknown services
}

# PHASE 1 FIX: Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # Exponential: 2s, 4s, 8s
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}  # Retry on these HTTP codes


class ServiceProxy:
    """
    Direct HTTP client for microservice calls.
    NO recursion - makes direct HTTP requests to service endpoints.
    """

    def __init__(self):
        """Initialize with service URLs and API keys from environment"""
        # Service-specific API keys (each microservice has its own key)
        self.api_keys = {
            "molecular-intelligence": os.getenv("MOL_INTEL_API_KEY") or os.getenv("MOLECULAR_INTELLIGENCE_API_KEY"),
            # Support common alias env var names for compliance/autodock
            "faves-compliance": os.getenv("NOVOMCP_COMPLIANCE_API_KEY"),
            "autodock-gpu": os.getenv("AUTODOCK_GPU_API_KEY") or os.getenv("AUTODOCK_API_KEY"),
            "gromacs-md": os.getenv("GROMACS_API_KEY"),
            "novo-quantum": os.getenv("NOVO_QUANTUM_API_KEY"),
            "lead-optimization": os.getenv("LEAD_OPT_API_KEY"),
            "molmim-optimizer": os.getenv("MOLMIM_OPTIMIZER_API_KEY"),
            "knowledge-graph": os.getenv("KNOWLEDGE_GRAPH_API_KEY"),
            "tdc-integration": os.getenv("TDC_INTEGRATION_API_KEY"),
            "openfold3": os.getenv("OPENFOLD3_API_KEY"),
            "chem-props": os.getenv("CHEM_PROPS_API_KEY"),
            "addie-models": os.getenv("ADDIE_MODELS_API_KEY"),
            "novoexpert": os.getenv("NOVOEXPERT_API_KEY"),
        }

        # Warn early if critical API keys are missing (helps catch 401 loops)
        for svc in ("autodock-gpu", "faves-compliance"):
            if not self.api_keys.get(svc):
                logger.warning(f"ServiceProxy: Missing API key for {svc}. Set env var to avoid HTTP 401.")

        # Service URLs - preferring env vars, with internal ALB fallbacks
        self.service_urls = {
            "molecular-intelligence": os.getenv(
                "MOL_INTEL_URL",
                ""
            ),
            "faves-compliance": os.getenv("NOVOMCP_COMPLIANCE_URL")
                or os.getenv("NOVOMCP_MOLECULE_INDEX_URL")
                or "",
            "autodock-gpu": os.getenv("AUTODOCK_GPU_URL")
                or os.getenv("AUTODOCK_URL")
                or "",
            "gromacs-md": os.getenv(
                "GROMACS_URL",
                ""
            ),
            "novo-quantum": os.getenv(
                "NOVO_QUANTUM_URL",
                ""
            ),
            "lead-optimization": os.getenv(
                "LEAD_OPT_URL",
                ""
            ),
            "molmim-optimizer": os.getenv(
                "MOLMIM_OPTIMIZER_URL",
                ""
            ),
            "knowledge-graph": os.getenv(
                "KNOWLEDGE_GRAPH_URL",
                ""
            ),
            "tdc-integration": os.getenv(
                "TDC_INTEGRATION_URL",
                ""
            ),
            "openfold3": os.getenv(
                "OPENFOLD3_URL",
                ""
            ),
            "chem-props": os.getenv(
                "CHEM_PROPS_URL",
                ""
            ),
            "addie-models": os.getenv(
                "ADDIE_MODELS_URL",
                ""
            ),
            "novoexpert": os.getenv(
                "NOVOEXPERT_URL",
                ""
            ),
        }

        # PHASE 1 FIX: HTTP client without hardcoded timeout (set per-request)
        # Timeout is now service-specific (see SERVICE_TIMEOUTS config above)
        from config import settings as _settings
        self.client = httpx.AsyncClient(
            timeout=None,  # Set per-request based on service
            verify=_settings.httpx_verify,
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=50)  # Increased for parallel ops
        )

    async def call_service(
        self,
        service: str,
        endpoint: str,
        method: str = "POST",
        data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generic service call method with retry logic and service-specific timeouts.

        PHASE 1 FIX: Added exponential backoff retry on transient failures (timeout, 5xx errors)
        PHASE 1 FIX: Service-specific timeouts to prevent premature failures

        Args:
            service: Service name (e.g., "molecular-intelligence")
            endpoint: Endpoint path (e.g., "/generate-batch")
            method: HTTP method (default: POST)
            data: Request payload

        Returns:
            Service response as dict
        """
        if service not in self.service_urls:
            raise ValueError(f"Unknown service: {service}")

        url = f"{self.service_urls[service]}{endpoint}"

        # Use service-specific API key
        headers = {}
        service_api_key = self.api_keys.get(service)
        if service_api_key:
            # Some services expect "API-Key" header instead of "X-API-Key"
            header_name = "API-Key" if service in ("novo-quantum", "gromacs-md") else "X-API-Key"
            headers[header_name] = service_api_key

        # Get service-specific timeout
        timeout = SERVICE_TIMEOUTS.get(service, SERVICE_TIMEOUTS["default"])

        # PHASE 1 FIX: Retry loop with exponential backoff
        last_exception = None
        for attempt in range(MAX_RETRIES):
            try:
                logger.info(f"ServiceProxy: Calling {service}{endpoint} (attempt {attempt + 1}/{MAX_RETRIES}, timeout={timeout}s)")

                if method == "POST":
                    response = await self.client.post(url, json=data, headers=headers, timeout=timeout)
                elif method == "GET":
                    response = await self.client.get(url, headers=headers, timeout=timeout)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                # Check if we got a retryable status code
                if response.status_code in RETRYABLE_STATUS_CODES:
                    if attempt < MAX_RETRIES - 1:
                        wait_time = RETRY_BACKOFF_BASE ** attempt  # 2s, 4s, 8s
                        logger.warning(f"ServiceProxy: {service}{endpoint} returned {response.status_code}, retrying in {wait_time}s (attempt {attempt + 1}/{MAX_RETRIES})")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        # Last attempt, raise the error
                        response.raise_for_status()

                # Success (2xx status)
                response.raise_for_status()
                logger.info(f"ServiceProxy: {service}{endpoint} succeeded on attempt {attempt + 1}")
                return response.json()

            except httpx.TimeoutException as e:
                last_exception = e
                if attempt < MAX_RETRIES - 1:
                    wait_time = RETRY_BACKOFF_BASE ** attempt
                    logger.warning(f"ServiceProxy: {service}{endpoint} timeout ({timeout}s), retrying in {wait_time}s (attempt {attempt + 1}/{MAX_RETRIES})")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(f"ServiceProxy: {service}{endpoint} timed out after {MAX_RETRIES} attempts")
                    return {
                        "status": "error",
                        "message": f"Timeout after {MAX_RETRIES} attempts ({timeout}s each): {str(e)}",
                        "service": service,
                        "endpoint": endpoint,
                        "error_type": "timeout"
                    }

            except httpx.HTTPStatusError as e:
                last_exception = e
                # Only retry on specific status codes
                if e.response.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES - 1:
                    wait_time = RETRY_BACKOFF_BASE ** attempt
                    logger.warning(f"ServiceProxy: {service}{endpoint} HTTP {e.response.status_code}, retrying in {wait_time}s (attempt {attempt + 1}/{MAX_RETRIES})")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    # Log response body (truncated) to surface validation details (e.g., 422)
                    try:
                        body_snippet = e.response.text[:500]
                    except Exception:
                        body_snippet = "<no body>"
                    logger.error(
                        f"ServiceProxy HTTP error calling {service}{endpoint}: HTTP {e.response.status_code} - {body_snippet}"
                    )
                    # Provide a helpful hint for common auth/validation failures
                    hint = None
                    if e.response.status_code == 401:
                        hint = "Authentication failed. Ensure X-API-Key is set: set AUTODOCK_GPU_API_KEY or NOVOMCP_COMPLIANCE_API_KEY in environment or Secrets Manager."
                    elif e.response.status_code == 422:
                        hint = "Validation failed. Check payload schema (e.g., molecule.id must be a non-null string)."
                    return {
                        "status": "error",
                        "message": f"HTTP {e.response.status_code}: {e.response.text[:500]}",
                        "service": service,
                        "endpoint": endpoint,
                        "error_type": "http_error",
                        "status_code": e.response.status_code,
                        **({"hint": hint} if hint else {})
                    }

            except Exception as e:
                last_exception = e
                # Retry on connection errors, but not on ValueError (programming errors)
                if attempt < MAX_RETRIES - 1 and not isinstance(e, ValueError):
                    wait_time = RETRY_BACKOFF_BASE ** attempt
                    logger.warning(f"ServiceProxy: {service}{endpoint} error: {e}, retrying in {wait_time}s (attempt {attempt + 1}/{MAX_RETRIES})")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(f"ServiceProxy error calling {service}{endpoint}: {e}", exc_info=True)
                    return {
                        "status": "error",
                        "message": str(e),
                        "service": service,
                        "endpoint": endpoint,
                        "error_type": "exception"
                    }

        # Should never reach here, but just in case
        logger.error(f"ServiceProxy: {service}{endpoint} failed after {MAX_RETRIES} attempts")
        return {
            "status": "error",
            "message": f"Failed after {MAX_RETRIES} attempts: {str(last_exception)}",
            "service": service,
            "endpoint": endpoint,
            "error_type": "max_retries_exceeded"
        }

    # Service-specific convenience methods

    async def call_molecular_intelligence(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call molecular-intelligence to QUERY enriched PubChem dataset.

        IMPORTANT: This does NOT generate new molecules - it QUERIES/FILTERS existing compounds
        - 115M molecules with 53 pre-calculated columns (11 PubChem + 3 Chem-Props + 39 ADMET)
        - Uses PyArrow predicate pushdown filtering on S3 Parquet files
        - Returns molecules with ADMET data already calculated
        """
        return await self.call_service(
            "molecular-intelligence",
            "/molecular-intelligence/generate-batch",  # POST endpoint that queries enriched PubChem with all 53 columns
            method="POST",
            data=parameters
        )

    async def call_faves_compliance(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call FAVES for ethics/safety/regulatory compliance validation.

        FAVES = Fairness, Accountability, Validity, Ethics, Safety

        IMPORTANT: FAVES is NOT for ADMET pharmacokinetics
        - ADMET data comes pre-calculated from molecular-intelligence (Phase 1)
        - FAVES validates: ethics, safety, regulatory compliance (DEA, FDA, controlled substances)
        - Detects: toxic substances, explosives, structural alerts, bias, misuse risk
        """
        return await self.call_service(
            "faves-compliance",
            "/faves-compliance/assess",
            method="POST",
            data=parameters
        )

    async def call_autodock_gpu(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Call AutoDock-GPU for batch molecular docking"""
        return await self.call_service(
            "autodock-gpu",
            "/batch-dock",
            method="POST",
            data=parameters
        )

    async def call_gromacs_md(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Call GROMACS-MD for molecular dynamics simulation"""
        return await self.call_service(
            "gromacs-md",
            "/simulate",
            method="POST",
            data=parameters
        )

    async def call_novo_quantum(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Call Novo-Quantum for quantum VQE calculations (Azure)"""
        return await self.call_service(
            "novo-quantum",
            "/process",
            method="POST",
            data=parameters
        )

    async def call_lead_optimization(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Call Lead-Optimization service"""
        return await self.call_service(
            "lead-optimization",
            "/optimize",
            method="POST",
            data=parameters
        )

    async def call_chem_props(self, smiles: str) -> Dict[str, Any]:
        """Call chem-props for RDKit property calculation (SA score, Lipinski, QED, etc.)"""
        url = self.service_urls.get("chem-props", "")
        api_key = self.api_keys.get("chem-props")
        try:
            response = await self.client.post(
                f"{url}/chem-props/calculate_single",
                params={"smiles": smiles},
                headers={"X-API-Key": api_key or ""},
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"chem-props call failed for {smiles[:50]}: {e}")
            return {"error": str(e)}

    async def call_addie_models(self, smiles: str) -> Dict[str, Any]:
        """Call addie-models for ML-based ADMET prediction (31 models)"""
        import uuid as _uuid
        mol_id = str(_uuid.uuid4())[:8]
        try:
            result = await self.call_service(
                "addie-models",
                "/addie/process",
                method="POST",
                data={
                    "molecules": [{"id": mol_id, "smiles": smiles}],
                    "include_descriptors": True,
                    "include_confidence": True
                }
            )
            # Extract single molecule from batch response
            results = result.get("results", [])
            mol_result = next((r for r in results if r.get("id") == mol_id), {})
            return mol_result.get("predictions", {})
        except Exception as e:
            logger.warning(f"addie-models call failed for {smiles[:50]}: {e}")
            return {"error": str(e)}

    async def enrich_variants(self, variants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Enrich optimization variants with chem-props (SA/properties) and addie-models (ADMET).
        Called by workflow engine after lead-optimization generates candidates."""
        enriched = []
        for variant in variants:
            smiles = variant.get("smiles")
            if not smiles:
                continue

            # Call chem-props and addie-models in parallel
            chem_task = asyncio.ensure_future(self.call_chem_props(smiles))
            admet_task = asyncio.ensure_future(self.call_addie_models(smiles))

            chem_result, admet_result = await asyncio.gather(chem_task, admet_task)

            # Merge chem-props
            if "error" not in chem_result:
                props = chem_result.get("properties", {})
                variant["sa_score"] = props.get("synthetic_accessibility")
                variant["qed"] = props.get("qed", variant.get("qed"))
                variant["lipinski_violations"] = props.get("lipinski_violations")
                variant["drug_likeness"] = props.get("drug_likeness")
                variant["veber_score"] = props.get("veber_score")

            # Merge addie-models ADMET
            if "error" not in admet_result:
                variant["admet"] = admet_result

            enriched.append(variant)

        return enriched

    async def call_molmim_optimizer(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Call MolMIM optimizer service"""
        return await self.call_service(
            "molmim-optimizer",
            "/optimize",
            method="POST",
            data=parameters
        )

    async def call_knowledge_graph(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Call Knowledge-Graph for literature/knowledge validation"""
        return await self.call_service(
            "knowledge-graph",
            "/validate",
            method="POST",
            data=parameters
        )

    async def call_tdc_integration(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Call TDC Integration for therapeutic data validation"""
        return await self.call_service(
            "tdc-integration",
            "/validate",
            method="POST",
            data=parameters
        )

    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()
