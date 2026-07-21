# addie-models

ADMET (absorption, distribution, metabolism, excretion, toxicity) prediction. 31 pretrained ML models covering CYP inhibition/substrate, clearance, half-life, hepatotoxicity, cardiotoxicity (hERG + DICTrank), DILI, Ames, and Tox21 endpoints.

## Pre-reqs

- Docker
- CPU works; GPU (any NVIDIA) speeds up batch inference ~10×
- ~4 GB RAM
- ~2 GB disk for weights (bundled in the image)

## Deploy

**Via docker-compose:**

Uncomment the `addie-models` block in `docker-compose.yml`, then:
```bash
docker compose up addie-models
```

**Standalone:**

```bash
# CPU only
docker run -d \
  --name novomcp-addie \
  -p 8033:8033 \
  --restart unless-stopped \
  ghcr.io/novomcp/addie-models:latest

# With GPU
docker run -d \
  --name novomcp-addie \
  --gpus all \
  -p 8033:8033 \
  --restart unless-stopped \
  ghcr.io/novomcp/addie-models:latest
```

## Wire into the engine

```bash
export ADDIE_MODELS_URL=http://localhost:8033
```

## Verify

```bash
curl -s http://localhost:8033/health
# {"status":"healthy","models_loaded":31,"gpu_available":true|false}

curl -s -X POST http://localhost:8033/predict \
  -H 'Content-Type: application/json' \
  -d '{"smiles":"CC(=O)Oc1ccccc1C(=O)O"}'
```

## Tools that light up

- `predict_admet`, 31 endpoints per molecule
- `get_molecule_profile`, fills the ADMET section (falls back to properties-only without this service)
- `screen_library`, batch ADMET across a compound list
- `batch_profile`, same, higher-level wrapper

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8033` | HTTP listen port |
| `BATCH_SIZE` | `32` | Molecules per inference batch |
| `USE_GPU` | `auto` | Force `cpu` or `gpu`; `auto` detects at boot |

## Speed

- **Single molecule:** ~50 ms (CPU) / ~10 ms (GPU)
- **Library screen (10,000 compounds):** ~5 min (CPU) / ~30 s (GPU)
