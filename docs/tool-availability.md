# Tool availability

NovoMCP ships a 67-tool catalog. Which ones are actually usable depends on what you've wired up. The engine only exposes tools whose dependencies are met — no tool clutter, no "unavailable" errors on tools you can't run.

## The three states

| State | What it means | What you see |
|---|---|---|
| **Available** | All the tool's dependencies (env vars, files) are present | Tool appears in `tools/list`, responds to calls |
| **Hidden** | A dependency is missing (e.g. GPU service URL not set) | Tool absent from `tools/list`. Set the required env var to unlock. |
| **Debug mode** | You set `NOVOMCP_SHOW_HIDDEN_TOOLS=1` | Every tool visible regardless of dependencies. Calls to unwired tools return "service unavailable". |

Once you set the env var for a hidden tool's dependency, it appears in `tools/list` on the next `initialize` handshake. No restart required for MCP clients if they refresh their tool list.

## v1 out-of-the-box tools (11)

Install the engine (`pip install -r requirements.txt && python main_https.py`), and you get:

- **RDKit-backed cheminformatics on user-supplied SMILES** — `calculate_properties`, `get_molecule_info`, `get_molecule_profile` (basic path), `batch_profile`, `screen_library`
- **Public-API integrations** (open APIs, no keys required) — `search_chembl`, `search_clinical_trials`, `search_biorxiv`. Transient network failures possible; these hit external services.
- **MD pre-flight (RCSB structure classification)** — `audit_system`
- **Platform meta** — `get_platform_info`
- **Autonomous discovery mode** — `run_novo_ag` (`agm`). In v1 with no compute stack wired, it returns a setup-guide message pointing to which services unlock the full funnel plus a manual-workflow recipe using the always-available tools. Once you configure `NOVOMCP_DB_HOST` + `ADDIE_MODELS_URL` + `AUTODOCK_GPU_URL` + `GROMACS_MD_URL`, it runs the full 11-stage funnel.

No API keys, no databases, no compute services. Every one works on a laptop with just Python + internet.

### What's NOT in v1 (but ships in the codebase, hidden)

- `search_similar`, `filter_molecules` — need a molecule index. Hidden until `NOVOMCP_MOLECULE_INDEX_URL` is set. **Roadmap: v1.1.5 similarity data connector** ships a reference index server that reads a local parquet slice of the same corpus that unblocks omics — no paid service required.
- Compliance path (`check_compliance` + tree tools) — needs a compliance service. Hidden until `NOVOMCP_COMPLIANCE_URL` is set.

### A note on env-var naming

The engine treats molecule indexing and compliance as capabilities, not vendors. FAVES is one valid backend among several (open Kaggle-hosted index, self-hosted parquet, or your own service all work) — but the engine only ever sees the generic `NOVOMCP_*` env var. Point it at whichever backend you have.

## Coming with future releases

Everything else is on the [product roadmap](https://github.com/NovoMCP/novomcp/blob/main/docs/product-roadmap.md) — carved out cleanly, coming in weekly releases, each unlocking a slice of the catalog. Here's the map:

### Omics tools — v1.1.x (SQLite bundle)

Requires: `NOVOMCP_DB_HOST` pointing at a Postgres with the omics schema loaded.

Unlocks: `target_discovery`, `stratify_patients`, `validate_target`.

Roadmap: ship `data/omics.sqlite.gz` + `scripts/load_omics.py`. One-command load into your Postgres.

### Literature search — v1.2.x (PubMed fallback)

Requires today: `PINECONE_API_KEY` + a curated Pinecone index.

Roadmap: fallback to public PubMed E-utilities so OSS users get real literature search without Pinecone.

Unlocks: `search_literature`, `search_patents`.

### Compute services — v1.3.x through v1.6.x

Each service is a Docker image (`ghcr.io/novomcp/<service>:latest`). Point the engine at wherever it's running:

| Env var | Unlocks | Roadmap |
|---|---|---|
| `AUTODOCK_GPU_URL` | `dock_molecules`, `dock_with_strain` | v1.3.x — Modal walkthrough |
| `GROMACS_MD_URL` | `run_molecular_dynamics`, `generate_dynamics` | v1.4.x — Runpod walkthrough |
| `OPENFOLD3_URL` | `predict_structure`, `get_protein_structure`, `get_structure_result` | v1.5.x |
| `NOVOMCP_QM_URL` | 8 quantum-mechanical tools (xTB / CREST / MCPB.py) | v1.6.x |
| `NOVOMCP_NNP_URL` | `compute_energy`, `optimize_geometry_nnp` (AIMNet2 / MACE / ANI-2x) | v1.6.x |
| `NOVOMCP_PROPERTIES_URL` | `predict_pka`, `predict_solubility`, `predict_bde` | v1.6.x |
| `NOVOMCP_NEB_URL` | `find_transition_state` | v1.6.x |
| `ADDIE_MODELS_URL` | `predict_admet` (31 ADMET predictions) | v1.3.x (bundled with docking walkthrough) |
| `NOVOEXPERT_URL` | `predict_clinical_outcomes` | v1.7.x — NovoExpert-3 weights (MIT) |
| `LEAD_OPTIMIZATION_URL` | `lead_optimization` | v1.6.x |
| `MOLMIM_OPTIMIZER_URL` | `optimize_molecule` | v2.x — replacing MolMIM with an OSS generator |

### Funnel-persistence tools — v1.8.x

Requires: `FUNNEL_BACKEND_URL` pointing at an audit/credit-ledger service.

Unlocks: `save_funnel_stage`, `save_funnel_context`, `save_funnel_memory`, `search_prior_runs`, `list_funnels`, `get_funnel_audit`, `get_funnel_context`, `get_pipeline_audit`, `get_credit_usage`, `generate_upload_url`, `get_file_status`, `list_files`, `list_jobs`, `get_job_status`, `cancel_job`.

Roadmap: reference implementation of the backend + docs on plugging in your own via the pluggable spine (`NOVO_AUDIT=custom`).

### Compliance service — v2.0.0

Requires: `NOVOMCP_COMPLIANCE_URL` pointing at any compliance service.

Unlocks: `check_compliance`, enriched paths of `get_molecule_profile` / `screen_library` / `filter_molecules`, `explore_chemical_space`, `drill_into_cluster`, `vector_search`, `compare_candidates`.

Roadmap: FAVES-as-API subscription at `app.novomcp.com/faves` (one valid backend; users can also wire their own).

### Enterprise data connectors — post-v2.0

Hidden from OSS entirely. `push_to_destination`, `pull_from_source` (Snowflake, Databricks, BigQuery, Supabase) are hosted-only tools; no OSS user should see them in `tools/list`.

### Materials Project — user brings API key

Requires: `MP_API_KEY` (free, sign up at [materialsproject.org](https://materialsproject.org/)).

Unlocks: `search_materials_project`.

## Debug mode

To see the full 67-tool catalog regardless of what's wired:

```bash
export NOVOMCP_SHOW_HIDDEN_TOOLS=1
python main_https.py
```

Tools that would normally be hidden show up in `tools/list`. Calls to unwired tools return structured "service unavailable" errors. Useful when developing / debugging or when you want to see everything at once.

## Why hidden and not visible-with-errors

The alternative would be to always show the full 67 tools and return "service unavailable" when unwired ones are called. That's what NovoMCP used to do. It felt like a broken product.

Hidden-until-wired means:
- **Users see a working demo, not a lot of gray options.** First impressions matter.
- **`tools/list` is the truth.** Every tool in it works. LLM clients that pick tools autonomously (Claude, Cursor, Zed) never try something that will fail.
- **Progressive disclosure.** As users add services, tools appear. The 13-tool starter set grows into the full catalog only when they've earned it by configuring what backs it.

## Related

- [Quickstart](quickstart.md) — get the 13 default tools running
- [Deploying services](deploying-services/README.md) — wire up compute services to unlock more tools
- [Optional data services](optional-data-services.md) — data + auxiliary services
- [Product roadmap](https://github.com/NovoMCP/novomcp/blob/main/docs/product-roadmap.md) *(private)* — timing on which tools ship in which release
