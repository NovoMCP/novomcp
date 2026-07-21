"""
Kubernetes Jobs control plane for novomcp (AWS EKS port of azure_jobs.py).

Submits long-running GPU jobs (MD) as one-shot k8s Jobs in the
`default` namespace. Each submission creates a unique Job resource
named `<template>-<job_id_suffix>`. The Pod inherits its template from a
ConfigMap (`<template>-template`) that the owning service's deploy script
keeps in sync.

Interface mirrors azure_jobs.AzureJobsClient so tools.py callers only need
a one-line constructor swap.

Implementation note
-------------------
Uses `httpx` against the k8s API directly rather than the `kubernetes`
Python client. The v30+ client has a known auth bug where
`load_incluster_config()` loads the SA token into `Configuration.api_key`
but the per-call `ApiClient` doesn't forward it, so every request lands as
`system:anonymous` and gets 403. Raw httpx with the SA token header is
also a smaller surface — three endpoints (read configmap, create job,
delete job) vs the entire client library.

Auth
----
Uses the in-cluster ServiceAccount token (mounted at
`/var/run/secrets/kubernetes.io/serviceaccount/token`) and the API server
CA at the same prefix. The pod's SA needs a Role with `create, get, list,
watch, delete` on `jobs.batch` + read on `configmaps` in `default` —
wired via `novomcp-job-runner` Role + RoleBinding
(see novomcp/k8s/job-rbac.yaml).

Why not AWS Batch
-----------------
Batch adds an extra control-plane layer (Compute Environments, Job Queues,
Job Definitions) that buys us very little over native k8s Jobs since we're
already on EKS with the GPU nodegroup wired up. The k8s Job primitive maps
1:1 to Azure Container Apps Jobs semantics — `backoffLimit=1` matches
`replicaRetryLimit=1`, `activeDeadlineSeconds=21600` matches
`replica-timeout 21600`, pod tolerations/nodeSelectors handle GPU placement.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
from typing import Any, Dict, Optional

import httpx
import yaml

logger = logging.getLogger("novomcp.k8s_jobs")

# ConfigMap holding the Job template lives at <job_template_name>-template
# (matches the `${APP_NAME}-job-template` produced by deploy-on-bastion.sh
TEMPLATE_CM_SUFFIX = "-template"
TEMPLATE_KEY = "job-template.yaml"

# In-cluster paths set by the kubelet on every Pod.
SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SA_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"


class JobStartError(RuntimeError):
    """Raised when a Job creation fails (RBAC, missing template, API error)."""

    def __init__(self, message: str, status_code: Optional[int] = None, body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class K8sJobsClient:
    """Create + cancel k8s Jobs from ConfigMap-stored templates.

    Same interface as the previous AzureJobsClient; tools.py callers swap
    constructors and continue working.
    """

    def __init__(self, namespace: Optional[str] = None):
        self.namespace = namespace or JOB_NAMESPACE
        self._http: Optional[httpx.AsyncClient] = None
        self._token: Optional[str] = None
        self._api_host: Optional[str] = None

    # ── In-cluster auth + httpx client (lazy init) ──────────────────────────
    def _ensure_client(self):
        if self._http is not None:
            return
        try:
            with open(SA_TOKEN_PATH, "r") as f:
                self._token = f.read().strip()
        except FileNotFoundError:
            raise JobStartError(
                f"SA token not found at {SA_TOKEN_PATH} — running outside the cluster?"
            )
        host = os.environ.get("KUBERNETES_SERVICE_HOST")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        if not host:
            raise JobStartError(
                "KUBERNETES_SERVICE_HOST is unset — not running in a k8s pod"
            )
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        self._api_host = f"https://{host}:{port}"
        self._http = httpx.AsyncClient(
            verify=SA_CA_PATH,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=30.0,
        )

    # ── Template loading (read-only ConfigMap) ──────────────────────────────
    async def _load_template(self, job_template_name: str) -> Dict[str, Any]:
        """Fetch the Job template ConfigMap and return the parsed manifest dict."""
        self._ensure_client()
        cm_name = f"{job_template_name}{TEMPLATE_CM_SUFFIX}"
        url = (
            f"{self._api_host}/api/v1/namespaces/{self.namespace}"
            f"/configmaps/{cm_name}"
        )
        try:
            resp = await self._http.get(url)
        except httpx.HTTPError as e:
            raise JobStartError(f"k8s ConfigMap GET failed: {e}") from e
        if resp.status_code != 200:
            raise JobStartError(
                f"Job template ConfigMap {cm_name} not retrievable from {self.namespace} "
                f"({resp.status_code}): {resp.text[:300]}",
                status_code=resp.status_code,
                body=resp.text,
            )
        cm = resp.json()
        body = (cm.get("data") or {}).get(TEMPLATE_KEY)
        if not body:
            raise JobStartError(
                f"ConfigMap {cm_name} is missing key {TEMPLATE_KEY}"
            )
        try:
            tmpl = yaml.safe_load(body)
        except yaml.YAMLError as e:
            raise JobStartError(f"ConfigMap {cm_name}/{TEMPLATE_KEY} is not valid YAML: {e}") from e
        if not isinstance(tmpl, dict) or tmpl.get("kind") != "Job":
            raise JobStartError(
                f"ConfigMap {cm_name}/{TEMPLATE_KEY} is not a Job manifest "
                f"(got kind={tmpl.get('kind') if isinstance(tmpl, dict) else type(tmpl)})"
            )
        return tmpl

    # ── Public surface (matches azure_jobs.AzureJobsClient) ─────────────────
    async def start_job_execution(
        self,
        job_name: str,
        execution_id: Optional[str] = None,
        env_overrides: Optional[Dict[str, str]] = None,
        attempt: int = 1,
    ) -> str:
        """Create a one-shot k8s Job from the template, return its name.

        The returned name is the analog of Azure's "execution name" — pass
        it to `stop_job_execution` to cancel.

        `env_overrides` are appended to the Pod's first container env list
        and override any same-named entries already in the template. This is
        how novomcp passes job config without an extra Redis
        hop (Azure rejected this path; k8s accepts it cleanly).

        `attempt` is the ORCHESTRATOR-assigned attempt number (the reason-aware
        retry layer increments it for infra-class resubmissions). It is
        authoritative — injected as the log-attempt marker (in-pod log prefix) AND
        recorded as annotations (novomcp/log-attempt, novomcp/job-id) so the
        control-plane terminal.json writer associates the exact attempt without
        racing on S3 prefix counts.
        """
        self._ensure_client()
        tmpl = await self._load_template(job_name)

        # Salt the k8s Job NAME with the attempt so a reason-aware retry
        # (attempt>1, same execution_id/job_id for checkpoint-resume) gets a
        # DISTINCT Job — never colliding with the prior attempt's Job (which
        # lingers until ttlSecondsAfterFinished). The novomcp/job-id annotation
        # below stays the stable job_id so terminal.json keys by job_id +
        # attempt (attempt-<n>/), not by Job name.
        salt = (f"{execution_id}#a{attempt}" if execution_id else os.urandom(8).hex())
        suffix = hashlib.sha1(salt.encode()).hexdigest()[:10]
        prefix = job_name[: max(1, 63 - 1 - len(suffix))]
        exec_name = f"{prefix}-{suffix}"

        body = copy.deepcopy(tmpl)
        body.setdefault("metadata", {})
        body["metadata"]["name"] = exec_name
        body["metadata"].setdefault("labels", {})
        body["metadata"]["labels"]["app.kubernetes.io/managed-by"] = "novomcp"
        body["metadata"]["labels"]["novomcp/template"] = job_name
        # Attempt + job_id annotations — authoritative for terminal.json.
        body["metadata"].setdefault("annotations", {})
        body["metadata"]["annotations"]["novomcp/log-attempt"] = str(attempt)
        if execution_id:
            body["metadata"]["annotations"]["novomcp/job-id"] = execution_id
        # log-attempt marker injected so the pod ships to the SAME attempt-<n>/.
        env_overrides = dict(env_overrides or {})

        if env_overrides:
            try:
                containers = body["spec"]["template"]["spec"]["containers"]
            except KeyError as e:
                raise JobStartError(f"Template missing spec.template.spec.containers: {e}") from e
            if not containers:
                raise JobStartError(f"Template {job_name} has no containers")
            existing = containers[0].setdefault("env", [])
            existing_keys = {e.get("name") for e in existing}
            for k, v in env_overrides.items():
                if k in existing_keys:
                    for entry in existing:
                        if entry.get("name") == k:
                            entry["value"] = v
                            entry.pop("valueFrom", None)
                            break
                else:
                    existing.append({"name": k, "value": v})

        url = (
            f"{self._api_host}/apis/batch/v1/namespaces/{self.namespace}/jobs"
        )
        try:
            resp = await self._http.post(
                url,
                content=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError as e:
            raise JobStartError(f"k8s Job create request failed: {e}") from e
        if resp.status_code not in (200, 201, 202):
            raise JobStartError(
                f"k8s Job create returned HTTP {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
                body=resp.text,
            )
        logger.info(f"Created k8s Job {exec_name} (template={job_name}) in {self.namespace}")
        return exec_name

    async def stop_job_execution(self, job_name: str, execution_name: str) -> None:
        """Delete the Job (cascading to its Pod). Idempotent — 404 is success."""
        self._ensure_client()
        url = (
            f"{self._api_host}/apis/batch/v1/namespaces/{self.namespace}"
            f"/jobs/{execution_name}?propagationPolicy=Background"
        )
        try:
            resp = await self._http.delete(url)
        except httpx.HTTPError as e:
            raise JobStartError(f"k8s Job delete request failed: {e}") from e
        if resp.status_code in (200, 202):
            logger.info(f"Deleted k8s Job {execution_name} (template={job_name})")
            return
        if resp.status_code == 404:
            logger.info(f"k8s Job {execution_name} already gone (404) — treating as stopped")
            return
        raise JobStartError(
            f"k8s Job delete returned HTTP {resp.status_code}: {resp.text[:500]}",
            status_code=resp.status_code,
            body=resp.text,
        )

    async def get_job_status(self, execution_name: str) -> Dict[str, Any]:
        """Read a Job's terminal state — for the k8s→SQL reconciler.

        Returns a dict the reconciler maps to a SQL status:
          {found, status, condition, reason, message, uid, completion_time}
        where status ∈ {"complete","failed","active","not_found"}.

        k8s marks a Job terminal by setting a `Complete` or `Failed` condition
        with status=True. A missing Job returns {found: False, status:
        "not_found"} (never raises) so the caller can apply its grace period
        rather than flipping the row to failed on a just-submitted or TTL-GC'd
        Job. Genuine API/RBAC/network errors DO raise (JobStartError) so the
        caller retains state and retries — never write on uncertainty.
        """
        self._ensure_client()
        url = (
            f"{self._api_host}/apis/batch/v1/namespaces/{self.namespace}"
            f"/jobs/{execution_name}"
        )
        try:
            resp = await self._http.get(url)
        except httpx.HTTPError as e:
            raise JobStartError(f"k8s Job GET failed: {e}") from e
        if resp.status_code == 404:
            return {"found": False, "status": "not_found"}
        if resp.status_code != 200:
            raise JobStartError(
                f"k8s Job GET returned HTTP {resp.status_code}: {resp.text[:300]}",
                status_code=resp.status_code,
                body=resp.text,
            )
        job = resp.json()
        meta = job.get("metadata", {}) or {}
        status = job.get("status", {}) or {}
        for c in (status.get("conditions") or []):
            if c.get("status") == "True" and c.get("type") in ("Complete", "Failed"):
                return {
                    "found": True,
                    "status": "complete" if c["type"] == "Complete" else "failed",
                    "condition": c.get("type"),
                    "reason": c.get("reason"),
                    "message": c.get("message"),
                    "uid": meta.get("uid"),
                    "completion_time": status.get("completionTime") or c.get("lastTransitionTime"),
                }
        # No terminal condition → still active/pending.
        return {
            "found": True,
            "status": "active",
            "uid": meta.get("uid"),
            "active": status.get("active"),
        }

    async def get_job_terminal_detail(self, execution_name: str) -> Dict[str, Any]:
        """Rich terminal detail for the control-plane terminal.json writer.

        Returns the Job's UID/condition/reason/message/timestamps/image +
        annotations (attempt, job_id) + a `pods` array enumerating EVERY pod
        incarnation (uid/name/node/start/exit-code+reason) the Job spawned —
        a backoffLimit retry creates more than one — plus the most-recent pod's
        fields hoisted to the top level + that node's instance-type/GPU label.
        {found: False} if the Job is gone (404). Best-effort on pod/node reads
        (they need extra RBAC) — missing pieces come back as None, never raise.
        """
        self._ensure_client()
        jurl = f"{self._api_host}/apis/batch/v1/namespaces/{self.namespace}/jobs/{execution_name}"
        try:
            jr = await self._http.get(jurl)
        except httpx.HTTPError as e:
            raise JobStartError(f"k8s Job GET failed: {e}") from e
        if jr.status_code == 404:
            return {"found": False}
        if jr.status_code != 200:
            raise JobStartError(f"k8s Job GET HTTP {jr.status_code}: {jr.text[:300]}",
                                status_code=jr.status_code, body=jr.text)
        job = jr.json()
        meta = job.get("metadata", {}) or {}
        ann = meta.get("annotations", {}) or {}
        jstatus = job.get("status", {}) or {}
        cond = next((c for c in (jstatus.get("conditions") or [])
                     if c.get("status") == "True" and c.get("type") in ("Complete", "Failed")), None)
        try:
            image = job["spec"]["template"]["spec"]["containers"][0].get("image")
        except Exception:
            image = None
        detail = {
            "found": True,
            "terminal": cond is not None,
            "k8s_job_name": execution_name,
            "k8s_job_uid": meta.get("uid"),
            "condition": cond.get("type") if cond else None,
            "reason": cond.get("reason") if cond else None,
            "message": cond.get("message") if cond else None,
            "job_created_at": meta.get("creationTimestamp"),
            "completed_at": jstatus.get("completionTime") or (cond.get("lastTransitionTime") if cond else None),
            "image_digest": image,
            "attempt": ann.get("novomcp/log-attempt"),
            "job_id": ann.get("novomcp/job-id"),
        }
        # Enumerate EVERY pod incarnation (backoffLimit retries create >1). Each
        # entry mirrors what that pod shipped under attempt-<n>/pod-<pod_uid>/, so
        # terminal.json can be cross-referenced to the per-pod log dirs. The
        # most-recent pod's fields are also hoisted to the top level (back-compat +
        # node-label lookup below).
        detail["pods"] = []
        try:
            purl = (f"{self._api_host}/api/v1/namespaces/{self.namespace}/pods"
                    f"?labelSelector=job-name%3D{execution_name}")
            pr = await self._http.get(purl)
            pods = (pr.json().get("items") or []) if pr.status_code == 200 else []
            # Deterministic order: by startTime (empty sorts first), name as tiebreak.
            pods.sort(key=lambda p: (((p.get("status") or {}).get("startTime") or ""),
                                     ((p.get("metadata") or {}).get("name") or "")))
            for pod in pods:
                pmeta, pstatus, pspec = pod.get("metadata", {}), pod.get("status", {}), pod.get("spec", {})
                cs = pstatus.get("containerStatuses") or []
                exit_code = reason = None
                if cs:
                    term = ((cs[0].get("state", {}) or {}).get("terminated")
                            or (cs[0].get("lastState", {}) or {}).get("terminated") or {})
                    exit_code = term.get("exitCode")
                    reason = term.get("reason")
                detail["pods"].append({
                    "pod_uid": pmeta.get("uid"),
                    "pod_name": pmeta.get("name"),
                    "node_name": pspec.get("nodeName"),
                    "started_at": pstatus.get("startTime"),
                    "exit_code": exit_code,
                    "reason": reason,
                })
            if detail["pods"]:
                last = detail["pods"][-1]
                detail["pod_name"] = last["pod_name"]
                detail["node_name"] = last["node_name"]
                detail["pod_started_at"] = last["started_at"]
                detail["container_exit_code"] = last["exit_code"]
                detail["container_reason"] = last["reason"]
        except Exception:
            pass
        # Node instance-type / GPU product label.
        node = detail.get("node_name")
        if node:
            try:
                nr = await self._http.get(f"{self._api_host}/api/v1/nodes/{node}")
                if nr.status_code == 200:
                    nlabels = (nr.json().get("metadata", {}) or {}).get("labels", {}) or {}
                    detail["instance_type"] = nlabels.get("node.kubernetes.io/instance-type")
                    detail["gpu_type"] = nlabels.get("nvidia.com/gpu.product")
            except Exception:
                pass
        return detail

    async def close(self):
        """Release the httpx client. Safe to call multiple times."""
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception:
                pass
            self._http = None
