"""
Tool Search — in-memory semantic retrieval over the MCP tool catalog.

For 68 tool descriptions growing toward ~100, this module holds a numpy
embedding index in RAM (~400 KB at 1536 dims) and serves cosine-similarity
retrieval per query. No Pinecone, no SQL VECTOR, no Redis — the catalog
is small and static; the codebase is the source of truth.

See docs/NovoMCP/AGENT-SDK-TOOL-SEARCH.md for architectural context.

Flow
----
- At server startup (via FastAPI lifespan), `build_index()` collects every
  tool's name + description + param names + enum values, batched-embeds
  them via Azure OpenAI `text-embedding-3-large` at 1536 dims (matches
  save_funnel_memory's matryoshka pattern), and stores the result.
- Per request, `search()` embeds the query and returns the top-K tools by
  cosine similarity, tier-filtered for the caller.
- If the embedding API is unreachable, a keyword-match fallback keeps the
  endpoint functional (degraded) instead of 500-ing.

Core whitelist
--------------
Eight tools are always returned regardless of query — platform info,
credits, funnel logging, autonomous trigger, job polling. Ensures the
caller can always orient itself even if retrieval misses.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .tools import MCP_TOOLS, ToolTier

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

# Embedding dims — matches save_funnel_memory (matryoshka-truncated from 3072).
def _embedding_dim_for_provider() -> int:
    """Pick the dimension for the in-memory index based on the active provider.

    Azure text-embedding-3-large with Matryoshka truncation: 1536.
    Cohere Embed v3: 1024 (fixed).
    """
    provider = os.getenv("EMBEDDING_PROVIDER", "azure").lower()
    if provider == "cohere":
        return 1024
    return 1536


EMBEDDING_DIMENSIONS = _embedding_dim_for_provider()

# Tools always loaded regardless of query. Keep this small; these slots
# compete for context with retrieved tools.
CORE_WHITELIST = {
    "get_platform_info",
    "get_credit_usage",
    "novo_compute_info",
    "save_funnel_stage",
    "get_funnel_audit",
    "run_novo_ag",
    "get_job_status",
    "list_jobs",
}

# Template manifests: when the caller names a known prompt template, we
# skip retrieval and pre-load its full tool set. Templates encode their
# flow; encoding their tool set alongside is a small extension.
TEMPLATE_MANIFESTS: Dict[str, List[str]] = {
    "discovery_funnel": [
        "search_prior_runs",
        "target_discovery",
        "validate_target",
        "search_literature",
        "search_biorxiv",
        "search_chembl",
        "predict_admet",
        "check_compliance",
        "predict_pka",
        "predict_solubility",
        "lead_optimization",
        "optimize_molecule",
        "dock_molecules",
        "dock_with_strain",
        "predict_clinical_outcomes",
        "audit_system",
        "run_molecular_dynamics",
        "generate_dynamics",
        "stratify_patients",
        "save_funnel_memory",
    ],
    "discovery_funnel_interactive": [
        "search_prior_runs",
        "target_discovery",
        "validate_target",
        "search_literature",
        "search_biorxiv",
        "search_chembl",
        "predict_admet",
        "check_compliance",
        "predict_pka",
        "predict_solubility",
        "lead_optimization",
        "optimize_molecule",
        "dock_molecules",
        "dock_with_strain",
        "predict_clinical_outcomes",
        "audit_system",
        "run_molecular_dynamics",
        "generate_dynamics",
        "stratify_patients",
        "save_funnel_memory",
        "save_funnel_stage",
    ],
    "screen_oled_library": [
        "optimize_geometry_nnp",
        "predict_frontier_orbitals",
        "run_excited_states",
        "run_qm_calculation",
        "compute_energy",
    ],
    "screen_electrolyte_library": [
        "optimize_geometry_nnp",
        "predict_redox_potential",
        "predict_frontier_orbitals",
        "run_qm_calculation",
        "run_qm_hessian",
    ],
}


# =============================================================================
# Dataclasses
# =============================================================================


@dataclass
class IndexedTool:
    """One tool in the search index."""

    name: str
    description: str
    tier: ToolTier
    input_schema: Dict[str, Any]
    searchable_text: str  # the blob that was embedded

    def to_public_dict(self) -> Dict[str, Any]:
        """Shape returned to callers."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass
class ToolSearchIndex:
    """In-memory embedding index over MCP_TOOLS."""

    tools: List[IndexedTool] = field(default_factory=list)
    embeddings: Optional[np.ndarray] = None  # shape (n_tools, EMBEDDING_DIMENSIONS)
    build_duration_seconds: Optional[float] = None
    embedding_fallback: bool = False  # True if we're in keyword-match degraded mode
    built_at: Optional[float] = None
    last_error: Optional[str] = None  # surfaces last build or query error for debugging
    build_attempts: int = 0

    @property
    def size(self) -> int:
        return len(self.tools)

    @property
    def is_ready(self) -> bool:
        return self.embeddings is not None and self.size > 0


# Module-level singleton. Populated by build_index() at server startup.
_INDEX = ToolSearchIndex()


# =============================================================================
# Searchable-text construction
# =============================================================================


def _schema_searchable_tokens(schema: Dict[str, Any]) -> List[str]:
    """Extract param names, enum values, and parameter descriptions from an
    input schema. These become part of the searchable surface — a tool with
    a `Literal["US", "EU"]` parameter should surface when a user asks about
    "EU compliance".
    """
    tokens: List[str] = []
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    if not isinstance(props, dict):
        return tokens

    for param_name, param_def in props.items():
        if not isinstance(param_def, dict):
            continue
        tokens.append(param_name)
        desc = param_def.get("description")
        if isinstance(desc, str):
            tokens.append(desc)
        enum_vals = param_def.get("enum")
        if isinstance(enum_vals, list):
            tokens.extend(str(v) for v in enum_vals if v is not None)
    return tokens


def _build_searchable_text(tool_name: str, tool_def: Dict[str, Any]) -> str:
    """Concatenate the fields we want the embedding model to see for each tool."""
    parts = [tool_name, tool_def.get("description", "")]
    title = tool_def.get("title")
    if isinstance(title, str) and title:
        parts.append(title)
    schema = tool_def.get("inputSchema") or {}
    parts.extend(_schema_searchable_tokens(schema))
    # Truncate — Azure OpenAI embedding max ~8191 tokens, we're well under
    # that on any single tool but defensive cap keeps pathological cases safe.
    blob = " ".join(p for p in parts if p).strip()
    return blob[:8000]


# =============================================================================
# Embedding via Azure OpenAI (env-var config, same pattern as AzureOpenAIClient)
# =============================================================================


def _azure_config() -> Optional[Dict[str, str]]:
    """Pull Azure OpenAI config from env vars. Mirrors AzureOpenAIClient's
    env-var fallback path (which has already proven to work in production for
    the GPT-5 orchestration tools). Avoids the AWS Secrets Manager dependency
    that EmbeddingGenerator's path requires.

    Env vars consumed:
        AZURE_OPENAI_API_KEY              — required
        AZURE_OPENAI_ENDPOINT             — required (e.g. https://eastus2.api.cognitive.microsoft.com/)
        AZURE_OPENAI_EMBEDDING_DEPLOYMENT — optional, defaults to 'text-embedding-3-large'
        AZURE_OPENAI_API_VERSION          — optional, defaults to '2024-12-01-preview'
    """
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    if not api_key or not endpoint:
        return None
    # Ensure endpoint ends with /
    if not endpoint.endswith("/"):
        endpoint = endpoint + "/"
    return {
        "api_key": api_key,
        "endpoint": endpoint,
        "deployment": os.getenv(
            "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large"
        ),
        "api_version": os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
    }


async def _embed_batch_openai(texts: List[str]) -> Tuple[Optional[np.ndarray], Optional[str]]:
    """Embed via OpenAI's direct /v1/embeddings endpoint. Uses OPENAI_API_KEY.

    OSS users who set OPENAI_API_KEY (per the pluggable-LLM auto-detection)
    get semantic tool search without any Azure setup.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, "OPENAI_API_KEY not set"

    model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
    base = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")

    import aiohttp
    url = f"{base.rstrip('/')}/embeddings"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"input": texts, "model": model, "dimensions": EMBEDDING_DIMENSIONS}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=60) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    err = f"OpenAI embedding HTTP {resp.status}: {body[:400]}"
                    logger.error(f"tool_search: {err}")
                    return None, err
                data = await resp.json()
                items = data.get("data", [])
                if len(items) != len(texts):
                    err = f"expected {len(texts)} embeddings, got {len(items)}"
                    logger.error(f"tool_search: {err}")
                    return None, err
                return np.array([item["embedding"] for item in items], dtype=np.float32), None
    except asyncio.TimeoutError:
        return None, "OpenAI embedding call timed out after 60s"
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        logger.error(f"tool_search: embedding call raised: {err}")
        return None, err


async def _embed_batch(texts: List[str], input_type: str = "search_document") -> Tuple[Optional[np.ndarray], Optional[str]]:
    """Embed a batch of texts at EMBEDDING_DIMENSIONS dims.

    Provider selection:
      1. If EMBEDDING_PROVIDER env var is set (openai|azure|cohere), use that.
      2. Otherwise auto-detect from present credentials, in this order:
         OPENAI_API_KEY → openai (simplest for OSS users)
         AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT → azure
         COHERE_API_KEY → cohere
      3. Nothing configured → return (None, "...") and caller falls back to
         keyword-match. This is the default local-mode behavior.

    Returns (vectors, error_message).
    """
    explicit = (os.getenv("EMBEDDING_PROVIDER") or "").lower()
    provider = explicit
    if not provider:
        # Auto-detect
        if os.getenv("OPENAI_API_KEY"):
            provider = "openai"
        elif os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"):
            provider = "azure"
        elif os.getenv("COHERE_API_KEY"):
            provider = "cohere"
        else:
            return None, "no embedding provider configured (set OPENAI_API_KEY, AZURE_OPENAI_API_KEY+ENDPOINT, or COHERE_API_KEY)"

    if provider == "openai":
        return await _embed_batch_openai(texts)
    if provider == "cohere":
        return await _embed_batch_cohere(texts, input_type)
    if provider != "azure":
        return None, f"unknown EMBEDDING_PROVIDER: {provider!r} (expected openai|azure|cohere)"

    cfg = _azure_config()
    if cfg is None:
        return None, "AZURE_OPENAI_API_KEY or AZURE_OPENAI_ENDPOINT env var not set"

    import aiohttp

    url = (
        f"{cfg['endpoint']}openai/deployments/{cfg['deployment']}/embeddings"
        f"?api-version={cfg['api_version']}"
    )
    headers = {"api-key": cfg["api_key"], "Content-Type": "application/json"}
    payload = {"input": texts, "dimensions": EMBEDDING_DIMENSIONS}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=60) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    err = f"Azure embedding HTTP {resp.status}: {body[:400]}"
                    logger.error(f"tool_search: {err}")
                    return None, err
                data = await resp.json()
                items = data.get("data", [])
                if len(items) != len(texts):
                    err = f"expected {len(texts)} embeddings, got {len(items)}"
                    logger.error(f"tool_search: {err}")
                    return None, err
                return np.array([item["embedding"] for item in items], dtype=np.float32), None
    except asyncio.TimeoutError:
        return None, "Azure embedding call timed out after 60s"
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        logger.error(f"tool_search: embedding call raised: {err}")
        return None, err


# =============================================================================
# Index build
# =============================================================================


async def build_index() -> ToolSearchIndex:
    """Build the in-memory tool-search index from MCP_TOOLS.

    Called once at server startup via FastAPI lifespan. Safe to call again
    to rebuild (e.g. after a hot reload of tool descriptions, or via the
    POST /mcp/tool-search/rebuild endpoint); each call replaces the global
    singleton atomically.
    """
    global _INDEX
    attempts = _INDEX.build_attempts + 1
    start = time.monotonic()

    tools: List[IndexedTool] = []
    for tool_name, tool_def in MCP_TOOLS.items():
        tier = tool_def.get("tier", ToolTier.FREE)
        if isinstance(tier, str):
            try:
                tier = ToolTier(tier)
            except ValueError:
                tier = ToolTier.FREE
        tools.append(
            IndexedTool(
                name=tool_name,
                description=tool_def.get("description", ""),
                tier=tier,
                input_schema=tool_def.get("inputSchema", {}),
                searchable_text=_build_searchable_text(tool_name, tool_def),
            )
        )

    if not tools:
        logger.warning("tool_search: MCP_TOOLS is empty, index not built")
        _INDEX = ToolSearchIndex(
            tools=[],
            embeddings=None,
            built_at=time.time(),
            last_error="MCP_TOOLS is empty",
            build_attempts=attempts,
        )
        return _INDEX

    texts = [t.searchable_text for t in tools]
    embeddings, error = await _embed_batch(texts)

    duration = time.monotonic() - start

    if embeddings is None or embeddings.shape[0] != len(tools):
        # Embedding backend unavailable — fall back to keyword-match. This
        # is the default local-mode behavior (no Azure OpenAI key set).
        logger.info(
            f"tool_search: keyword-match mode ({error}). indexed_tools={len(tools)}"
        )
        _INDEX = ToolSearchIndex(
            tools=tools,
            embeddings=None,
            build_duration_seconds=duration,
            embedding_fallback=True,
            built_at=time.time(),
            last_error=error or "embedding generation returned None",
            build_attempts=attempts,
        )
        return _INDEX

    # L2-normalize rows so cosine similarity = simple dot product at query time.
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embeddings = embeddings / norms

    _INDEX = ToolSearchIndex(
        tools=tools,
        embeddings=embeddings,
        build_duration_seconds=duration,
        embedding_fallback=False,
        built_at=time.time(),
        last_error=None,
        build_attempts=attempts,
    )
    logger.info(
        f"tool_search: index built — tools={len(tools)} "
        f"dims={EMBEDDING_DIMENSIONS} duration={duration:.2f}s"
    )
    return _INDEX


def get_index() -> ToolSearchIndex:
    """Return the current in-memory index (module singleton)."""
    return _INDEX


# =============================================================================
# Query
# =============================================================================


def _embedding_search(
    index: ToolSearchIndex, query_vec: np.ndarray, top_k: int
) -> List[Tuple[IndexedTool, float]]:
    """Cosine similarity over the index. `query_vec` must already be
    L2-normalized. Returns (tool, similarity) pairs ordered by similarity desc.
    """
    assert index.embeddings is not None, "embedding_search requires a built index"
    sims = index.embeddings @ query_vec  # (n_tools,)
    # Take top_k with argpartition then sort — faster than full sort at scale,
    # but for 62 tools it doesn't matter. Keep it simple.
    ranked_idx = np.argsort(-sims)[:top_k]
    return [(index.tools[i], float(sims[i])) for i in ranked_idx]


def _keyword_search(
    index: ToolSearchIndex, query: str, top_k: int
) -> List[Tuple[IndexedTool, float]]:
    """Fallback when embeddings are unavailable. Crude but deterministic —
    scores each tool by the count of distinct query tokens that appear in
    the tool's searchable_text (case-insensitive).
    """
    tokens = {t.lower() for t in query.split() if len(t) >= 3}
    scored: List[Tuple[IndexedTool, float]] = []
    for tool in index.tools:
        haystack = tool.searchable_text.lower()
        hits = sum(1 for tok in tokens if tok in haystack)
        if hits > 0:
            scored.append((tool, float(hits)))
    scored.sort(key=lambda x: -x[1])
    return scored[:top_k]


async def _embed_query(query: str) -> Optional[np.ndarray]:
    """Embed a single query string at EMBEDDING_DIMENSIONS dims."""
    vecs, error = await _embed_batch([query], input_type="search_query")
    if vecs is None or vecs.shape[0] != 1:
        if error:
            logger.warning(f"tool_search: query embedding failed: {error}")
        return None
    v = vecs[0].astype(np.float32)
    norm = float(np.linalg.norm(v))
    if norm == 0:
        return v
    return v / norm


async def _embed_batch_cohere(texts: List[str], input_type: str) -> Tuple[Optional[np.ndarray], Optional[str]]:
    """Cohere Embed v3 path. 1024d, supports search_query / search_document
    semantics. Reads COHERE_API_KEY from env.
    """
    api_key = os.getenv("COHERE_API_KEY")
    if not api_key:
        return None, "COHERE_API_KEY env var not set"
    import aiohttp

    url = "https://api.cohere.com/v2/embed"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": os.getenv("COHERE_EMBED_MODEL", "embed-english-v3.0"),
        "texts": [t[:8000] if t else " " for t in texts],
        "input_type": input_type,
        "embedding_types": ["float"],
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=60) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    err = f"Cohere embedding HTTP {resp.status}: {body[:400]}"
                    logger.error(f"tool_search: {err}")
                    return None, err
                data = await resp.json()
                items = data.get("embeddings", {}).get("float", [])
                if len(items) != len(texts):
                    err = f"expected {len(texts)} embeddings, got {len(items)}"
                    logger.error(f"tool_search: {err}")
                    return None, err
                return np.array(items, dtype=np.float32), None
    except asyncio.TimeoutError:
        return None, "Cohere embedding call timed out after 60s"
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        logger.error(f"tool_search: Cohere embedding call raised: {err}")
        return None, err


def _tier_ok(tool_tier: ToolTier, user_tier: ToolTier) -> bool:
    """Return True if `user_tier` has access to a tool requiring `tool_tier`.

    Ordering (from least to most privileged):
        FREE = PRO (legacy alias for FREE) < CORE < TEAM < ENTERPRISE

    Unknown / unrecognized tiers default to FREE on both sides — this is
    intentionally permissive, since server-level tool execution enforces the
    real gate on call. The tool-search visibility layer should not be stricter
    than execution itself.

    Note: the existing /mcp/prompts + /mcp/tools endpoints in router.py use
    an ordering list that is missing CORE entirely, which silently filters
    out all CORE-tiered tools (currently 2, including predict_clinical_outcomes)
    for every user. That's a separate longstanding bug. Fixing it there is
    a drive-by that belongs in its own PR.
    """
    rank = {
        ToolTier.FREE: 0,
        ToolTier.PRO: 0,  # Legacy — mapped to FREE per tools.py enum comment
        ToolTier.CORE: 1,
        ToolTier.TEAM: 2,
        ToolTier.ENTERPRISE: 3,
    }
    user_rank = rank.get(user_tier, 0)
    tool_rank = rank.get(tool_tier, 0)
    return user_rank >= tool_rank


async def search(
    query: str,
    user_tier: ToolTier,
    top_k: int = 5,
    template: Optional[str] = None,
    include_core_whitelist: bool = True,
) -> Dict[str, Any]:
    """Retrieve relevant tools for a query.

    Parameters
    ----------
    query : str
        The user's message or search text.
    user_tier : ToolTier
        The caller's entitlement tier. Tools requiring a higher tier are
        filtered out of results.
    top_k : int
        How many retrieved tools to return (before merging with whitelist /
        template manifest).
    template : Optional[str]
        If set to a known prompt-template name, skip retrieval and load the
        template's manifest instead. Caller is responsible for matching
        prompt invocation to this.
    include_core_whitelist : bool
        If True, always include the CORE_WHITELIST tools in the response
        (tier-filtered).

    Returns
    -------
    dict : {
        "query": str,
        "template": Optional[str],
        "tools": [ {name, description, inputSchema, similarity} ],
        "_meta": { "mode", "index_size", "retrieved", "whitelist", "manifest" }
    }
    """
    index = get_index()
    response: Dict[str, Any] = {"query": query, "template": template, "tools": []}
    meta: Dict[str, Any] = {
        "mode": "uninitialized",
        "index_size": index.size,
        "retrieved": 0,
        "whitelist": 0,
        "manifest": 0,
    }

    if index.size == 0:
        meta["mode"] = "empty"
        response["_meta"] = meta
        return response

    # By name so we can de-dup when combining retrieval + whitelist + manifest.
    selected: Dict[str, Dict[str, Any]] = {}

    def _add(tool: IndexedTool, similarity: Optional[float], source: str) -> None:
        if tool.name in selected:
            # Keep the highest similarity if we see the tool twice.
            if similarity is not None:
                prev = selected[tool.name].get("similarity")
                if prev is None or similarity > prev:
                    selected[tool.name]["similarity"] = similarity
            selected[tool.name]["_sources"].append(source)
            return
        if not _tier_ok(tool.tier, user_tier):
            return
        entry = tool.to_public_dict()
        if similarity is not None:
            entry["similarity"] = round(similarity, 4)
        entry["_sources"] = [source]
        selected[tool.name] = entry

    # 1) Template manifest — skip retrieval if a known template is named.
    if template:
        manifest_names = TEMPLATE_MANIFESTS.get(template)
        if manifest_names:
            by_name = {t.name: t for t in index.tools}
            for name in manifest_names:
                tool = by_name.get(name)
                if tool is not None:
                    _add(tool, None, "manifest")
            meta["manifest"] = len(
                [e for e in selected.values() if "manifest" in e["_sources"]]
            )
        else:
            logger.warning(
                f"tool_search: unknown template '{template}', falling through to retrieval"
            )

    # 2) Retrieval — embedding-primary, keyword fallback.
    retrieval_mode = "none"
    if not template or not TEMPLATE_MANIFESTS.get(template):
        if index.embeddings is not None:
            retrieval_mode = "embedding"
            query_vec = await _embed_query(query)
            if query_vec is not None:
                for tool, sim in _embedding_search(index, query_vec, top_k):
                    _add(tool, sim, "retrieval")
            else:
                retrieval_mode = "embedding_failed→keyword"
                for tool, score in _keyword_search(index, query, top_k):
                    _add(tool, score, "retrieval")
        else:
            retrieval_mode = "keyword"
            for tool, score in _keyword_search(index, query, top_k):
                _add(tool, score, "retrieval")
        meta["retrieved"] = len(
            [e for e in selected.values() if "retrieval" in e["_sources"]]
        )

    # 3) Core whitelist — always on unless explicitly disabled.
    if include_core_whitelist:
        by_name = {t.name: t for t in index.tools}
        for name in CORE_WHITELIST:
            tool = by_name.get(name)
            if tool is not None:
                _add(tool, None, "whitelist")
        meta["whitelist"] = len(
            [e for e in selected.values() if "whitelist" in e["_sources"]]
        )

    # Strip internal _sources marker and return.
    tools_out: List[Dict[str, Any]] = []
    for entry in selected.values():
        entry.pop("_sources", None)
        tools_out.append(entry)

    # Sort: similarity desc (manifest/whitelist tools without similarity sink to the bottom).
    tools_out.sort(key=lambda t: (-(t.get("similarity") or -1.0), t["name"]))

    meta["mode"] = retrieval_mode if not template else f"template:{template}"
    response["tools"] = tools_out
    response["_meta"] = meta
    return response


def status() -> Dict[str, Any]:
    """Diagnostic snapshot of the current index."""
    index = get_index()
    cfg = _azure_config()
    return {
        "ready": index.is_ready,
        "size": index.size,
        "embedding_dimensions": EMBEDDING_DIMENSIONS,
        "embedding_fallback": index.embedding_fallback,
        "build_duration_seconds": (
            round(index.build_duration_seconds, 3)
            if index.build_duration_seconds is not None
            else None
        ),
        "built_at": index.built_at,
        "build_attempts": index.build_attempts,
        "last_error": index.last_error,
        "azure_config_present": cfg is not None,
        "azure_deployment": cfg.get("deployment") if cfg else None,
        "core_whitelist_count": len(CORE_WHITELIST),
        "template_manifests": list(TEMPLATE_MANIFESTS.keys()),
        "total_mcp_tools": len(MCP_TOOLS),
    }
