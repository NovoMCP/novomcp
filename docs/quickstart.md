# Quickstart

Boot the NovoMCP engine locally in about 2 minutes. No API keys, nothing to provision — it runs on your hardware.

## Requirements

- **Python 3.10 or later.** Python 3.9 hit end-of-life October 2025; several transitive deps require 3.10+.

Check what you have:

```bash
python3 --version
```

If it says `Python 3.10.x` or newer, skip ahead. Otherwise install a supported version via any of these paths:

=== "macOS (Homebrew)"

    ```bash
    brew install python@3.11
    ```

=== "macOS (installer)"

    Download from [python.org/downloads](https://www.python.org/downloads/).

=== "Ubuntu / Debian"

    ```bash
    sudo apt install python3.11 python3.11-venv
    ```

=== "Fedora / RHEL"

    ```bash
    sudo dnf install python3.11
    ```

=== "Windows"

    Download from [python.org/downloads](https://www.python.org/downloads/), or use WSL and follow the Linux instructions.

=== "pyenv"

    ```bash
    pyenv install 3.11.9
    pyenv local 3.11.9
    ```

=== "uv / conda / mamba / Docker"

    All fine — any Python 3.10+ works. `python:3.11-slim` is a good Docker base.

## Install and run

### Fastest — `uvx` (no clone, no venv)

Run the engine straight from PyPI in one command:

```bash
uvx novomcp
```

[`uvx`](https://docs.astral.sh/uv/) fetches the `novomcp` package into a throwaway environment and launches the engine — nothing to clone or install. The first run downloads the dependency set (rdkit, MDAnalysis, …), so give it a minute; later runs are cached.

!!! tip "Just want the lightweight cheminformatics subset?"
    `uvx --from 'novomcp-lite[mcp]' novomcp-lite` starts a **stdio** MCP server in seconds — RDKit properties/profiling + public-API search, no backend. The full engine imports the same code, so the two never drift. See [novomcp-lite](https://github.com/NovoMCP/novomcp-lite).

### `pip install`

Into a virtual environment you control:

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install novomcp
novomcp
```

`novomcp` is the console command — it boots the MCP + REST server.

### From source

For hacking on the engine itself:

```bash
git clone https://github.com/NovoMCP/novomcp.git
cd novomcp/orchestrator
python3.11 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
python main_https.py      # or: novomcp
```

The engine boots with:

- **Auth**: none required (`LocalAuthGate`, every request resolves to a `local` user with unlimited tier)
- **Metering**: none (`NoopMeter`, no usage accounting)
- **Audit**: local file (`FileAuditSink`, appends JSON-lines to `~/.novo/audit.jsonl`)

If you accidentally create the venv with Python 3.9, `python main_https.py` fails fast with an actionable message telling you which install command to run.

### Everything at once — Docker Compose (engine + dashboard)

If you have Docker, one command builds and runs **both** the engine and the [web dashboard](#the-web-dashboard), wired to each other on a private network:

```bash
git clone https://github.com/NovoMCP/novomcp.git
cd novomcp
docker compose up
```

- Engine → `http://localhost:8018`
- Dashboard → `http://localhost:3000`

`docker compose up` starts both and streams both logs; `Ctrl-C` (or `docker compose down`) stops both. No start-order to worry about. Compose is also the [single-VM cloud recipe](deploying-to-cloud/README.md) — the same file scales from your laptop to one box in the cloud.

## First requests

In another shell:

### Health check

```bash
curl -s http://localhost:8018/health | python3 -m json.tool
```

Expected: `{"status": "healthy", ...}`.

### List tools

```bash
curl -s http://localhost:8018/mcp/tools \
  -H 'Authorization: Bearer x' \
  | python3 -m json.tool
```

`LocalAuthGate` accepts any bearer token — `x` works fine. Returns the 11 always-available tools; the other 57 in the catalog appear as you wire their backing services (see [tool-availability.md](tool-availability.md)).

### Get a molecule profile (aspirin)

```bash
curl -s -X POST http://localhost:8018/mcp/tools/get_molecule_profile \
  -H 'Authorization: Bearer x' \
  -H 'Content-Type: application/json' \
  -d '{"arguments": {"smiles": "CC(=O)Oc1ccccc1C(=O)O"}}' \
  | python3 -m json.tool
```

Returns properties (MW, logP, TPSA, QED, Lipinski) computed on-the-fly via RDKit. `admet_available` will be `false` because the ADMET service (`addie-models`) isn't wired locally — that's expected. See [Deploying services](deploying-services/README.md) to enable ADMET.

### MCP JSON-RPC handshake

```bash
curl -s -X POST http://localhost:8018/mcp/ \
  -H 'Authorization: Bearer x' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", "capabilities": {}}
  }' \
  | python3 -m json.tool
```

Returns server info, capabilities, and the funnel-id instructions blurb. This is the endpoint MCP-compatible clients (Claude Desktop, Cursor, Zed) connect to.

### Audit sink

```bash
tail -3 ~/.novo/audit.jsonl
```

Every tool call is logged as a JSON-lines row. Structure: `event`, `timestamp`, `payload` (tool, funnel_id, success, duration, surface).

## The web dashboard

The engine is headless — a REST/MCP backend. The **web dashboard** (Next.js) is the visual surface: molecule profiles, live engine/service status, and the config screens for LLM keys, compliance, observability, and data connectors.

The engine and the dashboard are **two separate processes**. The dashboard's server-side routes proxy to the engine at `NOVOMCP_ENGINE_URL` (default `http://localhost:8018`); the browser only ever talks to the dashboard.

!!! note "Start order doesn't matter"
    The dashboard degrades gracefully when the engine is down — every panel shows what's missing and offers a **Recheck** button — so you can start either first and connect them in any order.

=== "Docker (both at once)"

    ```bash
    docker compose up
    ```

    Brings up the engine **and** the dashboard together (see [above](#everything-at-once-docker-compose-engine-dashboard)). Open **http://localhost:3000**.

=== "Manual (no Docker)"

    Two terminals. In the first, run the engine ([Install and run](#install-and-run)). In the second:

    ```bash
    cd frontend-nextjs
    npm install
    npm run dev
    ```

    Open **http://localhost:3000**. Set `NOVOMCP_ENGINE_URL` if your engine runs somewhere other than `http://localhost:8018`.

Once connected, the dashboard shows **Tools available: 11 of 68** — the 11 that work with nothing wired, out of the full catalog. That count climbs as you [deploy services](deploying-services/README.md); the dashboard lists which env var unlocks each capability. See [Tool availability](tool-availability.md) for the full map.

## What next

- **[Configure an LLM provider](configuring-llm.md)** — enable intent recognition, orchestration planning, and semantic tool search
- **[Deploy compute services](deploying-services/README.md)** — wire up ADMET, docking, MD, structure prediction, QM
- **[Connect an MCP client](https://modelcontextprotocol.io/quickstart)** — point Claude Desktop / Cursor / Zed at `http://localhost:8018/mcp/`
- **[Read the engineering stories](engineering-stories/README.md)** — how the engine was designed and where it went wrong the first time
