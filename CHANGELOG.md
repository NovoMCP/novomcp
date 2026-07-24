# Changelog

All notable changes to NovoMCP are recorded here. The format is [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.2] - 2026-07-24

### Changed
- Install docs: recommend Python 3.11 or 3.12 explicitly and note that the pinned `numpy`/`rdkit` don't publish wheels for Python 3.13/3.14, so fresh machines on system Python hit a source-build wall. No functional change.

### Fixed
- `search_chembl`: retry the EBI ChEMBL API with exponential backoff on transient upstream errors (5xx / timeouts) before surfacing the failure. Still returns the upstream error honestly if EBI stays down — no fallback data.

## [1.1.1] - 2026-07-23

### Fixed
- MCP-over-HTTP: standards-compliant Streamable HTTP clients (e.g. `ollmcp`) now connect end-to-end in local mode. Added the server→client SSE stream on the MCP endpoint (its absence surfaced in strict clients as `unhandled errors in a TaskGroup`), made local mode auth-less for MCP clients so no `Authorization` header is required, and fixed `tools/list` failing on core-tier tools.
- `initialize` now reports the real engine version in `serverInfo` (sourced from `version.py`) instead of a hardcoded value.

## [1.1.0] - 2026-07-23

### Changed
- Engine, dashboard, and documentation updates.

## [1.0.0] - 2026-07-21 — first public release

**The open computational chemistry engine for drug discovery and materials science.**

### What's in the box

**The engine**
- 67 MCP tools spanning cheminformatics, ADMET prediction, molecular docking, molecular dynamics, protein structure prediction, quantum-mechanical calculations, autonomous discovery funnel, literature/patent search, regulatory compliance, and file intelligence
- Full REST API (`POST /v1/tools/{name}`) with a curated OpenAPI at `/v1/openapi.json`
- MCP JSON-RPC endpoint at `/mcp/` with the 2024-11-05 protocol
- 247 registered HTTP routes across MCP, REST, OAuth, admin, WebSocket, and health surfaces
- Boots turnkey: `pip install -r requirements.txt && python3 main_https.py`. Zero env vars required.

**In-process tool execution**
- `calculate_properties`, `search_similar`, and the basic path of `get_molecule_profile` run against RDKit locally with no downstream service. Real values for aspirin: MW 180.16, LogP 1.31, TPSA 63.6, QED 0.55, Lipinski pass.
- All other tools return structured `service unavailable` errors when their downstream compute is unwired. No crashes.

**Pluggable spine (auth, credit metering, audit)**
- `LocalAuthGate` (default): every request resolves to a local user with unlimited access, no keys required
- `NoopMeter` (default): zero credit accounting, always success
- `FileAuditSink` (default): every tool call lands as a JSON-line in `~/.novo/audit.jsonl` with tool name, funnel_id, success, credits, execution time, surface, and truncated error
- `custom` implementations swap in via `NOVO_AUTH=custom` / `NOVO_METER=custom` / `NOVO_AUDIT=custom` and a `spine_custom` module

**Pluggable LLM (OpenAI, Anthropic, Ollama, Azure OpenAI)**
- Auto-detects a provider from present credentials (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AZURE_OPENAI_API_KEY`, or a running Ollama at `http://localhost:11434`)
- Override with `NOVO_LLM=openai|anthropic|ollama|azure|disabled`
- Optional: intent recognition, orchestration planning, project enrichment, semantic tool search, autonomous campaign decisions. None required to run tool calls.

**Deployment**
- `docker compose up` brings up the engine and any subset of compute services you uncomment (chem-props, addie-models, autodock-gpu, gromacs-md, openfold3, novomcp-qm, novomcp-nnp, novomcp-neb, novomcp-properties, faves-compliance)
- Per-service deployment guides at `docs/deploying-services/` covering pre-reqs, deploy commands, env wiring, verification, and cost estimates for CPU vs GPU vs multi-GPU tiers
- Cloud reference deploys at `docs/deploying-to-cloud/` for AWS, GCP, and Azure with three tiers each: single VM (docker compose), managed Kubernetes (EKS/GKE/AKS), and serverless spine + on-demand GPU (Fargate/Cloud Run/Container Apps)

**Surfaces**
- Next.js dashboard (`frontend-nextjs/`) as an OSS subset (hosted-product-only pages like billing/keys/team excluded)
- Express/TypeScript MCP gateway (`novomcp-apps/`)
- Backend-configurable via `NOVOMCP_ENGINE_URL` env var, localhost defaults

### Licensing

- **Top-level tree** (surfaces, wrappers, connectors, protocol docs): Apache-2.0 (`LICENSE`)
- **Orchestration core** (`novomcp/mcp/`): Business Source License 1.1 with a change date of 2029-07-12 to Apache-2.0 (`LICENSE.core`)
- **Pre-trained model weights** in companion repositories (e.g., `novoexpert1-tdc-benchmark`): MIT

### What is not in this repo

By design, the following live separately:
- The FAVES operational compliance service (curated ruleset + certified deployment; the reference implementation ships here)
- Curated enrichment datasets at 122M-molecule scale
- Novo AG autonomous discovery-funnel heuristics
- Trained clinical outcomes model weights

None of these are required for the engine to run.

### Verified startup output

```
INFO  Loaded 17 service configurations
INFO  Starting NovoMCP Orchestration Service
INFO  HTTP client initialized
INFO  Spine assembled: auth=LocalAuthGate meter=NoopMeter audit=FileAuditSink
INFO  NovoMCP initialized with 67 tools
INFO  NovoMCP OAuth initialized
INFO  MCP root handler initialized
INFO  Application startup complete.
INFO  Uvicorn running on http://0.0.0.0:8018
```

Then `curl http://localhost:8018/health` returns `{"status":"healthy","service":"novomcp","redis":"disabled","services_available":31}` and `curl -X POST http://localhost:8018/mcp/tools/calculate_properties -H 'Authorization: Bearer x' -d '{"arguments":{"smiles":"CCO"}}'` returns real RDKit values.

[Unreleased]: https://github.com/NovoMCP/novomcp/compare/v1.1.2...HEAD
[1.1.2]: https://github.com/NovoMCP/novomcp/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/NovoMCP/novomcp/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/NovoMCP/novomcp/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/NovoMCP/novomcp/releases/tag/v1.0.0
