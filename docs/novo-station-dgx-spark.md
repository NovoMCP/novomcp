# Run the NovoMCP compute core on an NVIDIA DGX Spark

**Novo Station** is the NovoMCP engine's compute core — GPU compute included — running on one NVIDIA DGX Spark (GB10), with the web dashboard alongside it. You pull prebuilt images and run; you don't build anything. Computation runs on the box, and the engine itself needs no API key. Three default tools query public databases (ChEMBL, RCSB and bioRxiv), and compliance screening, if you add it, is called on the paid API at api.novomcp.com.

The compute core ports in full: MACE / ANI-2x neural-network potentials, the NVIDIA ALCHEMI toolkit, GROMACS GPU molecular dynamics, AutoDock-GPU, xTB / CREST / sTDA, and the 31 ADMET models. (Structure prediction and the generative / free-energy paths aren't in this port — see the [field report](https://www.novomcp.com/news/what-actually-breaks-when-you-port-a-chemistry-stack-to-dgx-spark).)

!!! note "Image availability"
    Every image in the stack is published for arm64 and resolves automatically on the Spark — the engine, the dashboard (`web`), `chem-props`, `addie-models` and `novomcp-properties` as multi-arch `:latest`; the compiled GPU/native services (`novomcp-nnp`, `gromacs-md`, `autodock-gpu`, `novomcp-qm`) as the arm64 `:spark` tag.

## Prerequisites

- An NVIDIA DGX Spark (GB10, aarch64, CUDA 13, driver ≥ 580) or GB10-class box.
- Docker + the NVIDIA Container Toolkit — `docker run --gpus all ... nvidia-smi` must print the GPU.
- Docker Compose v2 — `docker compose version` must work. Some GB10 images (e.g. cloud-hosted Sparks on stock Ubuntu 24.04) ship Docker without the Compose plugin; install it with `sudo apt-get install docker-compose-v2`.
- Disk: the GPU images are large (the NNP CUDA-13 image is ~17 GB); budget ~40 GB for the set.

## 1. Pull the images

Multi-arch images auto-select arm64 on the Spark:

```bash
# Engine + CPU services (multi-arch :latest)
docker pull ghcr.io/novomcp/novomcp:latest           # the engine
docker pull ghcr.io/novomcp/web:latest               # the web dashboard
docker pull ghcr.io/novomcp/chem-props:latest        # RDKit properties / similarity
docker pull ghcr.io/novomcp/addie-models:latest      # 31 ADMET models (DGL/GIN)
docker pull ghcr.io/novomcp/novomcp-properties:latest # aqueous solubility, bond dissociation energies

# GPU / native services (arm64 :spark builds)
docker pull ghcr.io/novomcp/novomcp-nnp:spark        # MACE / ALCHEMI + ANI-2x   (GPU)
docker pull ghcr.io/novomcp/gromacs-md:spark         # GPU molecular dynamics    (GPU)
docker pull ghcr.io/novomcp/autodock-gpu:spark       # GPU docking               (GPU)
docker pull ghcr.io/novomcp/novomcp-qm:spark         # xTB / CREST / sTDA        (CPU)
```

## 2. Bring the stack up

Grab the Spark compose file and start the stack:

```bash
curl -O https://raw.githubusercontent.com/NovoMCP/novomcp/main/compose.spark.yml
docker compose -f compose.spark.yml up -d
docker compose -f compose.spark.yml ps     # every service "running"
```

The engine comes up on **http://localhost:8018** and the dashboard on **http://localhost:3000**, with every compute service wired to both by URL on the internal network. The NNP image sets the two Grace environment fixes (`OMP_NUM_THREADS`, `LD_PRELOAD=libjemalloc`) in its own entrypoint — you don't touch them. The single GB10 GPU is time-shared across the GPU services.

## 3. First run

**Point an MCP client at it.** Any MCP-compatible assistant (Claude Desktop, `ollmcp`, an IDE) connects to `http://localhost:8018` and discovers the tools. Pair it with a local LLM (for example Ollama on the same box) and the agent runs on the Spark too. Computation stays local; the three default tools that query public databases (ChEMBL, RCSB and bioRxiv) still reach the network.

**Or call a tool directly** to confirm the GPU path end-to-end — a MACE geometry relaxation of ethanol on the GPU:

```bash
curl -s -X POST http://localhost:8018/mcp/tools/optimize_geometry_nnp \
  -H 'X-API-Key: local' -H 'Content-Type: application/json' \
  -d '{"arguments": {"smiles": "CCO", "method": "mace"}}'
```

It returns a relaxed energy near **−46.2 eV** — the MACE-MPA-0 reference. A quantum single point (`run_qm_calculation`, GFN2-xTB) and GPU docking (`dock_molecules`) work the same way.

**The autonomous funnel.** The engine's agent loop (`POST /v1/agent/chat`) can drive a full pipeline. On-box, the materials funnel runs entirely local — an on-device model calls the NNP and QM tools on the GPU and ranks candidates, with every step written to the local audit log. (The drug-discovery funnel additionally needs the cloud data layer — omics and literature — which isn't on the Spark. Compliance comes from NovoMCP's service on the paid API; see step 5.)

## 4. Open the dashboard

Open **http://localhost:3000** on the Spark. If the Spark is remote, forward the port over SSH and open the same address on your own machine:

```bash
ssh -L 3000:localhost:3000 -L 8018:localhost:8018 you@your-spark
```

- **Dashboard** shows the engine, how many tools are unlocked, and a tile per capability. On a fresh Novo Station the on-box services (ADMET, docking, dynamics, quantum, NN potentials) show as on, because the compose file wires them all.
- **Connections** lists every service with its live status. The services under *Self-hosted compute* are the ones running on the box. Structure prediction (OpenFold3) shows as not configured: it isn't part of the Spark port.
- **Profile** computes properties for a SMILES, with ADMET from the on-box ADMET service and an on-demand compliance check once compliance is connected.

What you save in Connections is written to `credentials.env` on the shared `novomcp-audit` volume, which the engine loads at startup. After saving, restart the engine:

```bash
docker compose -f compose.spark.yml restart engine
```

## 5. Add compliance screening (optional, paid)

NovoMCP's compliance service — controlled-substance and regulatory screening — is closed source and isn't on the box. The engine calls it on the paid API, and each check is billed to the account that owns your API key.

In the dashboard: **Connections → NovoMCP compliance**, set

- **Service URL:** `https://api.novomcp.com/v1/compliance`
- **API key:** your `nmcp_…` key

then restart the engine as above. The tile turns **connected** once the key is accepted (a bad key shows **key rejected**; checking the key costs nothing). `check_compliance` then appears in the tool list, and Profile gets a **Run compliance check** button.

Without the dashboard, add the same two values to the engine's environment in `compose.spark.yml` and run `docker compose -f compose.spark.yml up -d`:

```yaml
      NOVOMCP_COMPLIANCE_URL: https://api.novomcp.com/v1/compliance
      NOVOMCP_COMPLIANCE_API_KEY: nmcp_...
```

## Known limits on GB10 (measured)

!!! info "Measured on GB10"
    - **FP64 is slow** — ~0.43 TFLOP/s vs ~14.6 FP32 (~34×). Keep MACE geometry optimization in float32 on the GPU; QM double precision stays on CPU.
    - **Memory bandwidth** ~227 GB/s (~82% of the 273 GB/s spec). MD is bandwidth-bound — a GB10 runs this workload at roughly 0.4× an L40S (~2.5× slower), at ~56–66 W vs the L40S's measured ~190–215 W. The trade is efficiency and a self-contained box, not raw speed.
    - **128 GB unified memory** — helps the host-side setup of very large systems; the GPU-resident footprint of this MD stays modest.

Images were validated on NVIDIA GB10 (DGX Spark), CUDA 13.0, DGX OS (drivers 580.14x / 580.173.02); the MACE validation and the water benchmark were reproduced on a second, independent GB10. Pin your own DGX-OS / driver versions beside any number you reuse.

## Further reading

- The full field report — what broke, why, and the recipe: [What actually breaks when you port a chemistry stack to DGX Spark](https://www.novomcp.com/news/what-actually-breaks-when-you-port-a-chemistry-stack-to-dgx-spark).
- The reproducible MACE + GROMACS workload recipe, as a [DGX Spark playbook](https://github.com/NVIDIA/dgx-spark-playbooks).
- Per-service setup and GPU requirements: [Deploying services](deploying-services/README.md).
- Use the engine from PubChem pages or Word manuscripts with the [Chrome and Word add-ins](sideload-add-ins.md); an engine on a Spark across your network works with both.
