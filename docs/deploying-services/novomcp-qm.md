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
  ghcr.io/novomcp/novomcp-qm:latest
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
- `predict_frontier_orbitals`, HOMO/LUMO
- `run_qm_hessian`, Hessian and vibrational frequencies
- `run_excited_states`, excited-state energies
- `predict_redox_potential`, redox potentials
- `predict_reaction_thermodynamics`, ΔG, ΔH
- `parameterize_metal`, MCPB.py metal-site parameterization (two-phase, Gaussian-only, see below)

`dock_with_strain` also uses this service for its post-dock GFN2-xTB strain check, but it is gated on `autodock-gpu` (see that page). `compute_energy` is served by `novomcp-nnp`, not this service.

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

## parameterize_metal: two-phase, and Gaussian-only

`parameterize_metal` builds AMBER/GROMACS parameters for a metal-coordination site via MCPB.py, and it runs in two phases with your own QM package in the middle:

- **Phase 1** extracts the coordination fragment and emits Gaussian `.com` input files: a `small_fc` frequency job for the Hessian and a `large_mk` Pop(MK) job for the ESP charges. You run these in Gaussian yourself, externally.
- **Phase 2** consumes the two logs, extracts force constants (Seminario method) and RESP charges, and returns the `.frcmod`/`.prep` files plus GROMACS topology.

The honest boundary: this path is **Gaussian-only**. ORCA input is accepted and the Hessian parses, but the MK-ESP-to-RESP charge path is not wired end to end for ORCA, so a complete run needs Gaussian. Do not point Phase 2 at ORCA logs expecting charges back.

## Where the approximation stops

The service defaults to GFN2-xTB, a semi-empirical method. It gives good geometries and relative energies that are dependable for ranking conformers or comparing close analogs. It is not a stand-in for a DFT-accurate absolute energy, or for a subtle electronic effect the approximation smooths over. Use it to triage within a comparable series and escalate the survivors to a higher level of theory when the absolute number has to be right. A tool that oversells its accuracy costs you a wrong decision made quickly, which is worse than a slow one.
