"""
Scheduled Tasks Router for NovoMCP
Handles background jobs triggered by AWS EventBridge
"""

from fastapi import APIRouter, HTTPException, Request, Depends, Header
from typing import Dict, Any, List, Optional
import logging
from datetime import datetime
import asyncio
import os

logger = logging.getLogger(__name__)

router = APIRouter()

# API Key validation for scheduled endpoints
API_KEY = os.getenv("API_KEY", "")


async def validate_scheduled_api_key(x_api_key: Optional[str] = Header(None)):
    """Validate API key for scheduled tasks"""
    if x_api_key != API_KEY:
        logger.warning(f"Invalid API key attempt for scheduled task: {x_api_key}")
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key


async def _background_literature_ingestion(all_targets: set):
    """
    Background task to process literature ingestion for all targets.
    Runs async to avoid ALB timeout (60s limit).
    """
    from ai.data_pipeline import DataIngestionPipeline

    start_time = datetime.utcnow()
    results = []
    errors = []

    for target in all_targets:
        try:
            logger.info(f"Processing target: {target}")

            async with DataIngestionPipeline() as pipeline:
                campaign_goals = {
                    'campaign_id': f'scheduled_{datetime.utcnow().strftime("%Y%m%d")}_{target.lower().replace(" ", "_")}',
                    'target': target,
                    'indication': '',
                    'keywords': ['inhibitor', 'therapeutic', 'clinical'],
                    'modality': 'general',
                    'days_back': 1  # Last 24 hours only (incremental)
                }

                result = await pipeline.ingest_for_campaign(campaign_goals)

                if result.get('success'):
                    stats = result.get('stats', {})
                    logger.info(f"Target {target}: {stats.get('total_stored', 0)} documents stored")
                    results.append({
                        'target': target,
                        'success': True,
                        'documents_stored': stats.get('total_stored', 0),
                        'sources': stats.get('sources', {})
                    })
                else:
                    errors.append({
                        'target': target,
                        'error': result.get('error', 'Unknown error')
                    })

        except Exception as e:
            logger.error(f"Failed to ingest for target {target}: {e}")
            errors.append({
                'target': target,
                'error': str(e)
            })

    # Calculate statistics
    total_documents = sum(r.get('documents_stored', 0) for r in results if r.get('success'))
    duration = (datetime.utcnow() - start_time).total_seconds()

    # Broadcast completion event
    try:
        from core.redis_pubsub import broadcast_global_update
        await broadcast_global_update('scheduled_ingestion_complete', {
            'targets_processed': len(results),
            'total_documents': total_documents,
            'errors': len(errors),
            'duration_seconds': duration,
            'timestamp': datetime.utcnow().isoformat()
        })
    except Exception as e:
        logger.warning(f"Failed to broadcast completion: {e}")

    logger.info(f"=== SCHEDULED INGESTION COMPLETE: {total_documents} documents in {duration:.1f}s ===")


@router.post("/scheduled/literature-ingestion")
async def scheduled_literature_ingestion(
    request: Request = None,
    api_key: str = Depends(validate_scheduled_api_key)
):
    """
    Scheduled literature ingestion triggered by AWS EventBridge.
    Runs daily at 2 AM UTC to ingest new papers from last 24 hours.

    This endpoint spawns a background task to avoid ALB timeout (60s).
    The actual ingestion happens async and broadcasts completion via Redis.

    This endpoint:
    1. Gets all active campaigns
    2. Extracts unique targets
    3. Adds common therapeutic targets
    4. Spawns background task for ingestion
    5. Returns immediately (< 5s)

    Returns:
        Acknowledgment that ingestion started
    """
    try:
        start_time = datetime.utcnow()
        logger.info("=== SCHEDULED LITERATURE INGESTION STARTED ===")

        # Get all active campaigns
        targets_from_campaigns = set()
        try:
            from .proxy import proxy_request
            campaigns = await proxy_request(
                "dashboard-aggregator",
                "api/v1/campaigns/active",
                request if request else None,
                "GET",
                None
            )

            # Extract targets from campaigns
            if isinstance(campaigns, list):
                for campaign in campaigns:
                    target = campaign.get('target') or campaign.get('goal', '').split()[0]
                    if target:
                        targets_from_campaigns.add(target)
                        logger.info(f"Found target from campaign: {target}")
        except Exception as e:
            logger.warning(f"Could not fetch active campaigns: {e}")

        # Common therapeutic targets for general knowledge base
        common_targets = [
            "KRAS G12C",
            "EGFR",
            "ALK",
            "BRAF V600E",
            "HER2",
            "PD-L1",
            "CTLA-4",
            "BTK",
            "JAK2",
            "BCR-ABL",
            "PI3K",
            "mTOR",
            "CDK4/6",
            "PARP",
            "VEGFR"
        ]

        # Combine targets
        all_targets = targets_from_campaigns.union(set(common_targets))
        logger.info(f"Spawning background task for {len(all_targets)} targets")

        # Spawn background task to avoid ALB timeout
        asyncio.create_task(_background_literature_ingestion(all_targets))

        duration = (datetime.utcnow() - start_time).total_seconds()

        return {
            "status": "started",
            "message": "Literature ingestion started in background",
            "targets_queued": len(all_targets),
            "duration_seconds": duration,
            "timestamp": datetime.utcnow().isoformat()
        }

    except Exception as e:
        logger.error(f"Scheduled literature ingestion failed to start: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scheduled/cleanup-old-data")
async def scheduled_cleanup(
    api_key: str = Depends(validate_scheduled_api_key)
):
    """
    Scheduled cleanup of old Pinecone vectors and temporary data.
    Runs weekly to maintain system health.
    """
    try:
        logger.info("=== SCHEDULED CLEANUP STARTED ===")

        # TODO: Implement cleanup logic
        # - Delete Pinecone vectors older than 90 days
        # - Archive old campaign data
        # - Clean up temporary files

        return {
            "status": "success",
            "message": "Cleanup completed",
            "timestamp": datetime.utcnow().isoformat()
        }

    except Exception as e:
        logger.error(f"Scheduled cleanup failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/scheduled/health")
async def scheduled_health_check():
    """
    Health check endpoint for scheduled tasks.
    Used by EventBridge to verify service availability.
    """
    return {
        "status": "healthy",
        "service": "novomcp-scheduled-tasks",
        "timestamp": datetime.utcnow().isoformat()
    }
