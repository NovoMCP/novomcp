# API reference

NovoMCP exposes three interfaces:

1. **REST API** — HTTP + JSON. Best for scripts, curl, Postman, generated clients.
2. **MCP JSON-RPC** — the Model Context Protocol wire format. Best for LLM clients (Claude Desktop, Cursor, Zed, custom agents).
3. **In-process** — import as a Python library, call tools directly.

## REST API

The engine serves an OpenAPI 3.1 specification at two URLs once running:

- `http://localhost:8018/openapi.json`
- `http://localhost:8018/v1/openapi.json` (curated, adds MCP surface metadata)

### Browse the spec

Point any OpenAPI-compatible tool at that URL:

- **Swagger UI** — free hosted at [petstore.swagger.io](https://petstore.swagger.io/), paste the URL to get a browsable spec
- **Redoc** — try [redocly.com/redoc/](https://redocly.com/redoc/) with your local URL
- **Postman** — File → Import → Link → paste the URL
- **Insomnia / Bruno** — same pattern
- **openapi-generator** — generate a client in any language

### Tool call pattern

```
POST /mcp/tools/{tool_name}
Authorization: Bearer <token>
Content-Type: application/json

{
  "arguments": {
    "smiles": "CC(=O)Oc1ccccc1C(=O)O",
    "include_admet": true
  }
}
```

- `LocalAuthGate` (default): any bearer token accepted
- Hosted mode: real API keys via `managed backend`
- Compute-only tools (docking, MD, etc.) require a paid tier when running on the hosted REST API

### Auth options

| Mode | Token | Meter | Audit |
|---|---|---|---|
| Local | any string in `Authorization: Bearer` | none | file (`~/.novo/audit.jsonl`) |
| Hosted | real `nmcp_*` (core) or `ncmcp_*` (compute) keys | credit ledger via managed backend | Aurora + retention |
| Custom | your own `AuthGate` implementation | your own `CreditMeter` | your own `AuditSink` |

Set `NOVO_AUTH=custom` and provide a `spine_custom` module implementing the three protocols. See [`orchestrator/mcp/spine.py`](https://github.com/NovoMCP/novomcp/blob/main/orchestrator/mcp/spine.py) for the interfaces.

## MCP JSON-RPC

The engine speaks the [Model Context Protocol](https://modelcontextprotocol.io) 2024-11-05 revision at `POST /mcp/`.

### Handshake

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {}
  }
}
```

Returns server capabilities, protocol version, and an `instructions` blurb with funnel-id protocol notes.

### List tools

```json
{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
```

### Call a tool

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "get_molecule_profile",
    "arguments": {"smiles": "CC(=O)Oc1ccccc1C(=O)O"}
  }
}
```

### Connect an MCP client

- **Claude Desktop** — add to `claude_desktop_config.json`:
  ```json
  {
    "mcpServers": {
      "novomcp": {
        "url": "http://localhost:8018/mcp/",
        "headers": {"Authorization": "Bearer x"}
      }
    }
  }
  ```
- **Cursor / Zed / custom agents** — same URL, same auth pattern

## In-process

For scripts or notebooks that don't want the HTTP overhead:

```python
import asyncio
from orchestrator.mcp.tools import MCPToolExecutor

async def main():
    executor = MCPToolExecutor(service_urls={}, internal_api_key="")
    result = await executor._execute_get_molecule_profile(
        {"smiles": "CC(=O)Oc1ccccc1C(=O)O"}
    )
    print(result.data)

asyncio.run(main())
```

Not the primary path but supported. Useful for tests and Jupyter workflows.

## Tool categories

The 67 tools group into these categories (each with per-tool descriptions in the OpenAPI spec):

- **Cheminformatics** — `calculate_properties`, `get_molecule_profile`, `search_similar`, `filter_molecules`, `screen_library`, `batch_profile`, `get_molecule_info`
- **ADMET + safety** — `predict_admet`, `predict_clinical_outcomes`, `check_compliance`
- **Optimization** — `lead_optimization`, `optimize_molecule`
- **Docking** *(GPU)* — `dock_molecules`, `dock_with_strain`
- **Molecular dynamics** *(GPU)* — `run_molecular_dynamics`, `generate_dynamics`
- **Structure prediction** *(GPU)* — `predict_structure`, `get_protein_structure`, `get_structure_result`
- **Quantum-mechanical** — `run_qm_calculation`, `run_conformer_search`, `compute_energy`, `predict_frontier_orbitals`, `predict_pka`, `predict_solubility`, `predict_bde`, `predict_reaction_thermodynamics`, `run_qm_hessian`, `run_excited_states`, `predict_redox_potential`, `find_transition_state`, `parameterize_metal`
- **Neural network potentials** — `optimize_geometry_nnp`
- **Materials** — `search_materials_project`
- **Discovery funnel** — `target_discovery`, `validate_target`, `stratify_patients`, `search_chembl`, `search_literature`, `search_biorxiv`, `search_patents`, `search_prior_runs`
- **Autonomous mode** — `run_novo_ag` (returns the 11-stage protocol when the user says "Novo AG" or "agm")
- **Async job management** — `list_jobs`, `get_job_status`, `cancel_job`, `predict_reaction_thermodynamics`
- **Funnel persistence** — `save_funnel_stage`, `save_funnel_context`, `save_funnel_memory`, `get_funnel_context`, `get_funnel_audit`, `list_funnels`, `get_pipeline_audit`
- **File intelligence** — `generate_upload_url`, `get_file_status`, `list_files`
- **Discovery tools** — `explore_chemical_space`, `drill_into_cluster`, `vector_search`, `compare_candidates`
- **Enterprise integrations** — `push_to_destination`, `pull_from_source`
- **Platform + audit** — `get_platform_info`, `audit_system`, `get_credit_usage`

GPU-marked tools return `service unavailable` in local mode unless you deploy the corresponding compute service. See [Deploying services](deploying-services/README.md).
