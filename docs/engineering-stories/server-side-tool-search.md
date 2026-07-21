# Server-side tool search for a 62-tool MCP platform

*What happens when the AI vendor's fix doesn't reach you*

**Published:** April 23, 2026
**Author:** NovoMCP engineering

---

In April 2026, Anthropic shipped tool search in the Claude Agent SDK. It defers tool schema loading so agents can operate over catalogs of hundreds of tools without losing selection accuracy. Our MCP platform crossed that threshold this year. We now expose 62 tools to the AI assistants our customers use, ranging across target discovery, quantum chemistry, molecular dynamics, clinical outcome prediction, and materials science.

The SDK feature was designed for exactly our problem. It did not reach us.

We serve two surfaces beyond Claude.ai. One is NovoWorkbench, a desktop application written in Rust with a custom HTTP router that speaks to Claude, GPT-5.2, Gemini, and Ollama on equal footing. No SDK in the hot path. No Anthropic tool search. The other surface is Claude.ai itself, but there, remote MCP servers load all tool schemas upfront. The deferred-loading pattern only activates for SDK-based applications, not for servers reached over the protocol.

The problem the SDK solves exists everywhere. The SDK's solution reaches one slice of the problem.

So we built the same pattern on the server. This is what that looked like, what we learned, and why we think this is the correct posture for any enterprise MCP server over roughly 30 tools.

---

## The architecture

The core insight of tool search is simple: if the LLM only needs a few tools per turn, do not send it all 62 every turn. Send the summary, let the agent request specific tool schemas on demand.

Implementing that in a protocol-compliant way takes about 100 lines of Python.

**One embedding call at server startup.** On container boot we concatenate each tool's name, description, parameter names, and enum values into a short text blob. All 62 blobs go to an embedding model in a single batched HTTP request. The returned vectors are truncated to 1536 dimensions, L2-normalized so cosine similarity becomes a plain dot product, and held in a numpy array at module scope. Total cost: one second, one network round-trip, no persistent storage.

**One embedding call per query.** A new endpoint, `POST /mcp/tool-search`, takes a user query string. We embed the query, compute the dot product against all 62 tool vectors, return the top-K with similarity scores. Typical round trip: 25 milliseconds end to end.

**A core whitelist of eight tools that always surface.** Platform info, credit usage, funnel logging, the autonomous-mode trigger, job polling. Ensures a caller can orient itself even when retrieval misses.

**Template manifests for known workflows.** When a caller names a prompt template, our discovery funnel, an OLED screening pipeline, an electrolyte stability screen, the endpoint skips retrieval and returns that template's full tool set. Templates encode their flow; encoding their tool set alongside is a small extension that prevents retrieval from missing a tool the workflow depends on.

**A keyword-match fallback.** If the embedding provider is unreachable at startup or during a query, we fall back to substring matching on tool names and descriptions. Not as good as embeddings, but functional. The endpoint stays up; a diagnostic field surfaces the embedding failure to callers.

That is the entire retrieval layer. For 62 tools, it is about 380 kilobytes of RAM.

---

## Why in-memory, not a vector database

We already run managed vector infrastructure for two workloads. Literature search across millions of peer-reviewed papers, correct substrate, millions of vectors, cross-user persistence required. And our funnel memory index, which persists terminal summaries of past discovery runs across sessions and grows per-user over time, correct substrate, continuous growth, persistence required.

Tool search is neither of those. The catalog is small, static, and the same for every container replica. The codebase is the source of truth for tool descriptions. Nothing needs to persist. Nothing needs to survive a restart, rebuilding 62 embeddings in one second is faster and simpler than any disk-persistence scheme.

The pattern is: vector-database infrastructure earns its keep at tens of thousands of items and up, where the cost of network round-trips to the index is amortized across selectivity wins. At 62 items, a numpy dot product runs in half a millisecond. A managed vector query, however fast the service is, adds 50–100 milliseconds of network round-trip to every LLM turn. For retrieval that runs per-message, that latency is visible.

The first lesson of the build: **the right substrate depends on the corpus size, not the architecture's sophistication**. We considered using our existing vector infrastructure for consistency. We would have paid for that consistency in latency on every turn. We would not have gotten anything in return. Reflexively reaching for the existing vector database was the easy mistake to avoid.

---

## The silent-failure lesson

We shipped the endpoint. The first production probe of `/mcp/tool-search/status` returned:

```json
{
  "ready": false,
  "size": 0,
  "built_at": null
}
```

The index had not built. The container had started, the route was registered, queries returned empty. No errors in logs. No exceptions raised. Nothing to investigate except the absence of success.

The cause was a credential-lookup failure during the background index build. The exception raised was caught by a wrapper and logged at a level that did not surface prominently. The shared utility we had reached for worked correctly in adjacent services; in this specific code path, on this specific deployment surface, it did not. Everything looked fine. Nothing was.

The fix was straightforward: inject the embedding credentials through the same mechanism the rest of the server already uses for its primary LLM orchestration. One module, no new dependencies.

The lesson was harder: **the diagnostic surface of a new component matters more than its happy-path code**. We had built the full retrieval pipeline before building the observability. The status endpoint existed but reported only success flags, not failure reasons. We added a `last_error` field, a `build_attempts` counter, a configuration-present flag, and a manual-rebuild endpoint so operators could retry a failed build without restarting the container. The next production probe showed exactly what had gone wrong in fewer characters than this paragraph.

We now start new MCP components with observability, not with the feature. Build the status endpoint first. Surface the last error. Expose the configuration the component thinks it is using. Every minute spent on diagnostics during the build saves an hour of production spelunking when it is most expensive to spend.

---

## The latent-bug lesson

We built an evaluation set alongside the endpoint. Fifty prompts across six categories, funnel stages, Compute-tier tools, materials workflows, ambiguous cases, adversarial paraphrases, and negative cases that should not strongly surface any tool. For each prompt, a list of tools that must appear in the top ten results. A Python script that hits the endpoint, records the actual rankings, computes recall at ten, and reports per-category aggregates.

The first real run against production returned 95.8 percent recall. Above our ship gate of 90 percent, but not a clean pass. The failures clustered on queries that should have surfaced a specific tool, one whose description was sound, whose presence in the MCP catalog was confirmed, whose index vector had been built correctly. The retrieval system was doing its job. And yet the tool never appeared in results.

The cause was upstream of retrieval entirely. A comparison path in our visibility layer handled most code paths correctly but had an edge case that silently excluded certain tools under certain configurations. Name-based tool listings had never surfaced the gap because name-based listings answer "what passes the filter?" and return whatever the filter produces. Retrieval asks a different question, "what is relevant to this intent?", and fails visibly when the relevant thing is absent. The second question is less forgiving of silent filters.

We fixed the edge case. Recall moved from 95.8 percent to 100 percent.

**Latent gaps in discovery surface immediately under retrieval workloads.** Name-based listings and retrieval workloads answer different questions. Listings return what the filter produces and are trusted as authoritative. Retrieval exposes whether the relevant tool is reachable at all, and fails visibly when it is not. Any MCP server with tier-gated access should assume similar gaps exist somewhere in its visibility layer and that a retrieval workload will find them. The fix-surface is the platform, not the new endpoint.

---

## Composability, not capture

A server-side retrieval layer composes with Anthropic's client-side one. When Anthropic eventually ships tool search to remote MCP hosts, there is a draft specification and the direction is clear, two things happen. Claude.ai users get tool search automatically, at the client layer, based on Anthropic's ranker. They pay no context tax on our 62 tools. Simultaneously, NovoWorkbench users continue getting tool search from our server, at the retrieval layer, based on our embedding model, regardless of which AI provider the user selected.

Neither layer conflicts with the other. They operate at different scopes. One decides what to load for a session in a specific client. The other decides what to load for a query regardless of client. We get the benefit of Anthropic's improvements to Claude, without waiting for them, and without waiting specifically for a Rust Agent SDK that may never ship.

The important property here is not technical. It is economic. An AI platform that ships only client-side optimizations for its own SDK is asking customers to pick a model vendor and stay. A server-side retrieval layer serves every AI vendor that speaks the protocol. The platform's tool surface scales without the customer having to pick a side in the ongoing AI vendor competition.

This matters more as tool counts grow. The 62 tools we run today become 75 by year-end and plausibly 150 within eighteen months. Tool count grows with the platform's capability. The client that cannot defer schema loading hits an accuracy cliff around 30 to 50 tools. Customers running Ollama or GPT-5.2 against a platform that did not build server-side retrieval hit that cliff first and hardest.

---

## Benefits for enterprise MCP

Five things fall out of this pattern that matter to enterprise buyers. We had not anticipated all of them at the start; they emerged from the build.

**Every model, same quality.** Every model provider in the customer's environment gets identical tool-selection quality. No dependency on any single vendor's SDK. Important for pharma IT shops that cannot commit to one AI provider.

**Observability.** The retrieval path is auditable. Every query can be logged with its returned tools and similarity scores. Every index build records a duration, an error, an attempt count. Every description change runs against the eval set. None of this is available when the retrieval is a black box inside a client SDK.

**Description quality as a versioned artifact.** The eval set catches description regressions before deploy. A tool description is no longer a prose field; it is a piece of the discovery surface that is tested, committed, and rolled back like any other code change. For enterprise buyers who care about reproducibility of AI-agent behavior, this is a non-trivial property.

**Air-gapped deployment compatibility.** Self-hosted pharma and defense customers run NovoMCP in environments where a vendor's client-side SDK features may not reach. Server-side retrieval sits inside their perimeter. The capability ships with the software, not with a specific AI vendor relationship.

**Latent bug surface.** Retrieval workloads test discovery in a way that name-based tool calls do not. Any enterprise MCP server past 30 tools should assume it has latent gaps in its visibility layer and that retrieval will surface them.

---

## The numbers

The endpoint is live. 62 tools indexed in 1.4 seconds at container startup. 380 kilobytes of memory. 25 milliseconds per query end to end. 100 percent recall at ten on a 50-prompt evaluation set, 48 expected tools, 48 found. Keyword fallback on embedding failure, diagnostic status endpoint, manual rebuild for operators. Zero new infrastructure; the embedding call reuses credentials the platform already had.

The consumer side begins now. NovoWorkbench v1.1 wires the Rust router to `/mcp/tool-search` and `/mcp/prompts/{name}`, deprecating the hardcoded tool allowlist that has been drifting from our canonical descriptions. The full 62-tool surface becomes visible to Workbench users without a context-cost penalty, across every model provider they choose.

---

## What we would do differently

If we were starting again, we would build the status endpoint and the eval harness first, before the retrieval logic. The status endpoint would fail loudly on configuration problems, not silently. The eval harness would run against mocked embeddings before the real ones existed.

We would design visibility-layer comparisons with explicit defaults for unknown values, not ad-hoc lookups that raise on miss. Gracefully degrading filters are easier to audit than filters that silently exclude.

We would not have reused the shared credential client without revalidating it in the target deployment surface. The principle of reusing existing infrastructure is sound; reuse without verification in the specific environment that matters can hide configuration drift that only surfaces under load.

Everything else held up. The in-memory numpy index was correctly sized. The template-manifest shortcut avoided a class of retrieval misses. The core whitelist floored the worst case. The eval set caught real bugs and set a regression baseline.

---

## For other MCP platforms

If you are building an MCP server and approaching 30 tools, consider:

1. **Do not wait for client-side tool search to arrive.** It may, eventually, and partially. Meanwhile you can build server-side retrieval in an afternoon.
2. **Use in-memory retrieval until your catalog exceeds roughly ten thousand tools.** Network round-trips to a vector database are expensive on a per-turn query path. Dot products over small arrays are not.
3. **Build your status endpoint and your eval set before your retrieval logic.** The retrieval logic is the easy part. The failure modes and the regression surface are the hard parts.
4. **Assume you have latent discovery gaps.** Retrieval workloads will find them. Be ready to fix them across the whole platform, not just the new endpoint.
5. **Treat tool descriptions as versioned code.** Write the eval set that catches their regressions. Commit the baseline. Require it to pass before deploy.

Capability and capability-that-the-agent-can-find are different properties. The first is the work. The second is the infrastructure that makes the first visible. Both have to ship.

---

*NovoMCP exposes 62 tools across drug discovery, quantum chemistry, molecular dynamics, and materials science. The tool-search endpoint is live at `/mcp/tool-search` on both `ai.novomcp.com` and `compute.novomcp.com`.*
