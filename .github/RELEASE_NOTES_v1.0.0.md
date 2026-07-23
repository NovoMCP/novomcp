# v1.0.0 — Public launch

**NovoMCP is the open computational chemistry engine for drug discovery and materials science.**

`git clone`, `pip install`, `python main_https.py`. **11 tools work out of the box** on a fresh install (RDKit properties + structural alerts, ChEMBL / ClinicalTrials / bioRxiv search, autonomous discovery mode, platform meta). The other **56 tools in the catalog** unlock as you wire the optional services they need. Real property values in-process via RDKit; zero external services required at start.

## Highlights

- **67-tool catalog** covering cheminformatics, ADMET, molecular docking, molecular dynamics, protein structure prediction, quantum-mechanical calculations, autonomous discovery funnel, literature search, and regulatory compliance. **11 always-available** locally; the rest are hidden until you wire the backing service, so `tools/list` never lies about what works.
- **Turnkey local run.** No env vars, no keys, no databases. Fresh clone boots in ~2 seconds and returns real RDKit properties for any SMILES.
- **MCP JSON-RPC** at `/mcp/` — usable from any MCP-compatible AI assistant (Claude Desktop, Cursor, Codex, Zed, Cline, and others).
- **REST API** with a curated OpenAPI 3.1 spec at `/v1/openapi.json`.
- **Autonomous discovery mode** — `agm <disease>` in your MCP client returns an 11-stage discovery-funnel protocol the LLM executes. When the compute stack isn't wired, it returns a setup-guide with a manual-workflow recipe using the always-available tools.
- **Pluggable spine** (auth / credit metering / audit). Local defaults ship; swap in your own via three protocols in `orchestrator/mcp/spine.py`.
- **Pluggable LLM** (OpenAI / Anthropic / Ollama / Azure OpenAI). Auto-detects from present credentials. Optional; every tool call works without it.
- **Provider-agnostic env vars.** `NOVOMCP_COMPLIANCE_URL`, `NOVOMCP_MOLECULE_INDEX_URL`, etc. — the engine treats compliance and molecule indexing as capabilities, not vendors. FAVES is one valid backend among several.
- **Cloud reference deploys** for AWS, GCP, and Azure.
- **Docker Compose** for a single-command local stack (`docker compose up`).

## Install (choose one path)

Requires Python 3.10+ (3.9 hit EOL October 2025).

**Path 1 — git clone (works everywhere):**
```bash
git clone https://github.com/NovoMCP/novomcp.git
cd novomcp/orchestrator
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main_https.py
```

**Path 2 — Docker Compose (no Python setup needed):**
```bash
git clone https://github.com/NovoMCP/novomcp.git
cd novomcp
docker compose up
```

**Coming in v1.0.1** (~1-2 weeks post-launch): `pip install novomcp` from PyPI and prebuilt Docker image at `ghcr.io/NovoMCP/novomcp:latest`.

## First tool call

```bash
curl -X POST http://localhost:8018/mcp/tools/calculate_properties \
  -H 'Authorization: Bearer x' \
  -H 'Content-Type: application/json' \
  -d '{"arguments": {"smiles": "CC(=O)Oc1ccccc1C(=O)O"}}'
```

Returns real aspirin properties (MW 180.16, LogP 1.31, TPSA 63.6, QED 0.55) computed in-process via RDKit.

## Adding compute

Every non-trivial tool (docking, MD, structure prediction, ADMET, etc.) lights up when you wire its compute service. Two ways:

- **Docker Compose**: uncomment the service block in `docker-compose.yml`, `docker compose up`.
- **Standalone**: `docker run` the service on any host, `export <SERVICE>_URL=http://<host>:<port>`.

See [`docs/deploying-services/`](docs/deploying-services/) for per-service guides.

## Adding an LLM (optional)

Set any one of these and the optional intent recognition, orchestration planning, and semantic tool search features light up:

```bash
export OPENAI_API_KEY=sk-...                     # OpenAI (default)
# or
export ANTHROPIC_API_KEY=sk-ant-...              # Claude
# or
export NOVO_LLM=ollama                           # Local Ollama at :11434
```

See [`docs/configuring-llm.md`](docs/configuring-llm.md).

## Licensing

- **Top-level tree**: Apache-2.0
- **Orchestration core** (`orchestrator/mcp/`): BSL 1.1 with a change date of 2029-07-12 to Apache-2.0
- **Companion model artifacts**: MIT

Full third-party attribution in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md).

## Ships separately

By design, the FAVES certified hosted API (paid — SLA + audit-retention + drift monitoring + IQ/OQ/PQ), curated enriched datasets, Novo AG autonomous heuristics, and trained clinical-outcomes model weights live separately. The **open reference implementation** of the FAVES framework ships as the `faves-compliance` Docker image — self-host for triage; route the certified record-of-audit calls to the paid API when you need them.

## Companion repos

- [`novomcp-chrome-sideload`](https://github.com/NovoMCP/novomcp-chrome-sideload) — Chrome extension, load unpacked, configurable engine URL.
- [`novomcp-word-sideload`](https://github.com/NovoMCP/novomcp-word-sideload) — Word add-in, sideload manifest, same configurable engine URL.

## Reading further

- [Quickstart](docs/quickstart.md)
- [Tool availability](docs/tool-availability.md) — what unlocks with which env var
- [Deploying services](docs/deploying-services/)
- [Deploying to a cloud](docs/deploying-to-cloud/)
- [Configuring an LLM](docs/configuring-llm.md)
- [Engineering stories](docs/engineering-stories/) — postmortems and design notes on specific technical bets
- [Product roadmap](docs/product-roadmap.md) — coming weekly

## What's next

- **v1.0.1** (week 1-2 post-launch): `pip install novomcp` + prebuilt GHCR Docker image
- **v1.0.5**: Chrome + Word sideload UX polish
- **v1.1.x**: Omics data pack (SQLite bundle) — unlocks target discovery
- **v1.1.5**: Similarity data connector — unlocks molecule-index tools without paid FAVES

Weekly release cadence; every release comes with a feature landing and a marketing beat. Star + watch the repo to follow along.
