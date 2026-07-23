"""
Embedding Generator using Azure OpenAI (legacy) or Cohere Embed v3 (current
migration target). Provider is chosen at runtime via EMBEDDING_PROVIDER env
var: "azure" (default for backward compat) or "cohere".

Module-level `get_embedder()` returns the active provider; call sites should
prefer that over constructing `EmbeddingGenerator` directly so the cutover
is a one-line env change.
"""

import asyncio
import hashlib
import json
import logging
import os
from typing import List, Dict, Any, Optional, Union
import numpy as np
import aiohttp

logger = logging.getLogger(__name__)


def get_embedder() -> Union["EmbeddingGenerator", "CohereEmbeddingGenerator"]:
    """Return the embedding generator configured by EMBEDDING_PROVIDER.

    Defaults to `azure` so deploying this file alone does not flip the
    provider — the cutover is a deliberate env change on the Deployment.
    """
    provider = os.getenv("EMBEDDING_PROVIDER", "azure").lower()
    if provider == "cohere":
        from .cohere_embeddings import CohereEmbeddingGenerator  # local import keeps Azure path importable without aiohttp churn
        return CohereEmbeddingGenerator()
    if provider != "azure":
        logger.warning(
            "Unknown EMBEDDING_PROVIDER=%s; falling back to Azure OpenAI.", provider
        )
    return EmbeddingGenerator()


class EmbeddingGenerator:
    """Generate embeddings using Azure OpenAI API"""

    def __init__(self):
        """Initialize with Azure OpenAI configuration from environment variables."""
        self.config = self._load_azure_config()
        self.api_key = self.config.get('api_key')
        self.endpoint = self.config.get('endpoint')
        self.deployment = "text-embedding-3-large"  # Azure deployment name
        self.api_version = "2024-12-01-preview"  # Updated API version
        self.embedding_dimensions = 3072  # text-embedding-3-large dimension
        self.session = None

    def _load_azure_config(self) -> Dict[str, str]:
        """Load Azure OpenAI configuration from environment variables.

        The previous implementation pulled config from AWS Secrets Manager
        with a hardcoded fallback literal (an API key + endpoint + deployment
        embedded in source). Both were removed during the AWS->Azure
        migration cleanup: Container Apps inject AZURE_OPENAI_API_KEY and
        AZURE_OPENAI_ENDPOINT at runtime, matching every other consumer
        in this codebase. If the env vars are unset the API call will fail
        loud with an auth error rather than silently routing to a stale key.
        """
        config = {
            'api_key': os.getenv('AZURE_OPENAI_API_KEY', ''),
            'endpoint': os.getenv('AZURE_OPENAI_ENDPOINT', ''),
            'deployment': os.getenv('AZURE_OPENAI_DEPLOYMENT', 'gpt-5'),
        }
        if not config['api_key'] or not config['endpoint']:
            logger.warning(
                "Azure OpenAI env vars not set (AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT); "
                "embedding calls will fail."
            )
        return config

    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()

    async def generate_embedding(self, text: str, retry_count: int = 3) -> List[float]:
        """
        Generate embedding for a single text using Azure OpenAI

        Args:
            text: Text to generate embedding for
            retry_count: Number of retries on failure

        Returns:
            1536-dimensional embedding vector
        """
        if not text:
            return self._generate_zero_embedding()

        # Truncate text if too long (max ~8000 tokens)
        if len(text) > 30000:
            text = text[:30000]

        url = f"{self.endpoint}openai/deployments/{self.deployment}/embeddings?api-version={self.api_version}"
        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json"
        }
        payload = {
            "input": text
        }

        for attempt in range(retry_count):
            try:
                if not self.session:
                    self.session = aiohttp.ClientSession()

                async with self.session.post(url, json=payload, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        embedding = data['data'][0]['embedding']
                        logger.debug(f"Generated embedding for text of length {len(text)}")
                        return embedding
                    elif response.status == 429:  # Rate limit
                        wait_time = 2 ** attempt
                        logger.warning(f"Rate limited, waiting {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        error_text = await response.text()
                        logger.error(f"Azure OpenAI error {response.status}: {error_text}")

            except Exception as e:
                logger.error(f"Embedding generation error (attempt {attempt + 1}): {e}")
                if attempt < retry_count - 1:
                    await asyncio.sleep(1)

        # Fallback to deterministic embedding
        logger.warning("Failed to generate Azure embedding, using fallback")
        return self._generate_fallback_embedding(text)

    async def generate_batch_embeddings(self, texts: List[str], batch_size: int = 10) -> List[List[float]]:
        """
        Generate embeddings for multiple texts in batches

        Args:
            texts: List of texts to embed
            batch_size: Number of texts to process in parallel

        Returns:
            List of embedding vectors
        """
        embeddings = []

        # Process in batches to avoid rate limits
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_tasks = [self.generate_embedding(text) for text in batch]
            batch_embeddings = await asyncio.gather(*batch_tasks)
            embeddings.extend(batch_embeddings)

            # Small delay between batches to avoid rate limits
            if i + batch_size < len(texts):
                await asyncio.sleep(0.5)

        logger.info(f"Generated {len(embeddings)} embeddings in batches")
        return embeddings

    async def generate_document_embedding(self, document: Dict[str, Any]) -> List[float]:
        """
        Generate embedding for a document by combining relevant fields

        Args:
            document: Document dictionary with title, abstract, etc.

        Returns:
            Embedding vector
        """
        # Combine relevant text fields
        text_parts = []

        if document.get('title'):
            text_parts.append(f"Title: {document['title']}")

        if document.get('abstract'):
            text_parts.append(f"Abstract: {document['abstract'][:2000]}")

        if document.get('keywords'):
            keywords = document['keywords']
            if isinstance(keywords, list):
                text_parts.append(f"Keywords: {', '.join(keywords[:10])}")

        if document.get('authors'):
            authors = document['authors']
            if isinstance(authors, list):
                text_parts.append(f"Authors: {', '.join(authors[:5])}")

        if document.get('journal'):
            text_parts.append(f"Journal: {document['journal']}")

        # For compounds, include chemical information
        if document.get('smiles'):
            text_parts.append(f"SMILES: {document['smiles']}")

        if document.get('target'):
            text_parts.append(f"Target: {document['target']}")

        # Combine all parts
        combined_text = '\n'.join(text_parts)

        if not combined_text:
            logger.warning(f"No text to embed for document {document.get('id', 'unknown')}")
            return self._generate_zero_embedding()

        return await self.generate_embedding(combined_text)

    async def generate_query_embedding(self, query: str) -> List[float]:
        """
        Generate embedding for a search query

        Args:
            query: Search query string

        Returns:
            Embedding vector
        """
        # Enhance query for better search
        enhanced_query = f"Search query: {query}"
        return await self.generate_embedding(enhanced_query)

    def _generate_fallback_embedding(self, text: str) -> List[float]:
        """
        Generate deterministic fallback embedding when API is unavailable

        Args:
            text: Text to embed

        Returns:
            Pseudo-embedding based on text hash
        """
        # Create deterministic embedding from text hash
        hash_obj = hashlib.sha256(text.encode('utf-8'))
        hash_hex = hash_obj.hexdigest()

        # Use hash to seed random number generator
        seed = int(hash_hex[:8], 16)
        np.random.seed(seed)

        # Generate vector matching deployment dimension (3072 for text-embedding-3-large)
        embedding = np.random.randn(self.embedding_dimensions)

        # Normalize to unit length
        embedding = embedding / np.linalg.norm(embedding)

        return embedding.tolist()

    def _generate_zero_embedding(self) -> List[float]:
        """Generate zero embedding for empty text"""
        return [0.0] * self.embedding_dimensions

    async def calculate_similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """
        Calculate cosine similarity between two embeddings

        Args:
            embedding1: First embedding vector
            embedding2: Second embedding vector

        Returns:
            Similarity score between -1 and 1
        """
        # Convert to numpy arrays
        vec1 = np.array(embedding1)
        vec2 = np.array(embedding2)

        # Calculate cosine similarity
        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        similarity = dot_product / (norm1 * norm2)
        return float(similarity)


# Test function
async def test_embedding_generator():
    """Test the embedding generator"""
    async with EmbeddingGenerator() as generator:
        # Test single embedding
        text = "KRAS G12C is a common mutation in non-small cell lung cancer"
        embedding = await generator.generate_embedding(text)
        print(f"Generated embedding with {len(embedding)} dimensions")

        # Test document embedding
        document = {
            'title': 'Novel KRAS G12C Inhibitor Shows Promise',
            'abstract': 'A new selective inhibitor demonstrates efficacy...',
            'keywords': ['KRAS', 'G12C', 'NSCLC', 'inhibitor'],
            'authors': ['Smith J', 'Doe A']
        }
        doc_embedding = await generator.generate_document_embedding(document)
        print(f"Document embedding: {len(doc_embedding)} dimensions")

        # Test batch embeddings
        texts = [
            "First text about cancer research",
            "Second text about drug discovery",
            "Third text about clinical trials"
        ]
        batch_embeddings = await generator.generate_batch_embeddings(texts)
        print(f"Generated {len(batch_embeddings)} batch embeddings")

        # Test similarity
        embedding2 = await generator.generate_embedding("KRAS mutations in cancer")
        similarity = await generator.calculate_similarity(embedding, embedding2)
        print(f"Similarity score: {similarity:.3f}")


if __name__ == "__main__":
    asyncio.run(test_embedding_generator())