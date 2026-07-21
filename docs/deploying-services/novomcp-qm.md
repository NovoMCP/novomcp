# novomcp-qm

Quantum-mechanical calculations. Semi-empirical methods (xTB), conformer search (CREST), and metal-coordination parameterization (MCPB.py). CPU-only.

## Pre-reqs

- Docker
- 8+ CPU cores recommended for CREST conformer searches
- ~4 GB RAM per active calculation
- No GPU

## Deploy

```bash
docker run -d \
  --name novomcp-qm \
  -p 8031:8031 \
  --restart unless-stopped \
  ghcr.io/NovoMCP/novomcp-qm:latest
```

## Wire into the engine

```bash
export NOVOMCP_QM_URL=http://localhost:8031
```

## Verify

```bash
curl -s http://localhost:8031/health
# {"status":"healthy","xtb_version":"6.7.1","crest_available":true}
```

## Tools that light up

- `run_qm_calculation`, GFN2-xTB energy / opt / vibrational
- `run_conformer_search`, CREST conformer generation
- `compute_energy`, single-point energy
- `predict_frontier_orbitals`, HOMO/LUMO
- `predict_reaction_thermodynamics`, ΔG, ΔH
- `parameterize_metal`, MCPB.py Phase 1/2 for metal-coordinating residues
- `dock_with_strain`, post-dock GFN2-xTB strain filter (pairs with `autodock-gpu`)

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8031` | HTTP listen port |
| `MAX_CONCURRENT` | `4` | Simultaneous QM jobs |
| `CREST_NTHREADS` | `8` | Threads per CREST run |
| `SCRATCH_DIR` | `/tmp/qm` | Working directory (fast SSD preferred) |

## Speed

- **xTB single-point:** <1 s
- **xTB geometry optimization:** 5–30 s for drug-sized molecules
- **CREST conformer search:** 5–60 min depending on flexibility
- **MCPB.py metal parameterization:** 10–60 min for typical zinc/iron systems
