# Docking on Modal

Run the GPU docking service (`autodock-gpu`) on [Modal](https://modal.com)'s serverless GPUs, so `dock_molecules` works without you owning a card. Docking is **synchronous and short** (~30–60 s per ligand), which fits Modal's request/response + scale-to-zero model well: you pay for GPU-seconds per docking, not for an idle GPU.

## Prerequisites

- A Modal account and the CLI: `pip install modal` then `modal setup`.
- The engine running somewhere (laptop or cloud). You will point it at the Modal URL.

## Deploy the docking service

Modal can run an existing image with `Image.from_registry` and proxy HTTP to a server running inside it. The `autodock-gpu` image already starts its FastAPI server on port 8022, so the Modal app just needs to launch that and expose the port. Save this as `autodock_modal.py`:

```python
import modal

# The published image already contains AutoDock-GPU + its HTTP server.
image = modal.Image.from_registry("ghcr.io/novomcp/autodock-gpu:latest")
app = modal.App("novomcp-autodock", image=image)

@app.function(gpu="A10G", timeout=900, scaledown_window=120)
@modal.web_server(8022, startup_timeout=180)
def serve():
    # Start the image's own server on 8022. Replace the command below with the
    # image's documented entrypoint/CMD if it differs (check the repo's
    # Dockerfile) — Modal proxies whatever listens on the declared port.
    import subprocess
    subprocess.Popen(["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8022"])
```

Deploy it:

```bash
modal deploy autodock_modal.py
# -> https://<workspace>--novomcp-autodock-serve.modal.run
```

## Wire into the engine

```bash
export AUTODOCK_GPU_URL=https://<workspace>--novomcp-autodock-serve.modal.run
```

## Verify

```bash
curl -s "$AUTODOCK_GPU_URL/health"
# {"status":"healthy","service":"autodock-gpu","gpu_available":true}
```

The first call cold-starts a GPU container (~30–60 s); calls within the `scaledown_window` are warm. Because docking is a single request/response, the cold start lands on the first `dock_molecules` and not again until the container scales down.

## Cost and access

- You pay **per GPU-second**. An A10G docking (~30–60 s) is a few cents, and scale-to-zero means no idle cost between jobs. Pick a smaller GPU (T4/L4) to cut cost, or a bigger one (A100) for large libraries.
- **Security:** a Modal web URL is public, and the docking service has no auth of its own — anyone with the URL can run GPU jobs on your account. Protect it with Modal proxy auth (`requires_proxy_auth=True` on the web server, plus a proxy-auth token) or keep the URL private, and set a Modal spend limit.

## Notes

Verified against Modal's current docs (September 2026): [existing images](https://modal.com/docs/guide/existing-images), [GPU acceleration](https://modal.com/docs/guide/gpu). The exact server-start command depends on the image's entrypoint — adapt the `subprocess` line to match. This guide is reference-quality and has not yet been live-tested end to end.
