# novomcp-neb

Transition-state search via climbing-image nudged elastic band (CI-NEB). Uses tblite (GFN2-xTB) as the driving Hamiltonian with ASE's NEB implementation.

## Pre-reqs

- Docker
- CPU with 4+ cores; GPU optional
- ~4 GB RAM per active NEB job
- 20–60 minutes per transition-state search

## Deploy

```bash
docker run -d \
  --name novomcp-neb \
  -p 8034:8034 \
  --restart unless-stopped \
  ghcr.io/NovoMCP/novomcp-neb:latest
```

## Wire into the engine

```bash
export NOVOMCP_NEB_URL=http://localhost:8034
```

## Verify

```bash
curl -s http://localhost:8034/health
# {"status":"healthy","tblite_version":"0.4.0","ase_version":"3.22.1"}
```

## Tools that light up

- `find_transition_state`, reactant → transition state → product path with barrier height and imaginary frequency

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8034` | HTTP listen port |
| `NEB_IMAGES` | `9` | Number of images along the path (odd) |
| `NEB_MAX_STEPS` | `100` | Convergence iteration cap |
| `MAX_CONCURRENT` | `2` | Simultaneous NEB jobs |

## Notes

- Requires optimized reactant + product geometries as input. Pair with `novomcp-nnp::optimize_geometry_nnp` upstream for the endpoints.
- Not intended for enzymatic or metal-catalyzed reactions where GFN2-xTB underperforms; those cases need DFT (out of scope here).
- Reports barrier heights in kcal/mol and imaginary frequency in cm⁻¹.
