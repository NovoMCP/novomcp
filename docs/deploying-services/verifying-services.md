# Verifying your services

A running container is not the same as a working tool. `docker ps` showing "Up", or `/health` returning `200`, tells you the process started, not that the tool returns **real** results. This page gives a one-shot "does it actually compute" check for each service — the same call the engine makes, with the real output you should see.

NovoMCP never returns mock or fallback data. So the pass condition is simple: **the call returns physically-meaningful numbers that change with the input.** Constant/placeholder values, or a structured `service unavailable`, mean it isn't wired or isn't working — investigate, don't ship.

## The two checks

1. **Liveness** — `GET /health`. Is the service up and are its models loaded?
2. **Contract** — the exact call the engine's tool makes. Does it return real data?

Both use the service's URL, the same one you wired the engine to (`<SERVICE>_URL`). Substitute your own host:port.

## Before you start (GPU services)

- You need an **NVIDIA GPU** (CUDA). `/health` should report the GPU as available.
- **Cold start:** on a scale-from-zero setup, the *first* call spins a GPU node/container up — that can take **~2–3 minutes**. The first request may hang or warm-return; that latency is expected, not a failure. Subsequent calls hit the warm service; it scales back down when idle.
- **Auth:** if a service enforces an inbound API key, pass it in the header the engine uses — **`X-API-Key`** by default, or **`API-Key`** for `gromacs-md` and `alphaflow`. Set the value to the key the service is configured with (its own `API_KEY`). Services with no inbound key configured (the default for a fresh self-host) need no header.

---

## Synchronous services — verify with one curl

### `novomcp-nnp` — geometry optimization

```bash
# Liveness
curl -s "$NOVOMCP_NNP_URL/health"
# → {"status":"healthy","models":{"ani2x":{"available":true},"mace":{"available":true}}, ...}

# Contract — relax ethanol
curl -s -X POST "$NOVOMCP_NNP_URL/api/optimize-geometry" \
  -H 'Content-Type: application/json' \
  -d '{"smiles":"CCO","method":"auto"}'
```

**Pass:** `"converged": true`, a 9-atom `optimized_xyz`, a real `energy_ev` (ethanol lands near **−4217 eV** with ANI-2x), `forces_max_ev_ang` below the `fmax` threshold, ~a dozen BFGS steps. Change the SMILES and the geometry/energy change.

### `autodock-gpu` — molecular docking

```bash
# Liveness
curl -s "$AUTODOCK_GPU_URL/health"
# → {"status":"healthy","compute_available":{"gpu":true,...}}

# Contract — redock benzamidine into trypsin (3PTB), a classic control
curl -s -X POST "$AUTODOCK_GPU_URL/dock" \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $AUTODOCK_GPU_API_KEY" \
  -d '{"ligand_smiles":"NC(=N)c1ccccc1","protein_pdb_id":"3PTB",
       "exhaustiveness":8,"num_poses":3,"auto_detect_binding_site":true,
       "use_addie_reranking":false,"enable_reference_docking":false}'
```

**Pass:** `"status":"completed"`, `compute_backend:"gpu_local"`, a real `best_score` (benzamidine→trypsin lands near **−6.7 kcal/mol**), and `poses[]` with 3D coordinates and a `contacts` list. A correct redock finds the **S1 pocket** — H-bonds to `GLY216`/`CYS191`. (Omit the `X-API-Key` header if your service has no inbound key set.)

---

## Async services — verify through the tool

`gromacs-md`, `openfold3`, and `alphaflow` are **asynchronous**: you submit a job and poll for the result. The cleanest verification is to call the tool through the engine (which submits and smart-polls for you) rather than curling by hand:

| Tool | Service | Pass condition |
|---|---|---|
| `run_molecular_dynamics` | `gromacs-md` | a completed run with a real trajectory + energies (job-dispatched; needs its worker running) |
| `predict_structure` | `openfold3` | a returned structure — confirms the model/NIM backend is reachable |
| (ensemble generation) | `alphaflow` | a completed ensemble |

Because these run for minutes and (for `openfold3`) may proxy an external model backend, treat "job accepted → job completed with a real artifact" as the pass, and expect the first call to absorb the cold-start window above.

---

## CPU services

Same two checks, no GPU and no cold start. For example:

```bash
curl -s "$CHEM_PROPS_URL/health"
curl -s -X POST "$CHEM_PROPS_URL/api/properties" -H 'Content-Type: application/json' -d '{"smiles":"CC(=O)Oc1ccccc1C(=O)O"}'
# → real RDKit descriptors for aspirin (MW 180.16, LogP ~1.31, TPSA 63.6, QED ~0.55)
```

---

## If a check fails

- **`service unavailable` from the engine** → the `<SERVICE>_URL` env var is unset or wrong. See [`README.md`](./README.md#wiring-pattern).
- **`Invalid API key`** → the service enforces an inbound key; pass the right header (`X-API-Key` / `API-Key`) with the service's configured key.
- **Pod/container up but `/health` never ready** → check GPU visibility inside the container (`nvidia-smi`), the NVIDIA runtime/device plugin, and model weights.
- **First call hangs then works** → normal scale-from-zero cold start; not a bug.
- **Constant or suspiciously round output** → that's the one thing this project doesn't ship. Real compute varies with input; if it doesn't, something upstream is short-circuiting.
