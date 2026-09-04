# novomcp-nnp

Neural network potentials for fast geometry optimization and energy prediction. The published image serves two backends: **MACE** and **ANI-2x**. GPU-accelerated; CPU works but is 10–50× slower.

Two axes, kept orthogonal:
- **`method`** — *which potential* runs (`mace` / `ani-2x`).
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
# {"status":"healthy","service":"novomcp-nnp","version":"1.0.0","port":8032,
#  "models":{"ani2x":{"available":true},"mace":{"available":true}},
#  "ready_models":["ani2x","mace"]}
```

## Tools that light up

- `optimize_geometry_nnp`, fast single-molecule NNP geometry optimization
- `compute_energy`, single-point energy and forces (MLIP; batched via `engine=alchemi`)
- `batch_geometry_relaxation`, library relaxation in one batched pass (built for the geometry phase of `screen_oled_library` / `screen_electrolyte_library`)
- Model selection is the `method` argument (`auto | ani2x | mace`); execution engine is the `engine` argument (`ase | alchemi`)

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8032` | HTTP listen port |
| `DEFAULT_BACKEND` | `mace` | Model backend when caller doesn't specify `method`. Must be one the image serves (`mace` or `ani2x`). |
| `USE_GPU` | `auto` | Force `cpu` or `gpu`; `auto` detects at boot |
| `ALCHEMI_ENABLED` | `false` | Enable the `engine=alchemi` GPU-batched path (requires the `nvalchemi-toolkit` extra + a CUDA 12/13 GPU). When `false`, requests with `engine=alchemi` get a structured `503 alchemi backend not built` error. |

## ALCHEMI batched engine (optional)

The `engine=alchemi` path routes relaxation through the NVIDIA ALCHEMI Toolkit's batched dynamics — many systems co-resident on the GPU per kernel call. It runs the same `method` potential you'd use otherwise (MACE / ANI-2x); ALCHEMI is the *execution* layer, not a new model.

- **Build:** the image must install the toolkit extra — `pip install 'nvalchemi-toolkit[cu13]' --extra-index-url https://download.pytorch.org/whl/cu130 --extra-index-url https://pypi.nvidia.com` (CUDA 12/13 only; no CPU wheels). Keep it an *optional* build arg so the base image stays CPU-installable.
- **Endpoints:** `POST /api/optimize-geometry` accepts `engine` for single molecules; `POST /api/relax-batch` accepts `{smiles_list, method, engine, fmax}` and returns per-input relaxed XYZ + energy + convergence in input order, with per-item failures reported inline (a bad SMILES never fails the batch).
- **Status:** the toolkit is public beta (`API subject to change`) — pin the version and keep the adapter thin so upstream churn is contained to one module.

## Backend cheatsheet

- **MACE**, strong general-purpose potential, good on periodic systems and materials. Recommended default.
- **ANI-2x**, fast, covers H/C/N/O/S/F/Cl, wide validation on drug-like molecules.

AIMNet2 is referenced by the engine but is **not bundled in the current published image** — `/health` reports only the backends actually loaded, and requesting `method=aimnet2` returns a structured error. Use MACE or ANI-2x, or build the image with AIMNet2 weights yourself if you need it.

## Weights and licenses

The model weights are bundled in the image. The MACE backend uses **MACE-MPA-0, which is MIT-licensed** — not MACE-MP-0, whose Academic Software License forbids commercial use. So the MACE path is commercial-safe here. ANI-2x ships under its own upstream open license; if you run this commercially, confirm it against its source repository. The service code itself is Apache-2.0.

## Speed

- **MACE opt (drug-sized molecule):** ~0.5 s (GPU) / ~10 s (CPU)
- **Batch of 1000 molecules:** ~2 min (GPU)
