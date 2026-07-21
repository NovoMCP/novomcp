# autodock-gpu

GPU-accelerated molecular docking. AutoDock-GPU (Vina family) as a stateless HTTP service.

## Pre-reqs

- NVIDIA GPU (any modern card, L4 / A10G / A100 / L40S / H100)
- Docker + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
- CUDA 12.x drivers on the host
- ~4 GB GPU memory per concurrent docking job

## Deploy

**Via docker-compose (default):**

Uncomment the `autodock-gpu` block in `docker-compose.yml`, then:
```bash
docker compose up autodock-gpu
```

**Standalone (remote GPU box):**

```bash
docker run -d \
  --name novomcp-autodock-gpu \
  --gpus all \
  -p 8022:8022 \
  --restart unless-stopped \
  ghcr.io/novomcp/autodock-gpu:latest
```

Verify GPU is visible inside the container:
```bash
docker exec novomcp-autodock-gpu nvidia-smi
```

## Wire into the engine

```bash
# Local
export AUTODOCK_GPU_URL=http://localhost:8022

# Remote GPU box
export AUTODOCK_GPU_URL=http://gpu-box.local:8022
```

## Verify

```bash
curl -s http://localhost:8022/health
# {"status":"healthy","service":"autodock-gpu","gpu_available":true}
```

End-to-end via the engine:
```bash
curl -s -X POST http://localhost:8018/mcp/tools/dock_molecules \
  -H 'Authorization: Bearer x' \
  -H 'Content-Type: application/json' \
  -d '{
    "arguments": {
      "ligand_smiles": "CC(=O)Oc1ccccc1C(=O)O",
      "protein_pdb_id": "1CX2"
    }
  }'
```

## Tools that light up

- `dock_molecules`, synchronous docking against a PDB ID
- `dock_with_strain`, pairs with `novomcp-qm` for post-dock strain filtering

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8022` | HTTP listen port |
| `MAX_CONCURRENT` | `1` | Docking jobs per GPU (raise if you have headroom) |
| `TIMEOUT_SECONDS` | `600` | Per-job hard timeout |

## Cost + performance

- **Single docking:** ~30–60 seconds on L4/A10G
- **Small library screen (100 ligands):** ~10 minutes
- **Cheapest cloud spot for occasional use:** g4dn.xlarge (AWS) or n1-standard-4 + T4 (GCP) at roughly $0.30/hour spot

For batch screens, run the container on a spot instance and burst through the queue rather than paying for a dedicated GPU.
