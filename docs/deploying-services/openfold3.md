# openfold3

Protein structure prediction. OpenFold3 (open reimplementation) exposed as HTTP.

Alternatives on the same wire format: Chai-1, Boltz-2. You can swap the image and change nothing else, the engine talks to any of them via the same `PREDICT_STRUCTURE_URL` env var.

## Pre-reqs

- NVIDIA GPU with **≥40 GB memory** for full-length proteins (A100-40G, A100-80G, H100)
- L40S / A10G work for short sequences (<300 residues)
- Docker + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
- CUDA 12.x drivers
- Model weights (bundled in the image; ~15 GB download on first pull)

## Deploy

**Via docker-compose:**

Uncomment the `openfold3` block in `docker-compose.yml`, then:
```bash
docker compose up openfold3
```

**Standalone:**

```bash
docker run -d \
  --name novomcp-openfold3 \
  --gpus all \
  -p 8025:8025 \
  -v openfold3-weights:/weights \
  --restart unless-stopped \
  ghcr.io/novomcp/openfold3:latest
```

First boot pulls the model weights (~15 GB), takes a few minutes.

## Wire into the engine

```bash
export OPENFOLD3_URL=http://localhost:8025
# or the generic env var, which works for OpenFold3 / Chai / Boltz interchangeably:
export PREDICT_STRUCTURE_URL=http://localhost:8025
```

## Verify

```bash
curl -s http://localhost:8025/health
# {"status":"healthy","gpu_available":true,"weights_loaded":true}
```

## Tools that light up

- `predict_structure`, from sequence to PDB
- `get_structure_result`, retrieve completed predictions

## Swapping providers

To use Chai-1 or Boltz instead:
```bash
docker stop novomcp-openfold3
docker run -d --gpus all -p 8025:8025 --name novomcp-chai \
  ghcr.io/novomcp/chai-server:latest
```
Same env var, same tool call surface.

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8025` | HTTP listen port |
| `MAX_LENGTH` | `1500` | Reject sequences longer than this (memory guard) |
| `NUM_RECYCLES` | `3` | Recycles per prediction (accuracy vs speed) |

## Cost

- **Small protein (100 res):** ~30 seconds on A100
- **Large protein (1000 res):** ~5 minutes on A100-80G
- **Cheapest reliable option:** L40S at ~$1/hr spot; A10G struggles above ~400 residues
