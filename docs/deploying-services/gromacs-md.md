# gromacs-md

GPU-accelerated molecular dynamics. GROMACS with hydrogen-mass repartitioning (4 fs timestep), exposed as an async HTTP service.

## Pre-reqs

- NVIDIA GPU (L4/A10G at minimum for reasonable throughput; L40S/A100/H100 preferred)
- Docker + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
- CUDA 12.x drivers on the host
- ~8 GB GPU memory per concurrent simulation

## Deploy

**Via docker-compose (default):**

Uncomment the `gromacs-md` block in `docker-compose.yml`, then:
```bash
docker compose up gromacs-md
```

**Standalone (remote GPU box):**

```bash
docker run -d \
  --name novomcp-gromacs-md \
  --gpus all \
  -p 8021:8021 \
  -v /path/to/persistent/results:/results \
  --restart unless-stopped \
  ghcr.io/novomcp/gromacs-md:latest
```

## Wire into the engine

```bash
export GROMACS_MD_URL=http://localhost:8021
# or remote:
export GROMACS_MD_URL=http://gpu-box.local:8021
```

## Verify

```bash
curl -s http://localhost:8021/health
# {"status":"healthy","gpu_available":true,"gromacs_version":"2023.5"}
```

## Tools that light up

- `run_molecular_dynamics`, async MD (poll job status via `get_job_status`)
- `generate_dynamics`, same, higher-level wrapper with defaults

## Async pattern

MD jobs are long-running (minutes to hours). The engine returns a `job_id` immediately; poll status:
```bash
curl -s http://localhost:8018/mcp/tools/get_job_status \
  -H 'Authorization: Bearer x' \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"job_id":"<the-id-you-got-back>"}}'
```

Or set `email` in the original call to get notified on completion (requires Resend API key via env; see below).

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8021` | HTTP listen port |
| `MAX_CONCURRENT` | `1` | Simulations per GPU |
| `RESULTS_DIR` | `/results` | Where trajectories land |
| `RESEND_API_KEY` | (unset) | Email notifications on job completion |
| `HMR_HYDROGEN_MASS_DA` | `3.0` | HMR hydrogen mass (3 Da → 4 fs timestep) |

## Speed + cost

- **10 ns simulation on dasatinib•ABL kinase (~55K atoms):** ~18 minutes end-to-end (solvate + parameterize + minimize + equilibrate + 10 ns) on L40S. Sustained rate >1 µs/day.
- **Cheapest cloud spot:** g5.xlarge (A10G) at $0.30/hr spot; usable for short runs and hackathon-scale exploration.
- For multi-µs production simulations, prefer A100 40GB or H100.
