# Optional data services

Most NovoMCP tools run standalone — RDKit properties, ChEMBL search, OpenTargets queries, ADMET predictions (once `addie-models` is wired), docking, MD, etc.

A handful of tools need **your own data**, loaded into services you host. This page walks through what's optional, what data is needed, and how to wire it. Nothing here blocks the engine from booting.

## What's optional

### Omics database — target discovery + patient stratification

Tools that need it: `target_discovery`, `stratify_patients`.

They read from a PostgreSQL database with three tables preloaded:

| Table | Rows | Source |
|---|---|---|
| `omics_targets` | ~108K target-disease associations | Open Targets Platform + TCGA |
| `omics_pgx` | ~56 pharmacogene records | PharmGKB |
| `omics_resistance` | ~135K resistance variants | ClinVar (pathogenic) |

Any Postgres works — AWS Aurora, RDS, a Docker container, whatever. Point the engine at it via:

```bash
export NOVOMCP_DB_HOST=your-postgres-host.example.com
export NOVOMCP_DB_PORT=5432
export NOVOMCP_DB_NAME=novomcp
export NOVOMCP_DB_USER=novomcp
export DB_PASSWORD=your-password    # or NOVOMCP_DB_SECRET_ID for AWS Secrets Manager
```

Legacy aliases (kept for backwards compat with earlier deploys): `AURORA_HOST`, `AURORA_PORT`, `AURORA_DB`, `AURORA_USER`, `AURORA_SECRET_ID`.

If the database is unset, `target_discovery` and `stratify_patients` return a clean `PostgreSQL database not configured` error naming exactly what's missing.

Data isn't shipped with the OSS engine — it's public reference data (Open Targets, TCGA, ClinVar, PharmGKB), we compiled it into the schema. Future release will publish the compiled schema + data as a downloadable dataset (see `corpus-offload-plan.md`).

### Funnel-persistence backend — cross-run memory

Tools that need it: `save_funnel_stage`, `save_funnel_context`, `save_funnel_memory`, `search_prior_runs`, `list_funnels`, `get_funnel_audit`, `get_pipeline_audit`.

These persist audit + discovery-funnel state to a backing service so future runs can retrieve them via semantic search (`search_prior_runs`).

**Not required for the engine itself** — every tool call already lands in a local file audit sink at `~/.novo/audit.jsonl` (via `FileAuditSink`). The funnel-persistence tools are additive: they enable cross-run learning where "last month's kinase run" can be retrieved by search when you start a new one.

Two ways to wire it:

**A — Point at an HTTP audit/credit-ledger service** (like our hosted `dashboard-aggregator`):

```bash
export FUNNEL_BACKEND_URL=http://your-backend.example.com
# Legacy alias: DASHBOARD_AGGREGATOR_URL
```

**B — Implement a custom `AuditSink`** via the pluggable spine:

```bash
export NOVO_AUDIT=custom
# and provide a spine_custom module implementing AuditSink
```

See [`orchestrator/mcp/spine.py`](https://github.com/NovoMCP/novomcp/blob/main/orchestrator/mcp/spine.py) for the interfaces.

Without either, the tools return clean `requires a funnel-persistence backend` errors. Raw tool calls still audit to `~/.novo/audit.jsonl`.

### Compliance service (regulatory screening)

Tools that consult it: `check_compliance`, plus the compliance blocks attached to `get_molecule_profile`, `screen_library`, `batch_profile`.

The engine treats regulatory compliance as a **capability**, not a specific vendor. Any service that speaks the compliance protocol is valid. Three options:

1. **Open reference implementation** — the `faves-compliance` Docker image ships in this OSS release (see [`deploying-services/faves-compliance.md`](deploying-services/faves-compliance.md)). Runs locally for triage; no paid subscription.
2. **Hosted FAVES API** — the certified operational service at `api.novomcp.com`. Adds SLA, audit-log retention, drift monitoring, and IQ/OQ/PQ documentation for regulated submissions. Paid.
3. **Your own service** — implement the compliance protocol against a different ruleset.

Without any compliance service configured:
- Basic path (RDKit properties + structural alerts via RDKit FilterCatalog) still works
- `check_compliance` is hidden from `tools/list` and returns a structured 503 if called directly
- ADMET/compliance blocks on `get_molecule_profile` come back `null` with an availability flag — never an error

To wire (any backend):
```bash
export NOVOMCP_COMPLIANCE_URL=http://localhost:8004  # or wherever your service runs
```

### Molecule index (similarity + filter)

Tools that consult it: `search_similar`, `filter_molecules`, and the tree-guided retrieval tools (`explore_chemical_space`, `drill_into_cluster`, `vector_search`, `compare_candidates`).

Same pluggable pattern as compliance. Any service that indexes molecules and returns similarity/filter results is valid. Options:

1. **v1.1.5 reference index server** (roadmap) — single-file FastAPI that reads a local parquet slice of the curated 122M corpus. Free, self-hosted, one Docker command.
2. **FAVES hosted API** — the paid path also indexes the corpus.
3. **Your own service** — any implementation of the index protocol.

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
