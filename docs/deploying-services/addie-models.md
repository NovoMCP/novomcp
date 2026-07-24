# addie-models

ADMET (absorption, distribution, metabolism, excretion, toxicity) prediction. **31 base endpoints + a 22-model TDC state-of-the-art overlay** covering CYP inhibition/substrate, clearance, half-life, hepatotoxicity (DILI), cardiotoxicity (hERG + DICTrank), Ames, permeability, solubility, and the Tox21 nuclear-receptor/stress-response panels.

- **Source:** https://github.com/NovoMCP/addie-models
- **Weights:** https://huggingface.co/NovoMCP/addie-models (~510 MiB, MIT) — pulled automatically on first boot.

## Pre-reqs

- Docker
- CPU works; GPU (any NVIDIA) speeds up batch inference
- ~4 GB RAM
- ~1 GB disk for weights, **downloaded from Hugging Face on first boot** (no cloud credentials needed)

## Deploy

**Via docker-compose:**

Uncomment the `addie-models` block in `docker-compose.yml`, then:
```bash
docker compose up addie-models
```

**Standalone:**

```bash
docker run -d \
  --name novomcp-addie \
  -p 8025:8025 \
  --restart unless-stopped \
  ghcr.io/novomcp/addie-models:latest
# first boot downloads the weights from Hugging Face, then serves on :8025

# With GPU: add  --gpus all
```

## Wire into the engine

```bash
export ADDIE_MODELS_URL=http://localhost:8025
```

## Verify

```bash
curl -s http://localhost:8025/health
# {"status":"healthy","models_loaded":31, ...}

curl -s -X POST http://localhost:8025/addie/process \
  -H 'Content-Type: application/json' \
  -d '{"molecules":[{"id":"aspirin","smiles":"CC(=O)Oc1ccccc1C(=O)O"}]}'
```

## Tools that light up

- `predict_admet`, ADMET across all endpoints per molecule
- `get_molecule_profile`, fills the ADMET section (falls back to properties-only without this service)
- `screen_library`, batch ADMET across a compound list
- `batch_profile`, same, higher-level wrapper

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8025` | HTTP listen port |
| `STORAGE_BACKEND` | `HF` | Weights source: `HF` (Hugging Face) \| `S3` \| `AZURE` |
| `HF_MODEL_REPO` | `NovoMCP/addie-models` | Hugging Face weights repo (when `STORAGE_BACKEND=HF`) |
| `EXECUTOR_WORKERS` | `8` | Inference thread-pool size |
| `TDC_USE_GIN` | `true` | Use the GIN-featurized TDC ensembles |

GPU is auto-detected at boot (PyTorch); no flag needed beyond `--gpus all` on the container.
