"""
LLM provider abstraction.

The engine has several optional LLM-driven features (intent recognition,
orchestration planning, project enrichment, campaign decision engine,
semantic tool search). None are required; the engine falls back to
deterministic behavior when no provider is configured.

Providers ship in a plug-in style. Select via env var:

    NOVO_LLM = openai | anthropic | ollama | azure | disabled

If unset, the factory auto-detects based on which credentials are
present. If nothing is set, the disabled provider is returned and the
LLM-driven features silently no-op.

Providers implement a common surface matching the legacy AzureOpenAIClient:

    async complete(prompt, system_prompt=..., temperature=..., max_tokens=...,
                   response_format=...) -> dict{success, response, tokens, ...}
    async chat_completion(messages, tools=..., tool_choice=..., ...) -> raw
    parse_json_response(text) -> dict | None
    estimate_cost(tokens) -> float
    get_status() -> dict
    available: bool

All three implementations (OpenAI, Anthropic, Ollama) speak the same
OpenAI-Chat-Completions wire shape internally so the caller sees one
uniform surface.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _parse_json_response(response_text: str) -> Optional[Dict]:
    """Best-effort JSON extraction from an LLM response."""
    if not response_text:
        return None
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(response_text[start:end])
            except Exception:
                return None
    return None


# ---------------------------------------------------------------------------
# Disabled provider — the default when no credentials are configured.
# Callers get success=False + a helpful error; nothing crashes.
# ---------------------------------------------------------------------------

class DisabledLLMProvider:
    """No-op provider. Returns structured 'unavailable' responses."""

    available = False
    provider_name = "disabled"
    model = None

    async def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1500,
        response_format: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        return {
            "success": False,
            "error": (
                "No LLM provider configured. Set OPENAI_API_KEY, "
                "ANTHROPIC_API_KEY, AZURE_OPENAI_API_KEY, or run Ollama "
                "locally (http://localhost:11434). See docs/configuring-llm.md."
            ),
            "response": None,
        }

    async def chat_completion(self, *args, **kwargs):
        raise RuntimeError("No LLM provider configured")

    async def parse_json_response(self, response_text: str) -> Optional[Dict]:
        return _parse_json_response(response_text)

    def estimate_cost(self, tokens: Optional[Dict[str, int]]) -> float:
        return 0.0

    def get_status(self) -> Dict[str, Any]:
        return {"available": False, "provider": "disabled", "model": None}


# ---------------------------------------------------------------------------
# OpenAI (also covers OpenAI-compatible endpoints — Together, Groq, etc.)
# ---------------------------------------------------------------------------

class OpenAIProvider:
    """Standard OpenAI API. Works against any OpenAI-compatible endpoint."""

    provider_name = "openai"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")  # None = default api.openai.com
        self.available = False
        self.client = None

        if not self.api_key:
            logger.debug("OPENAI_API_KEY not set; OpenAI provider disabled")
            return
        try:
            from openai import OpenAI
        except ImportError:
            logger.debug("openai package not installed; run `pip install openai`")
            return
        try:
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self.client = OpenAI(**kwargs)
            self.available = True
            logger.info(f"OpenAI provider initialized (model={self.model})")
        except Exception as e:
            logger.error(f"OpenAI provider init failed: {e}")

    async def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1500,
        response_format: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        if not self.available:
            return {"success": False, "error": "OpenAI provider not available", "response": None}
        try:
            start = time.time()
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if response_format:
                kwargs["response_format"] = response_format
            resp = self.client.chat.completions.create(**kwargs)
            usage = resp.usage
            return {
                "success": True,
                "response": resp.choices[0].message.content,
                "duration_ms": int((time.time() - start) * 1000),
                "tokens": {
                    "input": usage.prompt_tokens if usage else None,
                    "output": usage.completion_tokens if usage else None,
                    "total": usage.total_tokens if usage else None,
                },
                "model": self.model,
            }
        except Exception as e:
            logger.error(f"OpenAI complete failed: {e}")
            return {"success": False, "error": str(e), "response": None}

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **_kwargs,
    ):
        if not self.available:
            raise RuntimeError("OpenAI provider not available")
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
        return self.client.chat.completions.create(**kwargs)

    async def parse_json_response(self, response_text: str) -> Optional[Dict]:
        return _parse_json_response(response_text)

    def estimate_cost(self, tokens: Optional[Dict[str, int]]) -> float:
        # Rough per-model pricing per 1K tokens. Update as pricing evolves.
        if not tokens or not tokens.get("total"):
            return 0.0
        pricing = {
            "gpt-4o":       (0.005, 0.015),
            "gpt-4o-mini":  (0.00015, 0.0006),
            "gpt-4-turbo":  (0.01, 0.03),
            "gpt-3.5-turbo": (0.0005, 0.0015),
        }
        in_cost, out_cost = pricing.get(self.model, (0.005, 0.015))
        return round(
            (tokens.get("input", 0) / 1000) * in_cost
            + (tokens.get("output", 0) / 1000) * out_cost,
            5,
        )

    def get_status(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "provider": self.provider_name,
            "model": self.model,
            "base_url": self.base_url or "https://api.openai.com/v1",
        }


# ---------------------------------------------------------------------------
# Anthropic (Claude)
# ---------------------------------------------------------------------------

class AnthropicProvider:
    """Anthropic Claude API."""

    provider_name = "anthropic"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
        self.available = False
        self.client = None
        if not self.api_key:
            logger.debug("ANTHROPIC_API_KEY not set; Anthropic provider disabled")
            return
        try:
            from anthropic import Anthropic
        except ImportError:
            logger.debug("anthropic package not installed; run `pip install anthropic`")
            return
        try:
            self.client = Anthropic(api_key=self.api_key)
            self.available = True
            logger.info(f"Anthropic provider initialized (model={self.model})")
        except Exception as e:
            logger.error(f"Anthropic provider init failed: {e}")

    async def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1500,
        response_format: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        if not self.available:
            return {"success": False, "error": "Anthropic provider not available", "response": None}
        try:
            start = time.time()
            kwargs = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system_prompt:
                kwargs["system"] = system_prompt
            resp = self.client.messages.create(**kwargs)
            text = "".join(block.text for block in resp.content if hasattr(block, "text"))
            usage = resp.usage
            return {
                "success": True,
                "response": text,
                "duration_ms": int((time.time() - start) * 1000),
                "tokens": {
                    "input": usage.input_tokens if usage else None,
                    "output": usage.output_tokens if usage else None,
                    "total": (usage.input_tokens + usage.output_tokens) if usage else None,
                },
                "model": self.model,
            }
        except Exception as e:
            logger.error(f"Anthropic complete failed: {e}")
            return {"success": False, "error": str(e), "response": None}

    async def chat_completion(self, *_args, **_kwargs):
        # Anthropic's tool-calling shape differs from OpenAI's; callers using
        # chat_completion should target providers whose native shape matches.
        raise NotImplementedError(
            "chat_completion is OpenAI-shaped; use complete() for Anthropic, "
            "or switch NOVO_LLM to openai / ollama for tools-API compatibility"
        )

    async def parse_json_response(self, response_text: str) -> Optional[Dict]:
        return _parse_json_response(response_text)

    def estimate_cost(self, tokens: Optional[Dict[str, int]]) -> float:
        if not tokens or not tokens.get("total"):
            return 0.0
        # Rough Claude pricing per 1M tokens as of writing.
        pricing = {
            "claude-sonnet-4-5":  (3.0, 15.0),
            "claude-opus-4-7":    (15.0, 75.0),
            "claude-haiku-4-5":   (0.8, 4.0),
        }
        in_cost, out_cost = pricing.get(self.model, (3.0, 15.0))
        return round(
            (tokens.get("input", 0) / 1_000_000) * in_cost
            + (tokens.get("output", 0) / 1_000_000) * out_cost,
            5,
        )

    def get_status(self) -> Dict[str, Any]:
        return {"available": self.available, "provider": self.provider_name, "model": self.model}


# ---------------------------------------------------------------------------
# Ollama — local LLM server (no key required)
# ---------------------------------------------------------------------------

class OllamaProvider:
    """Local Ollama server. No API key; defaults to http://localhost:11434."""

    provider_name = "ollama"

    def __init__(self, model: Optional[str] = None, base_url: Optional[str] = None):
        self.base_url = base_url or os.getenv("OLLAMA_URL", "http://localhost:11434")
        self.model = model or os.getenv("OLLAMA_MODEL", "llama3.2")
        self.available = self._reachable()
        if self.available:
            logger.info(f"Ollama provider initialized (model={self.model}, url={self.base_url})")
        else:
            logger.debug(f"Ollama not reachable at {self.base_url}; provider disabled")

    def _reachable(self) -> bool:
        try:
            import httpx
            r = httpx.get(f"{self.base_url}/api/tags", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False

    async def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1500,
        response_format: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        if not self.available:
            return {"success": False, "error": "Ollama not reachable", "response": None}
        try:
            import httpx
            start = time.time()
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            }
            if response_format and response_format.get("type") == "json_object":
                payload["format"] = "json"
            async with httpx.AsyncClient(timeout=120.0) as c:
                r = await c.post(f"{self.base_url}/api/chat", json=payload)
                r.raise_for_status()
                data = r.json()
            return {
                "success": True,
                "response": data.get("message", {}).get("content", ""),
                "duration_ms": int((time.time() - start) * 1000),
                "tokens": {
                    "input": data.get("prompt_eval_count"),
                    "output": data.get("eval_count"),
                    "total": (data.get("prompt_eval_count") or 0) + (data.get("eval_count") or 0),
                },
                "model": self.model,
            }
        except Exception as e:
            logger.error(f"Ollama complete failed: {e}")
            return {"success": False, "error": str(e), "response": None}

    async def chat_completion(self, *_args, **_kwargs):
        raise NotImplementedError(
            "chat_completion (tools API) is not implemented for Ollama; use complete()"
        )

    async def parse_json_response(self, response_text: str) -> Optional[Dict]:
        return _parse_json_response(response_text)

    def estimate_cost(self, tokens: Optional[Dict[str, int]]) -> float:
        return 0.0  # local — no cost

    def get_status(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "provider": self.provider_name,
            "model": self.model,
            "base_url": self.base_url,
        }


# ---------------------------------------------------------------------------
# Azure OpenAI — the legacy path, kept for existing deployments
# ---------------------------------------------------------------------------

class AzureOpenAIProvider:
    """Azure OpenAI. Preserves the historical deployment path."""

    provider_name = "azure"

    def __init__(self):
        self.api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
        self.endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
        self.model = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
        self.api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
        self.available = False
        self.client = None
        if not (self.api_key and self.endpoint):
            logger.debug("AZURE_OPENAI_API_KEY / _ENDPOINT not set; Azure provider disabled")
            return
        try:
            from openai import AzureOpenAI
        except ImportError:
            logger.debug("openai package not installed; run `pip install openai`")
            return
        try:
            self.client = AzureOpenAI(
                api_key=self.api_key,
                api_version=self.api_version,
                azure_endpoint=self.endpoint,
            )
            self.available = True
            logger.info(f"Azure OpenAI provider initialized (deployment={self.model})")
        except Exception as e:
            logger.error(f"Azure OpenAI provider init failed: {e}")

    async def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1500,
        response_format: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        if not self.available:
            return {"success": False, "error": "Azure OpenAI provider not available", "response": None}
        try:
            start = time.time()
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if response_format:
                kwargs["response_format"] = response_format
            resp = self.client.chat.completions.create(**kwargs)
            usage = resp.usage
            return {
                "success": True,
                "response": resp.choices[0].message.content,
                "duration_ms": int((time.time() - start) * 1000),
                "tokens": {
                    "input": usage.prompt_tokens if usage else None,
                    "output": usage.completion_tokens if usage else None,
                    "total": usage.total_tokens if usage else None,
                },
                "model": self.model,
            }
        except Exception as e:
            logger.error(f"Azure OpenAI complete failed: {e}")
            return {"success": False, "error": str(e), "response": None}

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **_kwargs,
    ):
        if not self.available:
            raise RuntimeError("Azure OpenAI provider not available")
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
        return self.client.chat.completions.create(**kwargs)

    async def parse_json_response(self, response_text: str) -> Optional[Dict]:
        return _parse_json_response(response_text)

    def estimate_cost(self, tokens: Optional[Dict[str, int]]) -> float:
        # Azure pricing tracks OpenAI's list. Use gpt-4-turbo default rates.
        if not tokens or not tokens.get("total"):
            return 0.0
        return round(
            (tokens.get("input", 0) / 1000) * 0.03
            + (tokens.get("output", 0) / 1000) * 0.06,
            5,
        )

    def get_status(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "provider": self.provider_name,
            "model": self.model,
            "endpoint": self.endpoint if self.available else None,
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_llm():
    """Return an LLM provider based on environment configuration.

    Selection order:
      1. NOVO_LLM env var if explicitly set (openai | anthropic | ollama |
         azure | disabled).
      2. Auto-detect from present credentials, in preference order:
         OpenAI → Anthropic → Azure OpenAI → Ollama (if reachable).
      3. DisabledLLMProvider if none of the above.

    The returned object presents a uniform surface (complete,
    chat_completion, parse_json_response, estimate_cost, get_status,
    available). Callers do not need to know which provider was chosen.
    """
    explicit = os.getenv("NOVO_LLM", "").strip().lower()

    if explicit == "disabled":
        return DisabledLLMProvider()
    if explicit == "openai":
        return OpenAIProvider()
    if explicit == "anthropic":
        return AnthropicProvider()
    if explicit == "ollama":
        return OllamaProvider()
    if explicit == "azure":
        return AzureOpenAIProvider()

    # Auto-detect
    if os.getenv("OPENAI_API_KEY"):
        p = OpenAIProvider()
        if p.available:
            return p
    if os.getenv("ANTHROPIC_API_KEY"):
        p = AnthropicProvider()
        if p.available:
            return p
    if os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"):
        p = AzureOpenAIProvider()
        if p.available:
            return p
    # Ollama only if user hinted at it or the URL is set.
    if os.getenv("OLLAMA_URL") or explicit == "ollama":
        p = OllamaProvider()
        if p.available:
            return p

    return DisabledLLMProvider()
