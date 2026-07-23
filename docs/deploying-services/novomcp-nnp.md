# novomcp-nnp

Neural network potentials for fast geometry optimization and energy prediction. Three backends: **AIMNet2**, **MACE**, and **ANI-2x**. GPU-accelerated; CPU works but is 10–50× slower.

## Pre-reqs

- Docker
- NVIDIA GPU recommended (L4 / A10G / L40S / A100 all fine)
- CPU-only fallback for small molecules and low-throughput use
- ~2 GB RAM per active calculation
- ~1 GB disk for weights (bundled)

## Deploy

```bash
# GPU
docker run -d \
  --name novomcp-nnp \
  --gpus all \
  -p 8032:8032 \
  --restart unless-stopped \
  ghcr.io/NovoMCP/novomcp-nnp:latest

# CPU only
docker run -d \
  --name novomcp-nnp \
  -p 8032:8032 \
  --restart unless-stopped \
  ghcr.io/NovoMCP/novomcp-nnp:latest
```

## Wire into the engine

```bash
export NOVOMCP_NNP_URL=http://localhost:8032
```

## Verify

```bash
curl -s http://localhost:8032/health
# {"status":"healthy","backends":["aimnet2","mace","ani-2x"],"gpu_available":true}
```

## Tools that light up

- `optimize_geometry_nnp`, fast NNP geometry optimization
- Backend selection is a tool argument (`backend: "aimnet2" | "mace" | "ani-2x"`); default is `aimnet2`

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8032` | HTTP listen port |
| `DEFAULT_BACKEND` | `aimnet2` | Backend when caller doesn't specify |
| `USE_GPU` | `auto` | Force `cpu` or `gpu`; `auto` detects at boot |

## Backend cheatsheet

- **AIMNet2**, best all-round accuracy for organics containing H/C/N/O/S/F/Cl. Recommended default.
- **MACE**, best for periodic systems and materials. Slightly slower than AIMNet2 for small molecules.
- **ANI-2x**, fast, covers H/C/N/O/S/F/Cl. Slightly less accurate than AIMNet2 but wider validation on drug-like molecules.

## Speed

- **AIMNet2 opt (drug-sized molecule):** ~0.5 s (GPU) / ~10 s (CPU)
- **Batch of 1000 molecules:** ~2 min (GPU)
