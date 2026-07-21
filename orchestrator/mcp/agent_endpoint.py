"""POST /v1/agent/chat — the Studio server-side agent (SSE).

Auth + billing reuse the existing MCP plumbing: `get_mcp_user` for the
tier/org/credits context, the live `MCPToolExecutor` for tool execution (which
records credits per call). The org's BYO LLM key comes from the vault
(`llm_vault`), never the client. The loop itself is `agent_runtime.run_agent_loop`;
this module just wires auth → vault → provider → loop and streams the events.

Stream protocol (text/event-stream): each `data:` line is a JSON event
({type: start|text|tool_use|tool_result|final|error, ...}); a terminal
`data: [DONE]` closes the stream.
"""
from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from . import router as _router  # get_mcp_user, _json_body, live _tool_executor
from .auth import MCPUser
from .agent_runtime import build_agent_tools, run_agent_loop
from .llm_vault import get_org_llm_config
from ai.llm_providers import LlmError, make_provider

logger = logging.getLogger(__name__)

agent_router = APIRouter(prefix="/v1/agent", tags=["NovoMCP Agent"])

DEFAULT_SYSTEM_PROMPT = (
    "You are NovoMCP — the computational chemistry engine for drug discovery and "
    "materials science — operating inside the user's Studio workspace. You have "
    "tools for molecular profiling, ADMET, FAVES compliance, docking, MD, "
    "QM/NNP, literature, and the 12-stage discovery funnel. Call tools to ground "
    "every quantitative claim in computed results — never invent property values. "
    "Be concise and cite the tool outputs you used."
)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@agent_router.post("/chat")
async def agent_chat(request: Request, user: MCPUser = Depends(_router.get_mcp_user)):
    """Run the agent tool-calling loop for the org's configured LLM, streaming
    events as SSE. Body: {messages: [...Anthropic-style turns], system?: str}."""
    if user.is_trial_blocked:
        raise HTTPException(
            status_code=402,
            detail={
                "error": getattr(user, "trial_block_reason", None) or "credits_exhausted",
                "message": "Your credits are depleted or your trial has expired.",
                "upgrade_url": "https://novomcp.com/pricing",
            },
        )

    body = await _router._json_body(request)
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(
            status_code=400,
            detail="Body must include a non-empty 'messages' array (Anthropic-style turns).",
        )
    system = body.get("system") or DEFAULT_SYSTEM_PROMPT

    # Optional: the client explicitly started an audited funnel (Studio "Start
    # Funnel"). Accept a well-formed funnel_id and scope every funnel-eligible
    # tool call to it inside the loop. Ignore anything that doesn't look like a
    # funnel id so a malformed value can't poison the audit log.
    funnel_id = body.get("funnel_id")
    if not (isinstance(funnel_id, str) and re.fullmatch(r"funnel_[A-Za-z0-9_]{1,80}", funnel_id)):
        funnel_id = None

    executor = _router._tool_executor
    if executor is None:
        raise HTTPException(status_code=503, detail="Agent runtime not initialized")

    cfg = await get_org_llm_config(user.org_id)
    if not cfg:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "llm_not_configured",
                "message": "No LLM provider is configured for your organization. "
                           "An admin can add one in Studio settings.",
            },
        )

    try:
        provider = make_provider(cfg["provider"], cfg["model"], cfg["api_key"], cfg.get("base_url"))
    except LlmError as exc:
        raise HTTPException(status_code=400, detail={"error": "llm_provider_error", "message": str(exc)})

    tools = build_agent_tools(user.tier.value)

    async def stream():
        yield _sse({"type": "start", "provider": cfg["provider"], "model": cfg["model"]})
        try:
            async for event in run_agent_loop(
                provider=provider,
                executor=executor,
                system=system,
                messages=messages,
                tools=tools,
                user_tier=user.tier.value,
                org_id=user.org_id,
                user_id=user.user_id,
                user_email=user.email,
                credits_available=user.credits_available,
                funnel_id=funnel_id,
            ):
                yield _sse(event)
        except LlmError as exc:
            yield _sse({"type": "error", "content": str(exc)})
        except Exception:  # defensive — don't leak a stack trace into the stream
            logger.exception("agent loop crashed")
            yield _sse({"type": "error", "content": "Agent runtime error."})
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
