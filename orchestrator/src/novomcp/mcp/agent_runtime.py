"""Server-side agent tool-calling loop for Studio (Phase-2b).

Ports NovoWorkbench's client-side Rust `chat_with_tools` loop to Python so the
web Studio shell can run the agent server-side: the org's BYO LLM picks NovoMCP
tools, we execute them via the existing MCPToolExecutor (auth + billing reused),
feed results back, and repeat up to a round cap.

`run_agent_loop` is an async generator yielding events (text / tool_use /
tool_result / final / error) — the HTTP endpoint streams these as SSE and the
WebAdapter bridges them into the chat panel. Provider-agnostic: it consumes the
normalized `LlmResponse` from `ai/llm_providers.py`.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Optional

from .tools import MCP_TOOLS, ToolTier, rest_tool_visible

DEFAULT_MAX_ROUNDS = 6
AGENT_SURFACE = "studio-agent"


def build_agent_tools(user_tier: str) -> list[dict]:
    """LLM tool definitions the agent may call, filtered to what this tier can
    use on the unified surface (compute-only tools require a paid tier — same
    `rest_tool_visible` gate as the REST API)."""
    tools: list[dict] = []
    for name, tool in MCP_TOOLS.items():
        if not rest_tool_visible(name, user_tier):
            continue
        tools.append({
            "name": tool["name"],
            "description": tool.get("description", ""),
            "input_schema": tool.get("inputSchema", {"type": "object"}),
        })
    return tools


async def run_agent_loop(
    *,
    provider,           # ai.llm_providers.LlmProvider (duck-typed: .chat(system, messages, tools))
    executor,           # mcp.tools.MCPToolExecutor (duck-typed: async .execute(...))
    system: str,
    messages: list[dict],
    tools: list[dict],
    user_tier: str,
    org_id: Optional[str],
    user_id: Optional[str],
    user_email: Optional[str],
    credits_available: Optional[float],
    funnel_id: Optional[str] = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    surface: str = AGENT_SURFACE,
) -> AsyncIterator[dict]:
    """Drive the model→tools→model loop, yielding stream events.

    `messages` is mutated in place (the running conversation, Anthropic-style),
    so the caller can persist it after the loop.

    `funnel_id`, when provided (the user explicitly started an audited funnel in
    the client), scopes every funnel-eligible tool call to that id: we inject it
    into the arguments of any tool whose schema declares a `funnel_id` param and
    that didn't already supply one. A system directive reinforces it, but the
    injection is the hard guarantee — server-side logging keys on the id we pass,
    not on the model choosing to echo it.
    """
    tier_enum = ToolTier(user_tier)

    # Tools that accept a funnel_id (per their JSON schema) — only inject into these.
    funnel_aware: set[str] = {
        t["name"]
        for t in tools
        if "funnel_id" in (t.get("input_schema", {}) or {}).get("properties", {})
    }
    if funnel_id:
        system = (
            f"{system}\n\nAn audited discovery funnel is active for this session: "
            f'funnel_id="{funnel_id}". Pass this exact funnel_id on every '
            "funnel-eligible tool call. Do not mint a new one or switch funnels."
        )

    for _round in range(max_rounds):
        response = await provider.chat(system, messages, tools)

        # Terminal: the model answered without requesting tools.
        if not response.wants_tools:
            yield {"type": "final", "content": response.text or ""}
            return

        # Record the assistant turn (optional text + the tool_use blocks).
        assistant_blocks: list[dict] = []
        if response.text:
            assistant_blocks.append({"type": "text", "text": response.text})
            yield {"type": "text", "content": response.text}
        # Bind funnel-eligible calls to the active funnel before recording/executing
        # so the transcript, the streamed event, and the executed args all agree.
        if funnel_id:
            for tu in response.tool_uses:
                if tu.name in funnel_aware and not (tu.input or {}).get("funnel_id"):
                    tu.input = {**(tu.input or {}), "funnel_id": funnel_id}
        for tu in response.tool_uses:
            assistant_blocks.append({"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input})
        messages.append({"role": "assistant", "content": assistant_blocks})

        # Execute each requested tool; collect tool_result blocks for the next turn.
        result_blocks: list[dict] = []
        for tu in response.tool_uses:
            yield {"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input}
            result = await executor.execute(
                tool_name=tu.name,
                arguments=tu.input,
                user_tier=tier_enum,
                org_id=org_id,
                user_id=user_id,
                user_email=user_email,
                credits_available=credits_available,
                surface=surface,
            )
            payload: Any = result.data if result.success else {"error": result.error or "tool failed"}
            yield {
                "type": "tool_result",
                "id": tu.id,
                "name": tu.name,
                "success": bool(result.success),
                "result": payload,
                "usage": getattr(result, "usage", {}) or {},
            }
            result_blocks.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": json.dumps(payload, default=str),
            })
        messages.append({"role": "user", "content": result_blocks})

    yield {"type": "error", "content": f"Exceeded the maximum of {max_rounds} tool-call rounds."}
