"""
Campaign Orchestration Router for NovoMCP
Provides campaign management and AI decision-making for autonomous discovery
"""

from fastapi import APIRouter, HTTPException, Request
from typing import Dict, Any, List, Optional
import logging
from datetime import datetime
import json
import httpx

logger = logging.getLogger(__name__)

router = APIRouter()

# Whitelist of constraint fields that can be locked/unlocked
LOCKABLE_CONSTRAINT_FIELDS = {
    'molecular.mw.min', 'molecular.mw.max',
    'molecular.logp.min', 'molecular.logp.max',
    'molecular.hbd.max', 'molecular.hba.max',
    'molecular.hba.min',
    'molecular.tpsa.max', 'molecular.tpsa.min',
    'admet.hepatotoxicity', 'admet.cardiotoxicity',
    'admet.respiratory_toxicity', 'admet.cyp_inhibition',
    'admet.overall_toxicity', 'admet.solubility'
}

@router.get("/campaigns/active")
async def get_active_campaigns(request: Request):
    """
    Get all active campaigns from campaigns table in research-db.
    Uses direct SQL query to eliminate HTTP overhead.
    """
    try:
        from core.db_helper import query_sql

        # Query active campaigns using direct SQL (NO HTTP OVERHEAD)
        campaigns = await query_sql("""
            SELECT
                c.id, c.tenant_id, c.name, c.goal, c.status, c.autonomy_level,
                c.created_at, c.updated_at, c.completed_at,
                c.constraints, c.metadata, c.dataSources, c.autonomy,
                c.workflow_state, c.circuit_breaker_state, c.target_protein, c.quantum_enabled,
                COALESCE(SUM(ci.total_molecules_generated), 0) as molecules_generated,
                COALESCE(SUM(ci.total_leads_discovered), 0) as successful_leads,
                COALESCE(COUNT(CASE WHEN ci.status = 'completed' THEN 1 END), 0) as experiments_run
            FROM campaigns c
            LEFT JOIN campaign_iterations ci ON c.id = ci.campaign_id AND ci.status = 'completed'
            WHERE c.status = 'active'
            GROUP BY c.id, c.tenant_id, c.name, c.goal, c.status, c.autonomy_level,
                c.created_at, c.updated_at, c.completed_at, c.constraints, c.metadata,
                c.dataSources, c.autonomy, c.workflow_state, c.circuit_breaker_state,
                c.target_protein, c.quantum_enabled
            ORDER BY c.created_at DESC
        """, ())

        # Parse JSON fields for each campaign
        for campaign in campaigns:
            for json_field in ['constraints', 'metadata', 'dataSources', 'autonomy', 'workflow_state', 'circuit_breaker_state']:
                if campaign.get(json_field):
                    try:
                        campaign[json_field] = json.loads(campaign[json_field])
                    except:
                        campaign[json_field] = {}

            # Convert datetime to ISO format
            for date_field in ['created_at', 'updated_at', 'completed_at']:
                if campaign.get(date_field):
                    campaign[date_field] = campaign[date_field].isoformat()

        logger.info(f"Found {len(campaigns)} active campaigns")
        return campaigns

    except Exception as e:
        logger.error(f"Failed to get active campaigns: {e}", exc_info=True)
        return []


@router.post("/ai/campaign-decision")
async def make_campaign_decision(request: Request):
    """
    Make an autonomous decision for a campaign using AI.
    This is the brain of the autonomous loop.
    Includes health checks and fallback logic.
    """
    try:
        data = await request.json()
        campaign_id = data.get('campaign_id')
        campaign_state = data.get('campaign_state', {})
        goal = data.get('goal', '')
        constraints = data.get('constraints', {})
        history = data.get('history', [])

        # Use Azure OpenAI to make intelligent decision
        from ai.azure_openai_client import get_azure_client
        client = get_azure_client()

        # Check if OpenAI client is available
        if not client or not client.available:
            logger.warning("Azure OpenAI not available, using fallback decision logic")
            # Provide a safe default action
            return {
                'campaign_id': campaign_id,
                'decision': {
                    'action': 'wait',
                    'reasoning': 'AI decision engine temporarily unavailable',
                    'parameters': {},
                    'priority': 'low',
                    'confidence': 0.1
                },
                'status': 'degraded'
            }

        # Get context from Pinecone (literature and similar campaigns)
        from core.pinecone_client import get_pinecone_client
        pc = get_pinecone_client()

        context = await pc.get_decision_context(goal, constraints, history)

        # Build decision prompt with rich context
        prompt = f"""
        You are an autonomous drug discovery AI managing a campaign.

        Campaign Goal: {goal}
        Current State: {json.dumps(campaign_state, indent=2)}
        Constraints: {json.dumps(constraints, indent=2)}
        Recent History: {json.dumps(history[-5:], indent=2) if history else 'No history'}

        CONTEXT FROM KNOWLEDGE BASE:
        Similar Successful Campaigns: {json.dumps(context.get('successful_patterns', []), indent=2)}
        Failed Patterns to Avoid: {json.dumps(context.get('failed_patterns', []), indent=2)}
        Recommendations: {json.dumps(context.get('recommendations', []), indent=2)}
        Relevant Literature Count: {len(context.get('relevant_literature', []))}

        Based on the current state and goal, decide the next action.

        Available actions:
        - generate_molecules: Generate new molecular candidates
        - optimize_leads: Optimize existing lead compounds
        - run_screening: Screen compounds against targets
        - analyze_results: Analyze recent results
        - pivot_strategy: Change approach based on learnings
        - request_review: Request human review
        - wait: No action needed now

        Respond with JSON:
        {{
            "action": "action_name",
            "reasoning": "explanation of why this action",
            "parameters": {{}},
            "priority": "high/medium/low",
            "confidence": 0.0-1.0
        }}
        """

        response = await client.get_completion(prompt, temperature=0.7)

        # Parse AI response
        try:
            decision = json.loads(response)
        except:
            # Fallback if response isn't valid JSON
            decision = {
                "action": "wait",
                "reasoning": "Could not parse AI response",
                "parameters": {},
                "priority": "low",
                "confidence": 0.1
            }

        # Add metadata
        decision['campaign_id'] = campaign_id
        decision['timestamp'] = datetime.utcnow().isoformat()

        # Store decision in database for audit trail
        from .proxy import proxy_request
        await proxy_request(
            "db-manager",
            "campaign-decisions",
            request,
            "POST",
            json.dumps(decision).encode()
        )

        return decision

    except Exception as e:
        logger.error(f"Failed to make campaign decision: {e}")
        return {
            "action": "wait",
            "reasoning": f"Error in decision making: {str(e)}",
            "parameters": {},
            "priority": "low",
            "confidence": 0.0
        }


@router.post("/ai/learn")
async def learn_from_outcome(request: Request):
    """
    Record learning from campaign outcomes.
    Stores patterns for continuous improvement in both Pinecone and database.
    """
    try:
        data = await request.json()
        campaign_id = data.get('campaign_id')
        decision = data.get('decision', {})
        outcome = data.get('outcome', {})
        context = data.get('context', {})
        timestamp = data.get('timestamp', datetime.utcnow().isoformat())

        # Analyze outcome for patterns
        success = outcome.get('status') == 'success'

        # Store in Pinecone for similarity search
        from core.pinecone_client import store_learning
        await store_learning(campaign_id, decision, outcome, context)

        # Create learning pattern record for cross-campaign intelligence
        import hashlib
        from core.db_helper import execute_sql

        # Generate pattern hash from decision type + outcome
        pattern_type = f"{decision.get('action', 'unknown')}_action"
        pattern_str = f"{pattern_type}_{decision.get('reasoning', '')}_{outcome.get('status', '')}"
        pattern_hash = hashlib.sha256(pattern_str.encode()).hexdigest()

        # Build context JSON
        learning_context = {
            "campaign_id": campaign_id,
            "decision_type": decision.get('action'),
            "decision_reasoning": decision.get('reasoning'),
            "outcome_status": outcome.get('status'),
            "outcome_message": outcome.get('message'),
            "confidence": decision.get('confidence', 0.5),
            "timestamp": timestamp,
            **context
        }

        success_rate = 1.0 if success else 0.0

        # Store learning pattern using direct SQL (NO HTTP OVERHEAD)
        # Use MERGE to update if pattern exists, insert if new
        await execute_sql("""
            MERGE INTO learning_patterns AS target
            USING (SELECT %s AS pattern_hash) AS source
            ON target.pattern_hash = source.pattern_hash
            WHEN MATCHED THEN
                UPDATE SET
                    occurrence_count = target.occurrence_count + 1,
                    success_rate = (target.success_rate * target.occurrence_count + %s) / (target.occurrence_count + 1),
                    last_seen = GETUTCDATE(),
                    context = %s
            WHEN NOT MATCHED THEN
                INSERT (pattern_hash, pattern_type, success_rate, occurrence_count, first_seen, last_seen, context, tenant_id)
                VALUES (%s, %s, %s, 1, GETUTCDATE(), GETUTCDATE(), %s, NULL);
        """, (
            pattern_hash,  # for matching
            success_rate,  # for updating success_rate
            json.dumps(learning_context),  # for updating context
            pattern_hash,  # for inserting
            pattern_type,  # for inserting
            success_rate,  # for inserting
            json.dumps(learning_context)  # for inserting
        ))

        # If failure, also report to negative-data service
        if not success:
            await proxy_request(
                "negative-data",
                "record-failure",
                request,
                "POST",
                json.dumps({
                    "campaign_id": campaign_id,
                    "failure_type": "decision_outcome",
                    "details": learning_record
                }).encode()
            )

        return {"status": "recorded", "success": success, "stored_in_pinecone": True}

    except Exception as e:
        logger.error(f"Failed to record learning: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/campaigns/metrics/{campaign_id}")
async def get_campaign_metrics(campaign_id: str, request: Request):
    """
    Get metrics and performance data for a campaign.
    """
    try:
        from .proxy import proxy_request

        # Get campaign metrics from dashboard-aggregator
        return await proxy_request(
            "dashboard-aggregator",
            f"api/v1/campaigns/{campaign_id}/metrics",
            request,
            "GET",
            None
        )
    except Exception as e:
        logger.error(f"Failed to get campaign metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/campaigns/literature-search")
async def search_campaign_literature(request: Request):
    """
    Search scientific literature relevant to a campaign using Pinecone.
    This provides context for autonomous decision making.
    """
    try:
        data = await request.json()
        query = data.get('query', '')
        campaign_goal = data.get('goal', '')
        filters = data.get('filters', {})
        top_k = data.get('top_k', 10)

        # Search through indexed scientific papers using Pinecone
        from core.pinecone_client import search_literature
        results = await search_literature(
            query=f"{query} {campaign_goal}",
            constraints=filters
        )

        return {
            "status": "success",
            "query": query,
            "results": results[:top_k],
            "count": len(results),
            "index": "novomcp-literature"
        }

    except Exception as e:
        logger.error(f"Failed to search literature: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/campaigns/{campaign_id}/ingest-literature")
async def ingest_campaign_literature(campaign_id: str, request: Request):
    """
    Ingest literature for a specific campaign from all sources:
    - PubMed (scientific papers)
    - USPTO (patents)
    - ClinicalTrials.gov (trials)
    - ChEMBL (bioactive compounds)
    - PubChem (chemical database)
    - bioRxiv (preprints)

    Fetches real data, generates embeddings, stores in Pinecone
    """
    try:
        data = await request.json()

        # Get campaign details using direct SQL (NO HTTP OVERHEAD)
        from routers.ai_orchestration import get_campaign_with_metrics_sql
        campaign = await get_campaign_with_metrics_sql(campaign_id)

        if not campaign:
            raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found")

        # Build campaign goals for ingestion
        campaign_goals = {
            'campaign_id': campaign_id,
            'target': campaign.get('target') or campaign.get('goal', '').split()[0],
            'indication': data.get('indication', campaign.get('indication', '')),
            'keywords': data.get('keywords', []),
            'modality': data.get('modality', 'small molecule')
        }

        logger.info(f"Starting literature ingestion for campaign {campaign_id}: {campaign_goals}")

        # Run ingestion pipeline
        from ai.data_pipeline import DataIngestionPipeline
        async with DataIngestionPipeline() as pipeline:
            result = await pipeline.ingest_for_campaign(campaign_goals)

        # Broadcast progress
        from core.redis_pubsub import broadcast_global_update
        await broadcast_global_update('literature_ingested', {
            'campaign_id': campaign_id,
            'stats': result.get('stats', {}),
            'timestamp': datetime.utcnow().isoformat()
        })

        return {
            "status": "success",
            "campaign_id": campaign_id,
            "ingestion_result": result,
            "message": f"Ingested {result.get('stats', {}).get('total_stored', 0)} documents from {len(result.get('stats', {}).get('sources', {}))} sources"
        }

    except Exception as e:
        logger.error(f"Literature ingestion failed for campaign {campaign_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/campaigns/literature-ingest-batch")
async def ingest_literature_batch(request: Request):
    """
    Ingest literature for multiple campaigns or general knowledge base population.
    Used for background/scheduled ingestion.
    """
    try:
        data = await request.json()
        campaign_ids = data.get('campaign_ids', [])
        targets = data.get('targets', [])

        results = []

        # Ingest for specific campaigns
        if campaign_ids:
            for campaign_id in campaign_ids:
                try:
                    # Call individual ingestion
                    from ai.data_pipeline import DataIngestionPipeline
                    async with DataIngestionPipeline() as pipeline:
                        result = await pipeline.ingest_for_campaign({'campaign_id': campaign_id})
                        results.append({
                            'campaign_id': campaign_id,
                            'success': result.get('success', False),
                            'stats': result.get('stats', {})
                        })
                except Exception as e:
                    logger.error(f"Batch ingestion failed for {campaign_id}: {e}")
                    results.append({
                        'campaign_id': campaign_id,
                        'success': False,
                        'error': str(e)
                    })

        # Ingest general knowledge for targets
        if targets:
            for target in targets:
                try:
                    from ai.data_pipeline import DataIngestionPipeline
                    async with DataIngestionPipeline() as pipeline:
                        result = await pipeline.ingest_for_campaign({
                            'campaign_id': f'knowledge_base_{target.lower().replace(" ", "_")}',
                            'target': target,
                            'indication': '',
                            'keywords': [],
                            'modality': 'general'
                        })
                        results.append({
                            'target': target,
                            'success': result.get('success', False),
                            'stats': result.get('stats', {})
                        })
                except Exception as e:
                    logger.error(f"Batch ingestion failed for target {target}: {e}")
                    results.append({
                        'target': target,
                        'success': False,
                        'error': str(e)
                    })

        successful = sum(1 for r in results if r.get('success', False))

        return {
            "status": "success",
            "total_processed": len(results),
            "successful": successful,
            "failed": len(results) - successful,
            "results": results
        }

    except Exception as e:
        logger.error(f"Batch literature ingestion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/campaigns/store-learning")
async def store_campaign_learning(request: Request):
    """
    Store campaign learnings and patterns in Pinecone for future reference.
    This builds the knowledge base for improving decisions over time.
    """
    try:
        data = await request.json()
        campaign_id = data.get('campaign_id')
        decision = data.get('decision', {})
        outcome = data.get('outcome', {})
        context = data.get('context', {})

        # Store in Pinecone patterns index for similarity search
        from core.pinecone_client import get_pinecone_client
        pc = get_pinecone_client()

        stored = await pc.store_learning_pattern(
            campaign_id=campaign_id,
            decision=decision,
            outcome=outcome,
            context=context
        )

        # Also store in database using direct SQL (NO HTTP OVERHEAD)
        import hashlib
        from core.db_helper import execute_sql

        # Generate pattern hash from decision + outcome for deduplication
        pattern_type = f"{decision.get('action', 'unknown')}_action"
        pattern_str = f"{pattern_type}_{json.dumps(decision, sort_keys=True)}_{json.dumps(outcome, sort_keys=True)}"
        pattern_hash = hashlib.sha256(pattern_str.encode()).hexdigest()

        # Build context JSON including campaign metadata
        learning_context = {
            "campaign_id": campaign_id,
            "decision": decision,
            "outcome": outcome,
            "timestamp": datetime.utcnow().isoformat(),
            **context
        }

        # Determine success rate from outcome
        success = outcome.get('status') == 'success' or outcome.get('success', False)
        success_rate = 1.0 if success else 0.0

        # Store learning pattern using direct SQL with MERGE (upsert)
        await execute_sql("""
            MERGE INTO learning_patterns AS target
            USING (SELECT %s AS pattern_hash) AS source
            ON target.pattern_hash = source.pattern_hash
            WHEN MATCHED THEN
                UPDATE SET
                    occurrence_count = target.occurrence_count + 1,
                    success_rate = (target.success_rate * target.occurrence_count + %s) / (target.occurrence_count + 1),
                    last_seen = GETUTCDATE(),
                    context = %s
            WHEN NOT MATCHED THEN
                INSERT (pattern_hash, pattern_type, success_rate, occurrence_count, first_seen, last_seen, context, tenant_id)
                VALUES (%s, %s, %s, 1, GETUTCDATE(), GETUTCDATE(), %s, NULL);
        """, (
            pattern_hash,  # for matching
            success_rate,  # for updating success_rate
            json.dumps(learning_context),  # for updating context
            pattern_hash,  # for inserting
            pattern_type,  # for inserting
            success_rate,  # for inserting
            json.dumps(learning_context)  # for inserting
        ))

        return {
            "status": "success",
            "message": "Learning stored in Pinecone and database",
            "pinecone_stored": stored,
            "pattern_hash": pattern_hash,
            "index": "novomcp-patterns"
        }

    except Exception as e:
        logger.error(f"Failed to store learning: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/campaigns/{campaign_id}")
async def get_campaign_details(campaign_id: str, request: Request):
    """Get detailed campaign information using direct SQL query"""
    try:
        from routers.ai_orchestration import get_campaign_with_metrics_sql

        # Fetch campaign with metrics using direct SQL (NO HTTP OVERHEAD)
        campaign = await get_campaign_with_metrics_sql(campaign_id)

        if not campaign:
            raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found")

        # Map to expected frontend format
        return {
            "id": campaign.get("id"),
            "name": campaign.get("name", "Unnamed Campaign"),
            "status": campaign.get("status", "active"),
            "progress_pct": campaign.get("metadata", {}).get("progress_pct", 0),
            "confidence": campaign.get("metadata", {}).get("confidence", 100),
            "molecules_evaluated": campaign["metrics"].get("molecules_generated", 0),
            "leads_identified": campaign["metrics"].get("successful_leads", 0),
            "decisions_per_hour": campaign.get("metadata", {}).get("decisions_per_hour", 0),
            "budget_used": campaign.get("metadata", {}).get("budget_used", 0),
            "created_at": campaign.get("created_at"),
            "goal": campaign.get("goal"),
            "autonomy_level": campaign.get("autonomy_level", 1),
            "constraints": campaign.get("constraints"),
            "metadata": campaign.get("metadata")
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get campaign details: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/campaigns/{campaign_id}/decisions")
async def get_campaign_decisions(campaign_id: str, request: Request, since_seq: int = 0, limit: int = 200):
    """Get recent campaign decisions using direct SQL query"""
    try:
        from core.db_helper import query_sql

        # Query campaign decisions using direct SQL (NO HTTP OVERHEAD)
        decisions = await query_sql("""
            SELECT
                id, campaign_id, timestamp, decision_type, reasoning,
                input_context, outcome, success_score, confidence,
                iteration_number
            FROM campaign_decisions
            WHERE campaign_id = %s
            ORDER BY timestamp DESC
            LIMIT %s
        """, (campaign_id, limit))

        # Parse JSON fields
        for decision in decisions:
            if decision.get('input_context'):
                try:
                    decision['input_context'] = json.loads(decision['input_context'])
                except:
                    decision['input_context'] = {}

            if decision.get('outcome'):
                try:
                    decision['outcome'] = json.loads(decision['outcome'])
                except:
                    decision['outcome'] = {}

            # Convert timestamp to ISO format
            if decision.get('timestamp'):
                decision['timestamp'] = decision['timestamp'].isoformat()

        logger.info(f"Found {len(decisions)} decisions for campaign {campaign_id}")
        return decisions

    except Exception as e:
        logger.error(f"Failed to get campaign decisions: {e}", exc_info=True)
        return []


@router.get("/campaigns/{campaign_id}/discoveries")
async def get_campaign_discoveries(campaign_id: str, request: Request, since_seq: int = 0, limit: int = 200):
    """Get recent campaign discoveries using direct SQL query"""
    try:
        from core.db_helper import query_sql

        # Query campaign discoveries using direct SQL (NO HTTP OVERHEAD)
        discoveries = await query_sql("""
            SELECT
                id, campaign_id, molecule_id, discovery_timestamp,
                properties, significance_score, alert_sent,
                discovery_type, smiles
            FROM campaign_discoveries
            WHERE campaign_id = %s
            ORDER BY discovery_timestamp DESC
            LIMIT %s
        """, (campaign_id, limit))

        # Parse JSON fields
        for discovery in discoveries:
            if discovery.get('properties'):
                try:
                    discovery['properties'] = json.loads(discovery['properties'])
                except:
                    discovery['properties'] = {}

            # Convert timestamp to ISO format
            if discovery.get('discovery_timestamp'):
                discovery['discovery_timestamp'] = discovery['discovery_timestamp'].isoformat()

        logger.info(f"Found {len(discoveries)} discoveries for campaign {campaign_id}")
        return discoveries

    except Exception as e:
        logger.error(f"Failed to get campaign discoveries: {e}", exc_info=True)
        return []


@router.get("/campaigns/{campaign_id}/iterations")
async def get_campaign_iterations(campaign_id: str, request: Request, limit: int = 50):
    """
    Get iteration history for a campaign from Research DB.
    Shows iteration funnel metrics, outcomes, and loop-back behavior.
    """
    try:
        from core.db_helper import query_sql

        # Query campaign_iterations table
        iterations = await query_sql("""
            SELECT
                iteration_id, iteration_number, started_at, completed_at,
                status, outcome, outcome_reason,
                phase_1_input, phase_1_output, phase_1_pass_rate,
                phase_2_input, phase_2_output, phase_2_pass_rate,
                phase_3_input, phase_3_output, phase_3_pass_rate,
                phase_4_input, phase_4_output, phase_4_pass_rate,
                total_molecules_generated, total_leads_discovered,
                loop_back_triggered, generation_params, quality_thresholds
            FROM campaign_iterations
            WHERE campaign_id = %s
            ORDER BY iteration_number DESC
            LIMIT %s
        """, (campaign_id, limit))

        logger.info(f"Found {len(iterations)} iterations for campaign {campaign_id}")
        return iterations

    except Exception as e:
        logger.error(f"Failed to get campaign iterations: {e}", exc_info=True)
        return []


@router.get("/campaigns/{campaign_id}/quality-gates")
async def get_campaign_quality_gates(campaign_id: str, request: Request, limit: int = 100):
    """
    Get quality gate evaluation history for a campaign from Research DB.
    Shows which gates passed/failed, severity levels, and failure details.
    """
    try:
        from core.db_helper import query_sql

        # Query quality_gate_evaluations table
        evaluations = await query_sql("""
            SELECT
                evaluation_id, iteration_number, evaluated_at,
                gate_id, phase, passed,
                molecules_evaluated, molecules_passed, pass_rate,
                severity, failure_type, failures_json
            FROM quality_gate_evaluations
            WHERE campaign_id = %s
            ORDER BY evaluated_at DESC
            LIMIT %s
        """, (campaign_id, limit))

        # Parse failures_json
        for eval in evaluations:
            if eval.get('failures_json'):
                try:
                    eval['failures'] = json.loads(eval['failures_json'])
                except:
                    eval['failures'] = []
            if 'failures_json' in eval:
                del eval['failures_json']

        logger.info(f"Found {len(evaluations)} quality gate evaluations for campaign {campaign_id}")
        return evaluations

    except Exception as e:
        logger.error(f"Failed to get quality gate evaluations: {e}", exc_info=True)
        return []


@router.get("/campaigns/{campaign_id}/learning-patterns")
async def get_campaign_learning_patterns(campaign_id: str, request: Request, limit: int = 50):
    """
    Get learning patterns discovered during campaign from Research DB.
    Shows what strategies worked/failed and success rates over time.
    """
    try:
        from core.db_helper import query_sql

        # Query learning_patterns table
        patterns = await query_sql("""
            SELECT
                pattern_id, iteration_number, discovered_at,
                pattern_hash, pattern_type, success_rate, occurrence_count,
                context
            FROM learning_patterns
            WHERE campaign_id = %s
            ORDER BY success_rate DESC, occurrence_count DESC
            LIMIT %s
        """, (campaign_id, limit))

        # Parse context JSON
        for pattern in patterns:
            if pattern.get('context'):
                try:
                    pattern['context_data'] = json.loads(pattern['context'])
                except:
                    pattern['context_data'] = {}

        logger.info(f"Found {len(patterns)} learning patterns for campaign {campaign_id}")
        return patterns

    except Exception as e:
        logger.error(f"Failed to get learning patterns: {e}", exc_info=True)
        return []


@router.get("/campaigns/{campaign_id}/failure-analysis")
async def get_failure_analysis(campaign_id: str, request: Request):
    """
    Comprehensive failure pattern analysis for a campaign.
    Provides actionable insights for improving autonomous decision-making.

    Returns:
        - Top 5 failure patterns with frequencies
        - Failure rates by workflow phase
        - Cross-campaign pattern analysis
        - Actionable recommendations
    """
    try:
        from core.db_helper import query_sql

        # 1. Aggregate failure patterns from campaign_iterations
        failure_patterns = await query_sql("""
            SELECT
                outcome_reason,
                COUNT(*) as occurrence_count,
                AVG(total_molecules_generated) as avg_molecules,
                AVG(total_leads_discovered) as avg_leads,
                MIN(started_at) as first_seen,
                MAX(completed_at) as last_seen
            FROM campaign_iterations
            WHERE campaign_id = %s
            AND outcome = 'failed'
            GROUP BY outcome_reason
            ORDER BY occurrence_count DESC
            LIMIT 5
        """, (campaign_id,))

        # 2. Calculate failure rates by phase from quality_gate_evaluations
        phase_failures = await query_sql("""
            SELECT
                phase,
                gate_id,
                COUNT(*) as total_evaluations,
                SUM(CASE WHEN passed = 0 THEN 1 ELSE 0 END) as failures,
                CAST(SUM(CASE WHEN passed = 0 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) as failure_rate,
                AVG(pass_rate) as avg_pass_rate
            FROM quality_gate_evaluations
            WHERE campaign_id = %s
            GROUP BY phase, gate_id
            ORDER BY failure_rate DESC
        """, (campaign_id,))

        # 3. Cross-campaign pattern analysis - find consistently failing patterns
        cross_campaign_failures = await query_sql("""
            SELECT
                pattern_type,
                COUNT(DISTINCT campaign_id) as campaigns_affected,
                AVG(success_rate) as avg_success_rate,
                SUM(occurrence_count) as total_occurrences
            FROM learning_patterns
            WHERE success_rate < 0.3
            GROUP BY pattern_type
            ORDER BY campaigns_affected DESC, total_occurrences DESC
            LIMIT 5
        """, ())

        # 4. Cross-campaign pattern analysis - find consistently successful patterns
        cross_campaign_successes = await query_sql("""
            SELECT
                pattern_type,
                COUNT(DISTINCT campaign_id) as campaigns_affected,
                AVG(success_rate) as avg_success_rate,
                SUM(occurrence_count) as total_occurrences
            FROM learning_patterns
            WHERE success_rate > 0.8
            GROUP BY pattern_type
            ORDER BY avg_success_rate DESC, total_occurrences DESC
            LIMIT 5
        """, ())

        # 5. Get overall campaign statistics
        campaign_stats = await query_sql("""
            SELECT
                COUNT(*) as total_iterations,
                SUM(CASE WHEN outcome = 'failed' THEN 1 ELSE 0 END) as failed_iterations,
                SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as successful_iterations,
                SUM(CASE WHEN outcome = 'partial' THEN 1 ELSE 0 END) as partial_iterations,
                CAST(SUM(CASE WHEN outcome = 'failed' THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) as overall_failure_rate,
                SUM(total_molecules_generated) as total_molecules,
                SUM(total_leads_discovered) as total_leads,
                SUM(CASE WHEN loop_back_triggered = 1 THEN 1 ELSE 0 END) as loop_backs
            FROM campaign_iterations
            WHERE campaign_id = %s AND status = 'completed'
        """, (campaign_id,))

        # 6. Generate actionable recommendations based on patterns
        recommendations = []

        # Analyze top failure patterns
        if failure_patterns and len(failure_patterns) > 0:
            top_failure = failure_patterns[0]
            failure_reason = top_failure.get('outcome_reason', 'Unknown')
            failure_count = top_failure.get('occurrence_count', 0)

            if 'quality gate' in failure_reason.lower() or 'gate' in failure_reason.lower():
                recommendations.append({
                    'priority': 'high',
                    'category': 'quality_gates',
                    'issue': f"Frequent quality gate failures: {failure_reason}",
                    'recommendation': 'Consider relaxing quality thresholds or adjusting molecular generation parameters',
                    'occurrences': failure_count
                })
            elif 'timeout' in failure_reason.lower() or 'runtime' in failure_reason.lower():
                recommendations.append({
                    'priority': 'high',
                    'category': 'performance',
                    'issue': f"Performance issues detected: {failure_reason}",
                    'recommendation': 'Reduce batch sizes or optimize computational resources',
                    'occurrences': failure_count
                })
            elif 'budget' in failure_reason.lower() or 'limit' in failure_reason.lower():
                recommendations.append({
                    'priority': 'medium',
                    'category': 'resources',
                    'issue': f"Resource constraints: {failure_reason}",
                    'recommendation': 'Increase budget limits or optimize resource allocation',
                    'occurrences': failure_count
                })

        # Analyze phase failures
        if phase_failures and len(phase_failures) > 0:
            worst_phase = phase_failures[0]
            phase_name = worst_phase.get('phase', 'Unknown')
            gate_id = worst_phase.get('gate_id', 'Unknown')
            failure_rate = worst_phase.get('failure_rate', 0)

            if failure_rate > 0.5:
                recommendations.append({
                    'priority': 'high',
                    'category': 'workflow_phase',
                    'issue': f"High failure rate in {phase_name} phase at {gate_id} gate ({failure_rate:.0%})",
                    'recommendation': f"Review {gate_id} criteria and consider adjusting thresholds or generation strategy",
                    'failure_rate': failure_rate
                })

        # Analyze cross-campaign patterns
        if cross_campaign_failures and len(cross_campaign_failures) > 0:
            common_failure = cross_campaign_failures[0]
            pattern_type = common_failure.get('pattern_type', 'Unknown')
            campaigns_affected = common_failure.get('campaigns_affected', 0)

            if campaigns_affected > 1:
                recommendations.append({
                    'priority': 'medium',
                    'category': 'strategy',
                    'issue': f"Pattern '{pattern_type}' consistently fails across {campaigns_affected} campaigns",
                    'recommendation': f"Avoid '{pattern_type}' strategy or investigate root cause",
                    'campaigns_affected': campaigns_affected
                })

        # Analyze loop-back frequency
        if campaign_stats and len(campaign_stats) > 0:
            stats = campaign_stats[0]
            loop_backs = stats.get('loop_backs', 0)
            total_iterations = stats.get('total_iterations', 1)
            loop_back_rate = loop_backs / total_iterations if total_iterations > 0 else 0

            if loop_back_rate > 0.3:
                recommendations.append({
                    'priority': 'high',
                    'category': 'loop_back',
                    'issue': f"High loop-back rate ({loop_back_rate:.0%}) indicates quality gate failures",
                    'recommendation': 'Consider adjusting quality thresholds or improving molecular generation strategy',
                    'loop_back_rate': loop_back_rate
                })

        # Build comprehensive response
        analysis = {
            'campaign_id': campaign_id,
            'timestamp': datetime.utcnow().isoformat(),
            'statistics': campaign_stats[0] if campaign_stats and len(campaign_stats) > 0 else {},
            'top_failure_patterns': failure_patterns,
            'phase_failure_analysis': phase_failures,
            'cross_campaign_insights': {
                'consistent_failures': cross_campaign_failures,
                'consistent_successes': cross_campaign_successes
            },
            'recommendations': recommendations
        }

        logger.info(f"Generated failure analysis for campaign {campaign_id}: {len(failure_patterns)} patterns, {len(recommendations)} recommendations")
        return analysis

    except Exception as e:
        logger.error(f"Failed to generate failure analysis: {e}", exc_info=True)
        return {
            'campaign_id': campaign_id,
            'error': str(e),
            'statistics': {},
            'top_failure_patterns': [],
            'phase_failure_analysis': [],
            'cross_campaign_insights': {'consistent_failures': [], 'consistent_successes': []},
            'recommendations': []
        }


@router.post("/campaigns/{campaign_id}/pause")
async def pause_campaign(campaign_id: str, request: Request):
    """Pause an active campaign"""
    try:
        # Delegate to AI orchestration router
        from routers.ai_orchestration import broadcast_global_update, normalize_campaign_metrics, get_campaign_with_metrics_sql

        # Fetch current campaign state for broadcast using direct SQL (NO HTTP OVERHEAD)
        campaign_data = await get_campaign_with_metrics_sql(campaign_id)
        if not campaign_data:
            raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found")

        await broadcast_global_update('campaign_paused', {
            'campaign_id': campaign_id,
            'status': 'paused',
            'metrics': normalize_campaign_metrics(campaign_data.get('metrics')),
            'timestamp': datetime.utcnow().isoformat()
        })

        return {
            "success": True,
            "campaign_id": campaign_id,
            "status": "paused",
            "message": "Campaign paused successfully"
        }
    except Exception as e:
        logger.error(f"Failed to pause campaign: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/campaigns/{campaign_id}/resume")
async def resume_campaign(campaign_id: str, request: Request):
    """Resume a paused campaign"""
    try:
        # Delegate to AI orchestration router
        from routers.ai_orchestration import broadcast_global_update, normalize_campaign_metrics, get_campaign_with_metrics_sql

        # Fetch current campaign state for broadcast using direct SQL (NO HTTP OVERHEAD)
        campaign_data = await get_campaign_with_metrics_sql(campaign_id)
        if not campaign_data:
            raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found")

        await broadcast_global_update('campaign_resumed', {
            'campaign_id': campaign_id,
            'status': 'active',
            'metrics': normalize_campaign_metrics(campaign_data.get('metrics')),
            'timestamp': datetime.utcnow().isoformat()
        })

        return {
            "success": True,
            "campaign_id": campaign_id,
            "status": "active",
            "message": "Campaign resumed successfully"
        }
    except Exception as e:
        logger.error(f"Failed to resume campaign: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/campaigns/{campaign_id}/stop")
async def stop_campaign(campaign_id: str, request: Request):
    """Stop a campaign permanently"""
    try:
        # Delegate to AI orchestration router
        from routers.ai_orchestration import broadcast_global_update, normalize_campaign_metrics, get_campaign_with_metrics_sql

        # Fetch current campaign state for broadcast using direct SQL (NO HTTP OVERHEAD)
        campaign_data = await get_campaign_with_metrics_sql(campaign_id)
        if not campaign_data:
            raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found")

        await broadcast_global_update('campaign_stopped', {
            'campaign_id': campaign_id,
            'status': 'completed',
            'metrics': normalize_campaign_metrics(campaign_data.get('metrics')),
            'timestamp': datetime.utcnow().isoformat()
        })

        return {
            "success": True,
            "campaign_id": campaign_id,
            "status": "completed",
            "message": "Campaign stopped successfully"
        }
    except Exception as e:
        logger.error(f"Failed to stop campaign: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/campaigns/{campaign_id}/adjust-thresholds")
async def adjust_thresholds(campaign_id: str, request: Request):
    """
    Human intervention: Adjust quality thresholds and constraints for a campaign.
    Next iteration will use the updated thresholds.

    Request body:
    {
        "requested_by": "user@example.com",
        "reason": "ADMET filters too strict, relaxing by 20%",
        "adjustments": {
            "constraints.molecular.mw.max": 550,
            "constraints.admet.hepatotoxicity": 0.65,
            "thresholds.binding_affinity": -7.0
        }
    }
    """
    try:
        import uuid
        from core.db_helper import execute_sql, query_sql

        data = await request.json()
        requested_by = data.get('requested_by', 'unknown')
        reason = data.get('reason', 'Manual threshold adjustment')
        adjustments = data.get('adjustments', {})

        if not adjustments:
            raise HTTPException(status_code=400, detail="No adjustments provided")

        # 1. Get current campaign config using direct SQL (NO HTTP OVERHEAD)
        campaign_results = await query_sql("""
            SELECT constraints, metadata
            FROM campaigns
            WHERE id = %s
        """, (campaign_id,))

        if not campaign_results or len(campaign_results) == 0:
            raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found")

        campaign_row = campaign_results[0]

        # Parse existing constraints and metadata
        constraints_str = campaign_row.get('constraints', '{}')
        metadata_str = campaign_row.get('metadata', '{}')
        existing_constraints = json.loads(constraints_str) if constraints_str else {}
        existing_metadata = json.loads(metadata_str) if metadata_str else {}

        # 2. Store previous config for audit trail
        previous_config = {
            'constraints': existing_constraints
        }

        # 3. Apply adjustments to campaign config
        new_constraints = existing_constraints.copy() if existing_constraints else {}
        new_metadata = existing_metadata.copy() if isinstance(existing_metadata, dict) else {}
        if 'thresholds' not in new_metadata or not isinstance(new_metadata.get('thresholds'), dict):
            new_metadata['thresholds'] = {}

        for path, value in adjustments.items():
            # Route keys:
            # - constraints.* → campaigns.constraints JSON
            # - thresholds.*  → campaigns.metadata.thresholds JSON (used by quality gates)
            if path.startswith('thresholds.'):
                parts = path.split('.')[1:]  # drop 'thresholds'
                target = new_metadata['thresholds']
                for part in parts[:-1]:
                    if part not in target or not isinstance(target[part], dict):
                        target[part] = {}
                    target = target[part]
                target[parts[-1]] = value
            else:
                # Default: write under constraints (support optional 'constraints.' prefix)
                parts = path.split('.')
                if parts[0] == 'constraints':
                    parts = parts[1:]
                target = new_constraints
                for part in parts[:-1]:
                    if part not in target or not isinstance(target[part], dict):
                        target[part] = {}
                    target = target[part]
                target[parts[-1]] = value

        logger.info(f"Adjusting thresholds for campaign {campaign_id}: {len(adjustments)} changes by {requested_by}")

        # 4. Update campaign in database using direct SQL (NO HTTP OVERHEAD)
        await execute_sql("""
            UPDATE campaigns
            SET constraints = %s, metadata = %s, updated_at = GETUTCDATE()
            WHERE id = %s
        """, (json.dumps(new_constraints), json.dumps(new_metadata), campaign_id))

        # 5. Log intervention to intervention_requests table
        intervention_id = str(uuid.uuid4())
        await execute_sql("""
            INSERT INTO intervention_requests (
                intervention_id, campaign_id, requested_at, requested_by,
                intervention_type, reason, previous_config, new_config, action_taken, status
            ) VALUES (%s, %s, GETUTCDATE(), %s, 'adjust_thresholds', %s, %s, %s, %s, 'completed')
        """, (
            intervention_id,
            campaign_id,
            requested_by,
            reason,
            json.dumps(previous_config),
            json.dumps(adjustments),
            f"Adjusted {len(adjustments)} threshold parameters"
        ))

        # 6. Broadcast event via WebSocket
        from routers.ai_orchestration import broadcast_global_update
        await broadcast_global_update('thresholds_adjusted', {
            'campaign_id': campaign_id,
            'requested_by': requested_by,
            'adjustments': adjustments,
            'reason': reason,
            'timestamp': datetime.utcnow().isoformat()
        })

        return {
            "success": True,
            "campaign_id": campaign_id,
            "intervention_id": intervention_id,
            "adjustments_applied": len(adjustments),
            "message": f"Thresholds adjusted successfully. Next iteration will use new values.",
            "changes": adjustments
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to adjust thresholds: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/campaigns/{campaign_id}/force-regenerate")
async def force_regenerate(campaign_id: str, request: Request):
    """
    Human intervention: Force immediate regeneration with custom parameters.
    Skips current iteration and starts a new one with specified params.

    Request body:
    {
        "requested_by": "user@example.com",
        "reason": "Current batch showing poor results, forcing regeneration with higher diversity",
        "parameters": {
            "count": 200,
            "diversity": 0.8,
            "novelty": 0.3,
            "strategy": "focused_exploration"
        }
    }
    """
    try:
        import uuid
        from core.db_helper import execute_sql

        data = await request.json()
        requested_by = data.get('requested_by', 'unknown')
        reason = data.get('reason', 'Manual regeneration trigger')
        parameters = data.get('parameters', {})

        if not parameters:
            # Use sensible defaults if no parameters provided
            parameters = {
                "count": 100,
                "diversity": 0.7,
                "novelty": 0.4,
                "strategy": "diverse_exploration"
            }

        logger.info(f"Force regenerate requested for campaign {campaign_id} by {requested_by}: {reason}")

        # 1. Get current campaign config using direct SQL (NO HTTP OVERHEAD)
        from routers.ai_orchestration import get_campaign_with_metrics_sql
        campaign = await get_campaign_with_metrics_sql(campaign_id)

        if not campaign:
            raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found")

        # 2. Log intervention
        intervention_id = str(uuid.uuid4())
        await execute_sql("""
            INSERT INTO intervention_requests (
                intervention_id, campaign_id, requested_at, requested_by,
                intervention_type, reason, previous_config, new_config, action_taken, status
            ) VALUES (%s, %s, GETUTCDATE(), %s, 'force_regenerate', %s, NULL, %s, %s, 'completed')
        """, (
            intervention_id,
            campaign_id,
            requested_by,
            reason,
            json.dumps(parameters),
            f"Triggered immediate regeneration with custom parameters"
        ))

        # 3. Trigger regeneration via AI orchestration
        from routers.ai_orchestration import orchestrate_decision

        # Build context with custom parameters
        context = {
            **campaign,
            'intervention_id': intervention_id,
            'force_regenerate': True,
            'custom_parameters': parameters
        }

        # Execute regeneration
        result = await orchestrate_decision(
            campaign_id=campaign_id,
            action="generate_new_molecules",
            parameters=parameters,
            context=context
        )

        # 4. Broadcast event via WebSocket
        from routers.ai_orchestration import broadcast_global_update
        await broadcast_global_update('force_regenerate', {
            'campaign_id': campaign_id,
            'requested_by': requested_by,
            'parameters': parameters,
            'reason': reason,
            'timestamp': datetime.utcnow().isoformat()
        })

        return {
            "success": True,
            "campaign_id": campaign_id,
            "intervention_id": intervention_id,
            "message": "Regeneration triggered successfully",
            "parameters_used": parameters,
            "result": result
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to force regenerate: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/campaigns/{campaign_id}/lock-constraints")
async def lock_constraints(campaign_id: str, request: Request):
    """
    Lock/unlock specific constraint fields to prevent AI modification during loop-back iterations.

    Request body:
    {
        "requested_by": "user@example.com",
        "reason": "User-specified MW range must not change",
        "locks": {
            "molecular.mw.max": true,
            "molecular.mw.min": true,
            "molecular.logp.min": false
        }
    }

    Response:
    {
        "success": true,
        "campaign_id": "uuid",
        "intervention_id": "uuid",
        "locks_applied": {"molecular.mw.max": true, ...},
        "invalid_fields": []
    }
    """
    try:
        import uuid
        from core.db_helper import execute_sql, query_sql

        data = await request.json()
        requested_by = data.get('requested_by', 'unknown')
        reason = data.get('reason', 'Manual constraint lock adjustment')
        locks = data.get('locks', {})

        if not locks:
            raise HTTPException(status_code=400, detail="No lock changes specified in 'locks' field")

        logger.info(f"Lock constraints requested for campaign {campaign_id} by {requested_by}: {locks}")

        # 1. Validate all field paths against whitelist
        invalid_fields = [field for field in locks.keys() if field not in LOCKABLE_CONSTRAINT_FIELDS]
        if invalid_fields:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid constraint fields: {invalid_fields}. Allowed fields: {list(LOCKABLE_CONSTRAINT_FIELDS)}"
            )

        # 2. Get current campaign and metadata
        campaign_rows = await query_sql(
            "SELECT id, metadata FROM campaigns WHERE id = %s",
            (campaign_id,)
        )

        if not campaign_rows:
            raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found")

        current_metadata = campaign_rows[0].get('metadata', {})
        if isinstance(current_metadata, str):
            try:
                current_metadata = json.loads(current_metadata)
            except:
                current_metadata = {}

        # 3. Merge locks into metadata['constraint_locks']
        if 'constraint_locks' not in current_metadata:
            current_metadata['constraint_locks'] = {}

        # Store previous state for audit
        previous_locks = dict(current_metadata['constraint_locks'])

        # Apply new locks
        current_metadata['constraint_locks'].update(locks)

        # 4. Update campaign metadata
        await execute_sql(
            "UPDATE campaigns SET metadata = %s, updated_at = GETUTCDATE() WHERE id = %s",
            (json.dumps(current_metadata), campaign_id)
        )

        # 5. Log intervention for audit trail
        intervention_id = str(uuid.uuid4())
        await execute_sql("""
            INSERT INTO intervention_requests (
                intervention_id, campaign_id, requested_at, requested_by,
                intervention_type, reason, previous_config, new_config, action_taken, status
            ) VALUES (%s, %s, GETUTCDATE(), %s, 'lock_constraints', %s, %s, %s, %s, 'completed')
        """, (
            intervention_id,
            campaign_id,
            requested_by,
            reason,
            json.dumps({'constraint_locks': previous_locks}),
            json.dumps({'constraint_locks': locks}),
            f"Updated constraint locks: {list(locks.keys())}"
        ))

        # 6. Broadcast WebSocket event for real-time UI updates
        from routers.ai_orchestration import broadcast_global_update
        await broadcast_global_update('constraint_locks_updated', {
            'campaign_id': campaign_id,
            'requested_by': requested_by,
            'locks_applied': locks,
            'reason': reason,
            'timestamp': datetime.utcnow().isoformat()
        })

        logger.info(f"Successfully updated constraint locks for campaign {campaign_id}")

        return {
            "success": True,
            "campaign_id": campaign_id,
            "intervention_id": intervention_id,
            "locks_applied": locks,
            "invalid_fields": [],
            "message": f"Successfully updated {len(locks)} constraint lock(s)"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update constraint locks: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/campaigns/{campaign_id}/interventions")
async def get_campaign_interventions(campaign_id: str, request: Request, limit: int = 50):
    """
    Get human intervention history for a campaign.
    Shows all manual adjustments, forced regenerations, pauses, etc.
    """
    try:
        from core.db_helper import query_sql

        interventions = await query_sql("""
            SELECT
                intervention_id, requested_at, requested_by,
                intervention_type, reason, action_taken, status,
                previous_config, new_config
            FROM intervention_requests
            WHERE campaign_id = %s
            ORDER BY requested_at DESC
            LIMIT %s
        """, (campaign_id, limit))

        # Parse JSON fields
        for intervention in interventions:
            if intervention.get('previous_config'):
                try:
                    intervention['previous_config_data'] = json.loads(intervention['previous_config'])
                except:
                    intervention['previous_config_data'] = None

            if intervention.get('new_config'):
                try:
                    intervention['new_config_data'] = json.loads(intervention['new_config'])
                except:
                    intervention['new_config_data'] = None

        logger.info(f"Found {len(interventions)} interventions for campaign {campaign_id}")
        return interventions

    except Exception as e:
        logger.error(f"Failed to get interventions: {e}", exc_info=True)
        return []


@router.patch("/campaigns/{campaign_id}/workflow-state")
async def update_campaign_workflow_state(campaign_id: str, request: Request):
    """
    Update campaign workflow_state JSON field
    Proxies to db-manager for database write operation

    Request body:
    {
        "path": "quantum_analysis",  # JSON path within workflow_state
        "data": {...},               # Data to merge or set
        "merge": true                # Whether to merge with existing (default: true)
    }

    Example usage for quantum results:
    {
        "path": "quantum_analysis",
        "data": {
            "job_id": "abc123",
            "s3_results_path": "s3://novo-intel/quantum/abc123/results.json",
            "status": "completed",
            "submitted_at": "2025-01-07T10:00:00Z",
            "completed_at": "2025-01-07T10:05:00Z"
        },
        "merge": true
    }
    """
    try:
        from .proxy import proxy_request

        # Get request body
        body = await request.json()

        # Proxy to db-manager for write operation
        result = await proxy_request(
            "db-manager",
            f"campaigns/{campaign_id}/workflow-state",
            request,
            method="PATCH",
            body=json.dumps(body).encode()
        )

        logger.info(f"Updated workflow_state for campaign {campaign_id} at path '{body.get('path')}'")
        return result

    except Exception as e:
        logger.error(f"Failed to update workflow_state: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: str, request: Request):
    """Delete a campaign permanently (for cleanup/testing)"""
    try:
        from .proxy import proxy_request
        from config import settings

        # Delete from db-manager (which handles write operations)
        result = await proxy_request(
            "db-manager",
            f"campaigns/{campaign_id}",
            request,
            method="DELETE"
        )

        return result
    except Exception as e:
        logger.error(f"Failed to delete campaign: {e}")
        raise HTTPException(status_code=500, detail=str(e))
