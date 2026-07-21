"""
Complete Data Ingestion Pipeline
Orchestrates the flow from APIs → Embeddings → Pinecone → Campaign Decisions
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import json

from .literature_api_client import LiteratureAPIClient
from .embedding_generator import EmbeddingGenerator, get_embedder

# Remove invalid cross-service import
# PineconeClient will be handled by Campaign Manager service
# NovoMCP focuses on orchestration, not direct database operations

logger = logging.getLogger(__name__)


class DataIngestionPipeline:
    """
    Complete pipeline for ingesting literature and storing in Pinecone
    Enables real-time autonomous discovery with actual data
    """

    def __init__(self):
        """Initialize all components of the pipeline"""
        self.api_client = None
        self.embedding_generator = None
        # Initialize Pinecone client for literature storage
        self.pinecone_client = None
        self.stats = {
            'total_fetched': 0,
            'total_embedded': 0,
            'total_stored': 0,
            'errors': []
        }

    async def __aenter__(self):
        """Async context manager entry"""
        self.api_client = await LiteratureAPIClient().__aenter__()
        self.embedding_generator = await get_embedder().__aenter__()

        # Initialize Pinecone client
        try:
            from core.pinecone_client import get_pinecone_client
            self.pinecone_client = get_pinecone_client()
            logger.info("Pinecone client initialized for literature ingestion")
        except Exception as e:
            logger.error(f"Failed to initialize Pinecone client: {e}")
            raise

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.api_client:
            await self.api_client.__aexit__(exc_type, exc_val, exc_tb)
        if self.embedding_generator:
            await self.embedding_generator.__aexit__(exc_type, exc_val, exc_tb)

    async def ingest_for_campaign(self, campaign_goals: Dict[str, Any]) -> Dict[str, Any]:
        """
        Complete ingestion pipeline for a campaign
        Fetches real data, generates embeddings, and stores in Pinecone

        Args:
            campaign_goals: {
                'campaign_id': 'uuid',
                'target': 'KRAS G12C',
                'indication': 'NSCLC',
                'keywords': ['resistance', 'combination'],
                'modality': 'small molecule'
            }

        Returns:
            Statistics and insights from ingestion
        """
        logger.info(f"Starting data ingestion for campaign: {campaign_goals.get('campaign_id', 'unknown')}")
        start_time = datetime.utcnow()

        # Reset stats
        self.stats = {
            'total_fetched': 0,
            'total_embedded': 0,
            'total_stored': 0,
            'errors': [],
            'sources': {}
        }

        try:
            # Step 1: Fetch from all sources
            logger.info("Step 1: Fetching from all literature sources...")
            all_data = await self.api_client.fetch_all_sources(campaign_goals)

            # Step 2: Process each source
            for source_name, documents in all_data.items():
                if documents and isinstance(documents, list):
                    logger.info(f"Processing {len(documents)} items from {source_name}")
                    stored_count = await self._process_source_documents(
                        documents, source_name, campaign_goals
                    )
                    self.stats['sources'][source_name] = {
                        'fetched': len(documents),
                        'stored': stored_count
                    }

            # Step 3: Extract actionable insights
            logger.info("Step 3: Extracting actionable insights...")
            insights = await self._extract_campaign_insights(campaign_goals)

            # Step 4: Store ingestion metadata
            await self._store_ingestion_metadata(campaign_goals, self.stats)

            # Calculate duration
            duration = (datetime.utcnow() - start_time).total_seconds()

            # Prepare result
            result = {
                'success': True,
                'campaign_id': campaign_goals.get('campaign_id'),
                'stats': self.stats,
                'insights': insights,
                'duration_seconds': duration,
                'timestamp': datetime.utcnow().isoformat()
            }

            logger.info(f"Ingestion complete: {self.stats['total_stored']} documents stored in {duration:.1f}s")
            return result

        except Exception as e:
            logger.error(f"Ingestion pipeline error: {e}")
            self.stats['errors'].append(str(e))
            return {
                'success': False,
                'error': str(e),
                'stats': self.stats
            }

    async def _process_source_documents(
        self,
        documents: List[Dict],
        source_name: str,
        campaign_goals: Dict
    ) -> int:
        """
        Process documents from a single source

        Args:
            documents: List of document dictionaries
            source_name: Name of the source
            campaign_goals: Campaign context

        Returns:
            Number of documents successfully stored
        """
        stored_count = 0

        # Process in batches for efficiency
        batch_size = 10
        for i in range(0, len(documents), batch_size):
            batch = documents[i:i + batch_size]

            # Generate embeddings for batch
            embeddings = []
            for doc in batch:
                try:
                    embedding = await self.embedding_generator.generate_document_embedding(doc)
                    embeddings.append(embedding)
                    self.stats['total_embedded'] += 1
                except Exception as e:
                    logger.error(f"Failed to embed document {doc.get('id', 'unknown')}: {e}")
                    embeddings.append(None)

            # Store in Pinecone
            for doc, embedding in zip(batch, embeddings):
                if embedding:
                    success = await self._store_document(doc, embedding, campaign_goals)
                    if success:
                        stored_count += 1
                        self.stats['total_stored'] += 1

            # Small delay between batches
            if i + batch_size < len(documents):
                await asyncio.sleep(0.1)

        self.stats['total_fetched'] += len(documents)
        return stored_count

    async def _store_document(
        self,
        document: Dict,
        embedding: List[float],
        campaign_goals: Dict
    ) -> bool:
        """
        Store a single document in Pinecone

        Args:
            document: Document data
            embedding: Document embedding
            campaign_goals: Campaign context

        Returns:
            Success status
        """
        try:
            # Prepare metadata
            metadata = {
                **document,
                'campaign_id': campaign_goals.get('campaign_id'),
                'campaign_target': campaign_goals.get('target'),
                'campaign_indication': campaign_goals.get('indication'),
                'indexed_at': datetime.utcnow().isoformat()
            }

            # Ensure metadata is JSON serializable
            metadata = self._clean_metadata(metadata)

            # Store in Pinecone literature index
            doc_id = document.get('id', f"{document.get('source', 'unknown')}_{hash(str(document))}")

            # Upsert to Pinecone literature index
            self.pinecone_client.literature_index.upsert(
                vectors=[(doc_id, embedding, metadata)]
            )

            logger.debug(f"Stored document {doc_id} in Pinecone literature index")
            return True

        except Exception as e:
            logger.error(f"Error storing document: {e}")
            self.stats['errors'].append(f"Store error: {str(e)}")
            return False

    def _clean_metadata(self, metadata: Dict) -> Dict:
        """Clean metadata to ensure JSON serialization"""
        clean = {}
        for key, value in metadata.items():
            if value is None:
                continue
            elif isinstance(value, (str, int, float, bool)):
                clean[key] = value
            elif isinstance(value, list):
                # Convert lists to JSON strings if they contain complex objects
                if value and isinstance(value[0], dict):
                    clean[key] = json.dumps(value)
                else:
                    clean[key] = str(value)
            elif isinstance(value, dict):
                clean[key] = json.dumps(value)
            else:
                clean[key] = str(value)
        return clean

    async def _extract_campaign_insights(self, campaign_goals: Dict) -> List[Dict]:
        """
        Extract actionable insights for the campaign

        Args:
            campaign_goals: Campaign context

        Returns:
            List of actionable insights
        """
        insights = []

        try:
            # Query Pinecone for relevant documents
            query_text = f"{campaign_goals.get('target', '')} {campaign_goals.get('indication', '')}"
            query_embedding = await self.embedding_generator.generate_query_embedding(query_text)

            # Search literature index for similar documents
            results = self.pinecone_client.literature_index.query(
                vector=query_embedding,
                top_k=20,
                include_metadata=True,
                filter={'campaign_id': campaign_goals.get('campaign_id')}
            )

            relevant_docs = results.get('matches', [])

            # Analyze documents for insights
            if relevant_docs:
                insights = self._analyze_documents_for_insights(relevant_docs, campaign_goals)

        except Exception as e:
            logger.error(f"Failed to extract insights: {e}")

        return insights

    def _analyze_documents_for_insights(
        self,
        documents: List[Dict],
        campaign_goals: Dict
    ) -> List[Dict]:
        """
        Analyze documents to extract actionable insights

        Args:
            documents: Relevant documents from Pinecone
            campaign_goals: Campaign context

        Returns:
            List of insights
        """
        insights = []

        # Group documents by source
        by_source = {}
        for doc in documents:
            source = doc.get('metadata', {}).get('source', 'unknown')
            if source not in by_source:
                by_source[source] = []
            by_source[source].append(doc)

        # Extract insights per source type
        if 'pubmed' in by_source and len(by_source['pubmed']) > 5:
            insights.append({
                'type': 'literature_trend',
                'title': 'Recent Research Activity',
                'description': f"Found {len(by_source['pubmed'])} recent papers on {campaign_goals.get('target')}",
                'priority': 'medium',
                'action_required': 'Review latest findings for novel approaches'
            })

        if 'clinical_trials' in by_source:
            active_trials = [d for d in by_source['clinical_trials']
                            if d.get('metadata', {}).get('status') == 'Recruiting']
            if active_trials:
                insights.append({
                    'type': 'competitive',
                    'title': 'Active Clinical Trials',
                    'description': f"{len(active_trials)} competing trials currently recruiting",
                    'priority': 'high',
                    'action_required': 'Consider differentiation strategy'
                })

        if 'patents' in by_source and len(by_source['patents']) > 0:
            insights.append({
                'type': 'ip_landscape',
                'title': 'Patent Activity',
                'description': f"Identified {len(by_source['patents'])} relevant patents",
                'priority': 'medium',
                'action_required': 'Review for freedom to operate'
            })

        if 'chembl' in by_source and len(by_source['chembl']) > 10:
            insights.append({
                'type': 'compound_landscape',
                'title': 'Known Active Compounds',
                'description': f"Found {len(by_source['chembl'])} bioactive compounds for target",
                'priority': 'high',
                'action_required': 'Analyze SAR for optimization opportunities'
            })

        return insights

    async def _store_ingestion_metadata(self, campaign_goals: Dict, stats: Dict):
        """Store metadata about the ingestion process"""
        try:
            metadata = {
                'campaign_id': campaign_goals.get('campaign_id'),
                'ingestion_time': datetime.utcnow().isoformat(),
                'stats': json.dumps(stats),
                'target': campaign_goals.get('target'),
                'indication': campaign_goals.get('indication')
            }

            # Could store in database or cache
            logger.info(f"Ingestion metadata: {metadata}")

        except Exception as e:
            logger.error(f"Failed to store metadata: {e}")

    async def search_campaign_literature(
        self,
        campaign_id: str,
        query: str,
        top_k: int = 10
    ) -> List[Dict]:
        """
        Search literature specific to a campaign

        Args:
            campaign_id: Campaign identifier
            query: Search query
            top_k: Number of results

        Returns:
            List of relevant documents
        """
        try:
            # Generate query embedding
            query_embedding = await self.embedding_generator.generate_query_embedding(query)

            # Search literature index with campaign filter
            results = self.pinecone_client.literature_index.query(
                vector=query_embedding,
                top_k=top_k,
                include_metadata=True,
                filter={'campaign_id': campaign_id}
            )

            return results.get('matches', [])

        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []


# Test function
async def test_pipeline():
    """Test the complete data ingestion pipeline"""
    async with DataIngestionPipeline() as pipeline:
        campaign_goals = {
            'campaign_id': 'test_campaign_001',
            'target': 'KRAS G12C',
            'indication': 'NSCLC',
            'keywords': ['resistance', 'combination therapy'],
            'modality': 'small molecule'
        }

        print("Starting pipeline test...")
        result = await pipeline.ingest_for_campaign(campaign_goals)

        print("\n=== Pipeline Results ===")
        print(f"Success: {result.get('success')}")
        print(f"Duration: {result.get('duration_seconds', 0):.1f} seconds")

        stats = result.get('stats', {})
        print(f"\n=== Statistics ===")
        print(f"Total Fetched: {stats.get('total_fetched')}")
        print(f"Total Embedded: {stats.get('total_embedded')}")
        print(f"Total Stored: {stats.get('total_stored')}")

        print(f"\n=== Source Breakdown ===")
        for source, counts in stats.get('sources', {}).items():
            print(f"  {source}: {counts}")

        print(f"\n=== Insights ===")
        for insight in result.get('insights', []):
            print(f"  - {insight.get('title')}: {insight.get('description')}")

        if stats.get('errors'):
            print(f"\n=== Errors ===")
            for error in stats['errors']:
                print(f"  - {error}")


if __name__ == "__main__":
    asyncio.run(test_pipeline())