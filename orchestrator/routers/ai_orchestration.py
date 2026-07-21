"""
AI Orchestration Router for NovoMCP
Provides intelligent orchestration, project enrichment, and workflow suggestions using GPT-5
"""

import os
import sys
# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Dict, Any, List, Optional, Callable
import logging
import time
import json
import asyncio
from datetime import datetime
import sys
import os
import uuid
from random import randint
from random import uniform
import httpx
from monitoring.circuit_breaker import get_circuit_manager, CircuitOpenException

# Queue backend: AWS SQS. Optional queue-name remapping via
# QUEUE_NAME_OVERRIDES so deployments can route legacy names to new
# queues without changing call sites.
import boto3

AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
QUEUE_NAME_OVERRIDES: dict = {}
_sqs_url_cache: dict = {}


def _resolve_queue_url(queue_name: str) -> str:
    """Resolve and cache the SQS queue URL for a given queue name.
    Applies QUEUE_NAME_OVERRIDES so deployments can rewrite queue names."""
    canonical = QUEUE_NAME_OVERRIDES.get(queue_name, queue_name)
    if canonical in _sqs_url_cache:
        return _sqs_url_cache[canonical]
    sqs = boto3.client('sqs', region_name=AWS_REGION)
    resp = sqs.get_queue_url(QueueName=canonical)
    _sqs_url_cache[canonical] = resp['QueueUrl']
    return resp['QueueUrl']


def send_to_queue(queue_name: str, message_body: dict) -> str:
    """Send message to SQS."""
    sqs = boto3.client('sqs', region_name=AWS_REGION)
    queue_url = _resolve_queue_url(queue_name)
    response = sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(message_body),
    )
    return response['MessageId']
from monitoring.metrics import get_metrics_collector

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.azure_openai_client import AzureOpenAIClient
from ai.intent_recognizer import IntentRecognizer
from ai.project_enricher import ProjectEnricher
from ai.orchestration_planner import OrchestrationPlanner
from ai.campaign_decision_engine import CampaignDecisionEngine
from ai.continuous_learning_system import ContinuousLearningSystem
from ai.literature_monitor import LiteratureMonitor
from service_config import get_service_config
from config import settings
from core.db_helper import execute_sql
from fastapi import WebSocket, WebSocketDisconnect
from typing import Set

logger = logging.getLogger(__name__)

DEFAULT_CAMPAIGN_METRICS = {
    "molecules_generated": 0,
    "successful_leads": 0,
    "experiments_run": 0,
    "learning_patterns": 0,
}


def normalize_campaign_metrics(raw_metrics: Any) -> Dict[str, Any]:
    """Normalize campaign metrics to ensure all required fields are present."""
    if not isinstance(raw_metrics, dict):
        raw_metrics = {}
    return {**DEFAULT_CAMPAIGN_METRICS, **raw_metrics}

from core.rate_limiter import rate_limit
router = APIRouter(prefix="/ai", tags=["AI Orchestration"], dependencies=[Depends(rate_limit("ai_orchestration"))])

service_config_manager = get_service_config()

# Initialize AI components
azure_client = AzureOpenAIClient()
intent_recognizer = IntentRecognizer(azure_client)
project_enricher = ProjectEnricher(azure_client)
orchestration_planner = OrchestrationPlanner(azure_client)
campaign_decision_engine = CampaignDecisionEngine(azure_client)
continuous_learning_system = ContinuousLearningSystem(azure_client)
literature_monitor = LiteratureMonitor(azure_client)

# WebSocket connection manager for real-time updates
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, campaign_id: str):
        await websocket.accept()
        if campaign_id not in self.active_connections:
            self.active_connections[campaign_id] = set()
        self.active_connections[campaign_id].add(websocket)

    def disconnect(self, websocket: WebSocket, campaign_id: str = None):
        if campaign_id and campaign_id in self.active_connections:
            self.active_connections[campaign_id].discard(websocket)

    async def send_update(self, campaign_id: str, message: dict):
        if campaign_id in self.active_connections:
            disconnected = set()
            for connection in self.active_connections[campaign_id]:
                try:
                    await connection.send_json(message)
                except:
                    disconnected.add(connection)
            # Remove disconnected websockets
            self.active_connections[campaign_id] -= disconnected

manager = ConnectionManager()

# Global WebSocket connection manager for dashboard-wide updates
class GlobalConnectionManager:
    def __init__(self):
        # Track all active global websocket connections
        self.connections: Set[WebSocket] = set()
        # Optional per-connection subscriptions (campaign_ids or event types)
        self.subscriptions: Dict[WebSocket, Dict[str, Any]] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.connections.add(websocket)
        # Default subscription is subscribe_all
        self.subscriptions[websocket] = {
            "all": True,
            "campaign_ids": set(),
            "events": set(["all"])  # event type filtering (optional)
        }

    def disconnect(self, websocket: WebSocket):
        self.connections.discard(websocket)
        if websocket in self.subscriptions:
            del self.subscriptions[websocket]

    def subscribe_all(self, websocket: WebSocket):
        if websocket in self.subscriptions:
            self.subscriptions[websocket]["all"] = True
            self.subscriptions[websocket]["campaign_ids"] = set()

    def subscribe_campaigns(self, websocket: WebSocket, campaign_ids: List[str]):
        if websocket in self.subscriptions:
            self.subscriptions[websocket]["all"] = False
            self.subscriptions[websocket]["campaign_ids"] = set(campaign_ids or [])

    def subscribe_events(self, websocket: WebSocket, events: List[str]):
        if websocket in self.subscriptions:
            self.subscriptions[websocket]["events"] = set(events or ["all"]) or set(["all"])

    async def broadcast(self, message: Dict[str, Any]):
        """Broadcast message to all global connections respecting campaign filters"""
        disconnected: Set[WebSocket] = set()

        # Log broadcast attempt with connection count
        event_type = message.get("type", "unknown")
        connection_count = len(self.connections)
        logger.info(f"Broadcasting {event_type} to {connection_count} connections")

        # Determine campaign context for filtering
        msg_campaign_id = None
        try:
            msg_campaign_id = message.get("data", {}).get("campaign_id")
        except Exception:
            msg_campaign_id = None

        for ws in list(self.connections):
            try:
                # Apply basic campaign filtering if specified
                sub = self.subscriptions.get(ws, {"all": True, "campaign_ids": set()})
                if msg_campaign_id and not sub.get("all", True):
                    if msg_campaign_id not in sub.get("campaign_ids", set()):
                        continue
                await ws.send_json(message)
            except Exception:
                disconnected.add(ws)

        # Clean up disconnected sockets
        for ws in disconnected:
            self.disconnect(ws)


global_ws_manager = GlobalConnectionManager()

@router.post("/orchestrate")
async def orchestrate(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Natural language orchestration with intent recognition using GPT-5.
    Analyzes user request and intelligently routes to appropriate services.
    
    Request body:
    {
        "request": "Generate 10 drug-like molecules for COVID-19",
        "context": {
            "user_id": "uuid",
            "org_id": "uuid",
            "conversation_id": "uuid"
        }
    }
    """
    try:
        start_time = time.time()
        
        user_request = request.get("request")
        context = request.get("context", {})
        
        if not user_request:
            raise HTTPException(status_code=400, detail="Request text is required")
        
        # Step 1: Recognize intent using GPT-5
        intent_result = await intent_recognizer.recognize(user_request, context)
        
        if not intent_result.get("success"):
            logger.error(f"Intent recognition failed: {intent_result.get('error')}")
            return {
                "success": False,
                "error": "Failed to understand request",
                "fallback": "Please try rephrasing your request"
            }
        
        intent = intent_result.get("intent")
        entities = intent_result.get("entities", {})
        
        # Step 2: Plan orchestration based on intent
        orchestration_plan = await orchestration_planner.plan(
            intent=intent,
            entities=entities,
            context=context
        )
        
        if not orchestration_plan.get("success"):
            return {
                "success": False,
                "error": "Failed to create orchestration plan",
                "intent": intent,
                "entities": entities
            }
        
        # Step 3: Execute orchestration plan
        service_calls = orchestration_plan.get("service_calls", [])
        results = []
        
        # Execute service calls (in parallel where possible)
        parallel_calls = [call for call in service_calls if not call.get("depends_on")]
        sequential_calls = [call for call in service_calls if call.get("depends_on")]
        
        # Execute parallel calls
        if parallel_calls:
            parallel_tasks = []
            for call in parallel_calls:
                # Use real service calls in production, fallback to mock for testing
                use_mock = os.getenv("USE_MOCK_SERVICES", "false").lower() == "true"
                if use_mock:
                    parallel_tasks.append(_mock_service_call(call))
                else:
                    parallel_tasks.append(_real_service_call(call))

            parallel_results = await asyncio.gather(*parallel_tasks)
            results.extend(parallel_results)

        # Execute sequential calls
        for call in sequential_calls:
            use_mock = os.getenv("USE_MOCK_SERVICES", "false").lower() == "true"
            if use_mock:
                result = await _mock_service_call(call)
            else:
                result = await _real_service_call(call)
            results.append(result)
        
        # Step 4: Aggregate results
        aggregated_response = {
            "success": True,
            "intent": intent,
            "entities": entities,
            "orchestration_plan": orchestration_plan.get("plan"),
            "results": results,
            "summary": orchestration_plan.get("summary"),
            "ai_metadata": {
                "model": azure_client.deployment_name,
                "tokens": {
                    "input": intent_result.get("tokens", {}).get("input", 0) + 
                            orchestration_plan.get("tokens", {}).get("input", 0),
                    "output": intent_result.get("tokens", {}).get("output", 0) + 
                             orchestration_plan.get("tokens", {}).get("output", 0)
                },
                "response_time_ms": int((time.time() - start_time) * 1000),
                "confidence": intent_result.get("confidence", 0.0)
            }
        }
        
        return aggregated_response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Orchestration error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/enrich-project")
async def enrich_project(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    AI-powered project enrichment using GPT-5.
    Analyzes project data and generates comprehensive insights and recommendations.
    
    Request body:
    {
        "project_id": "uuid",
        "name": "KRAS G12C Inhibitor Development",
        "description": "Developing selective inhibitors...",
        "comprehensive_analysis": true
    }
    """
    try:
        start_time = time.time()
        
        project_id = request.get("project_id")
        name = request.get("name")
        description = request.get("description")
        comprehensive = request.get("comprehensive_analysis", False)
        
        if not project_id or not name:
            raise HTTPException(status_code=400, detail="Project ID and name are required")
        
        # Use GPT-5 to enrich the project
        project_data = {
            "project_id": project_id,
            "name": name,
            "description": description,
            "therapeutic_area": request.get("therapeutic_area")
        }
        
        enriched_project = await project_enricher.enrich(project_data)
        
        # Build enrichment result with success flag
        enrichment_result = {
            "success": True,
            "enrichment": enriched_project
        }
        
        if not enriched_project:
            return {
                "success": False,
                "error": enrichment_result.get("error", "Enrichment failed"),
                "project_id": project_id
            }
        
        # Save enriched data back to database
        try:
            # Prepare metadata with enrichment results
            enriched_metadata = {
                "therapeutic_area": enriched_project.get("therapeutic_area", request.get("therapeutic_area")),
                "tags": enriched_project.get("tags", []),
                "drug_modality": enriched_project.get("drug_modality"),
                "development_stage": enriched_project.get("development_stage"),
                "ai_enriched": True,
                "enrichment_version": "1.0",
                "enriched_at": datetime.now().isoformat(),
                "completeness_score": enriched_project.get("metadata", {}).get("completeness_score"),
                "team_size": request.get("team_size"),
                "budget": request.get("budget")
            }
            
            # Update project in database
            async with httpx.AsyncClient() as client:
                update_response = await client.post(
                    "https://db-manager-alb-secure-2092039289.us-east-1.elb.amazonaws.com/write",
                    json={
                        "database": "research",
                        "table": "projects",
                        "operation": "update",
                        "data": {
                            "metadata": json.dumps(enriched_metadata)
                        },
                        "where": {
                            "id": project_id
                        }
                    },
                    headers={
                        "X-API-Key": os.getenv("DASHBOARD_AGGREGATOR_API_KEY", "")
                    },
                    timeout=10.0
                )
                
                database_updated = update_response.status_code == 200
                
        except Exception as e:
            logger.warning(f"Failed to save enrichment to database: {str(e)}")
            database_updated = False
        
        # Return enriched project data
        return {
            "success": True,
            "enriched_project": enriched_project,
            "database_updated": database_updated,
            "ai_metadata": {
                "model": "gpt-5",
                "enrichment_version": "1.0"
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Project enrichment error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/generate-campaign-config")
async def generate_campaign_config(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    AI-powered campaign configuration generator using GPT-5 + Pinecone literature analysis.
    Intelligently determines optimal molecular constraints, ADMET filters, and compute resources
    based on minimal user input (campaign intent).

    Request body:
    {
        "name": "KRAS G12C Discovery Campaign",
        "campaignType": "Oncology",
        "targetProtein": "6OIM",  # Optional PDB ID
        "goalDescription": "Discover covalent KRAS G12C inhibitors with sub-micromolar activity...",
        "targetMolecules": 50,
        "minActivityThreshold": 1000,  # nM
        "keyProperties": "oral bioavailability, selectivity, metabolic stability"
    }

    Returns AI-generated config with reasoning:
    {
        "constraints": {
            "molecular": {"mw": {"min": 400, "max": 700}, ...},
            "admet": {"hepatotoxicity": true, ...}
        },
        "dataSources": {
            "searchKeywords": [...],
            "therapeuticArea": "...",
            "modality": "...",
            "internalLibraries": [],
            "literatureUpdateMode": "daily"
        },
        "autonomy": {
            "level": "full",
            "interventionTriggers": ["budget_80_percent", "no_progress_48h", ...],
            "notificationChannels": ["email", "dashboard"],
            "humanApprovalRequired": []
        },
        "reasoning": {
            "molecular": "MW: 400-700 Da (avg KRAS inhibitor: 580 Da)...",
            "admet": "Hepatotoxicity screening enabled for oncology safety...",
            "dataSources": "Daily literature updates for fast-moving oncology field...",
            "autonomy": "Full autonomy with breakthrough + stall alerts...",
            "literatureSources": 15
        }
    }
    """
    try:
        start_time = time.time()

        # Debug: Log the raw request payload
        logger.info(f"🚀 Received campaign config request: {request}")
        logger.info(f"📋 Request keys: {list(request.keys()) if isinstance(request, dict) else 'Not a dict'}")

        # Extract campaign intent (be tolerant to variants and infer when possible)
        def _get_any(d: Dict[str, Any], keys, default=""):
            for k in keys:
                if d.get(k) not in (None, ""):
                    return d.get(k)
            return default

        raw = request or {}

        name = _get_any(raw, ["name", "campaign_name", "campaignName"]) or ""
        campaign_type = _get_any(raw, ["campaignType", "campaign_type", "therapeuticArea"]) or ""
        target_protein = _get_any(raw, ["targetProtein", "target_protein", "pdb", "pdb_id"]) or ""
        goal_description = _get_any(raw, ["goalDescription", "goal", "objective", "description"]) or ""

        # Normalize numeric fields with defaults
        target_molecules = raw.get("targetMolecules", raw.get("target_molecules", 50)) or 50
        min_activity_threshold = raw.get("minActivityThreshold", raw.get("min_activity_threshold", 1000)) or 1000

        # keyProperties may arrive as comma string or array; normalize to comma string for prompt
        key_properties_val = raw.get("keyProperties", raw.get("key_properties", ""))
        if isinstance(key_properties_val, list):
            key_properties = ", ".join([str(x) for x in key_properties_val if str(x).strip()])
        else:
            key_properties = key_properties_val or ""

        # Lightweight inference if some required fields missing
        goal_lower = (goal_description or "").lower()
        name_lower = (name or "").lower()

        if not campaign_type:
            if any(w in goal_lower for w in ["cancer", "oncology", "tumor", "lung"]):
                campaign_type = "Oncology"
            elif any(w in goal_lower for w in ["cns", "neurology", "brain"]):
                campaign_type = "CNS"
            elif any(w in goal_lower for w in ["antibacterial", "antibiotic", "infection", "resistance"]):
                campaign_type = "Anti-infective"

        if not name and goal_description:
            # Construct a descriptive fallback name
            if "kras" in goal_lower:
                name = "KRAS Discovery Campaign"
            else:
                name = "Drug Discovery Campaign"

        if not goal_description and name:
            # Minimal goal from name if user only provided a title
            goal_description = f"Discover and optimize candidates for {name}"

        # Debug: Log what was extracted
        logger.info(f"📝 Extracted values - name: '{name}', campaignType: '{campaign_type}', goalDescription: '{goal_description}'")

        # Final validation after inference
        missing = []
        if not name:
            missing.append("name")
        if not campaign_type:
            missing.append("campaignType")
        if not goal_description:
            missing.append("goalDescription")
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required fields: {', '.join(missing)}"
            )

        logger.info(f"Generating AI config for campaign: {name} ({campaign_type})")

        # Step 1: Search relevant literature via Pinecone (with graceful fallback)
        literature = []
        try:
            from core.pinecone_client import get_pinecone_client
            pinecone_client = get_pinecone_client()

            # Build literature search query
            literature_query = f"{campaign_type} {target_protein} {goal_description}"
            literature = await pinecone_client.search_literature(
                query=literature_query,
                filters={"year": {"$gte": 2020}},  # Recent papers only
                top_k=15
            )
            logger.info(f"Found {len(literature)} relevant papers for campaign config")
        except Exception as e:
            # If Pinecone or embeddings are not configured, continue without literature context.
            logger.warning(f"Literature search unavailable; proceeding without it: {e}")
            literature = []

        # Step 2: Optionally fetch PDB structure info if target_protein provided
        pdb_info = None
        if target_protein:
            try:
                from utils.pdb_cache import get_pdb
                pdb_content = await get_pdb(target_protein)
                # Extract basic info from PDB header
                pdb_lines = pdb_content.split('\n')
                pdb_title = ""
                for line in pdb_lines[:50]:  # Check first 50 lines for TITLE
                    if line.startswith("TITLE"):
                        pdb_title = line[10:].strip()
                        break
                pdb_info = {
                    "pdb_id": target_protein,
                    "title": pdb_title,
                    "size_kb": len(pdb_content) // 1024
                }
                logger.info(f"Fetched PDB {target_protein}: {pdb_title}")
            except Exception as e:
                logger.warning(f"Could not fetch PDB {target_protein}: {e}")
                pdb_info = None

        # Step 3: Extract explicit MW range if provided in goal, campaign name, or key properties
        import re
        mw_hint = ""
        extracted_mw_min = None
        extracted_mw_max = None

        # Search name, goal description, AND key properties for MW constraints
        search_text = f"{name} {goal_description} {key_properties or ''}"

        # Pattern 1: Range format "50-200 Da" or "50-200Da"
        range_match = re.search(r'(\d+)\s*-\s*(\d+)\s*Da', search_text, re.IGNORECASE)
        if range_match:
            extracted_mw_min = int(range_match.group(1))
            extracted_mw_max = int(range_match.group(2))
            logger.info(f"Detected explicit MW range: {extracted_mw_min}-{extracted_mw_max} Da (from: {search_text})")

        # Pattern 2: Max-only format "MW<=200", "MW<200", "MW≤200"
        if not extracted_mw_max:
            max_match = re.search(r'MW\s*[<≤]\s*=?\s*(\d+)', search_text, re.IGNORECASE)
            if max_match:
                extracted_mw_max = int(max_match.group(1))
                logger.info(f"Detected explicit MW max: {extracted_mw_max} Da (from: {search_text})")

        # Pattern 3: Min-only format "MW>=100", "MW>100", "MW≥100"
        if not extracted_mw_min:
            min_match = re.search(r'MW\s*[>≥]\s*=?\s*(\d+)', search_text, re.IGNORECASE)
            if min_match:
                extracted_mw_min = int(min_match.group(1))
                logger.info(f"Detected explicit MW min: {extracted_mw_min} Da (from: {search_text})")

        # Build hint for AI with extracted constraints
        if extracted_mw_min or extracted_mw_max:
            min_str = str(extracted_mw_min) if extracted_mw_min else "auto"
            max_str = str(extracted_mw_max) if extracted_mw_max else "auto"
            mw_hint = f"\n- **EXPLICIT MW REQUIREMENT**: User specified MW constraints: min={min_str}, max={max_str}. You MUST use these exact values for molecular.mw"

        # Detect fragment keywords in name OR goal
        search_text_lower = search_text.lower()
        is_fragment = any(kw in search_text_lower for kw in ['fragment', 'fbdd', 'fragment-based', 'fragment-sized', 'hinge binder fragment'])
        if is_fragment:
            mw_hint += "\n- **FRAGMENT CAMPAIGN DETECTED**: This is a fragment screening campaign. Use fragment MW ranges (100-250 Da unless explicitly specified), NOT drug-like ranges!"
            logger.info("Fragment campaign detected - will enforce fragment MW constraints")

        # Build GPT-5 prompt with literature context
        literature_summaries = []
        for paper in literature[:10]:  # Use top 10 papers
            literature_summaries.append({
                "title": paper.get("title", ""),
                "year": paper.get("year", ""),
                "relevance": round(paper.get("relevance", 0), 2)
            })

        from core.prompt_sanitizer import sanitize_for_prompt
        _s_name = sanitize_for_prompt(name, "name", 200)
        _s_type = sanitize_for_prompt(campaign_type, "campaign_type", 100)
        _s_target = sanitize_for_prompt(target_protein or 'Not specified', "target_protein", 200)
        _s_goal = sanitize_for_prompt(goal_description, "goal_description", 2000)
        _s_props = sanitize_for_prompt(key_properties or 'Not specified', "key_properties", 500)

        gpt_prompt = f"""You are a drug discovery AI expert. Analyze the following campaign intent and generate optimal molecular constraints, ADMET filters, and computational resource estimates.

**Campaign Intent:**
- Name: {_s_name}
- Type: {_s_type}
- Target Protein: {_s_target}
- Goal: {_s_goal}
- Target Molecules: {target_molecules}
- Min Activity Threshold: {min_activity_threshold} nM
- Key Properties: {_s_props}{mw_hint}

**Target Protein Info:**
{json.dumps(pdb_info, indent=2) if pdb_info else 'No PDB structure provided'}

**Relevant Literature (Top 10 of {len(literature)}):**
{json.dumps(literature_summaries, indent=2)}

**IMPORTANT - Target Protein Resolution:**
- Your FIRST task is to analyze the 'Target Protein' field from the Campaign Intent.
- If the value is a descriptive name (e.g., 'Cyclin-Dependent Kinase 2 (CDK2) ATP-binding site hinge region'), you MUST resolve it to the most relevant 4-character PDB ID (e.g., '1E9H'). Use the campaign goal and literature to determine the best PDB ID.
- The final generated configuration that you output MUST contain the resolved 4-character PDB ID in a new `targetProtein` field within the `dataSources` object.
- If you cannot resolve the name to a PDB ID with high confidence, set `dataSources.targetProtein` to an empty string and add a note in the `reasoning.dataSources` section explaining why.

**Task:** Generate comprehensive campaign configuration with the following structure:

1. **Molecular Property Constraints**:
   - MW (min/max in Da): Consider typical drug-like range and therapeutic area norms
   - LogP (min/max): Balance lipophilicity for target class
   - HBD (max): Hydrogen bond donors
   - HBA (max): Hydrogen bond acceptors
   - TPSA (max in Ų): Topological polar surface area

   **IMPORTANT - Molecular Weight Guidelines (PRIORITY ORDER)**:

   **PRIORITY 1 - Fragment Screening** (ALWAYS OVERRIDE OTHER CATEGORIES IF MENTIONED):
   - Keywords: "fragment", "fragment-based", "FBDD", "small fragment", "hinge binder fragment", "fragment-sized", "150-250 Da", "100-250 Da"
   - MW: 100-250 Da (use user-specified range if provided, e.g., "150-250 Da" → min: 150, max: 250)
   - LogP: -0.5 to 3.0
   - HBD: max 3
   - HBA: max 6
   - TPSA: max 90

   **PRIORITY 2 - Other Modalities** (only use if NO fragment keywords present):
   - **Lead-like compounds** (keywords: "lead-like", "hit-to-lead"): MW 200-400 Da
   - **Drug-like compounds**: MW 300-600 Da
   - **Oncology kinase inhibitors** (KRAS, EGFR, CDK2, etc.): MW 400-750 Da (typical approved drugs: 400-650 Da, covalent inhibitors often 500-700 Da)
   - **Antibiotics**: MW 300-600 Da
   - **CNS drugs**: MW 200-450 Da with stricter LogP (-0.5 to 3.5)

   **CRITICAL RULE**: Check the goal description for ANY fragment-related keywords FIRST. If found, IGNORE the therapeutic area (Oncology, CNS, etc.) for MW/LogP ranges and ONLY use fragment ranges. Fragment campaigns have fundamentally different molecular property requirements than full drug discovery!

2. **ADMET & Safety Filters**:
   - hepatotoxicity: boolean (enable for oncology/metabolic)
   - cyp450: boolean (check drug-drug interactions)
   - bbb: boolean (required only for CNS drugs)
   - solubility: number (LogS, typically -4 to -2)

3. **Data Sources**:
   - searchKeywords: Array of 5-8 relevant search terms for literature feeds
   - therapeuticArea: Primary therapeutic area (Oncology, Anti-infective, CNS, etc.)
   - modality: Drug modality (Small molecule, Antibody, etc.)
   - internalLibraries: Array of S3 paths to internal compound libraries (usually empty unless user specified)
   - literatureUpdateMode: "daily" (for fast-moving fields) or "onstart" (for stable/mature targets)

4. **Autonomy Configuration**:
   - level: "full" (experienced teams, clear objectives), "guided" (balanced, most campaigns), or "supervised" (high-stakes, novel targets)
   - interventionTriggers: Array of events requiring notification. Choose from:
     * "breakthrough_molecule" (potential therapeutic discovered)
     * "no_progress_48h" (campaign stalled)
     * "credits_80_percent" (80% credits consumed)
     * "credits_100_percent" (credits exhausted, campaign pauses)
     * "confidence_below_60" (AI confidence drop)
     * "anomaly_detected" (unusual patterns)
     * "objective_reached" (success criteria met)
   - notificationChannels: Choose from ["email", "dashboard", "slack", "sms"] (email + dashboard recommended)
   - humanApprovalRequired: Array of actions requiring approval. Choose from:
     * "synthesis_recommendations" (supervised mode)
     * "compound_purchases" (supervised mode)
     * "assay_selections" (guided/supervised)
     * "strategy_pivots" (supervised mode)
     * "campaign_termination" (all modes)
     * [] (empty for full autonomy)

5. **Reasoning**:
   - molecular: Explain MW/LogP choices with reference to typical drugs in this class
   - admet: Justify which ADMET filters are critical for this therapeutic area
   - dataSources: Explain literature update frequency choice and search strategy
   - autonomy: Justify autonomy level, intervention triggers, and approval requirements
   - literatureSources: Count of papers analyzed

**IMPORTANT RULES**:
- Budget and runtime are NO LONGER USED - campaigns now use org-level credits system
- For oncology/fast-moving fields: use "daily" literature updates + breakthrough alerts
- For mature/stable targets: use "onstart" literature updates
- Full autonomy = minimal approvals, Guided = assay approvals, Supervised = most approvals
- Always include "credits_80_percent" and "credits_100_percent" in intervention triggers
- Always include "breakthrough_molecule" and "no_progress_48h" triggers

**Output format (JSON only, no markdown):**
{{
  "constraints": {{
    "molecular": {{"mw": {{"min": 400, "max": 700}}, "logP": {{"min": 0, "max": 5}}, "hbd": {{"max": 5}}, "hba": {{"max": 10}}, "tpsa": {{"max": 140}}}},
    "admet": {{"hepatotoxicity": true, "cyp450": true, "bbb": false, "solubility": -3}}
  }},
  "dataSources": {{
    "targetProtein": "6OIM",
    "searchKeywords": ["KRAS G12C", "covalent inhibitor", "oncology", "kinase", "selectivity"],
    "therapeuticArea": "Oncology",
    "modality": "Small molecule",
    "internalLibraries": [],
    "literatureUpdateMode": "daily"
  }},
  "autonomy": {{
    "level": "full",
    "interventionTriggers": ["breakthrough_molecule", "no_progress_48h", "credits_80_percent", "credits_100_percent", "anomaly_detected"],
    "notificationChannels": ["email", "dashboard"],
    "humanApprovalRequired": []
  }},
  "reasoning": {{
    "molecular": "MW: 400-700 Da (avg approved KRAS inhibitor: 580 Da, covalent warheads typically add 50-100 Da). LogP: 0-5 for cell penetration. HBD/HBA/TPSA: Lipinski-compliant for oral bioavailability.",
    "admet": "Hepatotoxicity screening critical for oncology safety. CYP450 checks for drug-drug interactions common in cancer patients. BBB penetration not required (KRAS in peripheral tumors). Solubility -3 LogS for formulation.",
    "dataSources": "Daily literature updates recommended for fast-moving oncology field (new KRAS papers weekly). Search focuses on G12C-specific inhibitors and resistance mechanisms. No internal libraries specified.",
    "autonomy": "Full autonomy recommended for experienced oncology team. Breakthrough alerts for promising leads. Stall detection after 48h of no progress. Credit alerts at 80% and auto-pause at 100%. Anomaly detection for unexpected results. No human approval required for standard operations.",
    "literatureSources": {len(literature)}
  }}
}}
"""

        # Step 4: Call GPT-5 to generate config
        logger.info("Calling GPT-5 to generate campaign configuration...")
        gpt_response = await azure_client.complete(
            prompt=gpt_prompt,
            system_prompt="You are a drug discovery AI expert. Generate optimal campaign configurations based on scientific literature and best practices. Always output valid JSON only, with no markdown formatting.",
            temperature=0.3,  # Lower temperature for more consistent outputs
            max_tokens=2000,
            response_format={"type": "json_object"}
        )

        # Parse GPT-5 response
        try:
            # Check if API call was successful
            if not gpt_response.get("success"):
                raise ValueError(f"Azure OpenAI error: {gpt_response.get('error', 'Unknown error')}")

            # Extract JSON from response (handle markdown code blocks if present)
            response_text = gpt_response.get("response", "").strip()
            if response_text.startswith("```json"):
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif response_text.startswith("```"):
                response_text = response_text.split("```")[1].split("```")[0].strip()

            try:
                ai_config = json.loads(response_text)
            except json.JSONDecodeError:
                # Try to recover JSON from the response text if model added prose
                recovered = await azure_client.parse_json_response(response_text)
                if recovered is None:
                    raise
                ai_config = recovered

            # Validate structure
            if "constraints" not in ai_config or "reasoning" not in ai_config:
                raise ValueError("Missing required fields in GPT-5 response")

            # Post-generation validation: Enforce user-specified or fragment MW constraints
            if extracted_mw_min or extracted_mw_max or is_fragment:
                mol_constraints = ai_config.get("constraints", {}).get("molecular", {})
                mw_config = mol_constraints.get("mw", {})

                if extracted_mw_min or extracted_mw_max:
                    # User specified explicit MW constraints - enforce strictly
                    target_min = extracted_mw_min or mw_config.get("min", 100)
                    target_max = extracted_mw_max or mw_config.get("max", 250)

                    if mw_config.get("min") != target_min or mw_config.get("max") != target_max:
                        logger.warning(f"AI did not follow explicit MW requirement (min={target_min}, max={target_max}). Correcting...")
                        mol_constraints["mw"] = {"min": target_min, "max": target_max}
                elif is_fragment:
                    # Fragment campaign without explicit range - enforce 100-200 Da and fragment property window
                    logger.info("Fragment campaign detected → enforcing 100-200 Da and fragment windows")
                    mol_constraints["mw"] = {"min": 100, "max": 200}
                    mol_constraints["logP"] = {"min": -2.0, "max": 3.0}
                    mol_constraints["logp"] = {"min": -2.0, "max": 3.0}
                    mol_constraints["hbd"] = {"max": 3}
                    mol_constraints["hba"] = {"max": 5}
                    mol_constraints["tpsa"] = {"min": 20, "max": 70}
                    mol_constraints["rotatable_bonds"] = {"max": 4}
                    mol_constraints["aromatic_rings"] = {"min": 1, "max": 2}

                # Update config with corrected constraints
                if "constraints" not in ai_config:
                    ai_config["constraints"] = {}
                ai_config["constraints"]["molecular"] = mol_constraints
                logger.info(f"Fragment constraints enforced: MW {mol_constraints.get('mw')}")

            # For fragment campaigns, default to no drug-like forcing and fragments dataset; add CDK2 hinge pharmacophore hint
            if is_fragment:
                ai_constraints = ai_config.setdefault("constraints", {})
                ai_constraints["force_druglike"] = False
                ai_constraints.setdefault("allowed_elements", ["C","N","O","S","F","Cl","Br"])
                ai_config.setdefault("metadata", {})["dataset_preference"] = "fragments"

                # Add hinge pharmacophore hint for CDK2 targets
                tp = ai_config.get('dataSources', {}).get('targetProtein') or (target_protein or '')
                # Use available goal_description string here (goal_data is not in scope in this helper)
                goal_txt = str(goal_description or '').lower()
                if 'cdk2' in (tp or '').lower() or 'cdk2' in goal_txt:
                    ai_constraints.setdefault('pharmacophore', {})['hinge'] = {
                        'required': True,
                        'min_hbonds': 1,
                        'residues': ['LEU83', 'GLU81'],
                        'optional_contacts': ['VAL18'],
                        'allow_conserved_water': True
                    }

            logger.info(f"AI config generated successfully in {time.time() - start_time:.2f}s")

            # Build constraint locks metadata for frontend to store in campaign
            constraint_locks = {}
            if extracted_mw_min:
                constraint_locks["molecular.mw.min"] = True
            if extracted_mw_max:
                constraint_locks["molecular.mw.max"] = True

            return {
                "success": True,
                "config": ai_config,
                "metadata": {
                    "model": "gpt-5",
                    "literatureSources": len(literature),
                    "pdbAnalyzed": pdb_info is not None,
                    "generationTimeMs": int((time.time() - start_time) * 1000),
                    "constraint_locks": constraint_locks,  # Auto-locks for user-specified constraints
                    "user_specified_mw": {
                        "min": extracted_mw_min,
                        "max": extracted_mw_max
                    }
                }
            }

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse GPT-5 response as JSON: {e}")
            try:
                logger.error(f"Response: {str(gpt_response)[:500]}")
            except Exception:
                pass
            raise HTTPException(
                status_code=500,
                detail=f"Failed to parse AI response: {str(e)}"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Campaign config generation error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/suggest-workflow")
async def suggest_workflow(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Intelligent workflow suggestions using GPT-5.
    Analyzes goals and constraints to recommend optimal workflows.
    
    Request body:
    {
        "goal": "Optimize lead compound for ADMET",
        "current_molecules": ["SMILES1", "SMILES2"],
        "constraints": ["BBB penetration", "oral bioavailability"]
    }
    """
    try:
        start_time = time.time()
        
        goal = request.get("goal")
        current_molecules = request.get("current_molecules", [])
        constraints = request.get("constraints", [])
        
        if not goal:
            raise HTTPException(status_code=400, detail="Goal is required")
        
        # Use GPT-5 to suggest workflow
        if not azure_client.available:
            return {
                "success": False,
                "error": "AI service not available",
                "fallback": "manual_workflow_required"
            }
        
        # Create prompt for workflow suggestion
        system_prompt = """You are an expert in drug discovery workflows and NovoMCP platform.
        Suggest optimal workflows based on goals and constraints.
        Return a structured workflow with clear steps and service recommendations."""
        
        prompt = f"""Suggest an optimal workflow for this goal:
        Goal: {goal}
        Current Molecules: {len(current_molecules)} molecules available
        Constraints: {', '.join(constraints) if constraints else 'None specified'}
        
        Provide a detailed workflow with:
        1. Sequential steps to achieve the goal
        2. Services to use at each step
        3. Expected outcomes
        4. Risk factors
        5. Alternative approaches
        
        Format as JSON with structure:
        {{
            "workflow_name": "...",
            "estimated_duration": "...",
            "steps": [...],
            "services": [...],
            "expected_outcomes": [...],
            "risks": [...],
            "alternatives": [...]
        }}"""
        
        # Call GPT-5
        ai_response = await azure_client.complete(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.4,
            max_tokens=1500
        )
        
        if not ai_response.get("success"):
            return {
                "success": False,
                "error": "Failed to generate workflow suggestion",
                "ai_error": ai_response.get("error")
            }
        
        # Parse the response
        response_text = ai_response.get("response", "")
        workflow_data = await azure_client.parse_json_response(response_text)
        
        if not workflow_data:
            # Fallback to text response
            workflow_data = {
                "workflow_name": "Custom ADMET Optimization",
                "description": response_text,
                "steps": []
            }
        
        return {
            "success": True,
            "goal": goal,
            "workflow": workflow_data,
            "constraints_applied": constraints,
            "molecule_count": len(current_molecules),
            "ai_metadata": {
                "model": azure_client.deployment_name,
                "tokens": ai_response.get("tokens", {}),
                "response_time_ms": int((time.time() - start_time) * 1000),
                "confidence": 0.9
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Workflow suggestion error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/status")
async def ai_status() -> Dict[str, Any]:
    """Get AI service status and capabilities"""
    return {
        "service": "novomcp-ai",
        "status": "operational" if azure_client.available else "degraded",
        "azure_openai": azure_client.get_status(),
        "capabilities": {
            "orchestration": azure_client.available,
            "intent_recognition": azure_client.available,
            "project_enrichment": azure_client.available,
            "workflow_suggestions": azure_client.available
        },
        "endpoints": [
            "/ai/orchestrate",
            "/ai/enrich-project",
            "/ai/suggest-workflow",
            "/ai/status"
        ],
        "model": azure_client.deployment_name,
        "context_window": "400K tokens" if "gpt-5" in azure_client.deployment_name else "128K tokens"
    }

@router.post("/campaign/create")
async def create_campaign(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create and initialize an autonomous drug discovery campaign.
    Campaigns run continuously with AI-driven decision making.

    Request body:
    {
        "name": "KRAS G12C Inhibitor Campaign",
        "goal": "Discover selective KRAS G12C inhibitors",
        "constraints": {
            "budget": 100000,
            "timeline": "3 months",
            "safety_threshold": 0.8
        },
        "autonomy_level": "full_auto",
        "tenant_id": "org-uuid"
    }
    """
    try:
        import uuid
        campaign_id = str(uuid.uuid4())
        created_at_ts = datetime.utcnow()
        created_at_db = created_at_ts.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

        # Prepare campaign data (keep original structures for downstream use)
        goal_data = request.get('goal')
        constraints_data = request.get('constraints', {})
        metadata_data = request.get('metadata', {})
        # Ensure chat_history is present if initial_chat_transcript was provided
        try:
            if isinstance(metadata_data, dict):
                transcript = metadata_data.get('initial_chat_transcript')
                if transcript and not metadata_data.get('chat_history'):
                    if isinstance(transcript, list):
                        metadata_data['chat_history'] = transcript
        except Exception:
            pass
        data_sources_data = request.get('dataSources', {})
        autonomy_data = request.get('autonomy', {})
        target_protein = request.get('target_protein')
        quantum_enabled = request.get('quantum_enabled', False)

        # Normalize goal to always be an object for workflow engine compatibility
        # Workflow engine expects goal.successMetrics (quality_gates.py:536, workflow_engine.py:520)
        if isinstance(goal_data, str):
            # Legacy format: goal is a string (smoke tests, simple campaigns)
            goal_data = {
                "description": goal_data,
                "successMetrics": {
                    "targetMolecules": 10,
                    "minActivityThreshold": 0.7,
                    "targetProperties": []
                }
            }
        elif isinstance(goal_data, dict):
            # New format: goal is object from wizard UI, ensure successMetrics exists
            if "successMetrics" not in goal_data:
                goal_data["successMetrics"] = {
                    "targetMolecules": goal_data.get("targetMolecules", 10),
                    "minActivityThreshold": goal_data.get("minActivityThreshold", 0.7),
                    "targetProperties": goal_data.get("targetProperties", [])
                }
        elif goal_data is None:
            # Missing goal, provide defaults
            goal_data = {
                "description": "Therapeutic discovery campaign",
                "successMetrics": {
                    "targetMolecules": 10,
                    "minActivityThreshold": 0.7,
                    "targetProperties": []
                }
            }

        # Extract autonomy_level from autonomy object or fallback to direct field
        autonomy_level = autonomy_data.get('level', 'guided') if isinstance(autonomy_data, dict) else request.get('autonomy_level', 'guided')

        # AI-powered constraint extraction: Use GPT to intelligently determine constraints from campaign intent
        # IMPORTANT: This should run BEFORE applying hardcoded defaults
        if not constraints_data.get('molecular') or not data_sources_data:
            try:
                logger.info(f"Calling AI config generator to extract constraints from campaign intent")
                ai_config_request = {
                    "name": request.get('name'),
                    "campaignType": data_sources_data.get('therapeuticArea') or request.get('campaign_type'),
                    "targetProtein": target_protein,
                    "goalDescription": goal_data.get('description') if isinstance(goal_data, dict) else str(goal_data) if goal_data else "",
                    "targetMolecules": goal_data.get('successMetrics', {}).get('targetMolecules', 10) if isinstance(goal_data, dict) else 10,
                    "minActivityThreshold": goal_data.get('successMetrics', {}).get('minActivityThreshold', 1000) if isinstance(goal_data, dict) else 1000,
                    "keyProperties": request.get('key_properties', "")
                }

                # Call AI config generator
                ai_config_response = await generate_campaign_config(ai_config_request)
                ai_config = ai_config_response.get("config", {})

                # Merge AI-generated config with user-provided config (user overrides AI)
                if 'molecular' not in constraints_data and ai_config.get('constraints', {}).get('molecular'):
                    constraints_data['molecular'] = ai_config['constraints']['molecular']
                    logger.info(f"Applied AI-generated molecular constraints: {constraints_data['molecular']}")

                if not data_sources_data and ai_config.get('dataSources'):
                    data_sources_data = ai_config['dataSources']
                    logger.info(f"Applied AI-generated data sources: {data_sources_data.get('searchKeywords', [])}")

                # Use the resolved target protein from the AI
                if ai_config.get('dataSources', {}).get('targetProtein'):
                    resolved_target_protein = ai_config.get('dataSources', {}).get('targetProtein')
                    if resolved_target_protein and resolved_target_protein != target_protein:
                        logger.info(f"AI resolved target protein from '{target_protein}' to '{resolved_target_protein}'")
                        target_protein = resolved_target_protein

                if not autonomy_data and ai_config.get('autonomy'):
                    autonomy_data = ai_config['autonomy']
                    autonomy_level = autonomy_data.get('level', 'guided')
                    logger.info(f"Applied AI-generated autonomy config: level={autonomy_level}")

            except Exception as e:
                logger.warning(f"AI config generation failed, falling back to defaults: {e}")

        # Normalize constraints for workflow engine compatibility
        # Ensure nested structure exists: constraints.molecular, constraints.admet, etc.
        if not isinstance(constraints_data, dict):
            constraints_data = {}

        # Ensure molecular constraints exist (required by MolecularConstraintsGate)
        # Only apply defaults if AI config didn't provide them
        if 'molecular' not in constraints_data:
            logger.warning("No molecular constraints provided by AI or user, using fallback defaults")
            constraints_data['molecular'] = {
                "mw": {"min": 200, "max": 500},
                "logP": {"min": -0.4, "max": 5.6},
                "hbd": {"max": 5},
                "hba": {"max": 10},
                "tpsa": {"max": 140}
            }

        # Ensure ADMET constraints exist
        if 'admet' not in constraints_data:
            constraints_data['admet'] = {
                "hepatotoxicity": True,
                "cyp450": True,
                "bbb": False,
                "solubility": -4
            }

        # Preserve legacy budget/timeline fields alongside new structure
        if 'budget' in request.get('constraints', {}):
            constraints_data['budget_limit_usd'] = constraints_data.get('budget_limit_usd', request['constraints']['budget'])
        if 'timeline_days' in request.get('constraints', {}):
            constraints_data['timeline_days'] = constraints_data.get('timeline_days', request['constraints']['timeline_days'])

        # Initialize workflow state and circuit breaker
        workflow_state = {
            'current_phase': 'generation',
            'phase_iteration': 0,
            'history': [],
            'molecules': []
        }
        circuit_breaker_state = {
            'state': 'closed',
            'failure_count': 0,
            'trip_time': None,
            'phase_failures': {}
        }

        campaign_data = {
            'id': campaign_id,
            'tenant_id': request.get('tenant_id') or request.get('org_id') or 'public',
            'name': request.get('name') or f"Campaign {campaign_id[:8]}",
            'goal': goal_data,
            'constraints': constraints_data,
            'dataSources': data_sources_data,
            'autonomy': autonomy_data,
            'workflow_state': workflow_state,
            'circuit_breaker_state': circuit_breaker_state,
            'target_protein': target_protein,
            'autonomy_level': autonomy_level,
            'quantum_enabled': quantum_enabled,
            'status': 'active',
            'metadata': metadata_data,
            'created_at': created_at_ts.isoformat()
        }

        # Convert JSON fields to strings for SQL parameter compatibility
        def _jsonify(value):
            if isinstance(value, (dict, list)):
                return json.dumps(value)
            return value

        db_payload = {
            **campaign_data,
            'goal': _jsonify(goal_data),
            'constraints': _jsonify(constraints_data),
            'metadata': _jsonify(metadata_data),
            'dataSources': _jsonify(data_sources_data),
            'autonomy': _jsonify(autonomy_data),
            'workflow_state': _jsonify(workflow_state),
            'circuit_breaker_state': _jsonify(circuit_breaker_state),
            'created_at': created_at_db
        }

        # Prefer direct SQL insert when db-manager URL is missing/invalid or HTTP fails
        def _is_valid_url(u: Optional[str]) -> bool:
            try:
                return isinstance(u, str) and u.startswith("http") and "://" in u
            except Exception:
                return False

        async def _insert_campaign_via_sql():
            """Insert campaign directly into Azure SQL using pymssql paramstyle (%s)."""
            sql = (
                "INSERT INTO campaigns (id, tenant_id, name, goal, constraints, dataSources, autonomy, "
                "workflow_state, circuit_breaker_state, target_protein, autonomy_level, quantum_enabled, status, metadata, created_at, updated_at) "
                "VALUES (CAST(%s AS UNIQUEIDENTIFIER), CAST(%s AS UNIQUEIDENTIFIER), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            )
            return await execute_sql(sql, (
                campaign_id,
                campaign_data.get('tenant_id'),
                campaign_data.get('name'),
                db_payload['goal'],
                db_payload['constraints'],
                db_payload['dataSources'],
                db_payload['autonomy'],
                db_payload['workflow_state'],
                db_payload['circuit_breaker_state'],
                campaign_data.get('target_protein'),
                campaign_data.get('autonomy_level'),
                1 if campaign_data.get('quantum_enabled') else 0,
                campaign_data.get('status'),
                db_payload['metadata'],
                created_at_db,
                created_at_db,
            ))

        # Always use direct SQL to eliminate HTTP overhead and avoid db-manager coupling
        await _insert_campaign_via_sql()

        # Seed chat history from metadata.initial_chat_transcript if provided
        created_thread_id: Optional[str] = None
        try:
            transcript = (metadata_data or {}).get('initial_chat_transcript') if isinstance(metadata_data, dict) else None
            if transcript and isinstance(transcript, list):
                # Create a chat thread for this campaign
                thread_id = str(uuid.uuid4())
                await execute_sql(
                    "INSERT INTO campaign_chat_threads (id, campaign_id, status) VALUES (CAST(%s AS UNIQUEIDENTIFIER), CAST(%s AS UNIQUEIDENTIFIER), %s)",
                    (thread_id, campaign_id, 'active')
                )
                created_thread_id = thread_id

                # Insert messages in order; keep only role/content/timestamp
                for msg in transcript:
                    role = str(msg.get('role', 'assistant'))
                    content = str(msg.get('content', '') or '')
                    # Ignore empty content
                    if not content:
                        continue
                    message_id = str(uuid.uuid4())
                    # Use DB time for simplicity; optionally parse timestamp
                    # Escape single quotes in content for SQL literal safety is handled by parameterized execute_sql
                    await execute_sql(
                        (
                            "INSERT INTO campaign_chat_messages (id, thread_id, role, content, timestamp, "
                            "intent, sentiment, user_id, action_type, action_details, action_success, attachments, "
                            "campaign_iteration, campaign_candidates_count, campaign_discoveries_count, campaign_status) "
                            "VALUES (CAST(%s AS UNIQUEIDENTIFIER), CAST(%s AS UNIQUEIDENTIFIER), %s, %s, GETUTCDATE(), "
                            "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)"
                        ),
                        (message_id, thread_id, role, content)
                    )
                logger.info(f"Seeded chat transcript with {len(transcript)} messages for campaign {campaign_id}")
        except Exception as e:
            logger.warning(f"Failed to seed initial chat transcript: {e}")

        # Ensure a chat thread exists even if no transcript was provided
        if not created_thread_id:
            try:
                thread_id = str(uuid.uuid4())
                await execute_sql(
                    "INSERT INTO campaign_chat_threads (id, campaign_id, status) VALUES (CAST(%s AS UNIQUEIDENTIFIER), CAST(%s AS UNIQUEIDENTIFIER), %s)",
                    (thread_id, campaign_id, 'active')
                )
                created_thread_id = thread_id
                logger.info(f"Created empty chat thread for campaign {campaign_id}")
            except Exception as e:
                logger.warning(f"Failed to create default chat thread: {e}")

        # Auto-start campaign execution loop INTERNALLY (no external service call)
        try:
            from ai.campaign_loop import get_campaign_loop_manager
            loop_manager = get_campaign_loop_manager()
            if loop_manager:
                started = loop_manager.start_campaign(campaign_id)
                if started:
                    logger.info(f"Campaign {campaign_id} autonomous loop started internally")
                else:
                    logger.warning(f"Campaign {campaign_id} loop already running")
            else:
                logger.error("Campaign Loop Manager not initialized")
        except Exception as e:
            logger.error(f"Failed to start internal campaign loop: {str(e)}")

        # Start literature monitoring for campaign (non-blocking, ignore failures)
        try:
            await literature_monitor.schedule_continuous_monitoring(campaign_id)
        except Exception as e:
            logger.warning(f"schedule_continuous_monitoring failed (non-critical): {e}")

        # PROCESS INTERNAL LIBRARIES from S3 (if provided)
        # Internal libraries are campaign-specific proprietary data
        data_sources = request.get('dataSources', {})
        internal_libraries = data_sources.get('internalLibraries', [])

        if internal_libraries:
            logger.info(f"Processing {len(internal_libraries)} internal libraries for campaign {campaign_id}")

            try:
                from ai.internal_library_loader import InternalLibraryLoader

                library_loader = InternalLibraryLoader()

                # Run library loading in background (don't block campaign start)
                async def _load_internal_libraries_background():
                    try:
                        total_compounds = 0
                        for s3_path in internal_libraries:
                            logger.info(f"Loading internal library: {s3_path}")

                            # Load and parse library
                            result = await library_loader.load_library(
                                s3_path=s3_path,
                                campaign_id=campaign_id,
                                campaign_metadata={
                                    'campaign_name': campaign_data.get('name'),
                                    'target': campaign_data.get('goal', {}).get('description', '') if isinstance(campaign_data.get('goal'), dict) else campaign_data.get('goal', ''),
                                    'therapeutic_area': data_sources.get('therapeuticArea', ''),
                                    'modality': data_sources.get('modality', '')
                                }
                            )

                            if result.get('success'):
                                compounds = result.get('compounds', [])

                                # Store in Pinecone
                                store_result = await library_loader.store_in_pinecone(
                                    compounds=compounds,
                                    campaign_id=campaign_id
                                )

                                if store_result.get('success'):
                                    total_compounds += store_result.get('stored_count', 0)
                                    logger.info(f"Stored {store_result.get('stored_count')} compounds from {s3_path}")
                            else:
                                logger.error(f"Failed to load {s3_path}: {result.get('error')}")

                        logger.info(f"Internal library loading completed: {total_compounds} total compounds for campaign {campaign_id}")

                        # Broadcast completion
                        await broadcast_global_update('internal_libraries_loaded', {
                            'campaign_id': campaign_id,
                            'total_compounds': total_compounds,
                            'libraries_count': len(internal_libraries),
                            'timestamp': datetime.utcnow().isoformat()
                        })

                    except Exception as e:
                        logger.error(f"Background internal library loading failed: {e}", exc_info=True)

                # Schedule background task (non-blocking)
                import asyncio
                asyncio.create_task(_load_internal_libraries_background())

            except Exception as e:
                logger.warning(f"Failed to trigger internal library loading (non-critical): {e}")

        # NOTE: Global literature ingestion handled by EventBridge scheduled task
        # Campaigns query Pinecone with their searchKeywords, therapeuticArea, modality filters
        logger.info(f"Campaign {campaign_id} will query global Pinecone literature base with filters: "
                   f"keywords={data_sources.get('searchKeywords', [])}, "
                   f"area={data_sources.get('therapeuticArea')}, "
                   f"modality={data_sources.get('modality')}")

        # Fetch real campaign data for broadcast from dashboard-aggregator (READ operation)
        try:
            dash_agg_cfg = service_config_manager.get_service_config('dashboard-aggregator')
            dash_agg_url = (dash_agg_cfg.get('url') if dash_agg_cfg else None) or settings.SERVICES.get('dashboard-aggregator', {}).get('url')
            dash_agg_key = (dash_agg_cfg.get('api_key') if dash_agg_cfg else None) or settings.DASHBOARD_AGGREGATOR_API_KEY
            if dash_agg_key:
                dash_agg_key = dash_agg_key.strip()

            async with httpx.AsyncClient(timeout=10.0, verify=settings.httpx_verify) as client:
                campaign_response = await client.get(
                    f"{dash_agg_url}/campaigns/{campaign_id}",
                    headers={"X-API-Key": dash_agg_key or ""}
                )
                if campaign_response.status_code == 200:
                    real_campaign_data = campaign_response.json()
                else:
                    real_campaign_data = campaign_data
        except Exception as e:
            logger.warning(f"Failed to fetch campaign for broadcast: {e}")
            real_campaign_data = campaign_data

        # Broadcast campaign creation globally with real data
        await broadcast_global_update('campaign_created', {
            'campaign_id': campaign_id,
            'name': real_campaign_data.get('name'),
            'goal': real_campaign_data.get('goal'),
            'status': real_campaign_data.get('status'),
            'metrics': normalize_campaign_metrics(real_campaign_data.get('metrics')),
            'timestamp': datetime.utcnow().isoformat()
        })

        return {
            'success': True,
            'campaign_id': campaign_id,
            'status': 'active',
            'message': f"Campaign '{request.get('name')}' created and running autonomously",
            'thread_id': created_thread_id
        }

    except Exception as e:
        logger.error(f"Campaign creation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/campaign/decision")
async def make_campaign_decision(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Make an autonomous decision for a campaign.
    Called by Campaign Manager service for strategic decisions.

    Request body:
    {
        "campaign_id": "uuid",
        "context": {
            "molecules_generated": 150,
            "successful_leads": 5,
            "failure_count": 20,
            "timeline_remaining": 60,
            "budget_remaining": 75000
        }
    }
    """
    try:
        campaign_id = request.get('campaign_id')
        context = request.get('context', {})

        if not campaign_id:
            raise HTTPException(status_code=400, detail="Campaign ID required")

        # Add campaign_id to context
        context['campaign_id'] = campaign_id

        # Get AI decision
        decision = await campaign_decision_engine.make_autonomous_decision(context)

        # Check if human intervention is needed
        if decision.get('confidence', 1.0) < 0.5 or decision.get('action') == 'request_review':
            # Create intervention for low confidence decisions
            intervention_data = {
                'campaign_id': campaign_id,
                'campaign_name': context.get('campaign_name', 'Unknown Campaign'),
                'type': 'decision_review',
                'urgency': 'high' if decision.get('confidence', 1.0) < 0.3 else 'medium',
                'description': decision.get('reasoning', 'Low confidence decision requires human review'),
                'context': {
                    'action': decision.get('action'),
                    'confidence': decision.get('confidence'),
                    'alternatives': decision.get('alternatives', []),
                    'current_state': context
                },
                'options': decision.get('alternatives', ['approve', 'reject', 'modify'])
            }

            # Store intervention internally
            await create_intervention(intervention_data)

        # Check for milestones and broadcast
        molecules_generated = context.get('molecules_generated', 0)
        if molecules_generated > 0 and molecules_generated % 100 == 0:
            await broadcast_global_update('campaign_milestone', {
                'campaign_id': campaign_id,
                'milestone': 'molecules_generated',
                'value': molecules_generated,
                'timestamp': datetime.utcnow().isoformat()
            })

        # Check for lead milestones
        successful_leads = context.get('successful_leads', 0)
        if successful_leads > 0 and successful_leads % 5 == 0:
            await broadcast_global_update('campaign_milestone', {
                'campaign_id': campaign_id,
                'milestone': 'successful_leads',
                'value': successful_leads,
                'timestamp': datetime.utcnow().isoformat()
            })

        # Execute decision through appropriate workflow
        execution_result = await execute_campaign_decision(decision)

        # Learn from outcome
        patterns_extracted = await continuous_learning_system.extract_patterns(execution_result)
        await campaign_decision_engine.learn_from_outcome(decision, execution_result)

        # Broadcast learning pattern discovery (send fields UI expects)
        if patterns_extracted is not None:
            await broadcast_global_update('learning_pattern', {
                'campaign_id': campaign_id,
                'id': str(uuid.uuid4()),
                'name': decision.get('action', 'strategy_update'),
                'confidence': int(round(float(decision.get('confidence', 0.6)) * 100)),
                'improvement': 0,
                'timestamp': datetime.utcnow().isoformat()
            })

        # Send real-time update via WebSocket
        await manager.send_update(campaign_id, {
            'type': 'decision_made',
            'decision': decision,
            'result': execution_result,
            'timestamp': datetime.utcnow().isoformat()
        })

        # Also broadcast to global for dashboard with current metrics
        await broadcast_global_update('campaign_decision', {
            'campaign_id': campaign_id,
            'action': decision.get('action'),
            'confidence': decision.get('confidence'),
            'reasoning': decision.get('reasoning'),
            'priority': decision.get('priority'),
            'molecules_generated': context.get('molecules_generated', 0),
            'leads_identified': context.get('successful_leads', 0),
            'ai_confidence': decision.get('confidence'),
            'current_phase': context.get('current_phase', 'Discovery'),
            'timestamp': datetime.utcnow().isoformat()
        })

        # Persist decision to SQL database via db-manager
        try:
            await _real_service_call({
                'service': 'db-manager',
                'endpoint': '/campaign-decisions',
                'method': 'POST',
                'payload': {
                    'campaign_id': str(campaign_id),  # Convert UUID to string for JSON serialization
                    'decision_type': decision.get('action'),
                    'reasoning': decision.get('reasoning'),
                    'input_context': decision.get('parameters', {}),
                    'outcome': execution_result,
                    'success_score': decision.get('confidence', 0.5)
                }
            })
            logger.info(f"Persisted decision to SQL for campaign {campaign_id}")
        except Exception as e:
            logger.error(f"Failed to persist decision to SQL: {e}")

        return {
            'success': True,
            'campaign_id': campaign_id,
            'decision': decision,
            'execution_result': execution_result
        }

    except Exception as e:
        logger.error(f"Campaign decision failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

async def execute_campaign_decision(decision: dict) -> Dict[str, Any]:
    """
    DEPRECATED: Legacy stub function - campaigns now use orchestrate_decision() via workflow engine
    This function returns mock data and should not be used for production campaigns.
    """
    logger.warning("execute_campaign_decision() called - this is a deprecated stub function")
    return {
        'success': False,
        'error': 'This function is deprecated - use orchestrate_decision() instead',
        'action': decision.get('action')
    }


async def get_campaign_with_metrics_sql(campaign_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch campaign with aggregated metrics using direct SQL query.
    Eliminates HTTP overhead to dashboard-aggregator in autonomous loop.

    Returns campaign data with metrics aggregated from campaign_iterations table.
    """
    try:
        from core.db_helper import query_sql
        import json

        # Query campaign with aggregated metrics from iterations
        results = await query_sql("""
            SELECT
                c.id, c.tenant_id, c.name, c.goal, c.status, c.autonomy_level,
                c.created_at, c.updated_at, c.completed_at,
                c.constraints, c.metadata, c.dataSources, c.autonomy,
                c.workflow_state, c.circuit_breaker_state, c.target_protein, c.quantum_enabled,
                COALESCE(SUM(ci.phase_1_input), 0) as molecules_generated,
                COALESCE(SUM(ci.phase_4_output), 0) as successful_leads,
                COALESCE(COUNT(CASE WHEN ci.status = 'completed' THEN 1 END), 0) as experiments_run,
                COALESCE(COUNT(CASE WHEN ci.status = 'failed' THEN 1 END), 0) as failure_count
            FROM campaigns c
            LEFT JOIN campaign_iterations ci ON c.id = ci.campaign_id
            WHERE c.id = %s
            GROUP BY c.id, c.tenant_id, c.name, c.goal, c.status, c.autonomy_level,
                c.created_at, c.updated_at, c.completed_at, c.constraints, c.metadata,
                c.dataSources, c.autonomy, c.workflow_state, c.circuit_breaker_state,
                c.target_protein, c.quantum_enabled
        """, (campaign_id,))

        if not results or len(results) == 0:
            return None

        row = results[0]

        # Parse JSON fields
        constraints = json.loads(row.get('constraints', '{}')) if row.get('constraints') else {}
        metadata = json.loads(row.get('metadata', '{}')) if row.get('metadata') else {}
        dataSources = json.loads(row.get('dataSources', '{}')) if row.get('dataSources') else {}
        autonomy = json.loads(row.get('autonomy', '{}')) if row.get('autonomy') else {}
        workflow_state = json.loads(row.get('workflow_state', '{}')) if row.get('workflow_state') else {}
        circuit_breaker_state = json.loads(row.get('circuit_breaker_state', '{}')) if row.get('circuit_breaker_state') else {}

        # Build campaign object with metrics
        campaign = {
            'id': row.get('id'),
            'campaign_id': row.get('id'),  # Alias for compatibility
            'tenant_id': row.get('tenant_id'),
            'name': row.get('name'),
            'goal': row.get('goal'),
            'status': row.get('status'),
            'autonomy_level': row.get('autonomy_level'),
            'created_at': row.get('created_at').isoformat() if row.get('created_at') else None,
            'updated_at': row.get('updated_at').isoformat() if row.get('updated_at') else None,
            'completed_at': row.get('completed_at').isoformat() if row.get('completed_at') else None,
            'target_protein': row.get('target_protein'),
            'quantum_enabled': bool(row.get('quantum_enabled')),
            'constraints': constraints,
            'metadata': metadata,
            'dataSources': dataSources,
            'autonomy': autonomy,
            'workflow_state': workflow_state,
            'circuit_breaker_state': circuit_breaker_state,
            'metrics': {
                'molecules_generated': int(row.get('molecules_generated', 0)),
                'successful_leads': int(row.get('successful_leads', 0)),
                'experiments_run': int(row.get('experiments_run', 0)),
                'failure_count': int(row.get('failure_count', 0))
            }
        }

        return campaign

    except Exception as e:
        logger.error(f"Failed to fetch campaign with SQL: {e}", exc_info=True)
        return None


@router.get("/campaign/{campaign_id}/status")
async def get_campaign_status(campaign_id: str) -> Dict[str, Any]:
    """
    Get comprehensive status of an autonomous campaign.
    Uses direct SQL query to eliminate HTTP overhead in autonomous loop.
    """
    try:
        # Fetch campaign with metrics using direct SQL (NO HTTP OVERHEAD)
        campaign_data = await get_campaign_with_metrics_sql(campaign_id)

        if not campaign_data:
            raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found")

        # Get recent decisions
        recent_decisions = await campaign_decision_engine.get_decision_history(campaign_id, 5)

        # Determine current phase based on status and progress
        current_phase = "Discovery"
        if campaign_data['status'] == 'paused':
            current_phase = "Paused"
        elif campaign_data['status'] == 'completed':
            current_phase = "Completed"
        elif campaign_data['metrics']['successful_leads'] > 10:
            current_phase = "Lead Optimization"
        elif campaign_data['metrics']['molecules_generated'] > 100:
            current_phase = "Lead Generation"

        # Build response with real data
        status = {
            'campaign_id': campaign_id,
            'status': campaign_data['status'],
            'metrics': campaign_data['metrics'],
            'current_phase': current_phase,
            'ai_confidence': campaign_data.get('ai_confidence'),
            'estimated_completion': campaign_data.get('estimated_completion'),
            'recent_decisions': recent_decisions,
            'timestamp': datetime.utcnow().isoformat(),
            # Include campaign fields needed by decision engine
            'goal': campaign_data.get('goal'),
            'constraints': campaign_data.get('constraints'),
            'created_at': campaign_data.get('created_at'),
            'dataSources': campaign_data.get('dataSources'),
            'workflow_state': campaign_data.get('workflow_state'),
            'circuit_breaker_state': campaign_data.get('circuit_breaker_state'),
            'target_protein': campaign_data.get('target_protein')
        }

        return status

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get campaign status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/campaign/{campaign_id}/pause")
async def pause_campaign(campaign_id: str) -> Dict[str, Any]:
    """Pause an autonomous campaign"""
    try:
        # Update campaign status in database
        logger.info(f"Pausing campaign {campaign_id}")

        # Update status in db-manager
        try:
            db_mgr_cfg = service_config_manager.get_service_config('db-manager')
            db_mgr_url = (db_mgr_cfg.get('url') if db_mgr_cfg else None) or settings.SERVICES.get('db-manager', {}).get('url')
            db_mgr_key = (db_mgr_cfg.get('api_key') if db_mgr_cfg else None) or settings.DB_MANAGER_API_KEY
            if db_mgr_key:
                db_mgr_key = db_mgr_key.strip()

            async with httpx.AsyncClient(timeout=10.0, verify=settings.httpx_verify) as client:
                update_response = await client.post(
                    f"{db_mgr_url}/campaigns/{campaign_id}/update",
                    headers={
                        "X-API-Key": db_mgr_key or "",
                        "x-service-key": os.getenv("INTERNAL_SERVICE_KEY", "")
                    },
                    json={"status": "paused"}
                )
                if update_response.status_code != 200:
                    logger.error(f"Failed to update campaign status in db-manager: {update_response.status_code}")
        except Exception as e:
            logger.error(f"Failed to update campaign status in database: {e}")

        # Stop internal campaign loop if running
        try:
            from ai.campaign_loop import get_campaign_loop_manager
            loop_manager = get_campaign_loop_manager()
            if loop_manager:
                loop_manager.stop_campaign(campaign_id)
        except Exception as e:
            logger.warning(f"Failed to stop campaign loop for {campaign_id}: {e}")

        # Fetch current campaign state for broadcast from dashboard-aggregator (READ operation)
        try:
            dash_agg_cfg = service_config_manager.get_service_config('dashboard-aggregator')
            dash_agg_url = (dash_agg_cfg.get('url') if dash_agg_cfg else None) or settings.SERVICES.get('dashboard-aggregator', {}).get('url')
            dash_agg_key = (dash_agg_cfg.get('api_key') if dash_agg_cfg else None) or settings.DASHBOARD_AGGREGATOR_API_KEY
            if dash_agg_key:
                dash_agg_key = dash_agg_key.strip()

            async with httpx.AsyncClient(timeout=10.0, verify=settings.httpx_verify) as client:
                campaign_response = await client.get(
                    f"{dash_agg_url}/campaigns/{campaign_id}",
                    headers={"X-API-Key": dash_agg_key or ""}
                )
                campaign_data = campaign_response.json() if campaign_response.status_code == 200 else {}
        except Exception as e:
            logger.warning(f"Failed to fetch campaign for broadcast: {e}")
            campaign_data = {}

        # Broadcast pause event globally with real data
        await broadcast_global_update('campaign_paused', {
            'campaign_id': campaign_id,
            'status': 'paused',
            'metrics': normalize_campaign_metrics(campaign_data.get('metrics')),
            'timestamp': datetime.utcnow().isoformat()
        })

        return {
            'success': True,
            'campaign_id': campaign_id,
            'status': 'paused',
            'message': 'Campaign paused. Can be resumed at any time.'
        }

    except Exception as e:
        logger.error(f"Failed to pause campaign: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/campaign/{campaign_id}/resume")
async def resume_campaign(campaign_id: str) -> Dict[str, Any]:
    """Resume a paused campaign"""
    try:
        logger.info(f"Resuming campaign {campaign_id}")

        # Update status in db-manager
        try:
            db_mgr_cfg = service_config_manager.get_service_config('db-manager')
            db_mgr_url = (db_mgr_cfg.get('url') if db_mgr_cfg else None) or settings.SERVICES.get('db-manager', {}).get('url')
            db_mgr_key = (db_mgr_cfg.get('api_key') if db_mgr_cfg else None) or settings.DB_MANAGER_API_KEY
            if db_mgr_key:
                db_mgr_key = db_mgr_key.strip()

            async with httpx.AsyncClient(timeout=10.0, verify=settings.httpx_verify) as client:
                update_response = await client.post(
                    f"{db_mgr_url}/campaigns/{campaign_id}/update",
                    headers={
                        "X-API-Key": db_mgr_key or "",
                        "x-service-key": os.getenv("INTERNAL_SERVICE_KEY", "")
                    },
                    json={"status": "active"}
                )
                if update_response.status_code != 200:
                    logger.error(f"Failed to update campaign status in db-manager: {update_response.status_code}")
        except Exception as e:
            logger.error(f"Failed to update campaign status in database: {e}")

        # Restart internal campaign loop
        try:
            from ai.campaign_loop import get_campaign_loop_manager
            loop_manager = get_campaign_loop_manager()
            if loop_manager:
                loop_manager.start_campaign(campaign_id)
        except Exception as e:
            logger.warning(f"Failed to restart campaign loop for {campaign_id}: {e}")

        # Fetch current campaign state for broadcast from dashboard-aggregator (READ operation)
        try:
            dash_agg_cfg = service_config_manager.get_service_config('dashboard-aggregator')
            dash_agg_url = (dash_agg_cfg.get('url') if dash_agg_cfg else None) or settings.SERVICES.get('dashboard-aggregator', {}).get('url')
            dash_agg_key = (dash_agg_cfg.get('api_key') if dash_agg_cfg else None) or settings.DASHBOARD_AGGREGATOR_API_KEY
            if dash_agg_key:
                dash_agg_key = dash_agg_key.strip()

            async with httpx.AsyncClient(timeout=10.0, verify=settings.httpx_verify) as client:
                campaign_response = await client.get(
                    f"{dash_agg_url}/campaigns/{campaign_id}",
                    headers={"X-API-Key": dash_agg_key or ""}
                )
                campaign_data = campaign_response.json() if campaign_response.status_code == 200 else {}
        except Exception as e:
            logger.warning(f"Failed to fetch campaign for broadcast: {e}")
            campaign_data = {}

        # Broadcast resume event globally with real data
        await broadcast_global_update('campaign_resumed', {
            'campaign_id': campaign_id,
            'status': 'active',
            'metrics': normalize_campaign_metrics(campaign_data.get('metrics')),
            'timestamp': datetime.utcnow().isoformat()
        })

        return {
            'success': True,
            'campaign_id': campaign_id,
            'status': 'active',
            'message': 'Campaign resumed. AI is back to work.'
        }

    except Exception as e:
        logger.error(f"Failed to resume campaign: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/campaign/{campaign_id}/stop")
async def stop_campaign(campaign_id: str) -> Dict[str, Any]:
    """Stop an autonomous campaign permanently"""
    try:
        logger.info(f"Stopping campaign {campaign_id}")

        # Get final metrics
        final_status = await get_campaign_status(campaign_id)

        # Update status in db-manager to 'completed'
        try:
            db_mgr_cfg = service_config_manager.get_service_config('db-manager')
            db_mgr_url = (db_mgr_cfg.get('url') if db_mgr_cfg else None) or settings.SERVICES.get('db-manager', {}).get('url')
            db_mgr_key = (db_mgr_cfg.get('api_key') if db_mgr_cfg else None) or settings.DB_MANAGER_API_KEY
            if db_mgr_key:
                db_mgr_key = db_mgr_key.strip()

            async with httpx.AsyncClient(timeout=10.0, verify=settings.httpx_verify) as client:
                update_response = await client.post(
                    f"{db_mgr_url}/campaigns/{campaign_id}/update",
                    headers={
                        "X-API-Key": db_mgr_key or "",
                        "x-service-key": os.getenv("INTERNAL_SERVICE_KEY", "")
                    },
                    json={"status": "completed"}
                )
                if update_response.status_code != 200:
                    logger.error(f"Failed to update campaign status in db-manager: {update_response.status_code}")
        except Exception as e:
            logger.error(f"Failed to update campaign status in database: {e}")

        # Stop internal loop before broadcasting completion
        try:
            from ai.campaign_loop import get_campaign_loop_manager
            loop_manager = get_campaign_loop_manager()
            if loop_manager:
                loop_manager.stop_campaign(campaign_id)
        except Exception as e:
            logger.warning(f"Failed to stop campaign loop for {campaign_id}: {e}")

        # Broadcast stop event globally with final metrics
        await broadcast_global_update('campaign_stopped', {
            'campaign_id': campaign_id,
            'status': 'completed',
            'final_metrics': normalize_campaign_metrics(final_status.get('metrics')),
            'timestamp': datetime.utcnow().isoformat()
        })

        return {
            'success': True,
            'campaign_id': campaign_id,
            'status': 'completed',
            'final_metrics': final_status.get('metrics'),
            'message': 'Campaign completed. Results available for export.'
        }

    except Exception as e:
        logger.error(f"Failed to stop campaign: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

async def store_campaign_learning(
    campaign_id: str,
    decision: Dict[str, Any],
    outcome: Dict[str, Any],
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Internal helper to store campaign learning data.
    Called directly by campaign loop manager (no HTTP overhead).
    """
    try:
        logger.info(f"Recording learning for campaign {campaign_id}: action={decision.get('action')}, success={outcome.get('status') == 'success'}")

        # Feed into the campaign decision engine's learning system
        await campaign_decision_engine.learn_from_outcome(decision, outcome)

        # Store in Pinecone for cross-campaign learning
        stored_in_pinecone = False
        try:
            from core.pinecone_client import get_pinecone_client
            pinecone_client = get_pinecone_client()
            stored_in_pinecone = await pinecone_client.store_learning_pattern(
                campaign_id=campaign_id,
                decision=decision,
                outcome=outcome,
                context=context
            )
            if stored_in_pinecone:
                logger.info(f"Learning pattern stored in Pinecone for campaign {campaign_id}")
        except Exception as e:
            logger.warning(f"Failed to store learning in Pinecone: {e}")

        # Also persist to SQL database via db-manager (independent of Pinecone)
        try:
            import hashlib
            pattern_str = f"{decision.get('action')}_{decision.get('parameters')}_{outcome.get('success')}"
            pattern_hash = hashlib.sha256(pattern_str.encode()).hexdigest()[:16]

            await _real_service_call({
                'service': 'db-manager',
                'endpoint': '/learning-patterns',
                'method': 'POST',
                'payload': {
                    'pattern_hash': pattern_hash,
                    'pattern_type': decision.get('action'),
                    'success_rate': 1.0 if outcome.get('success') else 0.0,
                    'occurrence_count': 1,
                    'context': {
                        'decision': decision,
                        'outcome': outcome,
                        'campaign_id': campaign_id
                    }
                }
            })
            logger.info(f"Persisted learning pattern to SQL for campaign {campaign_id}")
        except Exception as e:
            logger.error(f"Failed to persist learning pattern to SQL: {e}")

        return {
            "success": True,
            "campaign_id": campaign_id,
            "stored_in_pinecone": stored_in_pinecone,
            "message": "Learning recorded successfully"
        }

    except Exception as e:
        logger.error(f"Failed to record learning: {str(e)}")
        return {
            "success": False,
            "campaign_id": campaign_id,
            "stored_in_pinecone": False,
            "message": str(e)
        }


@router.post("/learn")
async def learn_from_outcome(request: Request) -> Dict[str, Any]:
    """
    HTTP endpoint to record learning data from external campaign execution loops.
    Wraps the internal store_campaign_learning() helper.
    """
    try:
        payload = await request.json()

        campaign_id = payload.get("campaign_id")
        decision = payload.get("decision", {})
        outcome = payload.get("outcome", {})
        context = payload.get("context", {})

        # Call internal helper
        return await store_campaign_learning(campaign_id, decision, outcome, context)

    except Exception as e:
        logger.error(f"Failed to record learning: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/campaigns/loops/running")
async def get_running_campaign_loops() -> Dict[str, Any]:
    """
    Get list of currently running campaign loops.
    Returns campaign IDs and their status.
    """
    try:
        from ai.campaign_loop import get_campaign_loop_manager

        loop_manager = get_campaign_loop_manager()
        if not loop_manager:
            return {
                "running_campaigns": [],
                "message": "Campaign loop manager not initialized"
            }

        running = loop_manager.get_running_campaigns()
        return {
            "running_campaigns": running,
            "count": len(running)
        }

    except Exception as e:
        logger.error(f"Failed to get running campaigns: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/campaigns/{campaign_id}/loop/start")
async def start_campaign_loop(campaign_id: str) -> Dict[str, Any]:
    """
    Manually start a campaign loop.
    Useful for restarting stopped campaigns or starting old campaigns created before auto-start was implemented.
    """
    try:
        from ai.campaign_loop import get_campaign_loop_manager

        loop_manager = get_campaign_loop_manager()
        if not loop_manager:
            raise HTTPException(status_code=500, detail="Campaign loop manager not initialized")

        started = loop_manager.start_campaign(campaign_id)

        if started:
            return {
                "success": True,
                "campaign_id": campaign_id,
                "message": "Campaign loop started successfully"
            }
        else:
            return {
                "success": False,
                "campaign_id": campaign_id,
                "message": "Campaign loop is already running"
            }

    except Exception as e:
        logger.error(f"Failed to start campaign loop: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/campaigns/{campaign_id}/loop/stop")
async def stop_campaign_loop(campaign_id: str) -> Dict[str, Any]:
    """
    Manually stop a running campaign loop.
    """
    try:
        from ai.campaign_loop import get_campaign_loop_manager

        loop_manager = get_campaign_loop_manager()
        if not loop_manager:
            raise HTTPException(status_code=500, detail="Campaign loop manager not initialized")

        stopped = loop_manager.stop_campaign(campaign_id)

        if stopped:
            return {
                "success": True,
                "campaign_id": campaign_id,
                "message": "Campaign loop stopped successfully"
            }
        else:
            return {
                "success": False,
                "campaign_id": campaign_id,
                "message": "Campaign loop is not running"
            }

    except Exception as e:
        logger.error(f"Failed to stop campaign loop: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.websocket("/ws/campaign/{campaign_id}")
async def campaign_stream(websocket: WebSocket, campaign_id: str):
    """
    Consolidated WebSocket endpoint for real-time campaign updates.
    Streams decisions, results, and milestones as they happen.
    Compatible with frontend WebSocket manager.
    """
    # Accept the WebSocket connection first
    await websocket.accept()

    # Extract and validate token from query params
    token = websocket.query_params.get("token")
    if not token:
        await websocket.send_json({"error": "Authentication required"})
        await websocket.close(code=1008, reason="Authentication required")
        return

    # Validate the token
    try:
        import jwt
        decoded = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        user_id = decoded.get("sub")
        logger.info(f"WebSocket authenticated for user {user_id} on campaign {campaign_id}")
    except (jwt.InvalidSignatureError, jwt.ExpiredSignatureError, jwt.DecodeError) as e:
        logger.error(f"WebSocket authentication failed: {e}")
        await websocket.send_json({"error": "Invalid token"})
        await websocket.close(code=1008, reason="Invalid token")
        return

    # Register with manager after authentication
    if campaign_id not in manager.active_connections:
        manager.active_connections[campaign_id] = set()
    manager.active_connections[campaign_id].add(websocket)
    try:
        while True:
            # Keep connection alive and wait for messages
            data = await websocket.receive_text()

            try:
                message = json.loads(data)

                # Handle different message types
                if message.get("type") == "ping" or data == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": datetime.utcnow().isoformat()})
                elif message.get("type") == "subscribe":
                    # Subscribe to specific event types
                    event_types = message.get("events", ["all"])
                    logger.info(f"Campaign {campaign_id} subscribed to events: {event_types}")
                    await websocket.send_json({
                        "type": "subscription_confirmed",
                        "events": event_types,
                        "timestamp": datetime.utcnow().isoformat()
                    })
                elif message.get("type") == "heartbeat":
                    await websocket.send_json({"type": "heartbeat_ack", "timestamp": datetime.utcnow().isoformat()})

            except json.JSONDecodeError:
                # Handle plain text messages
                if data == "ping":
                    await websocket.send_text("pong")

    except WebSocketDisconnect:
        manager.disconnect(websocket, campaign_id)
        logger.info(f"WebSocket disconnected for campaign {campaign_id}")

@router.websocket("/ws/global")
async def global_stream(websocket: WebSocket):
    """
    Global WebSocket endpoint for dashboard-wide updates.
    Broadcasts updates for all campaigns and system-wide events.
    """
    # Accept the WebSocket connection first
    await websocket.accept()

    # Extract and validate token from query params
    token = websocket.query_params.get("token")
    if not token:
        await websocket.send_json({"error": "Authentication required"})
        await websocket.close(code=1008, reason="Authentication required")
        return

    # Validate the token and extract user info
    try:
        import jwt
        decoded = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        user_id = decoded.get("sub", "anonymous")
        logger.info(f"Global WebSocket authenticated for user {user_id}")
    except (jwt.InvalidSignatureError, jwt.ExpiredSignatureError, jwt.DecodeError) as e:
        logger.error(f"Global WebSocket authentication failed: {e}")
        await websocket.send_json({"error": "Invalid token"})
        await websocket.close(code=1008, reason="Invalid token")
        return

    try:
        # Register with manager after authentication
        global_ws_manager.connections.add(websocket)
        global_ws_manager.subscriptions[websocket] = {
            "all": True,
            "campaign_ids": set(),
            "events": set(["all"])
        }
        logger.info(f"Global WebSocket connected for user {user_id}")

        # Keep connection alive and listen for messages
        while True:
            data = await websocket.receive_text()

            try:
                message = json.loads(data)

                # Handle different message types
                if message.get("type") == "ping" or data == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": datetime.utcnow().isoformat()})
                elif message.get("type") == "subscribe_all":
                    global_ws_manager.subscribe_all(websocket)
                    await websocket.send_json({
                        "type": "subscription_confirmed",
                        "scope": "global",
                        "timestamp": datetime.utcnow().isoformat()
                    })
                elif message.get("type") == "subscribe_campaigns":
                    campaign_ids = message.get("campaign_ids", [])
                    global_ws_manager.subscribe_campaigns(websocket, campaign_ids)
                    await websocket.send_json({
                        "type": "subscription_confirmed",
                        "campaigns": campaign_ids,
                        "timestamp": datetime.utcnow().isoformat()
                    })
                elif message.get("type") == "subscribe_events":
                    events = message.get("events", ["all"])
                    global_ws_manager.subscribe_events(websocket, events)
                    await websocket.send_json({
                        "type": "subscription_confirmed",
                        "events": events,
                        "timestamp": datetime.utcnow().isoformat()
                    })
                elif message.get("type") == "heartbeat":
                    await websocket.send_json({"type": "heartbeat_ack", "timestamp": datetime.utcnow().isoformat()})

            except json.JSONDecodeError:
                # Handle plain text messages
                if data == "ping":
                    await websocket.send_text("pong")

    except WebSocketDisconnect:
        global_ws_manager.disconnect(websocket)
        logger.info(f"Global WebSocket disconnected")
    except Exception as e:
        logger.error(f"Error in global WebSocket: {e}")
        try:
            await websocket.close()
        except Exception:
            pass


# Helper function to broadcast to global connections
async def broadcast_global_update(event_type: str, data: Dict[str, Any]):
    """
    Broadcast updates to all global WebSocket connections across all ECS tasks.
    Uses Redis pub/sub to ensure all tasks receive the broadcast.
    """
    from core.redis_pubsub import get_redis_pubsub_manager

    message = {
        "type": event_type,
        "data": data,
        "timestamp": datetime.utcnow().isoformat()
    }

    # Publish to Redis for cross-task broadcasting
    redis_manager = get_redis_pubsub_manager()
    if redis_manager:
        try:
            await redis_manager.publish(event_type, data)
            logger.info(f"Broadcast {event_type} published to Redis")
            # IMPORTANT: Also broadcast to local connections immediately
            # Redis subscribers on other tasks will get it via pub/sub,
            # but we need to send to our own task's connections directly
            await global_ws_manager.broadcast(message)
        except Exception as e:
            logger.error(f"Failed to publish to Redis: {e}")
            # Fallback to local broadcast only
            await global_ws_manager.broadcast(message)
    else:
        # No Redis available, broadcast locally only
        logger.warning("Redis pub/sub not available, broadcasting locally only")
        await global_ws_manager.broadcast(message)


@router.get("/campaign/learning/insights")
async def get_learning_insights(tenant_id: str = None) -> Dict[str, Any]:
    """
    Get aggregated learning insights across campaigns.
    Maintains tenant isolation while sharing patterns.
    """
    try:
        if not tenant_id:
            raise HTTPException(status_code=400, detail="Tenant ID required")

        insights = await continuous_learning_system.share_learning_across_campaigns(tenant_id)

        return {
            'success': True,
            'tenant_id': tenant_id,
            'insights': insights,
            'generated_at': datetime.utcnow().isoformat()
        }

    except Exception as e:
        logger.error(f"Failed to get learning insights: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/campaign/{campaign_id}/literature-scan")
async def trigger_literature_scan(campaign_id: str, request: dict) -> Dict[str, Any]:
    """
    Trigger an immediate literature scan for a campaign.

    Request body:
    {
        "target": "KRAS G12C",
        "indication": "NSCLC",
        "keywords": ["resistance", "combination therapy"]
    }
    """
    try:
        campaign_goals = {
            'campaign_id': campaign_id,
            'target': request.get('target'),
            'indication': request.get('indication'),
            'keywords': request.get('keywords', [])
        }

        insights = await literature_monitor.scan_for_insights(campaign_goals)

        # Send insights via WebSocket
        await manager.send_update(campaign_id, {
            'type': 'literature_insights',
            'insights': insights.get('actionable_insights', []),
            'timestamp': datetime.utcnow().isoformat()
        })

        # Also broadcast globally so dashboards receive the update
        await broadcast_global_update('literature_insights', {
            'campaign_id': campaign_id,
            'insights_count': len(insights.get('actionable_insights', [])),
            'summary': insights.get('actionable_insights', [])[:1]
        })

        return {
            'success': True,
            'campaign_id': campaign_id,
            'insights_found': len(insights.get('actionable_insights', [])),
            'insights': insights
        }

    except Exception as e:
        logger.error(f"Literature scan failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/score-molecules-async")
async def score_molecules_async(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Submit AI scoring job to molecular-worker SQS queue.

    Request body:
    {
        "molecules": [
            {"id": "mol1", "smiles": "CCO"},
            {"id": "mol2", "smiles": "CC(C)O"}
        ],
        "user_id": "user-uuid",
        "org_id": "org-uuid"
    }

    Returns:
    {
        "job_id": "uuid",
        "status": "queued",
        "message": "AI scoring job submitted"
    }
    """
    try:
        molecules = request.get('molecules', [])
        user_id = request.get('user_id')
        org_id = request.get('org_id')

        if not molecules:
            raise HTTPException(status_code=400, detail="No molecules provided")

        # Generate job ID
        job_id = str(uuid.uuid4())

        # Build job message
        message_body = {
            "job_id": job_id,
            "job_type": "score_molecules",
            "parameters": {
                "molecules": molecules,
                "user_id": user_id,
                "org_id": org_id
            },
            "timestamp": datetime.utcnow().isoformat()
        }

        # Submit to queue (Azure or AWS based on QUEUE_BACKEND)
        send_to_queue('novomcp-molecular-jobs', message_body)

        # Initialize Redis status for polling
        import redis.asyncio as redis
        redis_url = os.getenv('REDIS_URL', 'redis://novomcp-redis-cluster.qkrqx3.ng.0001.use1.cache.amazonaws.com:6379')
        redis_client = redis.from_url(redis_url, decode_responses=True)

        redis_key = f"novomcp:job:{job_id}"
        await redis_client.hset(
            redis_key,
            mapping={
                "status": "queued",
                "job_id": job_id,
                "job_type": "score_molecules",
                "submitted_at": datetime.utcnow().isoformat()
            }
        )
        await redis_client.expire(redis_key, 3600)  # 1 hour TTL
        await redis_client.close()

        logger.info(f"Submitted scoring job {job_id} to SQS for {len(molecules)} molecules")

        return {
            "job_id": job_id,
            "status": "queued",
            "message": f"AI scoring job submitted for {len(molecules)} molecules"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit scoring job: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/calculate-properties-async")
async def calculate_properties_async(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Submit chemical properties calculation job to molecular-worker SQS queue.

    Request body:
    {
        "molecules": [
            {"id": "mol1", "smiles": "CCO"}
        ],
        "properties": ["mw", "logp", "hbd", "hba", "tpsa", "qed"],
        "user_id": "user-uuid",
        "org_id": "org-uuid"
    }

    Returns:
    {
        "job_id": "uuid",
        "status": "queued",
        "message": "Property calculation job submitted"
    }
    """
    try:
        # Safety guard: skip chem-props when PubChem enriched pipeline is active for engine-originated calls
        # Allow external enrichment scripts to proceed (they typically won't include campaign_id/source)
        pubchem_enriched_enabled = os.getenv('PUBCHEM_ENRICHMENT_ENABLED', 'true').lower() in ('1', 'true', 'yes')
        caller_source = str(request.get('source') or '').lower()
        from_campaign = bool(request.get('campaign_id')) or caller_source in ('campaign_engine', 'workflow_engine')
        if pubchem_enriched_enabled and from_campaign:
            logger.info("Skipping chem-props job: PubChem-enriched pipeline active (engine-originated call)")
            return {
                "job_id": None,
                "status": "skipped",
                "message": "Chem-Props calculation skipped due to PubChem-enriched pipeline"
            }

        molecules = request.get('molecules', [])
        properties = request.get('properties', ['mw', 'logp', 'hbd', 'hba', 'tpsa', 'qed'])
        user_id = request.get('user_id')
        org_id = request.get('org_id')

        if not molecules:
            raise HTTPException(status_code=400, detail="No molecules provided")

        # Generate job ID
        job_id = str(uuid.uuid4())

        # Build job message
        message_body = {
            "job_id": job_id,
            "job_type": "calculate_properties",
            "parameters": {
                "molecules": molecules,
                "properties": properties,
                "user_id": user_id,
                "org_id": org_id
            },
            "timestamp": datetime.utcnow().isoformat()
        }

        # Submit to queue (Azure or AWS based on QUEUE_BACKEND)
        send_to_queue('novomcp-molecular-jobs', message_body)

        # Initialize Redis status for polling
        import redis.asyncio as redis
        redis_url = os.getenv('REDIS_URL', 'redis://novomcp-redis-cluster.qkrqx3.ng.0001.use1.cache.amazonaws.com:6379')
        redis_client = redis.from_url(redis_url, decode_responses=True)

        redis_key = f"novomcp:job:{job_id}"
        await redis_client.hset(
            redis_key,
            mapping={
                "status": "queued",
                "job_id": job_id,
                "job_type": "calculate_properties",
                "submitted_at": datetime.utcnow().isoformat()
            }
        )
        await redis_client.expire(redis_key, 3600)  # 1 hour TTL
        await redis_client.close()

        logger.info(f"Submitted chem-props job {job_id} to SQS for {len(molecules)} molecules")

        return {
            "job_id": job_id,
            "status": "queued",
            "message": f"Property calculation job submitted for {len(molecules)} molecules"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit chem-props job: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/calculate-properties-status/{job_id}")
async def get_properties_job_status(job_id: str) -> Dict[str, Any]:
    """
    Get status of a chemical properties calculation job from Redis.

    Returns:
    {
        "job_id": "uuid",
        "status": "queued|processing|completed|failed",
        "progress": {
            "percentage": 50,
            "message": "Calculating properties for molecule 5/10",
            "step": "calculating"
        },
        "results": {...}
    }
    """
    try:
        # Query Redis (molecular-worker uses novomcp:job:{job_id})
        import redis.asyncio as redis

        redis_url = os.getenv('REDIS_URL', 'redis://novomcp-redis-cluster.qkrqx3.ng.0001.use1.cache.amazonaws.com:6379')
        redis_client = redis.from_url(redis_url, decode_responses=True)

        # Get job status from Redis (molecular-worker uses novomcp:job:{job_id})
        key = f"novomcp:job:{job_id}"
        job_data = await redis_client.hgetall(key)

        await redis_client.close()

        if not job_data:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        # Parse progress and results JSON
        progress = json.loads(job_data.get('progress', '{}')) if job_data.get('progress') else {}
        # Note: molecular-worker saves as 'result' (singular)
        results = json.loads(job_data.get('result', '{}')) if job_data.get('result') else None

        return {
            "job_id": job_id,
            "status": job_data.get('status', 'unknown'),
            "progress": progress,
            "results": results
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get job status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/predict-admet-async")
async def predict_admet_async(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Submit ADMET prediction job to molecular-worker SQS queue.

    Request body:
    {
        "molecules": [
            {"id": "mol1", "smiles": "CCO"}
        ],
        "models": ["hepatotoxicity", "cyp450", "herg", "solubility"],
        "therapeutic_area": "Oncology",
        "user_id": "user-uuid",
        "org_id": "org-uuid"
    }

    Returns:
    {
        "job_id": "uuid",
        "status": "queued",
        "message": "ADMET prediction job submitted"
    }
    """
    try:
        molecules = request.get('molecules', [])
        models = request.get('models', ['hepatotoxicity', 'cyp450', 'herg', 'solubility'])
        therapeutic_area = request.get('therapeutic_area', 'General')
        user_id = request.get('user_id')
        org_id = request.get('org_id')

        if not molecules:
            raise HTTPException(status_code=400, detail="No molecules provided")

        # Generate job ID
        job_id = str(uuid.uuid4())

        # Build job message
        message_body = {
            "job_id": job_id,
            "job_type": "predict_admet",
            "parameters": {
                "molecules": molecules,
                "models": models,
                "therapeutic_area": therapeutic_area,
                "user_id": user_id,
                "org_id": org_id
            },
            "timestamp": datetime.utcnow().isoformat()
        }

        # Submit to queue (Azure or AWS based on QUEUE_BACKEND)
        send_to_queue('novomcp-molecular-jobs', message_body)

        # Initialize Redis status for polling
        import redis.asyncio as redis
        redis_url = os.getenv('REDIS_URL', 'redis://novomcp-redis-cluster.qkrqx3.ng.0001.use1.cache.amazonaws.com:6379')
        redis_client = redis.from_url(redis_url, decode_responses=True)

        redis_key = f"novomcp:job:{job_id}"
        await redis_client.hset(
            redis_key,
            mapping={
                "status": "queued",
                "job_id": job_id,
                "job_type": "predict_admet",
                "submitted_at": datetime.utcnow().isoformat()
            }
        )
        await redis_client.expire(redis_key, 86400)  # 24 hours TTL (ADMET is slow, needs longer TTL)
        await redis_client.close()

        logger.info(f"Submitted ADMET prediction job {job_id} to SQS for {len(molecules)} molecules")

        return {
            "job_id": job_id,
            "status": "queued",
            "message": f"ADMET prediction job submitted for {len(molecules)} molecules"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit ADMET prediction job: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/predict-admet-status/{job_id}")
async def get_admet_job_status(job_id: str) -> Dict[str, Any]:
    """
    Get status of an ADMET prediction job from Redis.

    Returns:
    {
        "job_id": "uuid",
        "status": "queued|processing|completed|failed",
        "progress": {
            "percentage": 50,
            "message": "Predicting ADMET for molecule 5/10",
            "step": "predicting"
        },
        "results": {...}
    }
    """
    try:
        # Query Redis (molecular-worker uses novomcp:job:{job_id})
        import redis.asyncio as redis

        redis_url = os.getenv('REDIS_URL', 'redis://novomcp-redis-cluster.qkrqx3.ng.0001.use1.cache.amazonaws.com:6379')
        redis_client = redis.from_url(redis_url, decode_responses=True)

        # Get job status from Redis (molecular-worker uses novomcp:job:{job_id})
        key = f"novomcp:job:{job_id}"
        job_data = await redis_client.hgetall(key)

        await redis_client.close()

        if not job_data:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        # Parse progress and results JSON
        progress = json.loads(job_data.get('progress', '{}')) if job_data.get('progress') else {}
        # Note: molecular-worker saves as 'result' (singular)
        results = json.loads(job_data.get('result', '{}')) if job_data.get('result') else None

        return {
            "job_id": job_id,
            "status": job_data.get('status', 'unknown'),
            "progress": progress,
            "results": results
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get ADMET job status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/assess-faves-compliance-async")
async def assess_faves_compliance_async(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Submit FAVES compliance assessment job to molecular-worker SQS queue.

    Request body:
    {
        "molecules": [
            {"id": "mol1", "smiles": "CCO"}
        ],
        "assessments": ["ethical", "regulatory", "dual_use_risk"],
        "regulatory_frameworks": ["FDA", "EMA", "ICH"],
        "strict_mode": true,
        "user_id": "user-uuid",
        "org_id": "org-uuid"
    }

    Returns:
    {
        "job_id": "uuid",
        "status": "queued",
        "message": "FAVES compliance assessment job submitted"
    }
    """
    try:
        molecules = request.get('molecules', [])
        assessments = request.get('assessments', ['ethical', 'regulatory', 'dual_use_risk'])
        regulatory_frameworks = request.get('regulatory_frameworks', ['FDA', 'EMA', 'ICH'])
        strict_mode = request.get('strict_mode', True)
        user_id = request.get('user_id')
        org_id = request.get('org_id')

        if not molecules:
            raise HTTPException(status_code=400, detail="No molecules provided")

        # Generate job ID
        job_id = str(uuid.uuid4())

        # Build job message
        message_body = {
            "job_id": job_id,
            "job_type": "assess_faves_compliance",
            "parameters": {
                "molecules": molecules,
                "assessments": assessments,
                "regulatory_frameworks": regulatory_frameworks,
                "strict_mode": strict_mode,
                "user_id": user_id,
                "org_id": org_id
            },
            "timestamp": datetime.utcnow().isoformat()
        }

        # Submit to queue (Azure or AWS based on QUEUE_BACKEND)
        send_to_queue('novomcp-molecular-jobs', message_body)

        # Initialize Redis status for polling
        import redis.asyncio as redis
        redis_url = os.getenv('REDIS_URL', 'redis://novomcp-redis-cluster.qkrqx3.ng.0001.use1.cache.amazonaws.com:6379')
        redis_client = redis.from_url(redis_url, decode_responses=True)

        redis_key = f"novomcp:job:{job_id}"
        await redis_client.hset(
            redis_key,
            mapping={
                "status": "queued",
                "job_id": job_id,
                "job_type": "assess_faves_compliance",
                "submitted_at": datetime.utcnow().isoformat()
            }
        )
        await redis_client.expire(redis_key, 3600)  # 1 hour TTL
        await redis_client.close()

        logger.info(f"Submitted FAVES compliance job {job_id} to SQS for {len(molecules)} molecules")

        return {
            "job_id": job_id,
            "status": "queued",
            "message": f"FAVES compliance assessment job submitted for {len(molecules)} molecules"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit FAVES compliance job: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/assess-faves-compliance-status/{job_id}")
async def get_faves_compliance_job_status(job_id: str) -> Dict[str, Any]:
    """
    Get status of a FAVES compliance assessment job from Redis.

    Returns:
    {
        "job_id": "uuid",
        "status": "queued|processing|completed|failed",
        "progress": {
            "percentage": 50,
            "message": "Assessing compliance for molecule 5/10",
            "step": "assessing"
        },
        "results": {...}
    }
    """
    try:
        # Query Redis (molecular-worker uses novomcp:job:{job_id})
        import redis.asyncio as redis

        redis_url = os.getenv('REDIS_URL', 'redis://novomcp-redis-cluster.qkrqx3.ng.0001.use1.cache.amazonaws.com:6379')
        redis_client = redis.from_url(redis_url, decode_responses=True)

        # Get job status from Redis (molecular-worker uses novomcp:job:{job_id})
        key = f"novomcp:job:{job_id}"
        job_data = await redis_client.hgetall(key)

        await redis_client.close()

        if not job_data:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        # Parse progress and results JSON
        progress = json.loads(job_data.get('progress', '{}')) if job_data.get('progress') else {}
        # Note: molecular-worker saves as 'result' (singular)
        results = json.loads(job_data.get('result', '{}')) if job_data.get('result') else None

        return {
            "job_id": job_id,
            "status": job_data.get('status', 'unknown'),
            "progress": progress,
            "results": results
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get FAVES compliance job status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/dock-molecules-async")
async def dock_molecules_async(request: Dict[str, Any]) -> Dict[str, Any]:
    """Submit molecular docking job to molecular-worker SQS queue."""
    try:
        molecules = request.get('molecules', [])
        target_protein = request.get('target_protein')
        docking_params = request.get('docking_params', {'exhaustiveness': 16, 'num_modes': 9, 'energy_range': 3})

        if not molecules or not target_protein:
            raise HTTPException(status_code=400, detail="Molecules and target protein required")

        job_id = str(uuid.uuid4())

        # Build job message
        message_body = {
            "job_id": job_id,
            "job_type": "dock_molecules",
            "parameters": {
                "molecules": molecules,
                "target_protein": target_protein,
                "docking_params": docking_params,
                "user_id": request.get('user_id'),
                "org_id": request.get('org_id')
            },
            "timestamp": datetime.utcnow().isoformat()
        }

        # Submit to queue (Azure or AWS based on QUEUE_BACKEND)
        send_to_queue('novomcp-molecular-jobs', message_body)

        # Initialize Redis status for polling
        import redis.asyncio as redis
        redis_url = os.getenv('REDIS_URL', 'redis://novomcp-redis-cluster.qkrqx3.ng.0001.use1.cache.amazonaws.com:6379')
        redis_client = redis.from_url(redis_url, decode_responses=True)

        redis_key = f"novomcp:job:{job_id}"
        await redis_client.hset(
            redis_key,
            mapping={
                "status": "queued",
                "job_id": job_id,
                "job_type": "dock_molecules",
                "submitted_at": datetime.utcnow().isoformat()
            }
        )
        await redis_client.expire(redis_key, 3600)  # 1 hour TTL
        await redis_client.close()

        return {"job_id": job_id, "status": "queued", "message": f"Docking job submitted for {len(molecules)} molecules"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit docking job: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dock-molecules-status/{job_id}")
async def get_docking_job_status(job_id: str) -> Dict[str, Any]:
    """Get status of a molecular docking job from Redis."""
    try:
        import redis.asyncio as redis
        redis_url = os.getenv('REDIS_URL', 'redis://novomcp-redis-cluster.qkrqx3.ng.0001.use1.cache.amazonaws.com:6379')
        redis_client = redis.from_url(redis_url, decode_responses=True)

        job_data = await redis_client.hgetall(f"novomcp:job:{job_id}")
        await redis_client.close()

        if not job_data:
            raise HTTPException(status_code=404, detail="Job not found")

        status = job_data.get('status', 'unknown')
        results = {}

        if status == 'completed':
            result_data = job_data.get('result')
            if result_data:
                results = json.loads(result_data)

        return {
            "job_id": job_id,
            "status": status,
            "results": results
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get docking job status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/simulate-md-async")
async def simulate_md_async(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Submit molecular dynamics simulation job to molecular-worker SQS queue.

    Request body:
    {
        "complexes": [
            {"molecule_id": "mol1", "protein_pdb": "6OIM", "ligand_smiles": "CCO"}
        ],
        "simulation_params": {
            "simulation_time_ns": 10,
            "force_field": "AMBER99SB",
            "water_model": "TIP3P",
            "temperature_k": 300,
            "pressure_bar": 1.0,
            "include_analysis": true
        },
        "user_id": "user-uuid",
        "org_id": "org-uuid"
    }

    Returns:
    {
        "job_id": "uuid",
        "status": "queued",
        "message": "MD simulation job submitted"
    }
    """
    try:
        complexes = request.get('complexes', [])
        simulation_params = request.get('simulation_params', {
            'simulation_time_ns': 10,
            'force_field': 'AMBER99SB',
            'water_model': 'TIP3P'
        })
        user_id = request.get('user_id')
        org_id = request.get('org_id')

        if not complexes:
            raise HTTPException(status_code=400, detail="No protein-ligand complexes provided")

        # Generate job ID
        job_id = str(uuid.uuid4())

        # Build job message
        message_body = {
            "job_id": job_id,
            "job_type": "simulate_md",
            "parameters": {
                "complexes": complexes,
                "simulation_params": simulation_params,
                "user_id": user_id,
                "org_id": org_id
            },
            "timestamp": datetime.utcnow().isoformat()
        }

        # Submit to queue (Azure or AWS based on QUEUE_BACKEND)
        send_to_queue('novomcp-molecular-jobs', message_body)

        # Initialize Redis status for polling
        import redis.asyncio as redis
        redis_url = os.getenv('REDIS_URL', 'redis://novomcp-redis-cluster.qkrqx3.ng.0001.use1.cache.amazonaws.com:6379')
        redis_client = redis.from_url(redis_url, decode_responses=True)

        redis_key = f"novomcp:job:{job_id}"
        await redis_client.hset(
            redis_key,
            mapping={
                "status": "queued",
                "job_id": job_id,
                "job_type": "simulate_md",
                "submitted_at": datetime.utcnow().isoformat()
            }
        )
        await redis_client.expire(redis_key, 3600)  # 1 hour TTL
        await redis_client.close()

        logger.info(f"Submitted MD simulation job {job_id} to SQS for {len(complexes)} complexes")

        return {
            "job_id": job_id,
            "status": "queued",
            "message": f"MD simulation job submitted for {len(complexes)} complexes"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit MD simulation job: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/simulate-md-status/{job_id}")
async def get_md_simulation_job_status(job_id: str) -> Dict[str, Any]:
    """Get status of a molecular dynamics simulation job from Redis."""
    try:
        import redis.asyncio as redis
        redis_url = os.getenv('REDIS_URL', 'redis://novomcp-redis-cluster.qkrqx3.ng.0001.use1.cache.amazonaws.com:6379')
        redis_client = redis.from_url(redis_url, decode_responses=True)

        job_data = await redis_client.hgetall(f"novomcp:job:{job_id}")
        await redis_client.close()

        if not job_data:
            raise HTTPException(status_code=404, detail="Job not found")

        status = job_data.get('status', 'unknown')
        results = {}

        if status == 'completed':
            result_data = job_data.get('result')
            if result_data:
                results = json.loads(result_data)

        return {
            "job_id": job_id,
            "status": status,
            "results": results
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get MD simulation job status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/optimize-leads-async")
async def optimize_leads_async(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Submit lead optimization job to molecular-worker SQS queue.

    Request body:
    {
        "molecules": [{"id": "mol1", "smiles": "CCO"}],
        "target_protein_pdb_id": "6OIM",
        "optimization_type": "scaffold_hop",
        "max_variants": 10,
        "top_n": 5,
        "user_id": "user-uuid",
        "org_id": "org-uuid",
        "campaign_id": "campaign-uuid"
    }

    Returns:
    {
        "job_id": "uuid",
        "status": "queued",
        "message": "Lead optimization job submitted for N molecules"
    }
    """
    try:
        molecules = request.get('molecules', [])
        target_protein_pdb_id = request.get('target_protein_pdb_id')
        optimization_type = request.get('optimization_type', 'scaffold_hop')
        max_variants = request.get('max_variants', 10)
        top_n = request.get('top_n', 5)

        if not molecules or not target_protein_pdb_id:
            raise HTTPException(status_code=400, detail="Molecules and target protein required")

        job_id = str(uuid.uuid4())

        # Build job message
        message_body = {
            "job_id": job_id,
            "job_type": "optimize_leads",
            "parameters": {
                "molecules": molecules,
                "target_protein_pdb_id": target_protein_pdb_id,
                "optimization_type": optimization_type,
                "max_variants": max_variants,
                "top_n": top_n,
                "user_id": request.get('user_id'),
                "org_id": request.get('org_id'),
                "campaign_id": request.get('campaign_id')
            },
            "timestamp": datetime.utcnow().isoformat()
        }

        # Submit to queue (Azure or AWS based on QUEUE_BACKEND)
        send_to_queue('novomcp-molecular-jobs', message_body)

        # Initialize Redis status for polling
        import redis.asyncio as redis
        redis_url = os.getenv('REDIS_URL', 'redis://novomcp-redis-cluster.qkrqx3.ng.0001.use1.cache.amazonaws.com:6379')
        redis_client = redis.from_url(redis_url, decode_responses=True)

        redis_key = f"novomcp:job:{job_id}"
        await redis_client.hset(
            redis_key,
            mapping={
                "status": "queued",
                "job_id": job_id,
                "job_type": "optimize_leads",
                "submitted_at": datetime.utcnow().isoformat()
            }
        )
        await redis_client.expire(redis_key, 3600)  # 1 hour TTL
        await redis_client.close()

        return {
            "job_id": job_id,
            "status": "queued",
            "message": f"Lead optimization job submitted for {len(molecules)} molecules"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit lead optimization job: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/optimize-leads-status/{job_id}")
async def get_lead_optimization_job_status(job_id: str) -> Dict[str, Any]:
    """Get status of a lead optimization job from Redis."""
    try:
        import redis.asyncio as redis
        redis_url = os.getenv('REDIS_URL', 'redis://novomcp-redis-cluster.qkrqx3.ng.0001.use1.cache.amazonaws.com:6379')
        redis_client = redis.from_url(redis_url, decode_responses=True)

        job_data = await redis_client.hgetall(f"novomcp:job:{job_id}")
        await redis_client.close()

        if not job_data:
            raise HTTPException(status_code=404, detail="Job not found")

        status = job_data.get('status', 'unknown')
        results = {}

        if status == 'completed':
            result_data = job_data.get('result')
            if result_data:
                results = json.loads(result_data)

        return {
            "job_id": job_id,
            "status": status,
            "results": results
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get lead optimization job status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-manuscript-async")
async def generate_manuscript_async(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Submit manuscript generation job to molecular-worker SQS queue.

    Request body:
    {
        "campaign_id": "uuid",
        "manuscript_type": "auto"  // auto, full_manuscript, or internal_report
    }

    Returns:
    {
        "job_id": "uuid",
        "status": "queued",
        "message": "Manuscript generation job submitted"
    }
    """
    try:
        campaign_id = request.get('campaign_id')
        manuscript_type = request.get('manuscript_type', 'auto')

        if not campaign_id:
            raise HTTPException(status_code=400, detail="campaign_id is required")

        job_id = str(uuid.uuid4())

        # Build job message
        message_body = {
            "job_id": job_id,
            "job_type": "manuscript",
            "campaign_id": campaign_id,
            "parameters": {
                "campaign_id": campaign_id,
                "manuscript_type": manuscript_type
            }
        }

        # Submit to queue (Azure or AWS based on QUEUE_BACKEND)
        send_to_queue('novomcp-molecular-jobs', message_body)

        logger.info(f"📄 Manuscript generation job {job_id} submitted for campaign {campaign_id}")

        # Initialize Redis tracking
        import redis.asyncio as redis
        redis_url = os.getenv('REDIS_URL', 'redis://novomcp-redis-cluster.qkrqx3.ng.0001.use1.cache.amazonaws.com:6379')
        redis_client = redis.from_url(redis_url, decode_responses=True)

        redis_key = f"novomcp:job:{job_id}"
        await redis_client.hset(
            redis_key,
            mapping={
                "status": "queued",
                "job_id": job_id,
                "job_type": "manuscript",
                "campaign_id": campaign_id,
                "submitted_at": datetime.utcnow().isoformat()
            }
        )
        await redis_client.expire(redis_key, 3600)  # 1 hour TTL
        await redis_client.close()

        return {
            "job_id": job_id,
            "status": "queued",
            "message": f"Manuscript generation job submitted for campaign {campaign_id}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit manuscript generation job: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/generate-manuscript-status/{job_id}")
async def get_manuscript_job_status(job_id: str) -> Dict[str, Any]:
    """
    Get status of a manuscript generation job from Redis.

    Returns:
    {
        "job_id": "uuid",
        "status": "queued|processing|completed|failed",
        "progress": {
            "percentage": 50,
            "message": "Generating manuscript content with GPT-5",
            "step": "generating"
        },
        "results": {...}  // Only present when completed
    }
    """
    try:
        import redis.asyncio as redis
        redis_url = os.getenv('REDIS_URL', 'redis://novomcp-redis-cluster.qkrqx3.ng.0001.use1.cache.amazonaws.com:6379')
        redis_client = redis.from_url(redis_url, decode_responses=True)

        job_data = await redis_client.hgetall(f"novomcp:job:{job_id}")
        await redis_client.close()

        if not job_data:
            raise HTTPException(status_code=404, detail="Job not found")

        # Parse progress and results JSON
        progress = json.loads(job_data.get('progress', '{}')) if job_data.get('progress') else {}
        results = json.loads(job_data.get('result', '{}')) if job_data.get('result') else None

        return {
            "job_id": job_id,
            "status": job_data.get('status', 'unknown'),
            "progress": progress,
            "results": results
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get manuscript job status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/score-molecules-status/{job_id}")
async def get_scoring_job_status(job_id: str) -> Dict[str, Any]:
    """
    Get status of an AI scoring job from Redis.

    Returns:
    {
        "job_id": "uuid",
        "status": "queued|processing|completed|failed",
        "progress": {
            "percentage": 50,
            "message": "Scoring molecule 5/10",
            "step": "scoring"
        },
        "results": {...}  // Only present when completed
    }
    """
    try:
        # Query Redis (molecular-worker writes job status to Redis)
        import redis.asyncio as redis

        redis_url = os.getenv('REDIS_URL', 'redis://novomcp-redis-cluster.qkrqx3.ng.0001.use1.cache.amazonaws.com:6379')
        redis_client = redis.from_url(redis_url, decode_responses=True)

        # Get job status from Redis (molecular-worker uses novomcp:job:{job_id})
        key = f"novomcp:job:{job_id}"
        job_data = await redis_client.hgetall(key)

        await redis_client.close()

        if not job_data:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        # Parse progress and results JSON
        progress = json.loads(job_data.get('progress', '{}')) if job_data.get('progress') else {}
        # Note: molecular-worker saves as 'result' (singular)
        results = json.loads(job_data.get('result', '{}')) if job_data.get('result') else None

        return {
            "job_id": job_id,
            "status": job_data.get('status', 'unknown'),
            "progress": progress,
            "results": results
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get job status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _real_service_call(call: Dict[str, Any]) -> Dict[str, Any]:
    """Make real service calls using service configuration"""
    service_name = call.get("service")
    endpoint = call.get("endpoint", "")
    method = call.get("method", "POST")
    payload = call.get("payload", {})

    # Get circuit breaker and metrics collector
    circuit_manager = get_circuit_manager()
    circuit_breaker = circuit_manager.get_breaker(service_name)
    metrics = get_metrics_collector()

    async def make_call():
        start_time = time.time()

        # Use novomcp proxy for service calls to avoid direct ALB access issues
        # This routes through the same proxy layer that frontend uses
        novomcp_url = os.getenv("NOVOMCP_ENGINE_URL", "https://api.novomcp.com")

        # Construct proxy URL using the same pattern as frontend
        # Format: https://api.novomcp.com/proxy/{service_name}/{endpoint}
        # ALB will rewrite /proxy/ to /api/ and route to proxy router
        clean_endpoint = endpoint.lstrip('/')
        url = f"{novomcp_url}/proxy/{service_name}/{clean_endpoint}"

        logger.info(f"[TRACE] Constructed URL: {url}")
        logger.info(f"[TRACE] Method: {method}, Payload size: {len(json.dumps(payload))} bytes")
        logger.debug(f"Service call to {service_name}: {url}")

        # Prepare headers - proxy will handle authentication to services
        headers = {"Content-Type": "application/json"}
        # Add internal API key for novomcp proxy authentication
        headers["X-API-Key"] = os.getenv("API_KEY", "")

        # Make the actual HTTP request with timeout and retries
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Implement retry logic
            max_retries = 3
            retry_delay = 1.0

            for attempt in range(max_retries):
                try:
                    if method == "GET":
                        response = await client.get(url, headers=headers)
                    elif method == "POST":
                        response = await client.post(url, json=payload, headers=headers)
                    elif method == "PUT":
                        response = await client.put(url, json=payload, headers=headers)
                    elif method == "DELETE":
                        response = await client.delete(url, headers=headers)
                    else:
                        raise ValueError(f"Unsupported HTTP method: {method}")

                    # Record metrics
                    duration = time.time() - start_time
                    metrics.record_service_call(
                        service_name,
                        endpoint,
                        duration,
                        "success" if response.status_code < 300 else "error",
                        response.status_code
                    )

                    # Check response status
                    if response.status_code >= 200 and response.status_code < 300:
                        try:
                            response_data = response.json()
                        except:
                            response_data = {"raw": response.text}

                        # Broadcast significant service results
                        if service_name in ['molecular-worker', 'molecular-intelligence', 'addie-models'] and payload.get('campaign_id'):
                            await broadcast_global_update('service_result', {
                                'campaign_id': payload.get('campaign_id'),
                                'service': service_name,
                                'action': endpoint,
                                'success': True,
                                'timestamp': datetime.utcnow().isoformat()
                            })

                        return {
                            "service": service_name,
                            "endpoint": endpoint,
                            "status": "success",
                            "data": response_data,
                            "timestamp": datetime.utcnow().isoformat()
                        }
                    elif response.status_code >= 500:
                        # Server error, retry
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_delay * (attempt + 1))
                            continue
                        else:
                            raise httpx.HTTPStatusError(
                                f"Service returned {response.status_code}",
                                request=response.request,
                                response=response
                            )
                    else:
                        # Client error, don't retry
                        raise httpx.HTTPStatusError(
                            f"Service returned {response.status_code}: {response.text}",
                            request=response.request,
                            response=response
                        )

                except httpx.TimeoutException as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"Timeout calling {service_name}/{endpoint}, retrying...")
                        await asyncio.sleep(retry_delay * (attempt + 1))
                        continue
                    else:
                        raise e
                except httpx.ConnectError as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"Connection error to {service_name}/{endpoint}, retrying...")
                        await asyncio.sleep(retry_delay * (attempt + 1))
                        continue
                    else:
                        raise e

        # Record failed metric
        duration = time.time() - start_time
        metrics.record_service_call(
            service_name,
            endpoint,
            duration,
            "error",
            None
        )
        raise

    # Use circuit breaker to make the call
    try:
        return await circuit_breaker.call(make_call)
    except CircuitOpenException:
        # Circuit is open, return degraded response
        metrics.increment_counter(f"circuit_open.{service_name}")

        # Broadcast service degradation
        await broadcast_global_update('service_degraded', {
            'service': service_name,
            'status': 'circuit_open',
            'campaign_impact': payload.get('campaign_id') if payload else None,
            'timestamp': datetime.utcnow().isoformat()
        })

        return {
            "service": service_name,
            "endpoint": endpoint,
            "status": "circuit_open",
            "message": f"Service {service_name} is temporarily unavailable",
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Error calling service {service_name}/{endpoint}: {str(e)}")
        return {
            "service": service_name,
            "endpoint": endpoint,
            "status": "error",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }


async def _mock_service_call(call: Dict[str, Any]) -> Dict[str, Any]:
    """Fallback mock service call for testing"""
    logger.warning(f"Using mock service call for {call.get('service')}/{call.get('endpoint')}")
    await asyncio.sleep(0.1)  # Simulate network latency

    return {
        "service": call.get("service"),
        "endpoint": call.get("endpoint"),
        "status": "mock",
        "data": {
            "message": f"Mock response from {call.get('service')}",
            "timestamp": datetime.utcnow().isoformat()
        }
    }


# In-memory storage for pending interventions (replace with database in production)
_pending_interventions: Dict[str, Dict[str, Any]] = {}


@router.get("/interventions/pending")
async def get_pending_interventions() -> List[Dict[str, Any]]:
    """
    Get all pending interventions requiring human decision.
    Returns interventions sorted by urgency and timestamp.
    """
    try:
        # Return all pending interventions sorted by urgency and timestamp
        interventions = list(_pending_interventions.values())

        # Sort by urgency (high > medium > low) and timestamp (oldest first)
        urgency_order = {"high": 0, "medium": 1, "low": 2}
        interventions.sort(key=lambda x: (
            urgency_order.get(x.get("urgency", "medium"), 1),
            x.get("timestamp", "")
        ))

        return interventions
    except Exception as e:
        logger.error(f"Failed to get pending interventions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/interventions/decide")
async def decide_intervention(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Submit a human decision for a pending intervention.

    Request body:
    {
        "decision_id": "intervention-uuid",
        "choice": "approve" | "reject" | "modify",
        "reasoning": "Human reasoning for the decision",
        "modifications": {...}  // Optional modifications if choice is "modify"
    }
    """
    try:
        decision_id = request.get("decision_id")
        choice = request.get("choice")
        reasoning = request.get("reasoning", "")
        modifications = request.get("modifications")

        if not decision_id or not choice:
            raise HTTPException(
                status_code=400,
                detail="decision_id and choice are required"
            )

        # Get the intervention
        intervention = _pending_interventions.get(decision_id)
        if not intervention:
            raise HTTPException(
                status_code=404,
                detail=f"Intervention {decision_id} not found"
            )

        # Record the decision
        decision_record = {
            "intervention_id": decision_id,
            "campaign_id": intervention.get("campaign_id"),
            "choice": choice,
            "reasoning": reasoning,
            "modifications": modifications,
            "decided_at": datetime.utcnow().isoformat(),
            "decided_by": request.get("user_id", "unknown")
        }

        # Remove from pending
        del _pending_interventions[decision_id]

        # Broadcast the decision globally
        await broadcast_global_update('intervention_resolved', {
            "intervention_id": decision_id,
            "campaign_id": intervention.get("campaign_id"),
            "choice": choice,
            "timestamp": datetime.utcnow().isoformat()
        })

        logger.info(f"Intervention {decision_id} resolved with choice: {choice}")

        return {
            "success": True,
            "decision": decision_record,
            "message": f"Intervention resolved: {choice}"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to decide intervention: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/interventions/create")
async def create_intervention(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a new intervention requiring human decision.
    Used internally by the AI system when human input is needed.

    Request body:
    {
        "campaign_id": "campaign-uuid",
        "type": "decision_review" | "parameter_adjustment" | "error_handling",
        "urgency": "high" | "medium" | "low",
        "description": "Description of what needs human input",
        "context": {...},
        "options": ["option1", "option2", ...],
        "deadline": "ISO timestamp" // optional
    }
    """
    try:
        intervention_id = f"intervention_{uuid.uuid4()}"

        intervention = {
            "id": intervention_id,
            "campaign_id": request.get("campaign_id"),
            "campaign_name": request.get("campaign_name", "Unknown Campaign"),
            "type": request.get("type", "decision_review"),
            "urgency": request.get("urgency", "medium"),
            "description": request.get("description"),
            "context": request.get("context", {}),
            "options": request.get("options", ["approve", "reject"]),
            "deadline": request.get("deadline"),
            "timestamp": datetime.utcnow().isoformat(),
            "status": "pending"
        }

        # Store in pending interventions
        _pending_interventions[intervention_id] = intervention

        # Broadcast intervention creation
        await broadcast_global_update('intervention_required', intervention)

        logger.info(f"Created intervention {intervention_id} for campaign {request.get('campaign_id')}")

        return {
            "success": True,
            "intervention_id": intervention_id,
            "intervention": intervention
        }

    except Exception as e:
        logger.error(f"Failed to create intervention: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Internal Helper Functions for Campaign Loop Manager
# These are called directly by campaign_loop.py (no HTTP overhead)
# ============================================================================

async def _submit_and_poll_async_job(
    submit_func: Callable,
    status_func: Callable,
    payload: Dict[str, Any],
    campaign_id: str,
    timeout_seconds: int = 300
) -> Optional[Dict[str, Any]]:
    """
    Submit async job to SQS queue, poll for completion, return results.
    Uses internal function calls (same process, no HTTP overhead).

    This replaces the broken _run_pipeline pattern with proper async endpoints.
    Follows the async job polling pattern via SQS + Redis.

    Args:
        submit_func: Async function to submit job (e.g., calculate_properties_async)
        status_func: Async function to check status (e.g., get_properties_job_status)
        payload: Job payload dict
        campaign_id: Campaign ID for logging
        timeout_seconds: Max time to wait for completion

    Returns:
        Results dict if successful, None if failed/timeout
    """
    try:
        # 1. Submit job to SQS
        submit_response = await submit_func(payload)
        job_id = submit_response.get("job_id")

        if not job_id:
            logger.error(f"Campaign {campaign_id}: No job_id returned from {submit_func.__name__}")
            return None

        logger.info(f"Campaign {campaign_id}: Submitted {submit_func.__name__} job {job_id}")

        # 2. Poll for completion with exponential backoff
        start_time = time.time()
        poll_interval = 2  # Start with 2 seconds

        while (time.time() - start_time) < timeout_seconds:
            await asyncio.sleep(poll_interval)

            # Check status from Redis/S3
            status_response = await status_func(job_id)
            status = status_response.get("status")

            if status == "completed":
                results = status_response.get("results")
                logger.info(f"Campaign {campaign_id}: Job {job_id} completed in {time.time() - start_time:.1f}s")
                return results

            elif status == "failed":
                error = status_response.get("error", "Unknown error")
                logger.error(f"Campaign {campaign_id}: Job {job_id} failed: {error}")
                return None

            # Exponential backoff: 2s → 3s → 4.5s → 6.75s → ... (max 15s)
            poll_interval = min(poll_interval * 1.5, 15)

        # 3. Timeout - log warning and return None (campaign continues degraded)
        logger.warning(f"Campaign {campaign_id}: Job {job_id} timed out after {timeout_seconds}s")
        return None

    except Exception as e:
        logger.error(f"Campaign {campaign_id}: Async job submission failed: {e}")
        return None


async def orchestrate_decision(campaign_id: str, action: str, parameters: Dict, context: Dict) -> Dict[str, Any]:
    """
    Execute campaign decisions by orchestrating microservice calls.
    Called directly by campaign loop manager.

    NOW INTEGRATED WITH WORKFLOW ENGINE for structured phase-based execution.

    Args:
        campaign_id: Campaign identifier
        action: Decision action (generate_new_molecules, optimize_existing_leads, etc.)
        parameters: Action-specific parameters from decision engine
        context: Campaign context (goal, constraints, metrics, workflow_state, circuit_breaker_state)

    Returns:
        Execution result with status, artifacts, and metrics
    """
    try:
        from ai.workflow_engine import WorkflowEngine, PhaseAction
        from ai.circuit_breaker import CampaignCircuitBreaker, FailureCategory

        logger.info(f"Orchestrating {action} for campaign {campaign_id}")
        await _broadcast_progress(campaign_id, "orchestration_started", {"action": action})

        # Initialize circuit breaker
        circuit_breaker_data = context.get("circuit_breaker_state", {})
        circuit_breaker = CampaignCircuitBreaker.from_dict(campaign_id, circuit_breaker_data, context) if circuit_breaker_data else CampaignCircuitBreaker(campaign_id, context)

        # Check if circuit breaker allows operation
        can_proceed, reason = circuit_breaker.can_proceed()
        if not can_proceed:
            logger.warning(f"Circuit breaker blocked operation for campaign {campaign_id}: {reason}")
            return {
                "status": "blocked",
                "message": reason,
                "circuit_breaker": circuit_breaker.get_state()
            }

        # Determine if using workflow engine or legacy action dispatch
        use_workflow_engine = context.get("use_workflow_engine", True)

        if use_workflow_engine:
            # NEW: Use workflow engine for structured phase-based execution
            logger.info(f"Campaign {campaign_id} using workflow engine")

            # Build campaign config with full context
            campaign_config = {
                "id": campaign_id,
                "campaign_id": campaign_id,
                "goal": context.get("goal"),
                "constraints": context.get("constraints"),
                "dataSources": context.get("dataSources"),
                "autonomy": context.get("autonomy"),
                "workflow_state": context.get("workflow_state"),
                "target_protein": context.get("target_protein"),
                "iteration_number": context.get("iteration_number"),  # Required for quality gate storage
                "quantum_enabled": context.get("quantum_enabled", False)
            }

            # Initialize workflow engine
            workflow_engine = WorkflowEngine(campaign_config)

            # Execute current phase
            phase_result = await workflow_engine.execute_current_phase(orchestrate_decision)

            # Record success/failure in circuit breaker
            if phase_result.get("status") == "success":
                circuit_breaker.record_success(phase_result.get("phase", ""))
            else:
                circuit_result = circuit_breaker.record_failure(
                    phase=phase_result.get("phase", "unknown"),
                    category=FailureCategory.SERVICE_ERROR,
                    error=phase_result.get("message", "Unknown error")
                )
                if circuit_result["should_halt"]:
                    # Circuit breaker tripped - request human intervention
                    intervention = circuit_breaker.get_intervention_request()
                    await _execute_request_human_input(campaign_id, intervention, context)
                    phase_result["circuit_breaker_tripped"] = True
                    phase_result["intervention_requested"] = True

            # Evaluate quality gate
            gate_result = await workflow_engine.evaluate_phase_gate(phase_result)

            # Record quality gate failure if applicable
            if not gate_result["passed"]:
                circuit_result = circuit_breaker.record_failure(
                    phase=gate_result.get("phase", "unknown"),
                    category=FailureCategory.QUALITY_GATE,
                    error=f"Quality gate failed: {gate_result.get('gate_results')}"
                )
                if circuit_result["should_halt"]:
                    intervention = circuit_breaker.get_intervention_request()
                    await _execute_request_human_input(campaign_id, intervention, context)
                    gate_result["circuit_breaker_tripped"] = True
                    gate_result["intervention_requested"] = True
            else:
                circuit_breaker.record_success(gate_result.get("phase", ""))

            # Persist quality gate evaluation
            try:
                for gate_eval in gate_result.get("gate_results", []):
                    await _real_service_call({
                        'service': 'db-manager',
                        'endpoint': '/quality-gate-evaluations',
                        'method': 'POST',
                        'payload': {
                            'campaign_id': campaign_id,
                            'gate_id': gate_eval.get("gate_id"),
                            'phase': gate_result.get("phase"),
                            'passed': gate_eval.get("passed"),
                            'failures': json.dumps(gate_eval.get("failures", [])),
                            'metrics': json.dumps(gate_eval.get("metrics", {})),
                            'molecules_evaluated': gate_eval.get("molecules_evaluated", 0),
                            'molecules_passed': gate_eval.get("molecules_passed", 0)
                        }
                    })
            except Exception as e:
                logger.error(f"Failed to persist quality gate evaluation: {e}")

            # Execute phase transition
            phase_action = PhaseAction(gate_result["action"])
            next_phase = gate_result.get("next_phase")

            await workflow_engine.transition_phase(
                phase_action,
                next_phase,
                reason=f"Quality gate {'passed' if gate_result['passed'] else 'failed'}"
            )

            # Build comprehensive result
            result = {
                "status": phase_result.get("status"),
                "phase": phase_result.get("phase"),
                "phase_result": phase_result,
                "gate_result": gate_result,
                "workflow_state": workflow_engine.get_workflow_state(),
                "circuit_breaker_state": circuit_breaker.to_dict(),
                "action_taken": phase_action.value,
                "next_phase": next_phase.value if next_phase else None
            }

            # Defensive: ensure dict result shape
            if not isinstance(result, dict):
                result = {"status": "error", "message": f"Unexpected result type: {type(result).__name__}", "raw_result": str(result)}

        else:
            # LEGACY: Old action dispatch (kept for backward compatibility)
            logger.info(f"Campaign {campaign_id} using legacy action dispatch")

            if action == "generate_new_molecules":
                result = await _execute_generate_molecules(campaign_id, parameters, context)
            elif action == "optimize_existing_leads":
                result = await _execute_optimize_leads(campaign_id, parameters, context)
            elif action == "screen_compounds":
                result = await _execute_screen_compounds(campaign_id, parameters, context)
            elif action == "pivot_strategy":
                result = await _execute_pivot_strategy(campaign_id, parameters, context)
            elif action == "expand_chemical_space":
                result = await _execute_expand_space(campaign_id, parameters, context)
            elif action == "adjust_parameters":
                result = await _execute_adjust_parameters(campaign_id, parameters, context)
            elif action == "complete_milestone":
                result = await _execute_complete_milestone(campaign_id, parameters, context)
            elif action == "request_human_input":
                result = await _execute_request_human_input(campaign_id, parameters, context)
            else:
                result = {"status": "error", "message": f"Unknown action: {action}"}

            # Record in circuit breaker
            if result.get("status") == "success":
                circuit_breaker.record_success()
            else:
                circuit_result = circuit_breaker.record_failure(
                    phase=action,
                    category=FailureCategory.SERVICE_ERROR,
                    error=result.get("message", "Unknown error")
                )
                if circuit_result["should_halt"]:
                    intervention = circuit_breaker.get_intervention_request()
                    await _execute_request_human_input(campaign_id, intervention, context)
                    result["circuit_breaker_tripped"] = True

            result["circuit_breaker_state"] = circuit_breaker.to_dict()

            # Defensive: ensure dict result shape
            if not isinstance(result, dict):
                result = {"status": "error", "message": f"Unexpected result type: {type(result).__name__}", "raw_result": str(result)}

        # Broadcast completion
        await _broadcast_progress(campaign_id, "orchestration_completed", {
            "action": action,
            "status": result.get("status"),
            "summary": result.get("summary", ""),
            "phase": result.get("phase")
        })

        # Persist decision to SQL database via db-manager (only persist meaningful progress, not errors)
        if result.get("status") == "success":
            try:
                await _real_service_call({
                    'service': 'db-manager',
                    'endpoint': '/campaign-decisions',
                    'method': 'POST',
                    'payload': {
                        'campaign_id': str(campaign_id),  # Convert UUID to string for JSON serialization
                        'decision_type': result.get("phase", action),
                        'reasoning': parameters.get('reasoning', f"Workflow phase: {result.get('phase')}"),
                        'input_context': parameters,
                        'outcome': result.get("phase_result", result),
                        'success_score': 1.0
                    }
                })
                logger.info(f"Persisted decision to SQL for campaign {campaign_id} (phase: {result.get('phase')})")
            except Exception as e:
                logger.error(f"Failed to persist decision to SQL: {e}")

        return result

    except Exception as e:
        logger.error(f"Error orchestrating decision: {e}", exc_info=True)
        await _broadcast_progress(campaign_id, "orchestration_failed", {"action": action, "error": str(e)})
        return {"status": "error", "message": str(e)}


# ============================================================================
# SHARED ORCHESTRATION HELPERS
# ============================================================================

async def _run_pipeline(steps: List[Dict], campaign_id: str) -> Dict[str, Any]:
    """
    Execute a pipeline of service calls with circuit breaker protection.

    Args:
        steps: List of step dicts with {service, endpoint, method, payload, on_success}
        campaign_id: Campaign identifier for logging/metrics

    Returns:
        Pipeline execution result
    """
    results = []

    for i, step in enumerate(steps):
        try:
            logger.info(f"Pipeline step {i+1}/{len(steps)}: {step.get('description', step['service'])}")

            # Call service
            response = await _real_service_call({
                'service': step['service'],
                'endpoint': step['endpoint'],
                'method': step.get('method', 'POST'),
                'payload': step.get('payload'),
                'timeout': step.get('timeout', 30)
            })

            if response.get('status') != 'success':
                error_msg = response.get('message', 'Service call failed')
                logger.error(f"Pipeline step {i+1} failed: {error_msg}")
                return {
                    "status": "error",
                    "failed_step": i+1,
                    "step_description": step.get('description', step['service']),
                    "error": error_msg,
                    "partial_results": results
                }

            # Store result
            # Ensure result_data is never None to prevent downstream .get() errors
            result_data = response.get('data') if response.get('data') is not None else response
            results.append(result_data)

            # Execute on_success callback if provided
            if step.get('on_success'):
                await step['on_success'](result_data, campaign_id)

        except Exception as e:
            logger.error(f"Pipeline step {i+1} exception: {e}", exc_info=True)
            return {
                "status": "error",
                "failed_step": i+1,
                "error": str(e),
                "partial_results": results
            }

    return {
        "status": "success",
        "results": results,
        "steps_completed": len(steps)
    }

 


async def _record_results(campaign_id: str, result_type: str, data: Dict) -> None:
    """
    Persist orchestration results to database and update metrics.

    Args:
        campaign_id: Campaign identifier
        result_type: Type of result (molecules, scores, experiments, etc.)
        data: Result data to persist
    """
    try:
        # Update aggregated metrics based on result type
        if result_type == "molecules_generated":
            count = data.get("count", len(data.get("molecules", [])))
            await _update_campaign_metrics(campaign_id, {"molecules_generated": count}, increment=True)
        elif result_type == "leads_optimized":
            count = len(data.get("optimized_leads", []))
            await _update_campaign_metrics(campaign_id, {"successful_leads": count}, increment=True)
        elif result_type == "experiments_run":
            count = data.get("experiment_count", 1)
            await _update_campaign_metrics(campaign_id, {"experiments_run": count}, increment=True)

        logger.info(f"Recorded {result_type} for campaign {campaign_id}")

    except Exception as e:
        logger.error(f"Failed to record results: {e}")


async def _update_campaign_metrics(campaign_id: str, metrics: Dict, increment: bool = False) -> None:
    """Update campaign metrics via db-manager"""
    try:
        payload = {
            "metrics": metrics,
            "increment": increment
        }

        await _real_service_call({
            'service': "db-manager",
            'endpoint': f"/campaigns/{campaign_id}/metrics",
            'method': "POST",
            'payload': payload
        })
    except Exception as e:
        logger.error(f"Failed to update metrics: {e}")


async def _broadcast_progress(campaign_id: str, event_type: str, data: Dict) -> None:
    """
    Broadcast orchestration progress via WebSocket/Redis.

    Args:
        campaign_id: Campaign identifier
        event_type: Event type (orchestration_started, step_completed, etc.)
        data: Event data
    """
    try:
        event_data = {
            "type": event_type,
            "campaign_id": campaign_id,
            "timestamp": datetime.utcnow().isoformat(),
            **data
        }

        await broadcast_global_update(event_type, event_data)

    except Exception as e:
        logger.error(f"Failed to broadcast progress: {e}")


async def _schedule_async_work(campaign_id: str, job_type: str, payload: Dict) -> Dict[str, Any]:
    """
    Schedule long-running work with molecular-worker.

    Args:
        campaign_id: Campaign identifier
        job_type: Type of job (docking, md_simulation, screening, etc.)
        payload: Job payload

    Returns:
        Job handle with job_id for tracking
    """
    try:
        job_payload = {
            "campaign_id": campaign_id,
            "job_type": job_type,
            "parameters": payload,
            "priority": "normal"
        }

        response = await _real_service_call({
            'service': "molecular-worker",
            'endpoint': "/jobs/submit",
            'method': "POST",
            'payload': job_payload,
            'timeout': 10
        })

        if response.get('status') == 'success':
            job_id = response.get('data', {}).get('job_id')
            logger.info(f"Scheduled {job_type} job {job_id} for campaign {campaign_id}")
            return {"status": "scheduled", "job_id": job_id, "job_type": job_type}
        else:
            return {"status": "error", "message": response.get('message')}

    except Exception as e:
        logger.error(f"Failed to schedule async work: {e}")
        return {"status": "error", "message": str(e)}


# ============================================================================
# ACTION-SPECIFIC EXECUTION HANDLERS
# ============================================================================

def _validate_smiles(smiles: str) -> bool:
    """
    Validate SMILES string using RDKit.
    Returns True if valid, False otherwise.
    """
    try:
        from rdkit import Chem
        mol = Chem.MolFromSmiles(smiles)
        return mol is not None
    except Exception as e:
        logger.debug(f"SMILES validation error for '{smiles}': {e}")
        return False

async def _execute_generate_molecules(campaign_id: str, parameters: Dict, context: Dict) -> Dict[str, Any]:
    """
    Execute molecule generation pipeline:
    molecular-intelligence (PubChem enriched 115M, 53 columns) → Quality Gates

    Returns molecules with all 53 pre-calculated columns (no chem-props/ADMET service calls needed)
    """
    try:
        # Convert all UUID objects to strings for JSON serialization
        campaign_id = str(campaign_id)
        user_id = str(context.get("user_id", "system"))
        org_id = str(context.get("org_id") or context.get("tenant_id", ""))

        logger.info(f"[TRACE] _execute_generate_molecules called for campaign {campaign_id}")
        logger.info(f"[PRODUCTION] Using enriched PubChem data (53 columns) - NO Chem-Props or ADMET service calls")

        # Support both 'batch_size' (from intelligent_config) and 'count' (legacy), default to 1000 (PRODUCTION)
        count = parameters.get('batch_size', parameters.get('count', 1000))
        strategy = parameters.get('strategy', 'evolutionary')
        constraints = parameters.get('constraints', {})
        seed_molecules = parameters.get('seed_molecules') or []  # Handle None case

        logger.info(f"Generating {count} molecules for campaign {campaign_id} with strategy {strategy}, constraints_present={bool(constraints)}, seed_molecules={len(seed_molecules)}")
        logger.info(f"[PRODUCTION] Expected Phase 1+2 runtime: <15 seconds (instant PyArrow query + validation)")

        # PRODUCTION: Use molecular-intelligence (PubChem enriched, 115M molecules with 53 pre-calculated columns)
        # Transform constraints to molecular-intelligence format
        molecule_constraints = None
        if constraints:
            # Prefer already-compatible nested format from intelligent_config
            if isinstance(constraints.get("molecular"), dict):
                _mol = constraints.get("molecular", {})
                _mw = _mol.get("mw", {}) if isinstance(_mol, dict) else {}
                _logp_lc = _mol.get("logp", {}) if isinstance(_mol, dict) else {}
                _logp_uc = _mol.get("logP", {}) if isinstance(_mol, dict) else {}
                molecule_constraints = {
                    "molecular": {
                        "mw": {
                            "min": _mw.get("min", 200),
                            "max": _mw.get("max", 500),
                        },
                        "logp": {
                            "min": (_logp_lc.get("min") if isinstance(_logp_lc, dict) and _logp_lc.get("min") is not None else _logp_uc.get("min", -0.4)),
                            "max": (_logp_lc.get("max") if isinstance(_logp_lc, dict) and _logp_lc.get("max") is not None else _logp_uc.get("max", 5.6)),
                        },
                    },
                    # Respect caller-provided preference; default to True only if unspecified
                    "force_druglike": constraints.get("force_druglike", True),
                    "allowed_elements": constraints.get("allowed_elements", ["C", "N", "O", "S", "F", "Cl", "Br"]),
                }
            else:
                # Backward-compat: support flattened keys
                molecule_constraints = {
                    "molecular": {
                        "mw": {
                            "min": constraints.get('mw_min', 200),
                            "max": constraints.get('mw_max', 500)
                        },
                        "logp": {
                            "min": constraints.get('logp_min', -0.4),
                            "max": constraints.get('logp_max', 5.6)
                        }
                    },
                    # Respect caller-provided preference; default to True only if unspecified
                    "force_druglike": constraints.get("force_druglike", True),
                    "allowed_elements": constraints.get("allowed_elements", ["C", "N", "O", "S", "F", "Cl", "Br"])
                }

        generation_payload = {
            "batch_size": count,
            "algorithm": "pubchem-enriched-sampling",
            "seed_molecules": seed_molecules,  # For iterative refinement (Lead-Like partition)
            "constraints": molecule_constraints,
            # Include campaign context so proxy layer can broadcast service_result events
            "campaign_id": campaign_id,
            "user_context": {"user_id": user_id, "org_id": org_id}
        }

        # Pass through dataset preference; default to 'fragments' when fragment campaign is detected
        # Detect fragment campaign from name/goal
        try:
            campaign_name = (context.get('name') or '').lower()
            goal = context.get('goal') or {}
            if isinstance(goal, str):
                import json as _json
                try:
                    goal = _json.loads(goal)
                except Exception:
                    goal = {'description': goal}
            goal_desc = (goal.get('description') or '').lower() if isinstance(goal, dict) else ''
            fragment_detected = any(kw in (campaign_name + ' ' + goal_desc) for kw in ['fragment','fbdd','fragment-based','hinge'])
        except Exception:
            fragment_detected = False

        if parameters.get("dataset_preference"):
            generation_payload["dataset_preference"] = parameters.get("dataset_preference")
        elif fragment_detected:
            generation_payload["dataset_preference"] = "fragments"

        # For fragment campaigns, default force_druglike=false unless explicitly provided
        if fragment_detected:
            constraints_block = generation_payload.get('constraints') or {}
            if 'force_druglike' not in constraints_block:
                constraints_block['force_druglike'] = False
            generation_payload['constraints'] = constraints_block

        logger.info(
            f"Campaign {campaign_id}: Calling molecular-intelligence with batch_size={count}, "
            f"seed_molecules={len(seed_molecules)}, "
            f"dataset_preference={generation_payload.get('dataset_preference')}, "
            f"force_druglike={bool((generation_payload.get('constraints') or {}).get('force_druglike', False))}"
        )

        pipeline = [
            {
                "service": "molecular-intelligence",
                "endpoint": "/molecular-intelligence/generate-batch",
                "method": "POST",
                "payload": generation_payload,
                "timeout": 120
            }
        ]

        logger.info(f"[TRACE] Calling _run_pipeline with {len(pipeline)} steps for campaign {campaign_id}")
        logger.info(f"[TRACE] Pipeline step 1: service={pipeline[0]['service']}, endpoint={pipeline[0]['endpoint']}, payload keys={list(generation_payload.keys())}")

        gen_result = await _run_pipeline(pipeline, campaign_id)
        logger.info(f"[TRACE] _run_pipeline returned: status={gen_result.get('status')}, results_count={len(gen_result.get('results', []))}")
        logger.info(f"Campaign {campaign_id}: molecular-intelligence pipeline result: status={gen_result.get('status')}, message={gen_result.get('message', 'N/A')}")

        if gen_result['status'] != 'success':
            logger.error(f"Campaign {campaign_id}: molecular-intelligence query failed: {gen_result.get('message')}")
            return gen_result

        # molecular-intelligence response: try nested format then top-level
        result_data = gen_result['results'][0] if gen_result.get('results') else {}
        logger.info(f"[TRACE] result_data keys: {list(result_data.keys())}")

        # Try nested format first (results[0]['data']['molecules'])
        molecules = result_data.get('data', {}).get('molecules', [])

        # If empty, try top-level format (results[0]['molecules'])
        if not molecules:
            molecules = result_data.get('molecules', [])
            logger.info(f"[TRACE] Using top-level 'molecules' key (found {len(molecules)} molecules)")
        else:
            logger.info(f"[TRACE] Using nested 'data.molecules' key (found {len(molecules)} molecules)")

        logger.info(f"Campaign {campaign_id}: molecular-intelligence returned {len(molecules)} molecules")
        if not molecules:
            return {"status": "error", "message": "No molecules generated"}

        # Normalize molecule format (convert Pydantic models to dicts if needed)
        normalized_molecules = []
        for i, mol in enumerate(molecules):
            # Convert Pydantic model to dict if needed
            if hasattr(mol, 'dict'):
                mol_dict = mol.dict()
            elif isinstance(mol, dict):
                mol_dict = mol
            else:
                # Fallback: try to convert to dict
                mol_dict = dict(mol) if hasattr(mol, '__dict__') else {"smiles": str(mol)}

            # Ensure required fields exist
            if 'id' not in mol_dict or mol_dict['id'] is None:
                mol_dict['id'] = f"mol_{campaign_id}_{i}_{uuid.uuid4().hex[:8]}"

            if 'smiles' not in mol_dict:
                logger.warning(f"Molecule {i} missing SMILES, skipping")
                continue

            # PRODUCTION: Preserve ALL 53 enriched columns from molecular-intelligence
            # No need to recalculate - PubChem enriched data contains all properties + ADMET
            normalized_mol = {
                "id": mol_dict['id'],
                "smiles": mol_dict['smiles'],
                "generation_score": mol_dict.get('generation_score', 0)
            }

            # Flatten common nested containers if present (properties/admet)
            try:
                props = mol_dict.get('properties')
                if isinstance(props, dict):
                    # Molecular weight
                    if 'molecular_weight' in props and 'molecular_weight' not in normalized_mol:
                        normalized_mol['molecular_weight'] = props['molecular_weight']
                    if 'mw' in props and 'molecular_weight' not in normalized_mol:
                        normalized_mol['molecular_weight'] = props['mw']
                        normalized_mol['mw'] = props['mw']

                    # LogP
                    if 'logp' in props and 'logp' not in normalized_mol:
                        normalized_mol['logp'] = props['logp']
                    if 'xlogp' in props and 'xlogp' not in normalized_mol:
                        normalized_mol['xlogp'] = props['xlogp']

                    # TPSA
                    if 'tpsa' in props and 'tpsa' not in normalized_mol:
                        normalized_mol['tpsa'] = props['tpsa']

                    # HBA/HBD
                    if 'hba' in props and 'hba' not in normalized_mol:
                        normalized_mol['hba'] = props['hba']
                    if 'hbd' in props and 'hbd' not in normalized_mol:
                        normalized_mol['hbd'] = props['hbd']
                    if 'hba_count' in props and 'hba' not in normalized_mol:
                        normalized_mol['hba'] = props['hba_count']
                        normalized_mol['hba_count'] = props['hba_count']
                    if 'hbd_count' in props and 'hbd' not in normalized_mol:
                        normalized_mol['hbd'] = props['hbd_count']
                        normalized_mol['hbd_count'] = props['hbd_count']

                    # QED / SA / Drug-likeness
                    if 'qed' in props and 'qed' not in normalized_mol:
                        normalized_mol['qed'] = props['qed']
                        if 'qed_score' not in normalized_mol:
                            normalized_mol['qed_score'] = props['qed']
                    if 'synthetic_accessibility' in props and 'synthetic_accessibility' not in normalized_mol:
                        normalized_mol['synthetic_accessibility'] = props['synthetic_accessibility']
                        if 'sa_score' not in normalized_mol:
                            normalized_mol['sa_score'] = props['synthetic_accessibility']
                    if 'sa_score' in props and 'sa_score' not in normalized_mol:
                        normalized_mol['sa_score'] = props['sa_score']
                    if 'drug_likeness' in props and 'drug_likeness' not in normalized_mol:
                        normalized_mol['drug_likeness'] = props['drug_likeness']
                    if 'drugLikeness' in props and 'drug_likeness' not in normalized_mol:
                        normalized_mol['drug_likeness'] = props['drugLikeness']

                admet = mol_dict.get('admet')
                if isinstance(admet, dict):
                    # Copy ADMET keys if not already present
                    for k, v in admet.items():
                        if k not in normalized_mol:
                            normalized_mol[k] = v
            except Exception:
                pass

            # Preserve all enriched fields (53 columns total)
            # Handle field name variations: xlogp→logp, hbd_count→hbd, hba_count→hba
            for key, value in mol_dict.items():
                if key not in ['id', 'smiles', 'generation_score']:
                    normalized_mol[key] = value

            # Handle field name mappings for compatibility
            if 'xlogp' in normalized_mol and 'logp' not in normalized_mol:
                normalized_mol['logp'] = normalized_mol['xlogp']
            if 'hbd_count' in normalized_mol and 'hbd' not in normalized_mol:
                normalized_mol['hbd'] = normalized_mol['hbd_count']
            if 'hba_count' in normalized_mol and 'hba' not in normalized_mol:
                normalized_mol['hba'] = normalized_mol['hba_count']
            if 'qed' in normalized_mol and 'qed_score' not in normalized_mol:
                normalized_mol['qed_score'] = normalized_mol['qed']
            if 'synthetic_accessibility' in normalized_mol and 'sa_score' not in normalized_mol:
                normalized_mol['sa_score'] = normalized_mol['synthetic_accessibility']
            if 'drugLikeness' in normalized_mol and 'drug_likeness' not in normalized_mol:
                normalized_mol['drug_likeness'] = normalized_mol['drugLikeness']

            normalized_molecules.append(normalized_mol)

        molecules = normalized_molecules
        logger.info(f"Normalized {len(molecules)} molecules to expected format")
        await _broadcast_progress(campaign_id, "molecules_generated", {"count": len(molecules)})

        # Step 2: AI scoring (legitimate - not in enriched data)
        scoring_payload = {
            "molecules": [
                {"smiles": m['smiles'], "id": str(m.get('id') or f"mol_{i}")}
                for i, m in enumerate(molecules)
            ],
            "campaign_id": campaign_id,
            "user_id": user_id,
            "org_id": org_id
        }

        score_results = await _submit_and_poll_async_job(
            score_molecules_async,
            get_scoring_job_status,
            scoring_payload,
            campaign_id,
            timeout_seconds=180
        )

        if score_results:
            scores = score_results.get('scores', [])
            logger.info(f"Scored {len(scores)} molecules")
            await _broadcast_progress(campaign_id, "molecules_scored", {"count": len(scores)})
        else:
            logger.warning(f"Campaign {campaign_id}: AI scoring failed or timed out, continuing with empty scores")
            scores = []

        # Step 3: REMOVED - Chem-Props redundant (properties already in enriched data)
        # Properties (mw, logp, tpsa, hbd, hba, qed, sa_score) already in enriched PubChem data

        # Step 4: REMOVED - ADMET redundant (all 39 ADMET columns already in enriched data)
        # ADMET fields: overall_toxicity_score, hepatotoxicity_probability, cardiotoxicity_max_probability,
        # cyp_inhibition_risk_score, + 35 more fields from 31 ML models already pre-calculated

        # Step 5: Negative Data Check (sync) - legitimate, not in enriched data
        negative_data_results = []
        if molecules:
            try:
                # Call batch endpoint via proxy
                response = await _real_service_call({
                    'service': 'negative-data',
                    'endpoint': '/negative-data/batch-check',
                    'method': 'POST',
                    'payload': {
                        "smiles_list": [m['smiles'] for m in molecules],
                        "campaign_id": campaign_id
                    },
                    'timeout': 60
                })
                negative_data_results = response.get('results', [])
                logger.info(f"Checked {len(negative_data_results)} molecules against negative data")
            except Exception as e:
                logger.warning(f"Campaign {campaign_id}: Negative data check failed: {e}, continuing")
                negative_data_results = []

        # Step 6: Add AI scoring and negative data (don't overwrite enriched fields!)
        for i, mol in enumerate(molecules):
            # Convert UUID to string for JSON serialization
            if 'id' in mol and hasattr(mol['id'], 'hex'):
                mol['id'] = str(mol['id'])
            # Add AI score (only field not in enriched data)
            mol['score'] = scores[i]['score'] if i < len(scores) else None
            # Add negative data check results (not in enriched data)
            mol['negative_data'] = negative_data_results[i] if i < len(negative_data_results) else None
            mol['campaign_id'] = campaign_id
            # NOTE: Properties and ADMET already in enriched data - DO NOT overwrite!

        # Step 6.5: Apply molecular property filters (enriched data at top level)
        # Filter molecules based on constraints BEFORE quality gates
        # NOTE: Enriched fields now at TOP LEVEL of molecule dict (not nested in 'properties')
        filtered_molecules = []
        filter_stats = {"total": len(molecules), "filtered_out": 0, "reasons": {}}

        # Use request-time constraints if provided; fallback to campaign context
        req_constraints = (parameters or {}).get("constraints") or {}
        constraint_source = "unknown"
        if isinstance(req_constraints, dict) and req_constraints.get("molecular"):
            molecular_constraints = req_constraints.get("molecular") or {}
            constraint_source = "request_parameters"
        else:
            molecular_constraints = context.get("constraints", {}).get("molecular", {})
            constraint_source = "campaign_context"

        # Audit: log effective constraints applied and dataset preference
        try:
            eff_mw = (molecular_constraints or {}).get("mw", {})
            eff_logp = (molecular_constraints or {}).get("logP") or (molecular_constraints or {}).get("logp") or {}
            logger.info(
                f"[CONSTRAINT_DEBUG] Pre-filtering Step 6.5: Using constraints from {constraint_source} | "
                f"MW {eff_mw.get('min','?')}-{eff_mw.get('max','?')} Da, "
                f"LogP {eff_logp.get('min','?')}-{eff_logp.get('max','?')} | "
                f"Input molecules: {len(molecules)}"
            )
        except Exception as e:
            logger.warning(f"[CONSTRAINT_DEBUG] Failed to log constraint info: {e}")

        if molecular_constraints:
            logger.info(f"Applying molecular property filters to {len(molecules)} molecules (using enriched data)")

            for mol in molecules:
                # Check if molecule has required enriched fields (molecular_weight or mw)
                has_enriched_data = 'molecular_weight' in mol or 'mw' in mol
                if not has_enriched_data:
                    # No enriched properties - skip molecule
                    filter_stats["filtered_out"] += 1
                    filter_stats["reasons"]["no_enriched_data"] = filter_stats["reasons"].get("no_enriched_data", 0) + 1
                    continue

                failed_filters = []

                # MW (molecular weight) filter - check top level enriched fields
                mw_constraint = molecular_constraints.get("mw", {})
                mw = mol.get("molecular_weight") or mol.get("mw")
                if mw:
                    if mw_constraint.get("min") and mw < mw_constraint["min"]:
                        failed_filters.append(f"MW {mw:.1f} < min {mw_constraint['min']}")
                    if mw_constraint.get("max") and mw > mw_constraint["max"]:
                        failed_filters.append(f"MW {mw:.1f} > max {mw_constraint['max']}")

                # LogP filter - check top level enriched fields (logp or xlogp)
                # Accept either 'logP' or 'logp' in constraints
                logp_constraint = molecular_constraints.get("logP") or molecular_constraints.get("logp") or {}
                logp = mol.get("logp") or mol.get("xlogp")
                if logp is not None:
                    if logp_constraint.get("min") and logp < logp_constraint["min"]:
                        failed_filters.append(f"LogP {logp:.2f} < min {logp_constraint['min']}")
                    if logp_constraint.get("max") and logp > logp_constraint["max"]:
                        failed_filters.append(f"LogP {logp:.2f} > max {logp_constraint['max']}")

                # TPSA filter - check top level enriched fields
                tpsa_constraint = molecular_constraints.get("tpsa", {})
                tpsa = mol.get("tpsa")
                if tpsa is not None:
                    if tpsa_constraint.get("max") and tpsa > tpsa_constraint["max"]:
                        failed_filters.append(f"TPSA {tpsa:.1f} > max {tpsa_constraint['max']}")

                # HBD (hydrogen bond donors) filter - check top level enriched fields
                hbd_constraint = molecular_constraints.get("hbd", {})
                hbd = mol.get("hbd") or mol.get("hbd_count")
                if hbd is not None:
                    if hbd_constraint.get("max") and hbd > hbd_constraint["max"]:
                        failed_filters.append(f"HBD {hbd} > max {hbd_constraint['max']}")

                # HBA (hydrogen bond acceptors) filter - check top level enriched fields
                hba_constraint = molecular_constraints.get("hba", {})
                hba = mol.get("hba") or mol.get("hba_count")
                if hba is not None:
                    if hba_constraint.get("max") and hba > hba_constraint["max"]:
                        failed_filters.append(f"HBA {hba} > max {hba_constraint['max']}")

                if failed_filters:
                    # Molecule failed filters
                    filter_stats["filtered_out"] += 1
                    for reason in failed_filters:
                        filter_stats["reasons"][reason] = filter_stats["reasons"].get(reason, 0) + 1
                else:
                    # Molecule passed all filters
                    filtered_molecules.append(mol)

            logger.info(f"Property filtering complete: {len(filtered_molecules)}/{len(molecules)} molecules passed (filtered out: {filter_stats['filtered_out']})")
            if filter_stats["reasons"]:
                logger.info(f"Filter reasons: {filter_stats['reasons']}")

            # DIAGNOSTIC: If 100% filtered out, log sample molecule data
            if len(filtered_molecules) == 0 and len(molecules) > 0:
                sample_mol = molecules[0]
                logger.warning(
                    f"[FILTER_DEBUG] 100% molecules filtered! Sample molecule data: "
                    f"MW={sample_mol.get('molecular_weight')}, "
                    f"LogP={sample_mol.get('logp') or sample_mol.get('xlogp')}, "
                    f"TPSA={sample_mol.get('tpsa')}, "
                    f"Keys present: {list(sample_mol.keys())[:10]}"
                )

            # Broadcast filtering event
            await _broadcast_progress(campaign_id, "molecules_filtered", {
                "total": filter_stats["total"],
                "passed": len(filtered_molecules),
                "filtered_out": filter_stats["filtered_out"],
                "reasons": filter_stats["reasons"]
            })
        else:
            # No constraints - all molecules pass
            filtered_molecules = molecules
            logger.info(f"No molecular constraints defined - all {len(molecules)} molecules passed")

        # Use filtered molecules for the rest of the pipeline
        molecules = filtered_molecules

        # CRITICAL DEBUG: Log filtering results for 0-molecule debugging
        logger.info(
            f"[CONSTRAINT_DEBUG] Pre-filtering results: {filter_stats['total']} input → "
            f"{len(filtered_molecules)} passed ({filter_stats['filtered_out']} filtered out) | "
            f"Constraint source: {constraint_source}"
        )

        await _record_results(campaign_id, "molecules_generated", {
            "molecules": molecules,
            "count": len(molecules),
            "strategy": strategy,
            "avg_score": sum(s['score'] for s in scores) / len(scores) if scores else None
        })

        # Persist discoveries to SQL database (molecules with high significance)
        try:
            discoveries_persisted = 0
            for i, mol in enumerate(molecules):
                score = scores[i]['score'] if i < len(scores) else 0.0
                if score > 0.6:  # Significant discovery threshold
                    await _real_service_call({
                        'service': 'db-manager',
                        'endpoint': '/campaign-discoveries',
                        'method': 'POST',
                        'payload': {
                            'campaign_id': campaign_id,
                            'molecule_id': mol.get('id'),
                            'properties': {
                                'smiles': mol.get('smiles'),
                                'score': score,
                                'properties': mol.get('properties', {})
                            },
                            'significance_score': score
                        }
                    })
                    # Broadcast discovery event for Intelligence page
                    try:
                        await broadcast_global_update('discovery', {
                            'campaign_id': campaign_id,
                            'id': mol.get('id'),
                            'smiles': mol.get('smiles'),
                            'activity': mol.get('binding_affinity') or mol.get('generation_score') or score,
                            'significance': score,
                            'timestamp': datetime.utcnow().isoformat()
                        })
                    except Exception as e:
                        logger.warning(f"Failed to broadcast discovery: {e}")
                    discoveries_persisted += 1
            if discoveries_persisted > 0:
                logger.info(f"Persisted {discoveries_persisted} discoveries to SQL for campaign {campaign_id}")
        except Exception as e:
            logger.error(f"Failed to persist discoveries to SQL: {e}")

        # Step 7: FAVES Compliance Check (async) - Phase 3
        # Filter out invalid SMILES before sending to compliance service
        valid_molecules = []
        invalid_count = 0
        for i, m in enumerate(molecules):
            smiles = m.get('smiles', '')
            if _validate_smiles(smiles):
                # Ensure molecule id is a non-null string. Fall back to deterministic id if missing/empty/None.
                mol_id = m.get('id') or m.get('molecule_id') or f"mol_{i}"
                valid_molecules.append({"smiles": smiles, "id": str(mol_id)})
            else:
                invalid_count += 1
                logger.warning(f"Filtered invalid SMILES for campaign {campaign_id}: {smiles}")

        if invalid_count > 0:
            logger.warning(f"Campaign {campaign_id}: Filtered {invalid_count} invalid SMILES strings before compliance check")

        if valid_molecules:
            faves_payload = {
                "molecules": valid_molecules,
                "campaign_id": campaign_id,
                "user_id": user_id,
                "org_id": org_id,
                "therapeutic_area": context.get("dataSources", {}).get("therapeuticArea", "General")
            }

            faves_results = await _submit_and_poll_async_job(
                assess_faves_compliance_async,
                get_faves_compliance_job_status,
                faves_payload,
                campaign_id,
                timeout_seconds=180
            )

            if faves_results:
                compliance_status = faves_results.get('compliance', {})
                passed_count = compliance_status.get('passed', 0)
                failed_count = compliance_status.get('failed', 0)
                logger.info(f"FAVES compliance: {passed_count} passed, {failed_count} failed ({invalid_count} invalid SMILES filtered)")
                await _broadcast_progress(campaign_id, "faves_assessed", {"passed": passed_count, "failed": failed_count, "invalid_filtered": invalid_count})
            else:
                logger.warning(f"Campaign {campaign_id}: FAVES compliance check failed or timed out, continuing")
                compliance_status = {}
        else:
            logger.error(f"Campaign {campaign_id}: No valid SMILES to assess for compliance")
            compliance_status = {}

        # Knowledge graph enrichment (optional)
        if context.get('enable_knowledge_graph', False):
            kg_payload = {
                "molecules": [{"smiles": m['smiles'], "id": str(m.get('id') or "")} for m in molecules],
                "campaign_id": campaign_id
            }
            await _schedule_async_work(campaign_id, "knowledge_graph_enrichment", kg_payload)

        return {
            "status": "success",
            "action": "generate_new_molecules",
            "results": {
                "molecules": molecules,  # Include full molecule objects for workflow engine
                "candidate_ids": [m.get('id') for m in molecules],
                "count": len(molecules),
                "avg_score": sum(s['score'] for s in scores) / len(scores) if scores else None,
                # ADMET step is removed in the enriched pipeline; keep key for compatibility
                "admet_predictions": 0,
                "negative_data_checked": len(negative_data_results),
                "compliance_passed": compliance_status.get('passed', 0),
                "compliance_failed": compliance_status.get('failed', 0)
            }
        }

    except Exception as e:
        logger.error(f"Error executing generate_molecules: {e}", exc_info=True)
        logger.error(f"Campaign {campaign_id}: Exception type: {type(e).__name__}, Message: {str(e)}")
        return {"status": "error", "message": str(e), "error_type": type(e).__name__}


async def _execute_optimize_leads(campaign_id: str, parameters: Dict, context: Dict) -> Dict[str, Any]:
    """
    Execute lead optimization pipeline (Phase 5 + Phase 4):
    Lead Optimization (async) → Docking (async) → MD simulations (async) → Persist
    """
    try:
        # Convert all UUID objects to strings for JSON serialization
        campaign_id = str(campaign_id)
        user_id = str(context.get("user_id", "system"))
        org_id = str(context.get("org_id") or context.get("tenant_id", ""))

        lead_ids = parameters.get('lead_ids', [])
        optimization_strategy = parameters.get('strategy', 'structure_based')
        target_protein = context.get('target_protein')

        logger.info(f"Optimizing {len(lead_ids)} leads for campaign {campaign_id}")

        # Step 1: Lead Optimization (async) - Phase 5
        # This endpoint handles: fetch leads → MolMIM optimization → re-scoring
        optimization_payload = {
            "lead_ids": lead_ids,
            "strategy": optimization_strategy,
            "iterations": parameters.get('iterations', 5),
            "campaign_id": campaign_id,
            "user_id": user_id,
            "org_id": org_id
        }

        optimization_results = await _submit_and_poll_async_job(
            optimize_leads_async,
            get_lead_optimization_job_status,
            optimization_payload,
            campaign_id,
            timeout_seconds=300
        )

        if not optimization_results:
            logger.error(f"Campaign {campaign_id}: Lead optimization failed or timed out")
            return {"status": "error", "message": "Lead optimization failed"}

        optimized = optimization_results.get('optimized_molecules', [])
        scores = optimization_results.get('scores', [])
        improvement = optimization_results.get('improvement_percentage', 0)

        logger.info(f"Optimized {len(optimized)} molecules with {improvement}% improvement")
        await _broadcast_progress(campaign_id, "leads_optimized", {"count": len(optimized), "improvement": improvement})

        if not optimized:
            return {"status": "error", "message": "No molecules were optimized"}

        # Step 2: Molecular Docking (async) - Phase 4
        docking_status = {"status": "skipped", "reason": "no_target_protein"}
        docking_results = []

        if target_protein:
            # Fragment-aware docking defaults via payload injection
            try:
                campaign_name = (context.get('name') or '').lower()
                goal = context.get('goal') or {}
                if isinstance(goal, str):
                    import json as _json
                    try:
                        goal = _json.loads(goal)
                    except Exception:
                        goal = {'description': goal}
                goal_desc = (goal.get('description') or '').lower() if isinstance(goal, dict) else ''
                fragment_detected = any(kw in (campaign_name + ' ' + goal_desc) for kw in ['fragment','fbdd','fragment-based','hinge'])
            except Exception:
                fragment_detected = False

            docking_payload = {
                "molecules": [
                    {"smiles": m['smiles'], "id": str(m.get('id') or f"mol_{i}")}
                    for i, m in enumerate(optimized)
                ],
                "target_protein": target_protein,
                "docking_params": {"exhaustiveness": 12 if fragment_detected else 16, "num_modes": 9, "energy_range": 3},
                "campaign_id": campaign_id,
                "user_id": user_id,
                "org_id": org_id
            }

            docking_results_raw = await _submit_and_poll_async_job(
                dock_molecules_async,
                get_docking_job_status,
                docking_payload,
                campaign_id,
                timeout_seconds=600  # Docking can take longer
            )

            if docking_results_raw:
                docking_results = docking_results_raw.get('docking_results', [])
                logger.info(f"Docked {len(docking_results)} molecules")
                await _broadcast_progress(campaign_id, "docking_completed", {"count": len(docking_results)})
                docking_status = {"status": "completed", "count": len(docking_results)}
            else:
                logger.warning(f"Campaign {campaign_id}: Docking failed or timed out")
                docking_status = {"status": "failed", "reason": "timeout_or_error"}

        # Step 3: MD Simulations (async) - Phase 4
        # Only run MD on top scoring molecules
        md_status = {"status": "skipped", "reason": "insufficient_candidates"}
        md_results = []

        top_count = parameters.get('md_top_count', 5)
        if scores and len(scores) >= top_count:
            top_molecules = sorted(
                [{"smiles": optimized[i]['smiles'], "id": optimized[i].get('id', f"mol_{i}"), "score": scores[i].get('score', 0)}
                 for i in range(min(len(optimized), len(scores)))],
                key=lambda x: x['score'],
                reverse=True
            )[:top_count]

            md_payload = {
                "molecules": [{"smiles": m['smiles'], "id": m['id']} for m in top_molecules],
                "simulation_time_ns": parameters.get('simulation_time', 5 if fragment_detected else 10),
                "replica_runs": parameters.get('replica_runs', 3 if fragment_detected else 1),
                "campaign_id": campaign_id,
                "user_id": user_id,
                "org_id": org_id
            }

            md_results_raw = await _submit_and_poll_async_job(
                simulate_md_async,
                get_md_simulation_job_status,
                md_payload,
                campaign_id,
                timeout_seconds=900  # MD can take even longer
            )

            if md_results_raw:
                md_results = md_results_raw.get('simulation_results', [])
                logger.info(f"Completed MD simulations for {len(md_results)} molecules")
                await _broadcast_progress(campaign_id, "md_completed", {"count": len(md_results)})
                md_status = {"status": "completed", "count": len(md_results)}
            else:
                logger.warning(f"Campaign {campaign_id}: MD simulations failed or timed out")
                md_status = {"status": "failed", "reason": "timeout_or_error"}

        # Step 4: Enrich and persist optimized leads
        for i, mol in enumerate(optimized):
            mol['score'] = scores[i].get('score') if i < len(scores) else None
            mol['docking'] = docking_results[i] if i < len(docking_results) else None
            mol['campaign_id'] = campaign_id
            mol['parent_id'] = lead_ids[i] if i < len(lead_ids) else None

        await _record_results(campaign_id, "leads_optimized", {
            "optimized_molecules": optimized,
            "count": len(optimized),
            "avg_score": sum(s.get('score', 0) for s in scores) / len(scores) if scores else None,
            "improvement": improvement,
            "docked": len(docking_results),
            "md_simulated": len(md_results)
        })

        return {
            "status": "success",
            "action": "optimize_existing_leads",
            "results": {
                "optimized_count": len(optimized),
                "avg_score": sum(s.get('score', 0) for s in scores) / len(scores) if scores else None,
                "docking_status": docking_status,
                "md_status": md_status,
                "improvement": improvement
            }
        }

    except Exception as e:
        logger.error(f"Error executing optimize_leads: {e}")
        return {"status": "error", "message": str(e)}


async def _execute_screen_compounds(campaign_id: str, parameters: Dict, context: Dict) -> Dict[str, Any]:
    """
    Execute compound screening pipeline:
    Fetch compounds → TDC benchmarking → ZINC search → Property filtering → Persist
    """
    try:
        compound_source = parameters.get('source', 'zinc')
        query = parameters.get('query', {})
        max_results = parameters.get('max_results', 100)

        logger.info(f"Screening compounds from {compound_source} for campaign {campaign_id}")

        compounds = []

        # Step 1: Fetch compounds based on source
        if compound_source == 'zinc':
            zinc_pipeline = [
                {
                    "service": "zinc-integration",
                    "endpoint": "/zinc/search",
                    "method": "POST",
                    "payload": {
                        "query": query,
                        "max_results": max_results,
                        "filters": parameters.get('filters', {})
                    },
                    "timeout": 60
                }
            ]

            zinc_result = await _run_pipeline(zinc_pipeline, campaign_id)
            if zinc_result['status'] == 'success':
                compounds = zinc_result['results'][0].get('compounds', [])

        elif compound_source == 'pubchem':
            # PubChem search via external API
            pubchem_payload = {
                "query": query.get('text', ''),
                "max_results": max_results
            }
            # Assuming we have a pubchem integration service
            pubchem_pipeline = [
                {
                    "service": "external-integrations",
                    "endpoint": "/pubchem/search",
                    "method": "POST",
                    "payload": pubchem_payload,
                    "timeout": 60
                }
            ]
            pubchem_result = await _run_pipeline(pubchem_pipeline, campaign_id)
            if pubchem_result['status'] == 'success':
                compounds = pubchem_result['results'][0].get('compounds', [])

        elif compound_source == 'internal':
            # Search internal database via dashboard-aggregator
            internal_pipeline = [
                {
                    "service": "dashboard-aggregator",
                    "endpoint": "/api/molecules/search",
                    "method": "POST",
                    "payload": query,
                    "timeout": 30
                }
            ]
            internal_result = await _run_pipeline(internal_pipeline, campaign_id)
            if internal_result['status'] == 'success':
                compounds = internal_result['results'][0].get('molecules', [])

        if not compounds:
            return {"status": "error", "message": f"No compounds found from {compound_source}"}

        logger.info(f"Retrieved {len(compounds)} compounds")
        await _broadcast_progress(campaign_id, "compounds_retrieved", {"count": len(compounds), "source": compound_source})

        # Step 2: TDC benchmarking for relevant compounds
        if parameters.get('run_tdc_benchmark', False):
            tdc_payload = {
                "molecules": [c.get('smiles') for c in compounds[:50]],  # Limit to top 50
                "benchmark": parameters.get('tdc_benchmark', 'admet'),
                "campaign_id": campaign_id
            }

            tdc_pipeline = [
                {
                    "service": "tdc-integration",
                    "endpoint": "/tdc/benchmark",
                    "method": "POST",
                    "payload": tdc_payload,
                    "timeout": 120
                }
            ]

            tdc_result = await _run_pipeline(tdc_pipeline, campaign_id)
            tdc_scores = tdc_result['results'][0].get('scores', []) if tdc_result['status'] == 'success' else []
        else:
            tdc_scores = []

        # Optional: enrich compounds with PubChem fields from dashboard-aggregator by SMILES
        # Note: No dashboard-aggregator enrichment. Compounds are used as-is.

        # Step 3: Property-based filtering (no chem-props; use enriched top-level fields)
        property_filters = parameters.get('property_filters', {})
        if property_filters:
            def get_prop(c: Dict[str, Any], key: str):
                # Map common aliases to enriched top-level fields
                if key.lower() in ('mw', 'molecular_weight'):
                    return c.get('molecular_weight') or c.get('mw')
                if key.lower() in ('logp', 'xlogp'):
                    return c.get('logp') or c.get('xlogp')
                if key.lower() in ('tpsa',):
                    return c.get('tpsa')
                if key.lower() in ('hbd', 'hbd_count'):
                    return c.get('hbd') or c.get('hbd_count')
                if key.lower() in ('hba', 'hba_count'):
                    return c.get('hba') or c.get('hba_count')
                if key.lower() in ('qed', 'qed_score'):
                    return c.get('qed') or c.get('qed_score')
                return c.get(key)

            filtered_compounds: List[Dict[str, Any]] = []
            for comp in compounds:
                passes = True
                for prop, rng in property_filters.items():
                    val = get_prop(comp, prop)
                    if val is None:
                        passes = False
                        break
                    if 'min' in rng and val < rng['min']:
                        passes = False
                        break
                    if 'max' in rng and val > rng['max']:
                        passes = False
                        break
                if passes:
                    filtered_compounds.append(comp)

            compounds = filtered_compounds
            logger.info(f"Filtered to {len(compounds)} compounds meeting criteria (enriched data)")

        # Step 4: Scoring from enriched ADMET (no addie-models)
        if compounds:
            for comp in compounds:
                # Use existing enriched field; lower toxicity is better
                comp['admet_score'] = comp.get('overall_toxicity_score', 0.5)
                comp['campaign_id'] = campaign_id

        # Step 5: Persist screened compounds
        await _record_results(campaign_id, "compounds_screened", {
            "compounds": compounds,
            "count": len(compounds),
            "source": compound_source,
            "avg_score": sum(c.get('score', 0) for c in compounds) / len(compounds) if compounds else None
        })

        return {
            "status": "success",
            "action": "screen_compounds",
            "results": {
                "screened_count": len(compounds),
                "source": compound_source,
                "avg_score": sum(c.get('score', 0) for c in compounds) / len(compounds) if compounds else None,
                "tdc_benchmarked": len(tdc_scores),
                "top_candidates": sorted(compounds, key=lambda x: x.get('score', 0), reverse=True)[:10]
            }
        }

    except Exception as e:
        logger.error(f"Error executing screen_compounds: {e}")
        return {"status": "error", "message": str(e)}


async def _execute_pivot_strategy(campaign_id: str, parameters: Dict, context: Dict) -> Dict[str, Any]:
    """
    Execute strategy pivot:
    Analyze current results → Literature search → Update campaign constraints → Persist
    """
    try:
        new_strategy = parameters.get('new_strategy')
        reasoning = parameters.get('reasoning', '')

        logger.info(f"Pivoting strategy for campaign {campaign_id}: {new_strategy}")

        # Step 1: Analyze current campaign results
        analysis_pipeline = [
            {
                "service": "dashboard-aggregator",
                "endpoint": "/api/campaigns/analyze",
                "method": "POST",
                "payload": {"campaign_id": campaign_id},
                "timeout": 30
            }
        ]

        analysis_result = await _run_pipeline(analysis_pipeline, campaign_id)
        current_stats = analysis_result['results'][0] if analysis_result['status'] == 'success' else {}

        # Step 2: Literature search for new strategy validation
        if context.get('pinecone_client'):
            literature = await context['pinecone_client'].search_literature(
                query=f"{new_strategy} drug discovery {context.get('target', '')}",
                filters={"year": {"$gte": 2020}},
                top_k=5
            )
        else:
            literature = []

        # Step 3: Update campaign constraints
        new_constraints = parameters.get('constraints', {})
        update_payload = {
            "campaign_id": campaign_id,
            "updates": {
                "strategy": new_strategy,
                "constraints": new_constraints,
                "pivot_reasoning": reasoning,
                "pivoted_at": datetime.utcnow().isoformat()
            }
        }

        update_pipeline = [
            {
                "service": "db-manager",
                "endpoint": "/db/campaigns/update",
                "method": "PUT",
                "payload": update_payload,
                "timeout": 30
            }
        ]

        update_result = await _run_pipeline(update_pipeline, campaign_id)
        if update_result['status'] != 'success':
            return update_result

        # Step 4: Record pivot event
        await _record_results(campaign_id, "strategy_pivoted", {
            "old_strategy": current_stats.get('strategy'),
            "new_strategy": new_strategy,
            "reasoning": reasoning,
            "constraints": new_constraints,
            "literature_support": len(literature)
        })

        await _broadcast_progress(campaign_id, "strategy_pivoted", {
            "new_strategy": new_strategy,
            "reasoning": reasoning
        })

        return {
            "status": "success",
            "action": "pivot_strategy",
            "results": {
                "new_strategy": new_strategy,
                "constraints_updated": len(new_constraints),
                "literature_references": len(literature),
                "previous_stats": current_stats
            }
        }

    except Exception as e:
        logger.error(f"Error executing pivot_strategy: {e}")
        return {"status": "error", "message": str(e)}


async def _execute_expand_space(campaign_id: str, parameters: Dict, context: Dict) -> Dict[str, Any]:
    """
    Execute search space expansion:
    Identify expansion vectors → Generate diverse molecules → Persist
    """
    try:
        expansion_type = parameters.get('expansion_type', 'scaffold_hop')
        base_molecules = parameters.get('base_molecules', [])

        logger.info(f"Expanding search space for campaign {campaign_id}: {expansion_type}")

        # Step 1: Fetch base molecules if IDs provided
        if base_molecules and isinstance(base_molecules[0], str):
            fetch_pipeline = [
                {
                    "service": "dashboard-aggregator",
                    "endpoint": "/api/molecules/batch",
                    "method": "POST",
                    "payload": {"ids": base_molecules},
                    "timeout": 30
                }
            ]

            fetch_result = await _run_pipeline(fetch_pipeline, campaign_id)
            if fetch_result['status'] == 'success':
                base_molecules = fetch_result['results'][0].get('molecules', [])

        # Step 2: Generate expanded molecules based on type
        expansion_payload = {
            "base_molecules": [m.get('smiles') if isinstance(m, dict) else m for m in base_molecules],
            "expansion_type": expansion_type,
            "count": parameters.get('count', 20),
            "diversity_threshold": parameters.get('diversity', 0.7),
            "campaign_id": campaign_id
        }

        # Use molecular-intelligence for advanced generation
        expansion_pipeline = [
            {
                "service": "molecular-intelligence",
                "endpoint": "/molecular-intelligence/expand",
                "method": "POST",
                "payload": expansion_payload,
                "timeout": 120
            }
        ]

        expansion_result = await _run_pipeline(expansion_pipeline, campaign_id)
        if expansion_result['status'] != 'success':
            return expansion_result

        expanded_molecules = expansion_result['results'][0].get('molecules', [])
        logger.info(f"Generated {len(expanded_molecules)} expanded molecules")

        # Step 3: Derive ADMET score from enriched data via dashboard-aggregator (no addie-models)
        molecules_data = [
            {
                "smiles": smi,
                "admet_score": None,
                "campaign_id": campaign_id,
                "expansion_type": expansion_type
            }
            for smi in expanded_molecules
        ]

        await _record_results(campaign_id, "space_expanded", {
            "molecules": molecules_data,
            "expansion_type": expansion_type,
            "count": len(expanded_molecules),
            "avg_score": None
        })

        await _broadcast_progress(campaign_id, "space_expanded", {
            "expansion_type": expansion_type,
            "count": len(expanded_molecules)
        })

        return {
            "status": "success",
            "action": "expand_search_space",
            "results": {
                "expanded_count": len(expanded_molecules),
                "expansion_type": expansion_type,
                "avg_score": sum(s['score'] for s in scores) / len(scores) if scores else None
            }
        }

    except Exception as e:
        logger.error(f"Error executing expand_space: {e}")
        return {"status": "error", "message": str(e)}


async def _execute_adjust_parameters(campaign_id: str, parameters: Dict, context: Dict) -> Dict[str, Any]:
    """
    Execute parameter adjustment:
    Update scoring weights, thresholds, or generation parameters
    """
    try:
        adjustment_type = parameters.get('type')
        new_values = parameters.get('values', {})

        logger.info(f"Adjusting parameters for campaign {campaign_id}: {adjustment_type}")

        # Step 1: Update campaign parameters
        update_payload = {
            "campaign_id": campaign_id,
            "updates": {
                "parameters": new_values,
                "adjustment_type": adjustment_type,
                "adjusted_at": datetime.utcnow().isoformat()
            }
        }

        update_pipeline = [
            {
                "service": "db-manager",
                "endpoint": "/db/campaigns/update",
                "method": "PUT",
                "payload": update_payload,
                "timeout": 30
            }
        ]

        update_result = await _run_pipeline(update_pipeline, campaign_id)
        if update_result['status'] != 'success':
            return update_result

        # Step 2: Re-score existing molecules with new parameters if needed
        if adjustment_type in ['scoring_weights', 'thresholds']:
            rescore_pipeline = [
                {
                    "service": "dashboard-aggregator",
                    "endpoint": "/api/molecules/by-campaign",
                    "method": "POST",
                    "payload": {"campaign_id": campaign_id, "limit": 100},
                    "timeout": 30
                }
            ]

            molecules_result = await _run_pipeline(rescore_pipeline, campaign_id)
            if molecules_result['status'] == 'success':
                molecules = molecules_result['results'][0].get('molecules', [])

                # Schedule re-scoring job
                if molecules:
                    await _schedule_async_work(
                        campaign_id,
                        "rescore_molecules",
                        {
                            "molecule_ids": [m['id'] for m in molecules],
                            "new_weights": new_values.get('scoring_weights', {}),
                            "campaign_id": campaign_id
                        }
                    )

        # Step 3: Record adjustment
        await _record_results(campaign_id, "parameters_adjusted", {
            "adjustment_type": adjustment_type,
            "new_values": new_values,
            "timestamp": datetime.utcnow().isoformat()
        })

        await _broadcast_progress(campaign_id, "parameters_adjusted", {
            "type": adjustment_type,
            "values": new_values
        })

        return {
            "status": "success",
            "action": "adjust_parameters",
            "results": {
                "adjustment_type": adjustment_type,
                "updated_count": len(new_values)
            }
        }

    except Exception as e:
        logger.error(f"Error executing adjust_parameters: {e}")
        return {"status": "error", "message": str(e)}


async def _execute_complete_milestone(campaign_id: str, parameters: Dict, context: Dict) -> Dict[str, Any]:
    """
    Execute milestone completion:
    Generate final report, archive results, update campaign status
    """
    try:
        milestone_name = parameters.get('milestone_name')

        logger.info(f"Completing milestone '{milestone_name}' for campaign {campaign_id}")

        # Step 1: Generate comprehensive report
        report_pipeline = [
            {
                "service": "dashboard-aggregator",
                "endpoint": "/api/campaigns/report",
                "method": "POST",
                "payload": {
                    "campaign_id": campaign_id,
                    "include_molecules": True,
                    "include_metrics": True
                },
                "timeout": 60
            }
        ]

        report_result = await _run_pipeline(report_pipeline, campaign_id)
        report_data = report_result['results'][0] if report_result['status'] == 'success' else {}

        # Step 2: Archive milestone results
        archive_payload = {
            "campaign_id": campaign_id,
            "milestone_name": milestone_name,
            "report": report_data,
            "timestamp": datetime.utcnow().isoformat()
        }

        archive_pipeline = [
            {
                "service": "db-manager",
                "endpoint": "/db/milestones/create",
                "method": "POST",
                "payload": archive_payload,
                "timeout": 30
            }
        ]

        archive_result = await _run_pipeline(archive_pipeline, campaign_id)

        # Step 3: Update campaign status
        status_update = {
            "campaign_id": campaign_id,
            "updates": {
                "current_milestone": milestone_name,
                "milestone_completed_at": datetime.utcnow().isoformat(),
                "status": parameters.get('new_status', 'active')
            }
        }

        status_pipeline = [
            {
                "service": "db-manager",
                "endpoint": "/db/campaigns/update",
                "method": "PUT",
                "payload": status_update,
                "timeout": 30
            }
        ]

        await _run_pipeline(status_pipeline, campaign_id)

        # Step 4: Record milestone completion
        await _record_results(campaign_id, "milestone_completed", {
            "milestone_name": milestone_name,
            "report_summary": {
                "molecules_generated": report_data.get('total_molecules', 0),
                "top_score": report_data.get('top_score', 0),
                "experiments_run": report_data.get('experiments_run', 0)
            }
        })

        await _broadcast_progress(campaign_id, "milestone_completed", {
            "milestone": milestone_name
        })

        return {
            "status": "success",
            "action": "complete_milestone",
            "results": {
                "milestone_name": milestone_name,
                "molecules_generated": report_data.get('total_molecules', 0),
                "top_score": report_data.get('top_score', 0)
            }
        }

    except Exception as e:
        logger.error(f"Error executing complete_milestone: {e}")
        return {"status": "error", "message": str(e)}


async def _execute_request_human_input(campaign_id: str, parameters: Dict, context: Dict) -> Dict[str, Any]:
    """
    Execute human input request:
    Create notification for UI - campaign continues running autonomously.
    Does NOT pause the loop. Human can review and provide feedback asynchronously.
    """
    try:
        request_type = parameters.get('request_type', 'guidance')
        question = parameters.get('question', '')
        options = parameters.get('options', [])

        logger.info(f"Requesting human input for campaign {campaign_id}: {request_type}")

        # Step 1: Create human input request record
        request_payload = {
            "campaign_id": campaign_id,
            "request_type": request_type,
            "question": question,
            "options": options,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat()
        }

        request_pipeline = [
            {
                "service": "db-manager",
                "endpoint": "/db/human-requests/create",
                "method": "POST",
                "payload": request_payload,
                "timeout": 30
            }
        ]

        request_result = await _run_pipeline(request_pipeline, campaign_id)
        if request_result['status'] != 'success':
            return request_result

        request_id = request_result['results'][0].get('id')

        # Step 2: Broadcast notification to UI
        await _broadcast_progress(campaign_id, "human_input_requested", {
            "request_id": request_id,
            "request_type": request_type,
            "question": question,
            "options": options
        })

        # Note: Campaign loop continues running - does NOT pause automatically
        # Human can provide input via UI which will be incorporated in future decisions
        # Only manual pause/stop via API will halt the autonomous loop

        # Step 3: Record the request
        await _record_results(campaign_id, "human_input_requested", {
            "request_id": request_id,
            "request_type": request_type,
            "question": question
        })

        logger.info(f"Human input request {request_id} created for campaign {campaign_id}")

        return {
            "status": "success",
            "action": "request_human_input",
            "results": {
                "request_id": request_id,
                "request_type": request_type,
                "status": "pending"
            }
        }

    except Exception as e:
        logger.error(f"Error executing request_human_input: {e}")
        return {"status": "error", "message": str(e)}


@router.get("/pdb/{pdb_id}")
async def fetch_pdb_structure(pdb_id: str):
    """
    Fetch PDB structure from RCSB with caching

    Used by GROMACS, AutoDock, and Lead-Optimization services

    Args:
        pdb_id: PDB identifier (e.g., "6OIM")

    Returns:
        PDB file content
    """
    from utils.pdb_cache import get_pdb, get_cache_stats

    try:
        pdb_content = await get_pdb(pdb_id)
        cache_stats = get_cache_stats()

        return {
            "status": "success",
            "pdb_id": pdb_id,
            "pdb_content": pdb_content,
            "size_bytes": len(pdb_content),
            "cache_stats": cache_stats
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error fetching PDB {pdb_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pdb-cache/stats")
async def get_pdb_cache_stats():
    """Get PDB cache statistics"""
    from utils.pdb_cache import get_cache_stats

    try:
        stats = get_cache_stats()
        return {
            "status": "success",
            "cache_stats": stats
        }
    except Exception as e:
        logger.error(f"Error getting cache stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))
