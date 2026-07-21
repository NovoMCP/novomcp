# Configuring an LLM provider

The engine ships with several optional LLM-driven features, intent recognition, orchestration planning, project enrichment, semantic tool search, and autonomous campaign decisions. **None of these are required to run tools.** If no LLM is configured, those features silently no-op and everything else works normally.

You bring your own provider. Four are built in.

## Quickest path: OpenAI

```bash
export OPENAI_API_KEY=sk-...
python3 main_https.py
```

That's it. The engine auto-detects the key on startup and uses `gpt-4o-mini` by default. Override with `OPENAI_MODEL=gpt-4o` (or any model your key can call).

## Anthropic Claude

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python3 main_https.py
```

Default model: `claude-sonnet-4-5`. Override with `ANTHROPIC_MODEL=claude-opus-4-7` etc.

## Local Ollama (no API key, free)

Install [Ollama](https://ollama.ai) locally, pull a model, and:

```bash
ollama pull llama3.2
export NOVO_LLM=ollama          # or set OLLAMA_URL to hint the auto-detect
python3 main_https.py
```

Default: `llama3.2` on `http://localhost:11434`. Override with `OLLAMA_MODEL=mistral` and `OLLAMA_URL=http://your-server:11434`.

## Azure OpenAI

```bash
export AZURE_OPENAI_API_KEY=...
export AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
export AZURE_OPENAI_DEPLOYMENT=gpt-4o
python3 main_https.py
```

## OpenAI-compatible endpoints (Together, Groq, Fireworks, etc.)

Any endpoint that speaks the OpenAI chat-completions API works:

```bash
export OPENAI_API_KEY=your_provider_key
export OPENAI_BASE_URL=https://api.together.xyz/v1
export OPENAI_MODEL=meta-llama/Llama-3.3-70B-Instruct-Turbo
python3 main_https.py
```

## Selection order

The engine picks a provider in this order:

1. **`NOVO_LLM` env var**, set to `openai`, `anthropic`, `ollama`, `azure`, or `disabled` to force a specific choice
2. **Auto-detect from present credentials**, in order:
    1. `OPENAI_API_KEY` → OpenAI
    2. `ANTHROPIC_API_KEY` → Anthropic
    3. `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT` → Azure OpenAI
    4. `OLLAMA_URL` set → Ollama
3. **None** → disabled (LLM features no-op)

## What breaks without an LLM?

- **`tool_search` semantic mode**, falls back to keyword-match. Still works, just no embeddings.
- **`intent_recognizer`**, the free-text-to-tool-call planner is disabled. Direct tool calls still work.
- **`orchestration_planner`**, no auto-plan of multi-tool workflows. You call the tools directly instead.
- **`campaign_decision_engine`**, autonomous funnel decisions won't run. Manual funnel calls work fine.
- **`project_enricher`**, no auto-summarization of results.

Every actual tool call (`calculate_properties`, `dock_molecules`, `run_molecular_dynamics`, etc.) works with or without an LLM configured.

## Writing your own provider

The provider interface is in `mcp/../ai/llm_provider.py`. Any class with these methods can be swapped in:

```python
class MyProvider:
    provider_name = "mine"
    available = True
    model = "my-model"

    async def complete(self, prompt, system_prompt=None, temperature=0.3,
                       max_tokens=1500, response_format=None) -> dict: ...
    async def chat_completion(self, messages, tools=None, tool_choice=None,
                              temperature=0.7, max_tokens=2000): ...
    async def parse_json_response(self, response_text) -> dict | None: ...
    def estimate_cost(self, tokens) -> float: ...
    def get_status(self) -> dict: ...
```

Instantiate it and monkey-patch the factory, or wire it into `build_llm()` directly and set `NOVO_LLM=mine`.

## Cost visibility

Every LLM call returns a `tokens` dict (`input`, `output`, `total`) alongside the response. Multiply by your provider's per-token rate for accurate cost tracking, or call `provider.estimate_cost(tokens)` for a rough estimate using the shipped price tables (subject to drift; check your provider's current rates for billing).
