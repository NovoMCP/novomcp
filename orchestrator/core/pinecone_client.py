"""
Pinecone Client for NovoMCP
Handles literature search and learning patterns for campaign decisions
"""

import os
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from pinecone import Pinecone

logger = logging.getLogger(__name__)

# Maps the Pinecone index `source` metadata onto the research-explorer viewer's
# badge vocabulary (pubmed / patent / biorxiv / chembl / clinical_trial). The
# index stores real provenance (pmc_open_access, pubmed, uspto, clinicaltrials.gov,
# biorxiv); without this mapping the executor dropped `source` entirely and the
# viewer fell back to "UNKNOWN". Unmapped values pass through (the viewer renders
# them uppercased, e.g. "PMC"), so new sources degrade gracefully rather than
# regressing to "UNKNOWN".
_SOURCE_BADGE = {
    "pmc_open_access": "pmc",
    "pubmed": "pubmed",
    "uspto": "patent",
    "clinicaltrials.gov": "clinical_trial",
    "clinical_trials.gov": "clinical_trial",
    "biorxiv": "biorxiv",
    "medrxiv": "biorxiv",
    "preprint": "biorxiv",
}


def _badge_source(raw: Any) -> str:
    """Normalize an index `source` value to a research-explorer badge token."""
    s = str(raw or "").lower()
    return _SOURCE_BADGE.get(s, s or "literature")


def _clean_year(raw: Any) -> str:
    """Index years arrive as floats-as-strings ('2023.0'); render them clean."""
    s = str(raw or "").strip()
    return s[:-2] if s.endswith(".0") else s


class PineconeClient:
    """Client for Pinecone vector operations"""

    def __init__(self):
        # Initialize Pinecone
        api_key = os.getenv("PINECONE_API_KEY")
        if not api_key:
            # Debug-level in OSS local mode: expected when literature search
            # isn't wired. Callers already handle ValueError gracefully.
            logger.debug("PINECONE_API_KEY not set — literature/patent search unavailable")
            raise ValueError("PINECONE_API_KEY not configured")

        self.pc = Pinecone(api_key=api_key)

        # Index names depend on the embedding provider — different vector
        # spaces require different indices. PINECONE_LITERATURE_INDEX and
        # PINECONE_PATTERNS_INDEX let the cutover happen via env var.
        provider = os.getenv("EMBEDDING_PROVIDER", "azure").lower()
        default_lit = "novomcp-literature" if provider == "azure" else "novomcp-literature-v2"
        default_pat = "novomcp-patterns" if provider == "azure" else "novomcp-patterns-v2"
        self.literature_index = self.pc.Index(
            os.getenv("PINECONE_LITERATURE_INDEX", default_lit)
        )
        self.patterns_index = self.pc.Index(
            os.getenv("PINECONE_PATTERNS_INDEX", default_pat)
        )

        # Defer embedder construction until first call. CohereEmbeddingGenerator
        # is an async-context-manager — we lazily acquire it on first use.
        self._embedder = None
        self._embedder_provider = provider
        logger.info(
            "Pinecone client initialized (provider=%s, literature=%s, patterns=%s)",
            provider, self.literature_index, self.patterns_index,
        )

    async def _get_embedder(self):
        if self._embedder is None:
            from ai.embedding_generator import get_embedder
            self._embedder = get_embedder()
            if hasattr(self._embedder, "__aenter__"):
                await self._embedder.__aenter__()
        return self._embedder

    async def generate_embeddings(self, text: str, input_type: str = "search_query") -> List[float]:
        """Generate embeddings via the configured provider.

        Cohere benefits from input_type=search_query for read paths and
        search_document for upserts. The Azure wrapper ignores input_type.
        """
        try:
            g = await self._get_embedder()
            if input_type == "search_query" and hasattr(g, "generate_query_embedding"):
                return await g.generate_query_embedding(text)
            return await g.generate_embedding(text)
        except Exception as e:
            logger.error(f"Failed to generate embeddings: {e}")
            raise

    async def search_literature(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        top_k: int = 10,
        namespace: str = "uploads",
        query_embedding: Optional[List[float]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search scientific literature relevant to a campaign goal.

        Namespaces:
        - 'uploads': Scientific literature and papers (14,398 records)
        - 'patents': USPTO pharmaceutical patents (1,187 records)
        """
        try:
            # Use pre-computed embedding if provided, otherwise generate
            if query_embedding is None:
                query_embedding = await self.generate_embeddings(query)

            # Search literature index with namespace
            results = self.literature_index.query(
                vector=query_embedding,
                top_k=top_k,
                include_metadata=True,
                filter=filters or {},
                namespace=namespace
            )

            # Format results
            literature = []
            for match in results['matches']:
                md = match.get('metadata', {})
                literature.append({
                    "id": match['id'],
                    "score": match['score'],
                    "title": md.get('title', ''),
                    "abstract": md.get('abstract', ''),
                    "authors": md.get('authors', []),
                    "year": _clean_year(md.get('year', '')),
                    "doi": md.get('doi', ''),
                    # Stable per-paper ids for chunk dedup at the executor layer.
                    "pmcid": md.get('pmcid', ''),
                    "pmid": md.get('pmid', ''),
                    # Surface real provenance (was dropped → viewer showed "UNKNOWN").
                    "source": _badge_source(md.get('source')),
                    # Journal nests under metadata — where the research-explorer
                    # viewer reads it (result.metadata.journal). Present in the
                    # pubmed namespace; empty for the chunked PMC `uploads` corpus.
                    "metadata": {"journal": md.get('journal', '')},
                    "relevance": match['score']
                })

            logger.info(f"Found {len(literature)} relevant papers for query: {query[:100]}")
            return literature

        except Exception as e:
            logger.error(f"Literature search failed: {e}")
            return []

    async def search_similar_campaigns(
        self,
        goal: str,
        campaign_state: Dict[str, Any],
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Find similar past campaigns and their outcomes
        """
        try:
            # Create search query from goal and state
            search_text = f"Goal: {goal}\nState: {json.dumps(campaign_state)}"
            query_embedding = await self.generate_embeddings(search_text)

            # Search patterns index for similar campaigns
            results = self.patterns_index.query(
                vector=query_embedding,
                top_k=top_k,
                include_metadata=True,
                filter={"type": "campaign_outcome"}
            )

            # Format similar campaigns
            similar = []
            for match in results['matches']:
                metadata = match.get('metadata', {})
                similar.append({
                    "id": match['id'],
                    "similarity": match['score'],
                    "campaign_id": metadata.get('campaign_id', ''),
                    "goal": metadata.get('goal', ''),
                    "outcome": metadata.get('outcome', ''),
                    "success": metadata.get('success', False),
                    "key_decisions": metadata.get('key_decisions', []),
                    "learnings": metadata.get('learnings', '')
                })

            logger.info(f"Found {len(similar)} similar campaigns")
            return similar

        except Exception as e:
            logger.error(f"Similar campaign search failed: {e}")
            return []

    async def store_learning_pattern(
        self,
        campaign_id: str,
        decision: Dict[str, Any],
        outcome: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Store a learning pattern from a campaign decision and outcome
        """
        try:
            # Create learning document
            learning_text = f"""
            Campaign: {campaign_id}
            Decision: {decision.get('action')} - {decision.get('reasoning')}
            Outcome: {outcome.get('status')} - {outcome.get('message', '')}
            Context: {json.dumps(context or {})}
            """

            # Generate embedding
            embedding = await self.generate_embeddings(learning_text)

            # Prepare metadata
            metadata = {
                "type": "campaign_outcome",
                "campaign_id": campaign_id,
                "decision_action": decision.get('action'),
                "decision_reasoning": decision.get('reasoning'),
                "outcome_status": outcome.get('status'),
                "success": outcome.get('status') == 'success',
                "confidence": decision.get('confidence', 0.5),
                "timestamp": datetime.utcnow().isoformat(),
                "goal": context.get('goal', '') if context else '',
                "learnings": outcome.get('learnings', '')
            }

            # Store in patterns index
            record_id = f"learning_{campaign_id}_{datetime.utcnow().timestamp()}"
            self.patterns_index.upsert(
                vectors=[(record_id, embedding, metadata)]
            )

            logger.info(f"Stored learning pattern: {record_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to store learning pattern: {e}")
            return False

    async def get_decision_context(
        self,
        goal: str,
        constraints: Dict[str, Any],
        history: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Get comprehensive context for decision making
        """
        try:
            # Search relevant literature
            literature = await self.search_literature(
                query=f"{goal} drug discovery {constraints.get('target', '')}",
                filters={"year": {"$gte": 2020}},  # Recent papers only
                top_k=5
            )

            # Find similar campaigns
            similar = await self.search_similar_campaigns(
                goal=goal,
                campaign_state={"constraints": constraints},
                top_k=3
            )

            # Extract key learnings
            successful_patterns = []
            failed_patterns = []

            for campaign in similar:
                if campaign['success']:
                    successful_patterns.append({
                        "goal": campaign['goal'],
                        "decisions": campaign['key_decisions'],
                        "outcome": campaign['outcome']
                    })
                else:
                    failed_patterns.append({
                        "goal": campaign['goal'],
                        "decisions": campaign['key_decisions'],
                        "outcome": campaign['outcome']
                    })

            return {
                "relevant_literature": literature,
                "similar_campaigns": similar,
                "successful_patterns": successful_patterns,
                "failed_patterns": failed_patterns,
                "recommendations": self._generate_recommendations(
                    successful_patterns, failed_patterns
                )
            }

        except Exception as e:
            logger.error(f"Failed to get decision context: {e}")
            return {
                "relevant_literature": [],
                "similar_campaigns": [],
                "successful_patterns": [],
                "failed_patterns": [],
                "recommendations": []
            }

    def _generate_recommendations(
        self,
        successful: List[Dict],
        failed: List[Dict]
    ) -> List[str]:
        """Generate recommendations based on patterns"""
        recommendations = []

        # Analyze successful patterns
        if successful:
            common_decisions = {}
            for pattern in successful:
                for decision in pattern.get('decisions', []):
                    common_decisions[decision] = common_decisions.get(decision, 0) + 1

            # Recommend most common successful decisions
            for decision, count in sorted(common_decisions.items(), key=lambda x: x[1], reverse=True)[:3]:
                if count > 1:
                    recommendations.append(f"Consider {decision} (successful in {count} similar campaigns)")

        # Warn about failed patterns
        if failed:
            failed_decisions = set()
            for pattern in failed:
                for decision in pattern.get('decisions', []):
                    failed_decisions.add(decision)

            for decision in list(failed_decisions)[:2]:
                recommendations.append(f"Avoid {decision} (failed in similar campaigns)")

        return recommendations


# Singleton instance
_pinecone_client = None

def get_pinecone_client() -> PineconeClient:
    """Get or create Pinecone client singleton"""
    global _pinecone_client
    if _pinecone_client is None:
        _pinecone_client = PineconeClient()
    return _pinecone_client


# Export convenience functions
async def search_literature(query: str, constraints: Dict = None) -> List[Dict]:
    """Search literature helper"""
    client = get_pinecone_client()
    return await client.search_literature(query, constraints)


async def search_similar_campaigns(goal: str, state: Dict) -> List[Dict]:
    """Search similar campaigns helper"""
    client = get_pinecone_client()
    return await client.search_similar_campaigns(goal, state)


async def store_learning(campaign_id: str, decision: Dict, outcome: Dict, context: Dict = None) -> bool:
    """Store learning helper"""
    client = get_pinecone_client()
    return await client.store_learning_pattern(campaign_id, decision, outcome, context)