# Use cases

New to agent-callable tools? This page answers the practical question — *what would I actually do with this?* — with concrete workflows. If you know cheminformatics but have never wired an LLM to a set of tools before, start here.

## The shift: from library to callable engine

If you do computational chemistry, you already have RDKit, maybe a docking rig, maybe an ADMET model. You call them by writing scripts. NovoMCP doesn't replace any of that — it exposes those capabilities as **tools an AI assistant can call for you**, over one protocol (MCP) plus a REST API. What changes is who writes the glue:

- **Library workflow** — you write a script that parses SMILES, calls RDKit, filters, calls the next thing, handles the errors, formats the output.
- **Engine workflow** — you tell your assistant *"profile these 12 compounds, drop anything that fails Lipinski, and show me the survivors sorted by QED"* and it calls the tools in order, handling the plumbing.

Both are valid. You can call tools directly (curl, REST, or from a notebook) when you want determinism and reproducibility; the agent path is for exploration and for anyone who'd rather describe the goal than hand-write the pipeline. Same engine underneath either way.

## Workflow 1 — Profile a molecule you're reading about

You hit a compound in a paper or a database and want its druglikeness picture without opening a notebook. Ask your assistant (or `curl` the engine directly):

> "What's the molecular profile of aspirin?"

The `get_molecule_profile` tool returns molecular weight, LogP, TPSA, QED, hydrogen-bond donors/acceptors, rotatable bonds, aromatic rings, and Lipinski pass/fail — computed on the fly via RDKit, no data download required. This is one of the 11 tools that work fully local out of the box.

## Workflow 2 — Triage a shortlist

You have a handful of candidate structures and want to narrow them before committing compute. In one request:

> "Here are 15 SMILES. Calculate properties for all of them, flag any Lipinski violations, and rank the rest by QED."

The assistant fans this out across `calculate_properties` / `get_molecule_profile`, applies your filter, and hands back a ranked table. When you've wired the ADMET service (`addie-models`), the same shortlist can carry predicted solubility, permeability, and toxicity flags alongside the physicochemical properties.

## Workflow 3 — Find neighbors of a hit

You have one active compound and want structurally similar molecules to explore around it:

> "Find molecules similar to this scaffold and show me the closest 20."

`search_similar` returns near neighbors you can then profile or triage with the workflows above — the start of a lightweight hit-expansion loop, all from natural language.

## Workflow 4 — Run the discovery funnel

For end-to-end target-to-candidate exploration, the engine ships a governed **11-stage discovery funnel** — target discovery → validation → literature → known actives → ADMET → compliance → lead optimization → docking → clinical-outcomes gate → MD → patient stratification. Trigger it from any MCP client with "Novo AG" or `agm`; the engine returns the staged protocol for the assistant to execute, pausing for your input at each gate (human-in-the-loop, not a black box). The heavier stages (docking, MD, structure prediction) run once you've deployed the corresponding [compute services](deploying-services/README.md).

## Workflow 5 — Call tools directly from a script

Not everything should go through an assistant. When you want a deterministic, scriptable call — in a notebook, a CI job, or a pipeline — hit the REST surface:

```bash
curl -s -X POST http://localhost:8018/mcp/tools/get_molecule_profile \
  -H 'Authorization: Bearer x' \
  -H 'Content-Type: application/json' \
  -d '{"arguments": {"smiles": "CC(=O)Oc1ccccc1C(=O)O"}}' \
  | python3 -m json.tool
```

Same tool, same result the agent would get — just called by you, reproducibly. The full catalog is in the [API reference](api-reference.md) and the OpenAPI spec at `/v1/openapi.json`.

## Who this is for

- **Bench and computational chemists** who want a druglikeness/ADMET read without standing up a scripting environment for every question.
- **Teams building AI assistants** for drug discovery who need a ready-made, standards-compliant (MCP) tool surface instead of wrapping a dozen libraries themselves.
- **Anyone with an MCP client** (Claude Desktop, Cursor, Zed, Cline) who wants molecular intelligence available in the same place they already work.

If none of your work touches molecules, this isn't for you — and that's fine. But if it does, the value is not having to rebuild the plumbing between "I have a structure" and "I have an answer."

## Next

- **[Quickstart](quickstart.md)** — boot the engine and run the first calls
- **[Architecture](architecture.md)** — how the pieces fit together
- **[Tool availability](tool-availability.md)** — the 11 always-local tools and what each service unlocks
