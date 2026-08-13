# novomcp-nnp

Neural network potentials for fast geometry optimization and energy prediction. Three model backends: **AIMNet2**, **MACE**, and **ANI-2x**. GPU-accelerated; CPU works but is 10–50× slower.

Two axes, kept orthogonal:
- **`method`** — *which potential* runs (`aimnet2` / `mace` / `ani-2x`).
- **`engine`** — *how it executes*: `ase` (ASE BFGS optimizer, default) or `alchemi` (the [NVIDIA ALCHEMI Toolkit](https://github.com/NVIDIA/nvalchemi-toolkit) GPU-batched relaxation dynamics running the same `method` potential on batched CUDA kernels). The ALCHEMI engine is what powers `batch_geometry_relaxation`, which relaxes a whole library in one batched pass instead of a per-molecule loop.

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
  ghcr.io/novomcp/novomcp-nnp:latest

# CPU only
docker run -d \
  --name novomcp-nnp \
  -p 8032:8032 \
  --restart unless-stopped \
  ghcr.io/novomcp/novomcp-nnp:latest
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

- `optimize_geometry_nnp`, fast single-molecule NNP geometry optimization
- `batch_geometry_relaxation`, library relaxation in one batched pass (built for the geometry phase of `screen_oled_library` / `screen_electrolyte_library`)
- Model selection is the `method` argument (`auto | ani2x | mace`); execution engine is the `engine` argument (`ase | alchemi`)

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8032` | HTTP listen port |
| `DEFAULT_BACKEND` | `aimnet2` | Model backend when caller doesn't specify `method` |
| `USE_GPU` | `auto` | Force `cpu` or `gpu`; `auto` detects at boot |
| `ALCHEMI_ENABLED` | `false` | Enable the `engine=alchemi` GPU-batched path (requires the `nvalchemi-toolkit` extra + a CUDA 12/13 GPU). When `false`, requests with `engine=alchemi` get a structured `503 alchemi backend not built` error. |

## ALCHEMI batched engine (optional)

The `engine=alchemi` path routes relaxation through the NVIDIA ALCHEMI Toolkit's batched dynamics — many systems co-resident on the GPU per kernel call. It runs the same `method` potential you'd use otherwise (MACE / AIMNet2); ALCHEMI is the *execution* layer, not a new model.

- **Build:** the image must install the toolkit extra — `pip install 'nvalchemi-toolkit[cu13]' --extra-index-url https://download.pytorch.org/whl/cu130 --extra-index-url https://pypi.nvidia.com` (CUDA 12/13 only; no CPU wheels). Keep it an *optional* build arg so the base image stays CPU-installable.
- **Endpoints:** `POST /api/optimize-geometry` accepts `engine` for single molecules; `POST /api/relax-batch` accepts `{smiles_list, method, engine, fmax}` and returns per-input relaxed XYZ + energy + convergence in input order, with per-item failures reported inline (a bad SMILES never fails the batch).
- **Status:** the toolkit is public beta (`API subject to change`) — pin the version and keep the adapter thin so upstream churn is contained to one module.

## Backend cheatsheet

- **AIMNet2**, best all-round accuracy for organics containing H/C/N/O/S/F/Cl. Recommended default.
- **MACE**, best for periodic systems and materials. Slightly slower than AIMNet2 for small molecules.
- **ANI-2x**, fast, covers H/C/N/O/S/F/Cl. Slightly less accurate than AIMNet2 but wider validation on drug-like molecules.

## Speed

- **AIMNet2 opt (drug-sized molecule):** ~0.5 s (GPU) / ~10 s (CPU)
- **Batch of 1000 molecules:** ~2 min (GPU)
