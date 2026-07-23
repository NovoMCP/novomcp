"""
AzureOpenAIClient — backward-compat facade over the LLM provider abstraction.

Historically this class was hardcoded to Azure OpenAI. It now delegates
to whichever LLM provider `build_llm()` selects (OpenAI, Anthropic,
Ollama, Azure OpenAI, or disabled). The class name is preserved so
existing consumers (`IntentRecognizer`, `OrchestrationPlanner`,
`ProjectEnricher`, `CampaignDecisionEngine`, etc.) keep working without
modification.

For new code, prefer:

    from ai.llm_provider import build_llm
    llm = build_llm()

The provider selection is documented in `docs/configuring-llm.md`.
"""

import logging
from typing import Any, Dict, List, Optional

from .llm_provider import build_llm

logger = logging.getLogger(__name__)


class AzureOpenAIClient:
    """Facade: delegates to whichever provider the environment selects.

    The name is historical — the underlying provider may be OpenAI,
    Anthropic, Ollama, Azure, or disabled depending on env config.
    """

    def __init__(self, config=None):
        self.config = config
        self._provider = build_llm()
        # Mirror the legacy public attributes so existing code inspecting
        # `.available`, `.deployment_name`, etc. keeps working.
        self.available = self._provider.available
        self.deployment_name = getattr(self._provider, "model", None)
        self.endpoint = getattr(self._provider, "endpoint", None) or getattr(
            self._provider, "base_url", None
        )
        self.api_key = getattr(self._provider, "api_key", None)
        self.api_version = getattr(self._provider, "api_version", None)
        self.client = getattr(self._provider, "client", None)
        if self.available:
            logger.info(f"AI client using provider={self._provider.provider_name}")

    async def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1500,
        response_format: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        return await self._provider.complete(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        functions: Optional[List[Dict[str, Any]]] = None,
        function_call: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ):
        # Backward-compat: convert legacy `functions` arg to `tools`
        if functions and not tools:
            tools = [{"type": "function", "function": f} for f in functions]
            if function_call and function_call != "auto":
                tool_choice = {"type": "function", "function": {"name": function_call}}
            elif function_call == "auto":
                tool_choice = "auto"
        return await self._provider.chat_completion(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def parse_json_response(self, response_text: str) -> Optional[Dict]:
        return await self._provider.parse_json_response(response_text)

    def estimate_cost(self, tokens: Optional[Dict[str, int]]) -> float:
        return self._provider.estimate_cost(tokens)

    def get_status(self) -> Dict[str, Any]:
        s = self._provider.get_status()
        # Legacy shape included a `capabilities` list — preserve it.
        s.setdefault("capabilities", [
            "intent_recognition",
            "project_enrichment",
            "orchestration_planning",
            "natural_language_understanding",
            "entity_extraction",
        ])
        return s

    # -----------------------------------------------------------------
    # Prompt-fragment helpers (kept as class methods so legacy call sites
    # in ai/orchestration_planner.py and others don't break).
    # -----------------------------------------------------------------

    def get_service_context(self) -> str:
        return (
            "Available NovoMCP compute services (each is optional; only some "
            "may be wired in a given deployment): chem-props (RDKit properties), "
            "addie-models (ADMET), autodock-gpu (docking), gromacs-md (molecular "
            "dynamics), openfold3 (protein structure), novomcp-qm (quantum "
            "mechanics), novomcp-nnp (neural network potentials), "
            "(free-energy perturbation), faves-compliance (regulatory screening), "
            "novoexpert (clinical outcomes). Route tool calls only to services "
            "reachable in the current environment; the executor returns a "
            "structured 'service unavailable' error when one is not configured."
        )

    def get_orchestration_context(self) -> str:
        return (
            "Orchestration guidelines: parallelize independent tool calls; "
            "cache read results within a session; use async job APIs for "
            "long-running operations (MD); handle structured error "
            "responses gracefully — a missing service is not a fatal error."
        )
