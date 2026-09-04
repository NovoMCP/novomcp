# MD on Runpod

Run the GPU molecular-dynamics service (`gromacs-md`) on a [Runpod](https://runpod.io) GPU pod, so `run_molecular_dynamics` works without you owning a card. MD is **async and long-running** (minutes to hours per simulation), so a **persistent pod** fits better than a scale-to-zero serverless endpoint — the engine submits a job and polls `get_job_status` while the pod keeps working.

## Prerequisites

- A Runpod account (`console.runpod.io`).
- The engine running somewhere. You will point it at the pod's proxy URL.

## Create a template and deploy a pod

1. **Templates → New Template**:
   - **Container Image:** `ghcr.io/novomcp/gromacs-md:latest` (GHCR is public — no registry credentials needed).
   - **Expose HTTP Ports:** `8021`. Runpod's reverse proxy publishes it at a public HTTPS URL; only one HTTP port per pod, which is all this service needs.
2. **Deploy** → choose a **GPU pod** (L40S or A100 recommended; L4/A10G is the practical minimum, ~8 GB GPU memory per simulation) → select your template → **Deploy**.
3. Once the pod is running, its endpoint is `https://<POD_ID>-8021.proxy.runpod.net`.

## Wire into the engine

```bash
export GROMACS_MD_URL=https://<POD_ID>-8021.proxy.runpod.net
```

## Verify

```bash
curl -s "$GROMACS_MD_URL/health"
# {"status":"healthy","gpu_available":true,"gromacs_version":"2023.5"}
```

End to end, `run_molecular_dynamics` returns a job id immediately and you poll `get_job_status` — the pod runs the simulation in the background, which is exactly why a persistent pod (not serverless) is the right shape here.

## Cost and access

- A persistent pod **bills for GPU time while it runs** (per-hour). Stop the pod when you are not running MD; for occasional use, a spot / community-cloud GPU is the cheapest.
- The serverless option suits short request/response services; a multi-minute MD job is not that, so use a pod.
- **Security:** the proxy URL is public (an obscure pod id, but not authenticated), and the MD service has no auth of its own. Treat the URL as a secret and set a Runpod spend cap so a leaked URL can't run up an unbounded bill.

## Notes

Verified against Runpod's current docs (September 2026): [custom pod templates](https://docs.runpod.io/pods/templates/create-custom-template). This guide is reference-quality and has not yet been live-tested end to end.
