# NovoMCP

**The open computational chemistry engine for drug discovery and materials science.**

NovoMCP is an agent-callable engine that exposes molecular intelligence, cheminformatics, ADMET prediction, molecular docking, molecular dynamics, quantum-mechanical calculations, protein structure prediction, and a governed discovery funnel through the [Model Context Protocol](https://modelcontextprotocol.io) and a curated REST API.

One engine. Two domains. Four surfaces.

## Why NovoMCP?

Molecular intelligence is scattered — RDKit in one notebook, an ADMET model in another, a docking rig on a cluster, a compliance check in a spreadsheet, literature search in a browser tab. Wiring all of it into something an AI agent can actually *call* is the work nobody wants to redo per project. NovoMCP collapses it into one engine:

- **Agent-callable by default.** 67 tools over MCP JSON-RPC plus a curated REST API — the same catalog whether you drive it from Claude Desktop, Cursor, a script, or the discovery funnel.
- **Runs on your laptop, day one.** 11 tools work fully local with no API keys and no cloud. The rest unlock as you deploy compute or bring data — nothing is gated behind a signup to get started.
- **An engine, not a wrapper.** Intent recognition, orchestration planning, semantic tool search, and a governed 11-stage discovery funnel are built in — not left as an exercise for the caller.
- **Pluggable where it counts.** Auth, metering, audit, and LLM provider each swap via env vars, so the same core runs unauthenticated on a laptop or metered-and-audited in production.

New here? [Boot it in 2 minutes](quickstart.md), then see [how it fits together](architecture.md).

## Get started

<div class="grid cards" markdown>

- __:material-download: Install locally__

    ---

    Boot the engine on your laptop in 2 minutes. No API keys, no cloud dependencies.

    [Quickstart :octicons-arrow-right-24:](quickstart.md)

- __:material-api: API reference__

    ---

    Full REST API + OpenAPI 3.1 spec + JSON-RPC (MCP) surface.

    [API reference :octicons-arrow-right-24:](api-reference.md)

- __:material-server: Deploy compute services__

    ---

    Wire up ADMET, docking, MD, QM, structure prediction. CPU + GPU options.

    [Deploying services :octicons-arrow-right-24:](deploying-services/README.md)

- __:material-cloud: Deploy to the cloud__

    ---

    AWS, GCP, Azure guides. Three tiers each: single VM, managed K8s, serverless.

    [Deploying to the cloud :octicons-arrow-right-24:](deploying-to-cloud/README.md)

</div>

## What it does

- **11 tools work fully local** out of a 67-tool catalog. The rest unlock as you deploy compute services, provide your own data, or subscribe to hosted APIs. See [Tool availability](tool-availability.md) for the full map.
- **Full 67-tool catalog** spans cheminformatics (properties, similarity, filtering), ADMET prediction, docking, molecular dynamics, protein structure prediction, quantum-mechanical calculations, literature/patent search, regulatory compliance
- **REST API** with a curated OpenAPI 3.1 spec at `/v1/openapi.json`
- **MCP JSON-RPC** at `/mcp/` — usable from any MCP-compatible client (Claude Desktop, Cursor, Codex, Zed, Cline, and others)
- **Autonomous discovery funnel** — trigger with "Novo AG" or "agm" from any MCP client, returns an 11-stage protocol for the LLM to execute
- **Pluggable everything** — auth (`AuthGate`), metering (`CreditMeter`), audit (`AuditSink`), LLM providers (OpenAI, Anthropic, Ollama, Azure) all swap via env vars

## What lives elsewhere

By design, the following ship separately:

- **FAVES certified hosted API** — the operational commitments (SLA, audit-log retention, drift monitoring, IQ/OQ/PQ) that a regulated submission needs, delivered as a paid service at `api.novomcp.com`. The V4 framework paper is [published on Zenodo](https://zenodo.org/) under CC-BY-NC-ND 4.0. The **open reference implementation** of the framework ships in this OSS release as the `faves-compliance` Docker image — run locally for triage, route the shortlist through the hosted API when you need the certified record-of-audit.
- **Curated 122M-molecule enriched corpus** — publishing to AWS Open Data / Kaggle / Zenodo as a downloadable dataset. Cached lookups happen through the FAVES compliance service when configured.
- **Trained clinical outcomes model weights** — NovoExpert-3, separate license.

None of these are required for the engine to run.

## Licensing

- **Top-level tree** (surfaces, wrappers, connectors, protocol docs): **Apache-2.0** ([`LICENSE`](https://github.com/NovoMCP/novomcp/blob/main/LICENSE))
- **Orchestration core** (`orchestrator/mcp/`): **Business Source License 1.1** with a change date of 2029-07-12 to Apache-2.0 ([`LICENSE.core`](https://github.com/NovoMCP/novomcp/blob/main/LICENSE.core)). Non-production use, self-hosting, and modification are permitted immediately.
- **Pre-trained model weights** in companion repositories: **MIT**.

## Contributing

- **Issues + PRs welcome** on the public repo. See [`CONTRIBUTING.md`](https://github.com/NovoMCP/novomcp/blob/main/.github/CONTRIBUTING.md) for the workflow.
- **Discussions:** use GitHub Discussions for questions and design conversations.
- **Support:** best-effort. NovoMCP is a reference-quality open-source project; there is no support commitment.

## Reading further

- [Engineering stories](engineering-stories/README.md) — postmortems and design notes on specific technical bets. Real writeups, not marketing.
- [Changelog](changelog.md) — version history.
- [GitHub repository](https://github.com/NovoMCP/novomcp) — source code, issue tracker, releases.
