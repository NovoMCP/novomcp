# NovoMCP

**The open computational chemistry engine for drug discovery and materials science.**

NovoMCP is an agent-callable engine that exposes molecular intelligence, cheminformatics, ADMET prediction, molecular docking, molecular dynamics, quantum-mechanical calculations, protein structure prediction, and a governed 11-stage discovery funnel, through the [Model Context Protocol](https://modelcontextprotocol.io) and a curated REST API.

One engine. Two domains. Four surfaces.

- **Surfaces:** `novo` CLI, MCP connector, REST API, Workbench (desktop + web), Next.js dashboard.
- **Wrappers over open compute:** RDKit, GROMACS, AutoDock-GPU, OpenFold, Chai, Boltz, Gnina, xTB, ANI-2x, AIMNet2, MACE.
- **Governance:** every tool call carries a `funnel_id` and lands in a pluggable audit sink. Local mode writes JSON-lines to `~/.novo/audit.jsonl`; hosted mode writes to a certified audit service.

## Quickstart

**Requires Python 3.10 or later.** Python 3.9 hit end-of-life October 2025; several transitive deps require 3.10+. Check what you have:

```bash
python3 --version
```

If it says `Python 3.10.x` or newer, you're set — skip ahead. Otherwise install a supported Python via whatever channel fits your setup:

- **macOS with Homebrew:** `brew install python@3.11`
- **macOS with the official installer:** download from [python.org/downloads](https://www.python.org/downloads/)
- **Ubuntu / Debian:** `sudo apt install python3.11 python3.11-venv`
- **Fedora / RHEL:** `sudo dnf install python3.11`
- **Windows:** [python.org/downloads](https://www.python.org/downloads/) installer, or use WSL and follow the Linux path
- **pyenv (any OS):** `pyenv install 3.11.9 && pyenv local 3.11.9`
- **Docker / devcontainer:** use `python:3.11-slim` as your base image
- **`asdf`, `mise`, `uv`, `conda`, `mamba`:** all fine — install any 3.10+

Then run the engine locally with no external services:

```bash
git clone https://github.com/<your-org>/novomcp.git
cd novomcp/orchestrator
python3.11 -m venv .venv && source .venv/bin/activate    # or whichever python 3.10+ binary you installed
python -m pip install --upgrade pip
pip install -r requirements.txt
python main_https.py
```

If you accidentally create the venv with Python 3.9, `python main_https.py` fails fast with an actionable message telling you which install command to run.

The `pip install --upgrade pip` step avoids a common failure mode on macOS/Linux where an older pip can't resolve `psycopg2-binary` wheels; it's optional if you already have pip 23+.

That's it. The engine boots with:

- **Auth**: none required (`LocalAuthGate`, every request resolves to a `local` user with unlimited tier).
- **Metering**: none (`NoopMeter`, no credit accounting).
- **Audit**: local file (`FileAuditSink`, appends JSON-lines to `~/.novo/audit.jsonl`).

Then, in another shell:

```bash
curl -X POST http://localhost:8018/mcp/tools/get_molecule_profile \
  -H 'Authorization: Bearer x' \
  -H 'Content-Type: application/json' \
  -d '{"arguments": {"smiles": "CC(=O)Oc1ccccc1C(=O)O"}}'
```

You get a full molecular profile for aspirin: properties, ADMET predictions, and structural alerts computed against the locally wrapped services (RDKit for properties in local mode; ADMET falls through to the addie-models service when that's wired).

## Configuration

The engine's spine is configurable via environment variables:

| Variable | Values | Default | Effect |
|---|---|---|---|
| `NOVO_AUTH` | `local` \| `hosted` | `local` | `local` = no auth; `hosted` = API-key / JWT via a managed backend |
| `NOVO_METER` | `local` \| `hosted` | `local` | `local` = no credit accounting; `hosted` = credit-per-call metering |
| `NOVO_AUDIT` | `local` \| `hosted` | `local` | `local` = JSON-lines to file; `hosted` = certified audit service |
| `NOVO_AUDIT_PATH` | filesystem path | `~/.novo/audit.jsonl` | Where `FileAuditSink` writes |

Setting any of `NOVO_AUTH` / `NOVO_METER` / `NOVO_AUDIT` to `custom` loads implementations from a `spine_custom` module. Write your own against the three protocols in `novomcp/mcp/spine.py`, `AuthGate`, `CreditMeter`, `AuditSink`, and put the module on the import path. The interfaces are stable.

## Surfaces

Every surface is a thin backend-configurable client. Point it at a local engine (default) or a hosted one (with a key).

- **`novo` CLI**, `npm i -g @novomcp/novo`, deterministic, scriptable commands (`novo dock ...`, `novo funnel run`)
- **MCP connector**, usable from Claude, Gemini, Cursor, Zed, or any MCP-compatible client
- **REST API**, `POST /v1/tools/{name}` with a curated OpenAPI at `/v1/openapi.json`
- **Workbench** (desktop + web), visual funnel viewer, structure visualization, live tool execution
- **Chrome extension** and **Word add-in**, sideload-first, backend-configurable (dev-mode install docs in each surface repo)

## Architecture

```
                                           ┌──────────────────────┐
                                           │  Spine (pluggable)   │
                                           │  ┌──────────────┐    │
                                           │  │  AuthGate    │    │
   ┌────────────┐    ┌──────────────┐      │  ├──────────────┤    │
   │ Surfaces   │──▶│ Engine core  │──────▶│  │ CreditMeter  │    │
   │ CLI / MCP  │    │ (funnel +    │      │  ├──────────────┤    │
   │ REST / UI  │    │  tool exec)  │      │  │ AuditSink    │    │
   └────────────┘    └──────┬───────┘      │  └──────────────┘    │
                            │              └──────────────────────┘
                            ▼
                    ┌───────────────┐
                    │ Service       │  RDKit • GROMACS • AutoDock
                    │ wrappers      │  OpenFold • Boltz • Gnina
                    └───────────────┘  xTB • ANI-2x • Chai
```

The spine (auth / metering / audit) is a runtime boundary, the same code runs standalone or as part of a larger deployment depending on which implementations you wire in.

## Licensing

- **Top-level tree** (surfaces, wrappers, connectors, protocol docs), **Apache-2.0** (`LICENSE`)
- **Orchestration core** (`novomcp/mcp/`), **Business Source License 1.1** with a change date of 2029-07-12 to Apache-2.0 (`LICENSE.core`). Non-production use, self-hosting, and modification are permitted immediately.
- **Pre-trained model weights** in companion repositories, **MIT**.

## Contributing

- **Issues + PRs welcome** on the public repo. See `.github/CONTRIBUTING.md` for the workflow.
- **Support**: best-effort. This is a reference-quality open-source project; there is no support commitment.
- **Contribution attribution** goes in the `CHANGELOG` on each release.

## Reading further

- [`docs/engineering-stories/`](docs/engineering-stories/), postmortems + design notes on specific technical bets.

---

NovoMCP is made by [Ari Harrison](https://ariharrisonlab.github.io/) and contributors.
