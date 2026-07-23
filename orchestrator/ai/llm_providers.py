"""Provider-agnostic LLM client for the Studio agent runtime (bring-your-own
key, tool-use).

The agent loop speaks ONE normalized format — Anthropic-style messages (role +
content blocks: text / tool_use / tool_result) — and each provider translates to
its own wire format. `chat()` returns a normalized {text, tool_uses} regardless
of provider, so `mcp/agent_runtime.py` stays provider-agnostic.

Supported: Anthropic (Messages API), OpenAI (Chat Completions), and Gemini /
Mistral / Cohere via their OpenAI-compatible endpoints (thin OpenAIProvider
subclasses). Keys are the org's BYO keys, fetched server-side from the vault —
never the platform's.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx


@dataclass
class ToolUse:
    """A tool the model wants to call."""
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LlmResponse:
    """Normalized model turn: free text, a request to use tools, or both."""
    text: Optional[str] = None
    tool_uses: list[ToolUse] = field(default_factory=list)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_uses)


class LlmError(Exception):
    """Provider/transport error surfaced to the agent loop."""


class LlmProvider:
    """Base class. `chat(system, messages, tools)` → LlmResponse.

    `messages` are Anthropic-style: a list of {role, content} where content is a
    string or a list of blocks. `tools` are {name, description, input_schema}.
    """

    name: str = "base"
    default_base_url: str = ""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: Optional[str] = None,
        *,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._client = client  # injectable for tests

    async def chat(self, system: str, messages: list[dict], tools: list[dict]) -> LlmResponse:
        raise NotImplementedError

    async def _post(self, url: str, headers: dict, body: dict) -> dict:
        try:
            if self._client is not None:
                resp = await self._client.post(url, headers=headers, json=body, timeout=self.timeout)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise LlmError(f"{self.name}: transport error: {exc}") from exc
        if resp.status_code >= 400:
            raise LlmError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.json()


class AnthropicProvider(LlmProvider):
    name = "anthropic"
    default_base_url = "https://api.anthropic.com"

    async def chat(self, system: str, messages: list[dict], tools: list[dict]) -> LlmResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,  # already Anthropic-native
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = tools
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        data = await self._post(f"{self.base_url}/v1/messages", headers, body)

        text_parts: list[str] = []
        tool_uses: list[ToolUse] = []
        for block in data.get("content", []):
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_uses.append(ToolUse(id=block["id"], name=block["name"], input=block.get("input", {})))
        return LlmResponse(text="\n".join(p for p in text_parts if p) or None, tool_uses=tool_uses)


class OpenAIProvider(LlmProvider):
    name = "openai"
    default_base_url = "https://api.openai.com"
    # Path appended to base_url. Subclasses (Gemini/Cohere/…) override when their
    # OpenAI-compatible endpoint isn't under /v1.
    completions_path = "/v1/chat/completions"

    async def chat(self, system: str, messages: list[dict], tools: list[dict]) -> LlmResponse:
        body: dict[str, Any] = {"model": self.model, "messages": self._to_openai_messages(system, messages)}
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t["input_schema"]}}
                for t in tools
            ]
            body["tool_choice"] = "auto"
        headers = {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        data = await self._post(f"{self.base_url}{self.completions_path}", headers, body)

        msg = data["choices"][0]["message"]
        tool_uses: list[ToolUse] = []
        for call in (msg.get("tool_calls") or []):
            fn = call["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_uses.append(ToolUse(id=call["id"], name=fn["name"], input=args))
        return LlmResponse(text=msg.get("content"), tool_uses=tool_uses)

    @staticmethod
    def _to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
        """Translate Anthropic-style blocks → OpenAI chat messages (assistant
        tool_calls + role:'tool' results)."""
        out: list[dict] = []
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            role, content = m["role"], m["content"]
            if isinstance(content, str):
                out.append({"role": role, "content": content})
                continue
            if role == "assistant":
                text_parts: list[str] = []
                tool_calls: list[dict] = []
                for b in content:
                    if b["type"] == "text":
                        text_parts.append(b["text"])
                    elif b["type"] == "tool_use":
                        tool_calls.append({
                            "id": b["id"],
                            "type": "function",
                            "function": {"name": b["name"], "arguments": json.dumps(b.get("input", {}))},
                        })
                am: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
                if tool_calls:
                    am["tool_calls"] = tool_calls
                out.append(am)
            else:  # user turn: may carry tool_result blocks and/or text
                text_parts = []
                for b in content:
                    if b["type"] == "tool_result":
                        c = b["content"]
                        out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": c if isinstance(c, str) else json.dumps(c)})
                    elif b["type"] == "text":
                        text_parts.append(b["text"])
                if text_parts:
                    out.append({"role": "user", "content": "\n".join(text_parts)})
        return out


# Gemini, Mistral, and Cohere all expose OpenAI-compatible chat-completions
# endpoints (with function calling), so they reuse OpenAIProvider's translation
# wholesale — only the base URL + path differ. Keys are still the org's BYO keys.
class GeminiProvider(OpenAIProvider):
    name = "gemini"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
    completions_path = "/chat/completions"


class MistralProvider(OpenAIProvider):
    name = "mistral"
    default_base_url = "https://api.mistral.ai"
    completions_path = "/v1/chat/completions"


class CohereProvider(OpenAIProvider):
    name = "cohere"
    default_base_url = "https://api.cohere.ai/compatibility"
    completions_path = "/v1/chat/completions"


_PROVIDERS = {
    "anthropic": AnthropicProvider,
    "claude": AnthropicProvider,
    "openai": OpenAIProvider,
    "gpt": OpenAIProvider,
    "gemini": GeminiProvider,
    "google": GeminiProvider,
    "mistral": MistralProvider,
    "cohere": CohereProvider,
}


def make_provider(provider: str, model: str, api_key: str, base_url: Optional[str] = None, **kwargs) -> LlmProvider:
    cls = _PROVIDERS.get((provider or "").lower())
    if cls is None:
        raise LlmError(f"Unsupported LLM provider '{provider}' (supported: {', '.join(sorted(set(_PROVIDERS)))})")
    return cls(model=model, api_key=api_key, base_url=base_url, **kwargs)
