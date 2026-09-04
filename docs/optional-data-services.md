# Optional data services

Most NovoMCP tools run standalone — RDKit properties, ChEMBL search, OpenTargets queries, ADMET predictions (once `addie-models` is wired), docking, MD, etc.

A handful of tools need **your own data**, loaded into services you host. This page walks through what's optional, what data is needed, and how to wire it. Nothing here blocks the engine from booting.

## What's optional

### Omics database — target discovery + patient stratification

Tools that need it: `target_discovery`, `validate_target`, `stratify_patients`.

Two ways to provide the omics data — a **local SQLite pack** (easiest, no server) or **your own Postgres**. Either works; the engine picks the pack automatically when it's present.

#### Option A — the SQLite data pack (no database server)

Install a pack and the engine routes `omics.*` reads to it automatically — the tools light up with no `NOVOMCP_DB_HOST` set:

```bash
# target discovery:
python scripts/omics-pack/install_omics_pack.py omics-core.sqlite.gz
# + patient stratification (PGx layer):
python scripts/omics-pack/install_omics_pack.py omics-core.sqlite.gz omics-pgx.sqlite.gz
```

Packs merge into `~/.novo/omics/omics.db` (override with `NOVOMCP_OMICS_DB`). They ship as **two license-distinct downloads**:

| Pack | Tables | Powers | License |
|---|---|---|---|
| **omics-core** | `omics_targets` (~108K), `omics_perturbation`\*, `omics_resistance` (~135K) | `target_discovery`, `validate_target` | CC-BY-4.0 |
| **omics-pgx** | `omics_pgx` (~56) | `stratify_patients` (PGx layer) | CC-BY-SA-4.0 + ODbL — opt-in |

\* Permissive sources only (TCGA / GTEx / Expression Atlas CC0 / SRA); NonCommercial sources (e.g. DisGeNET) are excluded at export time. Full per-source attribution is in each pack's `NOTICE` file. `omics-pgx` is a separate opt-in pack because PharmGKB (CC-BY-SA) and gnomAD (ODbL) impose ShareAlike terms that must not mix into the Apache-2.0 engine or the permissive core.

Maintainers build the packs from a Postgres holding the `omics` schema — see `scripts/omics-pack/`.

#### Option B — your own Postgres

Any Postgres works — AWS Aurora, RDS, a Docker container. Load the `omics` schema, then point the engine at it:

```bash
export NOVOMCP_DB_HOST=your-postgres-host.example.com
export NOVOMCP_DB_PORT=5432
export NOVOMCP_DB_NAME=novomcp
export NOVOMCP_DB_USER=novomcp
export DB_PASSWORD=your-password    # or NOVOMCP_DB_SECRET_ID for AWS Secrets Manager
```

Legacy aliases (kept for backwards compat with earlier deploys): `AURORA_HOST`, `AURORA_PORT`, `AURORA_DB`, `AURORA_USER`, `AURORA_SECRET_ID`.

---

If **neither** a pack nor a Postgres is configured, `target_discovery` / `validate_target` / `stratify_patients` are hidden from `tools/list` (and return a clean "not configured" error if invoked), naming exactly what's missing.

The data is public reference data (Open Targets, UniProt, Reactome, ClinVar, PharmGKB, CPIC, gnomAD) compiled into the schema; it's distributed as the downloadable packs above, not bundled in the engine repo.

### Funnel-persistence backend — cross-run memory

Tools that need it: `save_funnel_stage`, `save_funnel_context`, `save_funnel_memory`, `search_prior_runs`, `list_funnels`, `get_funnel_audit`, `get_pipeline_audit`.

These persist audit + discovery-funnel state to a backing service so future runs can retrieve them via semantic search (`search_prior_runs`).

**Not required for the engine itself** — every tool call already lands in a local file audit sink at `~/.novo/audit.jsonl` (via `FileAuditSink`). The funnel-persistence tools are additive: they enable cross-run learning where "last month's kinase run" can be retrieved by search when you start a new one.

Two ways to wire it:

**A — Point at an HTTP audit/usage-ledger service** (like our hosted `managed backend`):

```bash
export FUNNEL_BACKEND_URL=http://your-backend.example.com
```

**B — Implement a custom `AuditSink`** via the pluggable spine:

```bash
export NOVO_AUDIT=custom
# and provide a spine_custom module implementing AuditSink
```

See [`orchestrator/mcp/spine.py`](https://github.com/NovoMCP/novomcp/blob/main/orchestrator/mcp/spine.py) for the interfaces.

Without either, the tools return clean `requires a funnel-persistence backend` errors. Raw tool calls still audit to `~/.novo/audit.jsonl`.

### Compliance service (generic hook)

The tool that consults it: `check_compliance`.

`check_compliance` is a **generic, unbranded hook**. The engine bundles no ruleset of its own — it simply forwards the molecule and context to whatever service you wire and returns that service's response. Bring your own compliance backend (regulatory screening, an internal policy service, an observability sink — anything that speaks the protocol).

Without a compliance service configured:
- Basic path (RDKit properties + structural alerts via RDKit FilterCatalog) still works
- `check_compliance` is hidden from `tools/list` and returns a structured 503 if called directly

To wire:
```bash
export NOVOMCP_COMPLIANCE_URL=http://localhost:8004  # or wherever your service runs
```

### Molecule index (similarity + filter)

Tools that consult it: `search_similar`, `filter_molecules`.

The engine treats molecule indexing as a **capability**, not a specific vendor. Any service that indexes molecules and returns similarity/filter results is valid. Options:

1. **Reference index server** — [`NovoMCP/similarity-index`](https://github.com/NovoMCP/similarity-index): FPSim2 exact-Tanimoto search over the open corpus, free and self-hosted. Build an index from the public corpus and serve it in one Docker command:
   ```bash
   docker run -p 8080:8080 -v "$PWD/corpus:/data" ghcr.io/novomcp/similarity-index serve --index /data/index
   ```
   Measured latency is threshold-dependent (sub-second at typical thresholds ≥0.7; looser thresholds grow with corpus size). See the repo's README to build the index.
2. **Your own service** — any implementation of the index protocol (`POST /api/search/similar`, `POST /api/search/filter`).

Note: the **tree-guided retrieval** tools (`explore_chemical_space`, `drill_into_cluster`, `vector_search`, `compare_candidates`) rely on an approximate vector index + cluster summaries, which the exact-Tanimoto reference server does not provide — they stay hidden unless you wire a backend that implements them.

Without any molecule index configured, the index-backed tools are hidden from `tools/list`. The RDKit-in-process path still handles user-supplied SMILES (`calculate_properties`, `get_molecule_profile` basic path, `batch_profile`, `screen_library`).

To wire (any backend):
```bash
export NOVOMCP_MOLECULE_INDEX_URL=http://localhost:8080
```

### Literature + patent search — Pinecone (v1.2.x adds public PubMed fallback)

Tools that need it: `search_literature`, `search_patents`.

These query a Pinecone vector index of curated papers and USPTO patents. Without a Pinecone key, both tools are hidden from `tools/list`. `search_biorxiv` and `search_clinical_trials` use public APIs and don't need Pinecone — they work out of the box.

To wire:
```bash
export PINECONE_API_KEY=your-key
export PINECONE_LITERATURE_INDEX=novomcp-literature-v2
export PINECONE_PATENTS_INDEX=novomcp-patents-v1
```

## What's not optional

The following are self-contained in the engine and require no external services beyond what's listed in `requirements.txt`:

- `calculate_properties`, `get_molecule_info` — RDKit descriptors
- `search_similar` — Morgan fingerprint similarity (basic path)
- `search_chembl` — public ChEMBL REST API
- `search_clinical_trials` — public ClinicalTrials.gov API
- `run_novo_ag` — returns the discovery-funnel prompt template
- Every read-side funnel tool (they work locally with the file audit sink)

## What requires GPU compute

Separately from data: some tools need GPU-backed compute services (docking, MD, protein structure). See [Deploying services](deploying-services/README.md).

## Fully local mode

If you set nothing beyond what `pip install -r requirements.txt` provides, the engine boots and returns clean "service unavailable" errors for anything requiring external data or compute. Every tool that CAN work locally does — no crashes, no cryptic errors. Add services incrementally as you need them.
