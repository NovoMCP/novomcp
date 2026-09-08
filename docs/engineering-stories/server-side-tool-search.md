# Server-side tool search for a 68-tool MCP platform

**Published:** April 23, 2026
**Author:** NovoMCP engineering

---

NovoMCP exposes 68 tools to the AI assistants its customers run, across target discovery, quantum chemistry, molecular dynamics, docking, and materials science. Every tool is discoverable by intent, on every model provider, without loading 68 schemas into the context window on each turn. The retrieval layer is roughly 100 lines of Python. It builds in one second at container startup, answers a query in 25 milliseconds, and ships inside the engine. It reached 100 percent recall on a 50-prompt evaluation set the day it went live.

That capability is the subject of this piece. The reason it had to be built on the server is the market constraint underneath it.

Client-side tool search exists. Anthropic shipped it in the Claude Agent SDK in April 2026: deferred schema loading that keeps an agent accurate over catalogs of hundreds of tools. It reaches SDK-based applications on Claude. It does not reach a remote MCP server, and it does not reach the other model providers a pharma IT shop runs in parallel. NovoMCP serves two surfaces beyond Claude.ai. NovoWorkbench is a Rust desktop that speaks to Claude, GPT-5.2, Gemini, and Ollama on equal footing, with no SDK in the hot path. The MCP protocol is the other, and there remote servers load every schema upfront. The problem is universal. A client-side fix covers one slice of it.

NovoMCP builds the pattern into the server, where it serves every model the customer chooses. This is the architecture, the evidence, and the standard it establishes for any enterprise MCP server past roughly 30 tools.

---

## The architecture

The mechanism is direct. When the model needs a few tools per turn, the engine sends a summary and lets the agent request specific schemas on demand, rather than shipping all 68 every turn. Protocol-compliant, in about 100 lines of Python.

**One embedding call at startup.** On container boot the engine concatenates each tool's name, description, parameter names, and enum values into a short text blob. All 68 blobs go to an embedding model in a single batched request. The vectors are truncated to 1536 dimensions, L2-normalized so cosine similarity is a plain dot product, and held in a numpy array at module scope. One second, one round-trip, no persistent storage.

**One embedding call per query.** `POST /mcp/tool-search` takes a query string, embeds it, computes the dot product against all 68 tool vectors, and returns the top-K with similarity scores. Round trip, 25 milliseconds end to end.

**A core whitelist of eight tools that always surface.** Platform info, usage lookup, funnel logging, the autonomous-mode trigger, job polling. A caller orients itself even when retrieval misses.

**Template manifests for known workflows.** When a caller names a prompt template (the discovery funnel, an OLED screening pipeline, an electrolyte-stability screen) the endpoint skips retrieval and returns that template's full tool set. The template already encodes its flow; encoding its tool set alongside removes a class of retrieval miss.

**A keyword-match fallback.** If the embedding provider is unreachable at startup or at query time, the endpoint falls back to substring matching on names and descriptions. Lower quality, fully functional. The endpoint stays up, and a diagnostic field reports the embedding failure to callers.

That is the entire retrieval layer. For 68 tools it holds 420 kilobytes of RAM.

---

## The substrate rule

NovoMCP already runs managed vector infrastructure for two workloads. Literature search across millions of peer-reviewed papers: millions of vectors, cross-user persistence, the correct substrate. Funnel memory, which persists terminal summaries of past discovery runs and grows per user over time: continuous growth, persistence required, again the correct substrate.

Tool search is neither. The catalog is small, static, and identical across every container replica. The codebase is the source of truth for tool descriptions. Nothing needs to persist, and rebuilding 68 embeddings in one second beats any disk-persistence scheme.

**The substrate is chosen by corpus size, not by architectural sophistication.** At 68 items a numpy dot product runs in half a millisecond. A managed vector query, however fast the service, adds 50 to 100 milliseconds of network round-trip to every LLM turn, and this retrieval runs per message. Vector-database infrastructure earns its place at tens of thousands of items and up, where selectivity wins amortize the round-trip. Below that line, the round-trip is pure latency the customer feels on every message. Reaching for the existing vector database would have bought consistency and paid for it in latency on every turn, with nothing in return.

---

## Diagnostics ship before the feature

The first production probe of `/mcp/tool-search/status` returned:

```json
{
  "ready": false,
  "size": 0,
  "built_at": null
}
```

The index had not built. The container had started, the route was registered, queries returned empty. No errors in the logs. No exceptions surfaced. The only signal was the absence of success.

The cause was a credential-lookup failure in the background index build. The exception was caught by a wrapper and logged below the level that draws attention. The shared utility worked correctly in adjacent services; on this code path, on this deployment surface, it did not. The fix injected the embedding credentials through the same mechanism the server already uses for its primary LLM orchestration. One module, no new dependencies.

The standard is the durable output. **A new component ships its status endpoint before its feature.** The retrieval pipeline existed before the observability did, and the status endpoint reported success flags without failure reasons. The engine now carries a `last_error` field, a `build_attempts` counter, a configuration-present flag, and a manual-rebuild endpoint that retries a failed build without a container restart. The next probe reported the exact failure in fewer characters than this paragraph.

New MCP components at NovoMCP start with observability, not with the feature. Build the status endpoint first. Surface the last error. Expose the configuration the component believes it is using. Every minute on diagnostics during the build returns an hour of production investigation at the moment that hour is most expensive.

---

## Retrieval finds the gaps that listings hide

The endpoint shipped with an evaluation set. Fifty prompts across six categories: funnel stages, Compute-tier tools, materials workflows, ambiguous cases, adversarial paraphrases, and negative cases that should surface nothing strongly. Each prompt names the tools that must appear in the top ten. A script hits the endpoint, records the rankings, computes recall at ten, and reports per-category aggregates.

The first production run returned 95.8 percent recall, above the 90 percent ship gate and short of clean. The misses clustered on queries that should have surfaced a specific tool whose description was sound, whose presence in the catalog was confirmed, whose vector had been built correctly. Retrieval was doing its job. The tool never appeared.

The cause sat upstream of retrieval. A comparison path in the visibility layer handled most cases correctly and had an edge case that silently excluded certain tools under certain configurations. Name-based tool listings had never exposed the gap, because a listing answers "what passes the filter?" and returns whatever the filter produces. Retrieval asks "what is relevant to this intent?" and fails visibly when the relevant thing is absent. Fixing the edge case moved recall from 95.8 percent to 100 percent.

**A retrieval workload surfaces the latent gaps a listing workload conceals.** Listings return what the filter produces and are trusted as authoritative. Retrieval exposes whether the relevant tool is reachable at all. Any MCP server with tier-gated access carries similar gaps somewhere in its visibility layer, and a retrieval workload will find them. The fix surface is the platform, not the new endpoint.

---

## Two layers, no conflict

Server-side retrieval composes with client-side retrieval. When Anthropic ships tool search to remote MCP hosts (a draft specification exists and the direction is set) two things happen at once. Claude.ai users get tool search at the client layer, on Anthropic's ranker, with no context tax on the 68 tools. NovoWorkbench users continue to get tool search at the retrieval layer, on the engine's embedding model, on whichever provider they selected.

The layers operate at different scopes and do not conflict. One decides what to load for a session in a specific client. The other decides what to load for a query on any client. NovoMCP takes the benefit of Anthropic's improvements to Claude without waiting for them, and without waiting for a Rust Agent SDK that may never ship.

The property that matters here is economic, not technical. A platform that ships only client-side optimizations for its own SDK asks the customer to pick a model vendor and stay. A server-side retrieval layer serves every AI vendor that speaks the protocol. The tool surface scales without the customer picking a side in the AI-vendor competition. That matters more as tool counts grow: the 68 tools today become 75 by year-end and plausibly 150 within eighteen months. A client that cannot defer schema loading hits an accuracy cliff around 30 to 50 tools. Customers running Ollama or GPT-5.2 against a platform without server-side retrieval hit that cliff first and hardest.

---

## What enterprise MCP buyers get

**Every model, one quality bar.** Identical tool-selection quality across every provider in the customer's environment, with no dependency on a single vendor's SDK. Decisive for pharma IT that cannot commit to one AI provider.

**An auditable retrieval path.** Every query logs its returned tools and similarity scores. Every index build records a duration, an error, and an attempt count. Every description change runs against the eval set. None of this exists when retrieval is a black box inside a client SDK.

**Description quality as a versioned artifact.** A tool description is a piece of the discovery surface: tested, committed, and rolled back like any other code. The eval set catches description regressions before deploy. For buyers who require reproducible AI-agent behavior, this is load-bearing.

**Air-gapped compatibility.** Self-hosted pharma and defense customers run NovoMCP where a vendor's client-side SDK features do not reach. Server-side retrieval sits inside the perimeter. The capability ships with the software, not with an AI-vendor relationship.

**A latent-bug surface.** Retrieval tests discovery in a way name-based tool calls do not. Any enterprise MCP server past 30 tools carries latent gaps in its visibility layer, and retrieval surfaces them.

---

## The numbers

The endpoint is live. `68 tools indexed in 1.4 seconds` at container startup. `420 kilobytes` of memory. `25 milliseconds` per query end to end. `100 percent recall at ten` on a 50-prompt evaluation set, 48 expected tools, 48 found. Keyword fallback on embedding failure, a diagnostic status endpoint, a manual rebuild for operators. Zero new infrastructure; the embedding call reuses credentials the engine already held.

The consumer side begins now. NovoWorkbench v1.1 wires the Rust router to `/mcp/tool-search` and `/mcp/prompts/{name}`, retiring the hardcoded tool allowlist that had drifted from the canonical descriptions. The full 68-tool surface becomes visible to Workbench users with no context-cost penalty, across every model provider they choose.

---

## The standard NovoMCP runs

Tool search is not a feature bolted onto the platform. It is a discipline the engine applies to its own discovery surface, and it holds for any MCP server approaching 30 tools.

1. **Build server-side retrieval now.** Client-side tool search may arrive, eventually and partially. Server-side retrieval is an afternoon of work and reaches every provider.
2. **Use in-memory retrieval until the catalog exceeds roughly ten thousand tools.** Network round-trips to a vector database are expensive on a per-turn path. Dot products over small arrays are not.
3. **Ship the status endpoint and the eval set before the retrieval logic.** The retrieval logic is the easy part. The failure modes and the regression surface are the work.
4. **Assume latent discovery gaps exist.** Retrieval finds them. Fix them across the platform, not only in the new endpoint.
5. **Treat tool descriptions as versioned code.** Write the eval set that catches their regressions. Commit the baseline. Require it to pass before deploy.

Capability and capability the agent can find are different properties. The first is the work. The second is the infrastructure that makes the first visible. Both ship.

---

*NovoMCP exposes 68 tools across drug discovery, quantum chemistry, molecular dynamics, and materials science. The tool-search endpoint is live at `/mcp/tool-search` on both `ai.novomcp.com` and `compute.novomcp.com`.*
