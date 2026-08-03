"""
Scale-from-zero helper for GPU HTTP services on EKS.

Background
----------
The four GPU HTTP services (autodock-gpu, alphaflow, openfold3, gromacs-md)
default to `replicas=0` for cost (no GPU node burns money when nobody's
using the tool). cluster-autoscaler scales the GPU nodegroup 0→1 when a
Pending pod exists, but at `replicas=0` no pod is ever created — classic
chicken-and-egg. This helper closes it: on dispatch, if the target Service
has no ready endpoints, patch the Deployment to `replicas=1`, then poll the
EndpointSlice until ready (or timeout).

Two entry points
----------------
- `ensure_warm(service)` — blocking. Used inside synchronous dispatches that
  must reach the service before returning (e.g., the scaffolding behind a
  funnel pre-warm called early in a chain).
- `kickstart_warmup(service)` — fire-and-forget. Returns immediately after
  the scale patch lands. For "wake it up and tell the user to come back"
  flows used by tools that hand the LLM a `phase: warming, retry_after_s`
  envelope.

Both are idempotent. If the Deployment already has replicas≥1 and the
Service already has ≥1 ready endpoint, the call is a fast no-op.

Auth
----
Same pattern as `core/k8s_jobs.py`: raw httpx against the k8s API using the
in-cluster SA token + CA. The kubernetes Python client v30+ has a known
auth bug where in-cluster requests land as `system:anonymous`. Permissions
required: `get, patch` on `apps/v1/deployments`, `get` on `core/v1/services`
and `discovery.k8s.io/v1/endpointslices`. Wired via the
`novomcp-service-scaler` Role + RoleBinding in `default`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

SCALER_NAMESPACE = os.getenv("GPU_SERVICE_NAMESPACE", "default")
SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SA_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

# Default per-service warm timeout. GPU-node provisioning via cluster-autoscaler
# (~2 min) + image pull (~2 min, large CUDA + AmberTools or AlphaFold weights)
# + container start (~30s). Allow a generous ceiling.
DEFAULT_WARM_TIMEOUT_S = float(os.getenv("GPU_WARM_TIMEOUT_S", "360"))
DEFAULT_POLL_INTERVAL_S = float(os.getenv("GPU_WARM_POLL_S", "5"))


class ScalerError(RuntimeError):
    """Raised on unrecoverable scale/poll failure."""


class K8sServiceScaler:
    """Scale-aware dispatch helper for GPU HTTP services.

    Single instance per process; reuse the async httpx client.
    """

    def __init__(self, namespace: Optional[str] = None):
        self.namespace = namespace or SCALER_NAMESPACE
        self._http: Optional[httpx.AsyncClient] = None
        self._token: Optional[str] = None
        self._api_host: Optional[str] = None
        # In-process kickstart de-dupe: if we already triggered a warmup for a
        # service in the last `_warmup_ttl_s`, don't fire another scale patch.
        # Reset implicitly when the entry expires.
        self._warmup_ts: dict[str, float] = {}
        self._warmup_ttl_s = 30.0

    def _ensure_client(self):
        if self._http is not None:
            return
        try:
            with open(SA_TOKEN_PATH, "r") as f:
                self._token = f.read().strip()
        except FileNotFoundError:
            raise ScalerError(
                f"SA token not found at {SA_TOKEN_PATH} — not running in a cluster pod"
            )
        host = os.environ.get("KUBERNETES_SERVICE_HOST")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        if not host:
            raise ScalerError("KUBERNETES_SERVICE_HOST is unset — not running in a k8s pod")
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        self._api_host = f"https://{host}:{port}"
        self._http = httpx.AsyncClient(
            verify=SA_CA_PATH,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=15.0,
        )

    # ── Public surface ──────────────────────────────────────────────────────

    async def is_warm(self, service: str) -> bool:
        """Return True iff the Service has ≥1 ready endpoint."""
        self._ensure_client()
        return await self._has_ready_endpoint(service)

    async def kickstart_warmup(self, service: str) -> bool:
        """Fire-and-forget scale-to-1. Returns True if a scale patch was issued.

        Idempotent: if the Service already has ready endpoints, or a recent
        warmup was triggered within `_warmup_ttl_s`, this is a no-op.
        """
        self._ensure_client()
        now = time.monotonic()
        last = self._warmup_ts.get(service, 0.0)
        if now - last < self._warmup_ttl_s:
            return False
        if await self._has_ready_endpoint(service):
            return False
        try:
            await self._scale_to_at_least_one(service)
            self._warmup_ts[service] = now
            logger.info("kickstart_warmup: scaled %s/%s to 1", self.namespace, service)
            return True
        except Exception as e:
            logger.warning("kickstart_warmup failed for %s: %s", service, e)
            return False

    async def scale_to_zero(self, service: str) -> bool:
        """Scale a GPU service Deployment back to replicas=0 (the idle-reaper
        half of scale-from-zero). Returns True iff a scale-down patch was issued.

        No-op (returns False) when the Deployment is already at 0 or at N>1 (so
        we never stomp a manual bump). Same `deployments/scale` RBAC as warmup.
        The caller (the idle reaper) is responsible for the idle/in-flight check;
        this method only performs the patch. Never raises — a failed reap must
        not disrupt request serving (a warmup will re-wake on the next call).
        """
        self._ensure_client()
        try:
            scaled = await self._scale_to_zero(service)
            if scaled:
                # Drop any kickstart de-dupe stamp so an immediate next request
                # re-fires warmup rather than assuming it's still warm.
                self._warmup_ts.pop(service, None)
                logger.info("scale_to_zero: scaled %s/%s to 0 (idle)", self.namespace, service)
            return scaled
        except Exception as e:
            logger.warning("scale_to_zero failed for %s: %s", service, e)
            return False

    async def ensure_warm(
        self,
        service: str,
        timeout_s: float = DEFAULT_WARM_TIMEOUT_S,
        poll_s: float = DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        """Block until the Service has a ready endpoint, scaling if needed.

        Raises ScalerError on timeout.
        """
        self._ensure_client()
        if await self._has_ready_endpoint(service):
            return
        await self._scale_to_at_least_one(service)
        self._warmup_ts[service] = time.monotonic()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if await self._has_ready_endpoint(service):
                return
            await asyncio.sleep(poll_s)
        raise ScalerError(
            f"Service {self.namespace}/{service} did not become ready within {timeout_s:.0f}s"
        )

    async def close(self):
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ── Internals ───────────────────────────────────────────────────────────

    async def _has_ready_endpoint(self, service: str) -> bool:
        """True iff at least one EndpointSlice for `service` reports a ready address.

        EndpointSlice (discovery.k8s.io/v1) supersedes legacy Endpoints
        (deprecated in k8s 1.33+). Slices are labelled with
        `kubernetes.io/service-name=<service>`.
        """
        url = (
            f"{self._api_host}/apis/discovery.k8s.io/v1/namespaces/{self.namespace}"
            f"/endpointslices?labelSelector=kubernetes.io/service-name%3D{service}"
        )
        try:
            resp = await self._http.get(url)
        except httpx.HTTPError as e:
            raise ScalerError(f"endpointslices GET failed for {service}: {e}") from e
        if resp.status_code != 200:
            raise ScalerError(
                f"endpointslices GET {service} returned {resp.status_code}: {resp.text[:200]}"
            )
        items = resp.json().get("items", []) or []
        for slice_ in items:
            for ep in slice_.get("endpoints", []) or []:
                cond = ep.get("conditions") or {}
                if cond.get("ready") is True:
                    return True
        return False

    async def _scale_to_at_least_one(self, service: str) -> None:
        """Patch the Deployment's `spec.replicas` to 1 if currently 0.

        Uses the scale subresource (apps/v1) so we only need
        `patch deployments/scale` permission, not patch on the full Deployment.
        """
        # Read current desired replicas first; only patch if 0 to avoid
        # surprising someone who's manually bumped it to N>1.
        scale_url = (
            f"{self._api_host}/apis/apps/v1/namespaces/{self.namespace}"
            f"/deployments/{service}/scale"
        )
        try:
            resp = await self._http.get(scale_url)
        except httpx.HTTPError as e:
            raise ScalerError(f"deployments/scale GET failed for {service}: {e}") from e
        if resp.status_code == 404:
            raise ScalerError(
                f"Deployment {self.namespace}/{service} not found — cannot warm"
            )
        if resp.status_code != 200:
            raise ScalerError(
                f"deployments/scale GET {service} returned {resp.status_code}: {resp.text[:200]}"
            )
        current = (resp.json().get("spec") or {}).get("replicas", 0)
        if current and current >= 1:
            return  # someone else already scaled it; we'll just wait
        # Merge-patch to set replicas=1
        patch_body = {"spec": {"replicas": 1}}
        try:
            resp = await self._http.patch(
                scale_url,
                content=json.dumps(patch_body).encode(),
                headers={"Content-Type": "application/merge-patch+json"},
            )
        except httpx.HTTPError as e:
            raise ScalerError(f"deployments/scale PATCH failed for {service}: {e}") from e
        if resp.status_code not in (200, 201):
            raise ScalerError(
                f"deployments/scale PATCH {service} returned {resp.status_code}: {resp.text[:200]}"
            )

    async def _scale_to_zero(self, service: str) -> bool:
        """Patch the Deployment's `spec.replicas` to 0 — only if currently ==1.

        Reads current replicas first: skip if already 0, and skip if N>1 (a
        manual/operator bump we must not override). Returns True iff patched.
        Uses the scale subresource (apps/v1) — `patch deployments/scale`.
        """
        scale_url = (
            f"{self._api_host}/apis/apps/v1/namespaces/{self.namespace}"
            f"/deployments/{service}/scale"
        )
        try:
            resp = await self._http.get(scale_url)
        except httpx.HTTPError as e:
            raise ScalerError(f"deployments/scale GET failed for {service}: {e}") from e
        if resp.status_code == 404:
            raise ScalerError(f"Deployment {self.namespace}/{service} not found")
        if resp.status_code != 200:
            raise ScalerError(
                f"deployments/scale GET {service} returned {resp.status_code}: {resp.text[:200]}"
            )
        current = (resp.json().get("spec") or {}).get("replicas", 0)
        if current != 1:
            # 0 → nothing to do; >1 → operator intent, leave it alone.
            return False
        patch_body = {"spec": {"replicas": 0}}
        try:
            resp = await self._http.patch(
                scale_url,
                content=json.dumps(patch_body).encode(),
                headers={"Content-Type": "application/merge-patch+json"},
            )
        except httpx.HTTPError as e:
            raise ScalerError(f"deployments/scale PATCH(0) failed for {service}: {e}") from e
        if resp.status_code not in (200, 201):
            raise ScalerError(
                f"deployments/scale PATCH(0) {service} returned {resp.status_code}: {resp.text[:200]}"
            )
        return True


# Module-level singleton — instantiate lazily via `get_scaler()`.
_scaler_singleton: Optional[K8sServiceScaler] = None


def get_scaler() -> K8sServiceScaler:
    global _scaler_singleton
    if _scaler_singleton is None:
        _scaler_singleton = K8sServiceScaler()
    return _scaler_singleton
