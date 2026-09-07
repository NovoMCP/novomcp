# Two MCP servers, one workflow: AWS Open Data discovery to NovoMCP analysis

An agent asks AWS's Registry of Open Data (over MCP) for cheminformatics data,
gets back exactly one dataset (the NovoMCP Open Corpus), then takes a compound
that corpus contains and analyzes it through the NovoMCP engine (over MCP).

Two independent MCP servers compose into one pipeline:

1. `awslabs.roda-mcp-server` (stdio, launched via `uvx`) discovers the dataset.
2. The NovoMCP engine (streamable-http on `:8018`) analyzes a molecule from it.

No AWS account, no API key. The registry index and the corpus both read anonymously.

## Run it

Prerequisites: [`uv`](https://astral.sh/uv) installed, and the NovoMCP engine
reachable at `http://localhost:8018/`.

Start the engine in one terminal:

```bash
uvx novomcp
# or: docker run --rm -p 8018:8018 ghcr.io/novomcp/novomcp:latest
```

Run the demo in another:

```bash
uv run --with mcp python two_mcp_demo.py
```

`uv` fetches `awslabs.roda-mcp-server` on demand; the first run pulls it once.

## What it prints

See [`transcript.txt`](./transcript.txt) for a captured run. The shape:

- **Step 1** `search_datasets("cheminformatics")` over the RODA MCP server returns
  `total_count: 1` -- the NovoMCP Open Corpus, sole result across the whole registry.
- **Step 2** the same for `"ADMET"` -- also one result, also ours.
- **Step 3** the corpus is all 122,454,458 PubChem compounds, so it contains any
  PubChem CID. The demo takes one (imatinib, CID 5291).
- **Step 4** the NovoMCP engine profiles it: molecular weight 493.6, logP 4.59,
  TPSA 86.3, and the rest, computed in-process via RDKit.

## Honest notes

- **The "sole result" claim is live and time-stamped.** The registry grows. It was
  verified at 1,177 datasets on 2026-08-04 and re-verified at 1,199 on 2026-09-07,
  still sole result both times. The script prints a warning if `total_count != 1`,
  so the demo tells the truth even if a new chemistry dataset lands with matching
  metadata. Re-run before you rely on the claim.
- **ADMET and compliance are capability-gated.** On a bare engine,
  `get_molecule_profile` returns RDKit descriptors; its 30+ ADMET predictions light
  up when you wire `ADDIE_MODELS_URL` at the open addie-models service, and
  compliance screening lights up when you wire a compliance service at
  `NOVOMCP_COMPLIANCE_URL`. The demo surfaces what runs on a bare install rather
  than calling a tool whose service isn't wired.
- **Reading the corpus live from S3 is an optional extra, not shown here.** The
  data reads anonymously in place from `s3://novomcp-open-corpus/novomcp-open-corpus-lite/`
  -- but note the bucket is in **us-east-2** (a client defaulting to us-east-1 gets
  an HTTP 301). The demo uses a known-in-corpus compound to stay fast and robust;
  swapping in a live parquet read is a one-function change.
