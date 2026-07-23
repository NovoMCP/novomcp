"""
Cohere Embed v3 wrapper — drop-in replacement for EmbeddingGenerator
when EMBEDDING_PROVIDER=cohere.

Differences vs Azure OpenAI `text-embedding-3-large`:
  * 1024 dimensions (fixed) vs 3072 (or 1536 truncated)
  * Supports `input_type` distinction: search_document for upserts,
    search_query for searches. We use this — it materially improves
    retrieval quality vs treating them identically.
  * Bills $0.10 / 1M input tokens (~5x cheaper than Azure).
  * Direct REST to api.cohere.com — no AWS/Azure dependency at runtime.

Mirrors the interface of `embedding_generator.EmbeddingGenerator` so the
call sites can swap via a factory without code changes.
"""

import asyncio
import hashlib
import logging
import os
from typing import Any, Dict, List

import aiohttp
import numpy as np

logger = logging.getLogger(__name__)

COHERE_API_URL = "https://api.cohere.com/v2/embed"
COHERE_MODEL = os.getenv("COHERE_EMBED_MODEL", "embed-english-v3.0")
COHERE_DIMENSIONS = 1024  # Embed v3 is 1024d, fixed


class CohereEmbeddingGenerator:
    """Generate embeddings using Cohere Embed v3 over the direct REST API."""

    def __init__(self) -> None:
        self.api_key = os.getenv("COHERE_API_KEY", "")
        self.model = COHERE_MODEL
        self.embedding_dimensions = COHERE_DIMENSIONS
        self.session: aiohttp.ClientSession | None = None
        if not self.api_key:
            logger.warning(
                "COHERE_API_KEY env var not set; embedding calls will fail."
            )

    async def __aenter__(self) -> "CohereEmbeddingGenerator":
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.session:
            await self.session.close()
            self.session = None

    async def _embed(
        self, texts: List[str], input_type: str, retry_count: int = 3
    ) -> List[List[float]]:
        """Embed a batch of texts with explicit input_type semantics."""
        if not texts:
            return []
        texts = [t[:8000] if t else " " for t in texts]
        payload = {
            "model": self.model,
            "texts": texts,
            "input_type": input_type,
            "embedding_types": ["float"],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        for attempt in range(retry_count):
            try:
                if not self.session:
                    self.session = aiohttp.ClientSession()
                async with self.session.post(
                    COHERE_API_URL, json=payload, headers=headers, timeout=60
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data["embeddings"]["float"]
                    if response.status == 429:
                        wait = 2**attempt
                        logger.warning("Cohere 429 rate-limited; sleeping %ss", wait)
                        await asyncio.sleep(wait)
                        continue
                    err = await response.text()
                    logger.error("Cohere error %s: %s", response.status, err[:300])
            except Exception as e:  # network, timeout
                logger.error("Cohere request error attempt %d: %s", attempt + 1, e)
                if attempt < retry_count - 1:
                    await asyncio.sleep(1)

        # Fallback — match the Azure wrapper's deterministic-from-hash behaviour
        # so callers never see None and indexing can still proceed.
        logger.warning("Cohere embed failed; using deterministic fallback for %d texts", len(texts))
        return [self._fallback(t) for t in texts]

    async def generate_embedding(self, text: str, retry_count: int = 3) -> List[float]:
        """Single text → 1024d vector. Treated as a document by default."""
        if not text:
            return self._zero()
        out = await self._embed([text], input_type="search_document", retry_count=retry_count)
        return out[0]

    async def generate_query_embedding(self, query: str) -> List[float]:
        """Query embedding (uses input_type=search_query for retrieval quality)."""
        if not query:
            return self._zero()
        out = await self._embed([query], input_type="search_query")
        return out[0]

    async def generate_batch_embeddings(
        self, texts: List[str], batch_size: int = 96
    ) -> List[List[float]]:
        """Cohere accepts up to 96 texts/call — single round-trip per batch."""
        embeddings: List[List[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            embeddings.extend(await self._embed(batch, input_type="search_document"))
            if i + batch_size < len(texts):
                await asyncio.sleep(0.2)
        logger.info("Cohere generated %d embeddings", len(embeddings))
        return embeddings

    async def generate_document_embedding(self, document: Dict[str, Any]) -> List[float]:
        """Build text from a document dict (same fields as the Azure wrapper)."""
        parts = []
        if document.get("title"):
            parts.append(f"Title: {document['title']}")
        if document.get("abstract"):
            parts.append(f"Abstract: {document['abstract'][:2000]}")
        keywords = document.get("keywords")
        if isinstance(keywords, list) and keywords:
            parts.append("Keywords: " + ", ".join(map(str, keywords[:10])))
        authors = document.get("authors")
        if isinstance(authors, list) and authors:
            parts.append("Authors: " + ", ".join(map(str, authors[:5])))
        if document.get("journal"):
            parts.append(f"Journal: {document['journal']}")
        if document.get("smiles"):
            parts.append(f"SMILES: {document['smiles']}")
        if document.get("target"):
            parts.append(f"Target: {document['target']}")
        text = "\n".join(parts)
        if not text:
            return self._zero()
        return await self.generate_embedding(text)

    async def calculate_similarity(
        self, embedding1: List[float], embedding2: List[float]
    ) -> float:
        v1, v2 = np.array(embedding1), np.array(embedding2)
        n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
        if n1 == 0 or n2 == 0:
            return 0.0
        return float(np.dot(v1, v2) / (n1 * n2))

    def _fallback(self, text: str) -> List[float]:
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        np.random.seed(int(h[:8], 16))
        v = np.random.randn(self.embedding_dimensions)
        v = v / np.linalg.norm(v)
        return v.tolist()

    def _zero(self) -> List[float]:
        return [0.0] * self.embedding_dimensions
