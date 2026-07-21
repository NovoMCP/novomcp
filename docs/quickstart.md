# Quickstart

Boot the NovoMCP engine locally in about 2 minutes. No API keys, no cloud dependencies.

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

```bash
git clone https://github.com/NovoMCP/novomcp.git
cd novomcp/orchestrator
python3.11 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python main_https.py
```

The engine boots with:

- **Auth**: none required (`LocalAuthGate`, every request resolves to a `local` user with unlimited tier)
- **Metering**: none (`NoopMeter`, no credit accounting)
- **Audit**: local file (`FileAuditSink`, appends JSON-lines to `~/.novo/audit.jsonl`)

If you accidentally create the venv with Python 3.9, `python main_https.py` fails fast with an actionable message telling you which install command to run.

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

`LocalAuthGate` accepts any bearer token — `x` works fine. Returns the 11 always-available tools; the other 56 in the catalog appear as you wire their backing services (see [tool-availability.md](tool-availability.md)).

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

Every tool call is logged as a JSON-lines row. Structure: `event`, `timestamp`, `payload` (tool, funnel_id, success, credits, duration, surface).

## What next

- **[Configure an LLM provider](configuring-llm.md)** — enable intent recognition, orchestration planning, and semantic tool search
- **[Deploy compute services](deploying-services/README.md)** — wire up ADMET, docking, MD, structure prediction, QM
- **[Connect an MCP client](https://modelcontextprotocol.io/quickstart)** — point Claude Desktop / Cursor / Zed at `http://localhost:8018/mcp/`
- **[Read the engineering stories](engineering-stories/README.md)** — how the engine was designed and where it went wrong the first time
