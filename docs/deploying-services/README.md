# Deploying compute services

NovoMCP's engine is thin, it orchestrates tool calls but the heavy lifting (docking, molecular dynamics, protein structure, quantum-mechanical calculations, ADMET inference) lives in **separate compute services**. Each is optional. You deploy the ones you need, point the engine at them via env vars, and the corresponding tools "light up."

The engine works without any of them. Property calculation, similarity search, literature lookups, and compliance filtering run in-process. Everything else requires a downstream service.

## Tool → service dependency matrix

| Tool | Service | Notes |
|---|---|---|
| `get_molecule_profile` | `chem-props` (+ `addie-models` for full ADMET) | CPU-only. Works partially without either, returns basic properties. |
| `calculate_properties` | `chem-props` | CPU-only. RDKit descriptors, BOILED-Egg. |
| `predict_admet` | `addie-models` | CPU or GPU. 31 pretrained ADMET models. |
| `search_similar` | `chem-props` | CPU-only. Morgan fingerprint similarity. |
| `search_chembl` | (none, external ChEMBL API) | Requires internet only. |
| `search_literature` | (none, external Pinecone) | Requires `PINECONE_API_KEY`. Falls back to zero results without. |
| `search_patents` | (none, external Pinecone) | Same as above. |
| `check_compliance` | `faves-compliance` | CPU-only. Regulatory screening. |
| `dock_molecules` | `autodock-gpu` | **NVIDIA GPU required.** AutoDock-GPU. |
| `dock_with_strain` | `autodock-gpu` + `novomcp-qm` | Docking + GFN2-xTB strain check. |
| `run_molecular_dynamics` | `gromacs-md` | **NVIDIA GPU required.** GROMACS with HMR. |
| `generate_dynamics` | `gromacs-md` | Same. |
| `predict_structure` | `openfold3` | **NVIDIA GPU required.** Or use Chai, Boltz. |
| `get_protein_structure` | (external RCSB PDB) | Requires internet. |
| `run_qm_calculation` | `novomcp-qm` | CPU. xTB / CREST. |
| `run_conformer_search` | `novomcp-qm` | CPU. CREST. |
| `compute_energy` | `novomcp-qm` | CPU. Single-point energy. |
| `optimize_geometry_nnp` | `novomcp-nnp` | GPU or CPU. AIMNet2 / MACE / ANI-2x. |
| `predict_frontier_orbitals` | `novomcp-qm` | CPU. |
| `predict_pka` | `novomcp-properties` | CPU. Trained pKa model. |
| `predict_solubility` | `novomcp-properties` | CPU. |
| `predict_bde` | `novomcp-properties` | CPU. |
| `find_transition_state` | `novomcp-neb` | GPU. NEB via tblite. |
| `parameterize_metal` | `novomcp-qm` | CPU. MCPB.py workflow. |
| `run_novo_ag` | (all of the above for its 11 stages) | Autonomous funnel. |

## Wiring pattern

Every service is wired through an environment variable. Point the engine at wherever the service is running:

```bash
# Local docker
export CHEM_PROPS_URL=http://localhost:8003

# Remote box on your LAN
export CHEM_PROPS_URL=http://10.0.0.42:8003

# Cloud endpoint
export AUTODOCK_GPU_URL=https://autodock.mycompany.com
```

If the env var is unset, the tool returns a structured `service unavailable` error, the engine keeps running, the caller learns immediately, no crash.

Full env-var list per service is documented in each service's page.

## Deployment tiers

**Tier 1, Turnkey CPU-only** (a laptop is enough):
```bash
docker compose up
```
The bundled `docker-compose.yml` runs the engine + optional CPU services (`chem-props`, `addie-models`). Uncomment the blocks you want.

**Tier 2, Add a GPU** (workstation or single cloud GPU instance):
Deploy each GPU service as its own container. See per-service pages below.

**Tier 3, Multi-host** (team deployment):
Engine on one box, GPU services on another. All wired via env vars. See [`deploying-to-cloud/`](../deploying-to-cloud/README.md) for reference AWS / GCP / Azure setups.

**Tier 4, Marketplace one-click** (coming soon):
AWS Marketplace and GCP Cloud Marketplace listings under development.

## Per-service pages

**CPU-only services** (laptop-friendly):
- [`chem-props.md`](./chem-props.md), molecular property calculator (RDKit)
- [`novomcp-qm.md`](./novomcp-qm.md), quantum-mechanical calculations (xTB, CREST, MCPB.py)
- [`novomcp-properties.md`](./novomcp-properties.md), pKa, solubility, BDE
- [`faves-compliance.md`](./faves-compliance.md), regulatory screening
- [`addie-models.md`](./addie-models.md), ADMET prediction (CPU works; GPU is faster)

**GPU services**:
- [`autodock-gpu.md`](./autodock-gpu.md), molecular docking (AutoDock-GPU)
- [`gromacs-md.md`](./gromacs-md.md), molecular dynamics (GROMACS, HMR)
- [`openfold3.md`](./openfold3.md), protein structure prediction (also Chai-1, Boltz-2 compatible)
- [`novomcp-nnp.md`](./novomcp-nnp.md), neural network potentials (AIMNet2, MACE, ANI-2x)
- [`novomcp-neb.md`](./novomcp-neb.md), transition-state search (CI-NEB)

Every service follows the same pattern: docker image at `ghcr.io/novomcp/<service>:latest`, env var `<SERVICE_NAME_UPPER>_URL` wires the engine to it, structured errors when unreachable.

## Building from source

Each compute service has its own repository. The engine talks to them over HTTP; there's no vendored build path in this repo. If you want to modify or rebuild a service, clone its source and follow its own README.
