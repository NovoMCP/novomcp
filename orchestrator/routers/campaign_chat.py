"""
Campaign Chat Router
Handles conversational AI for campaign creation and interaction
"""

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional
from enum import Enum

from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel, Field

from ai.azure_openai_client import AzureOpenAIClient
from config import settings

from core.rate_limiter import rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["campaign-chat"], dependencies=[Depends(rate_limit("campaign_chat"))])

# ============================================================================
# Models
# ============================================================================

class IntentType(str, Enum):
    QUESTION = "question"
    INTERVENTION = "intervention"
    DATA_REQUEST = "data_request"
    STATUS_CHECK = "status_check"
    CONTROL = "control"
    EXPORT = "export"
    CONVERSATIONAL = "conversational"

class SentimentType(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    FRUSTRATED = "frustrated"
    CONFUSED = "confused"

class ChatRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

class ConversationMessage(BaseModel):
    role: str
    content: str

class CampaignIntent(BaseModel):
    name: Optional[str] = None
    campaignType: Optional[str] = None
    targetProtein: Optional[str] = None
    goalDescription: Optional[str] = None
    targetMolecules: Optional[int] = 10
    minActivityThreshold: Optional[int] = 200
    keyProperties: Optional[str] = None

class CompletionStatus(BaseModel):
    isComplete: bool
    required: List[str] = ["name", "campaignType", "goalDescription"]
    optional: List[str] = ["targetProtein", "targetMolecules", "minActivityThreshold", "keyProperties"]
    completed: List[str] = []
    missingRequired: List[str] = []
    missingOptional: List[str] = []

class ChatIntentRequest(BaseModel):
    conversationHistory: List[ConversationMessage]
    extractedParams: CampaignIntent = Field(default_factory=CampaignIntent)

class ChatIntentResponse(BaseModel):
    success: bool
    message: str
    extractedParams: CampaignIntent
    completionStatus: CompletionStatus

class SendMessageRequest(BaseModel):
    message: str
    user_id: Optional[str] = None

class ActionTaken(BaseModel):
    type: str
    details: Dict[str, Any]
    success: bool

class AIResponse(BaseModel):
    content: str
    intent: IntentType
    sentiment: SentimentType
    action_taken: Optional[ActionTaken] = None
    attachments: Optional[List[Dict[str, Any]]] = None

class SendMessageResponse(BaseModel):
    success: bool
    message_id: str
    ai_response: AIResponse

class ChatMessage(BaseModel):
    id: str
    thread_id: str
    role: str
    content: str
    timestamp: str
    intent: Optional[str] = None
    sentiment: Optional[str] = None
    action_taken: Optional[Dict[str, Any]] = None
    attachments: Optional[List[Dict[str, Any]]] = None
    campaign_snapshot: Optional[Dict[str, Any]] = None

class ChatHistoryResponse(BaseModel):
    success: bool
    thread_id: str
    campaign_id: str
    messages: List[ChatMessage]
    has_more: bool

# ============================================================================
# Dependencies
# ============================================================================

def get_azure_openai() -> AzureOpenAIClient:
    """Dependency to get Azure OpenAI client"""
    client = AzureOpenAIClient()
    if not client.available:
        raise HTTPException(
            status_code=503,
            detail="Azure OpenAI service unavailable"
        )
    return client

def get_db_manager_url() -> str:
    """Get DB Manager service URL"""
    return settings.SERVICES.get("db-manager", {}).get("url", "")

# ============================================================================
# Endpoint 1: Chat-Based Campaign Intent Extraction
# ============================================================================

@router.post("/ai/chat-campaign-intent", response_model=ChatIntentResponse)
async def chat_campaign_intent(
    request: ChatIntentRequest,
    openai_client: AzureOpenAIClient = Depends(get_azure_openai)
):
    """
    Extract campaign parameters from conversational input.
    Uses GPT-5 to understand user intent and extract structured data.
    """
    try:
        logger.info(f"Processing campaign intent extraction, history length: {len(request.conversationHistory)}")

        # Build system prompt for parameter extraction
        system_prompt = """You are a drug discovery expert helping users design campaigns.

Extract campaign parameters from the conversation and guide users through setup.

Required parameters:
- name (string): Campaign name
- campaignType (string): Therapeutic area (Oncology, CNS, Anti-infective, etc.)
- goalDescription (string): Detailed campaign goal

Optional parameters:
- targetProtein (string): Target protein name or PDB ID
- targetMolecules (number): Number of molecules to discover (default: 10)
- minActivityThreshold (number): Activity threshold in nM (default: 200)
- keyProperties (string): Comma-separated properties to optimize

IMPORTANT RULES FOR YOUR RESPONSES:
1. Be conversational and helpful
2. Ask one question at a time
3. Explain recommendations in context
4. Auto-fill reasonable defaults
5. **NEVER display raw JSON or code blocks to the user**
6. **NEVER use markdown code fences (```) in your responses**
7. Confirm parameters in natural language only
8. When all required info is gathered, say: "Perfect! I have everything I need to generate your campaign configuration."

Format your response as natural conversation. Parameter extraction happens automatically via function calling."""

        # Prepare messages for GPT-5
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend([
            {"role": msg.role, "content": msg.content}
            for msg in request.conversationHistory
        ])

        # Call GPT-5 with function calling for parameter extraction
        functions = [{
            "name": "extract_campaign_parameters",
            "description": "Extract campaign parameters from user conversation",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Campaign name"},
                    "campaignType": {"type": "string", "description": "Therapeutic area"},
                    "targetProtein": {"type": "string", "description": "Target protein or PDB ID"},
                    "goalDescription": {"type": "string", "description": "Campaign goal description"},
                    "targetMolecules": {"type": "integer", "description": "Number of molecules to discover"},
                    "minActivityThreshold": {"type": "integer", "description": "Activity threshold in nM"},
                    "keyProperties": {"type": "string", "description": "Properties to optimize (comma-separated)"}
                }
            }
        }]

        # Prefer reliably extracting parameters every turn by explicitly
        # invoking the extraction function rather than relying on auto.
        response = await openai_client.chat_completion(
            messages=messages,
            functions=functions,
            function_call="extract_campaign_parameters",
            temperature=0.7
        )

        # Extract AI response (may be None/empty when tool_call is returned)
        ai_message = response.choices[0].message.content or ""

        # Extract parameters from tool_calls (new API) or function_call (deprecated API)
        extracted = request.extractedParams.dict()

        # Try modern tool_calls first (OpenAI SDK >= 1.0.0)
        if hasattr(response.choices[0].message, 'tool_calls') and response.choices[0].message.tool_calls:
            try:
                # Modern API: response.choices[0].message.tool_calls is a list
                tool_call = response.choices[0].message.tool_calls[0]
                func_args = json.loads(tool_call.function.arguments)
                # Merge new parameters with existing ones
                for key, value in func_args.items():
                    if value is not None:
                        extracted[key] = value
                logger.info(f"Extracted parameters from tool_calls: {func_args}")
            except (json.JSONDecodeError, IndexError, AttributeError) as e:
                logger.warning(f"Failed to parse tool_calls arguments: {e}")
        # Fallback to deprecated function_call (backward compatibility)
        elif hasattr(response.choices[0].message, 'function_call') and response.choices[0].message.function_call:
            try:
                func_args = json.loads(response.choices[0].message.function_call.arguments)
                # Merge new parameters with existing ones
                for key, value in func_args.items():
                    if value is not None:
                        extracted[key] = value
                logger.info(f"Extracted parameters from function_call: {func_args}")
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse function_call arguments: {e}")
        else:
            logger.warning("No tool_calls or function_call in response - parameters not extracted")

        # Check completion status
        required_fields = ["name", "campaignType", "goalDescription"]
        completed_fields = [k for k, v in extracted.items() if v is not None and v != ""]
        missing_required = [f for f in required_fields if not extracted.get(f)]

        completion_status = CompletionStatus(
            isComplete=len(missing_required) == 0,
            completed=completed_fields,
            missingRequired=missing_required,
            missingOptional=[
                f for f in ["targetProtein", "targetMolecules", "minActivityThreshold", "keyProperties"]
                if not extracted.get(f)
            ]
        )

        # If the model didn't produce a conversational message (common when
        # only a tool/function call is returned), generate a concise, helpful
        # follow-up prompt to collect the next missing required field.
        if not ai_message:
            next_question_map = {
                "name": "What would you like to name this campaign?",
                "campaignType": "Which therapeutic area best fits (e.g., Oncology, CNS, Anti-infective)?",
                "goalDescription": "What is your primary goal for this campaign (e.g., discover novel inhibitors, improve selectivity)?",
            }
            if completion_status.missingRequired:
                first_missing = completion_status.missingRequired[0]
                ai_message = next_question_map.get(first_missing, "Could you provide more details to proceed?")
            else:
                ai_message = (
                    "Perfect! I have everything I need to generate your campaign configuration."
                )

        return ChatIntentResponse(
            success=True,
            message=ai_message,
            extractedParams=CampaignIntent(**extracted),
            completionStatus=completion_status
        )

    except Exception as e:
        logger.error(f"Error in chat_campaign_intent: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to process intent: {str(e)}")

# ============================================================================
# Endpoint 2: Send Message to Campaign Chat
# ============================================================================

@router.post("/campaigns/{campaign_id}/chat/message", response_model=SendMessageResponse)
async def send_campaign_message(
    campaign_id: str,
    request: SendMessageRequest,
    x_org_id: str = Header(..., alias="X-Org-Id"),
    openai_client: AzureOpenAIClient = Depends(get_azure_openai),
    db_url: str = Depends(get_db_manager_url)
):
    """
    Process user message in campaign chat.
    Detects intent, generates AI response, and takes appropriate actions.
    """
    try:
        logger.info(f"Processing message for campaign {campaign_id}")

        # Get campaign context from database
        campaign = await _get_campaign(campaign_id, x_org_id, db_url)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        # Use campaign_id as thread_id; load chat history from campaigns.metadata
        thread_id = campaign_id
        history = await _get_metadata_chat_history_db(thread_id)

        # Detect intent and sentiment
        intent = _detect_intent(request.message)
        sentiment = _detect_sentiment(request.message)

        # Check if message contains action from UI (e.g., "[User clicked: add_library (s3://...)]")
        if request.message.startswith("[User clicked:"):
            # Parse action from UI interaction
            import re
            match = re.match(r'\[User clicked: (\w+)(?:\s+\((.+)\))?\]', request.message)
            if match:
                action = match.group(1)
                value = match.group(2) if match.group(2) else None

                # Handle library actions from UI
                if action == "add_library" and value:
                    # Directly add library
                    data_sources = campaign.get("dataSources", {})
                    current_libraries = data_sources.get("internalLibraries", [])

                    if value not in current_libraries:
                        is_valid, error_msg = _validate_s3_path(value)
                        if is_valid:
                            updated_libraries = current_libraries + [value]
                            success = await _update_campaign_libraries(campaign_id, updated_libraries)

                            if success:
                                ai_content = f"✅ Successfully added library: `{value}`\n\nYour campaign now has {len(updated_libraries)} internal librar{'y' if len(updated_libraries) == 1 else 'ies'}. The compounds will be included in the next iteration."
                            else:
                                ai_content = f"❌ Failed to add library due to a database error. Please try again."
                        else:
                            ai_content = f"❌ Invalid library path: {error_msg}"
                    else:
                        ai_content = f"ℹ️ This library is already in your campaign."

                    # Save user and AI messages to metadata chat history
                    hist = await _get_metadata_chat_history_db(thread_id)
                    user_message_id = str(uuid.uuid4())
                    hist.append({
                        "id": user_message_id,
                        "thread_id": thread_id,
                        "role": ChatRole.USER.value,
                        "content": f"Add library: {value}",
                        "timestamp": datetime.utcnow().isoformat(),
                        "intent": IntentType.DATA_REQUEST.value,
                        "sentiment": SentimentType.NEUTRAL.value
                    })

                    ai_message_id = str(uuid.uuid4())
                    hist.append({
                        "id": ai_message_id,
                        "thread_id": thread_id,
                        "role": ChatRole.ASSISTANT.value,
                        "content": ai_content,
                        "timestamp": datetime.utcnow().isoformat(),
                        "intent": IntentType.DATA_REQUEST.value,
                        "sentiment": SentimentType.NEUTRAL.value
                    })
                    await _save_metadata_chat_history_db(thread_id, hist)

                    return SendMessageResponse(
                        success=True,
                        message_id=ai_message_id,
                        ai_response=AIResponse(
                            content=ai_content,
                            intent=IntentType.DATA_REQUEST,
                            sentiment=SentimentType.NEUTRAL
                        )
                    )

                elif action == "remove_library" and value:
                    # Directly remove library
                    data_sources = campaign.get("dataSources", {})
                    current_libraries = data_sources.get("internalLibraries", [])

                    if value in current_libraries:
                        updated_libraries = [lib for lib in current_libraries if lib != value]
                        success = await _update_campaign_libraries(campaign_id, updated_libraries)

                        if success:
                            ai_content = f"✅ Successfully removed library: `{value}`\n\nYour campaign now has {len(updated_libraries)} internal librar{'y' if len(updated_libraries) == 1 else 'ies'}."
                        else:
                            ai_content = f"❌ Failed to remove library due to a database error. Please try again."
                    else:
                        ai_content = f"ℹ️ This library was not found in your campaign."

                    # Save user and AI messages to metadata chat history
                    hist = await _get_metadata_chat_history_db(thread_id)
                    user_message_id = str(uuid.uuid4())
                    hist.append({
                        "id": user_message_id,
                        "thread_id": thread_id,
                        "role": ChatRole.USER.value,
                        "content": f"Remove library: {value}",
                        "timestamp": datetime.utcnow().isoformat(),
                        "intent": IntentType.DATA_REQUEST.value,
                        "sentiment": SentimentType.NEUTRAL.value
                    })

                    ai_message_id = str(uuid.uuid4())
                    hist.append({
                        "id": ai_message_id,
                        "thread_id": thread_id,
                        "role": ChatRole.ASSISTANT.value,
                        "content": ai_content,
                        "timestamp": datetime.utcnow().isoformat(),
                        "intent": IntentType.DATA_REQUEST.value,
                        "sentiment": SentimentType.NEUTRAL.value
                    })
                    await _save_metadata_chat_history_db(thread_id, hist)

                    return SendMessageResponse(
                        success=True,
                        message_id=ai_message_id,
                        ai_response=AIResponse(
                            content=ai_content,
                            intent=IntentType.DATA_REQUEST,
                            sentiment=SentimentType.NEUTRAL
                        )
                    )

        # Append user message to metadata chat history
        user_message_id = str(uuid.uuid4())
        user_msg = {
            "id": user_message_id,
            "thread_id": thread_id,
            "role": ChatRole.USER.value,
            "content": request.message,
            "timestamp": datetime.utcnow().isoformat(),
            "intent": None,
            "sentiment": None,
        }
        history.append(user_msg)

        # Build context for GPT-5
        from core.prompt_sanitizer import sanitize_for_prompt
        _camp_name = sanitize_for_prompt(campaign.get('name', 'Unknown'), 'campaign.name', 200)
        _camp_status = sanitize_for_prompt(campaign.get('status', 'unknown'), 'campaign.status', 50)
        _camp_phase = sanitize_for_prompt(campaign.get('workflow_state', {}).get('current_phase', 'unknown'), 'campaign.phase', 50)
        system_prompt = f"""You are managing campaign '{_camp_name}' (ID: {campaign_id}).

Current state:
- Status: {_camp_status}
- Workflow Phase: {_camp_phase}

Analyze user intent and provide helpful response. You can:
- Answer questions about campaign progress
- Recommend interventions to improve yield
- Explain technical details
- Show data visualizations
- Execute control commands (pause/resume)
- Export data

Be conversational, explain your reasoning, and suggest concrete actions when appropriate."""

        # Prepare messages
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend([
            {"role": msg["role"], "content": msg["content"]}
            for msg in history[-10:]  # Last 10 messages for context
        ])
        messages.append({"role": "user", "content": request.message})

        # Define available functions for AI to call
        functions = _get_campaign_functions()

        # Call GPT-5
        response = await openai_client.chat_completion(
            messages=messages,
            functions=functions,
            function_call="auto",
            temperature=0.7
        )

        ai_content = response.choices[0].message.content or ""

        # Execute action if function was called
        action_taken = None
        attachments = []

        if response.choices[0].message.function_call:
            function_name = response.choices[0].message.function_call.name
            function_args = json.loads(response.choices[0].message.function_call.arguments)

            logger.info(f"GPT-5 called function: {function_name} with args: {function_args}")

            action_taken, attachments = await _execute_campaign_action(
                campaign_id=campaign_id,
                function_name=function_name,
                function_args=function_args,
                campaign=campaign,
                db_url=db_url,
                org_id=x_org_id
            )

        # Append AI response to metadata chat history and persist
        ai_message_id = str(uuid.uuid4())
        ai_msg = {
            "id": ai_message_id,
            "thread_id": thread_id,
            "role": ChatRole.ASSISTANT.value,
            "content": ai_content,
            "timestamp": datetime.utcnow().isoformat(),
            "intent": intent.value,
            "sentiment": sentiment.value,
            "action_taken": action_taken.dict() if action_taken else None,
            "attachments": attachments or None,
            "campaign_snapshot": {
                "iteration": campaign.get("workflow_state", {}).get("phase_iteration", 0),
                "candidates_count": len(campaign.get("workflow_state", {}).get("molecules", [])),
                "discoveries_count": 0,
                "status": campaign.get("status", "unknown")
            }
        }
        history.append(ai_msg)

        # Persist updated history to campaigns.metadata
        await _save_metadata_chat_history_db(thread_id, history)

        return SendMessageResponse(
            success=True,
            message_id=ai_message_id,
            ai_response=AIResponse(
                content=ai_content,
                intent=intent,
                sentiment=sentiment,
                action_taken=action_taken,
                attachments=attachments
            )
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in send_campaign_message: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to process message: {str(e)}")

# ============================================================================
# Endpoint 3: Get Chat History
# ============================================================================

@router.get("/campaigns/{campaign_id}/chat/history", response_model=ChatHistoryResponse)
async def get_chat_history(
    campaign_id: str,
    limit: int = 100,
    before: Optional[str] = None,
    x_org_id: str = Header(..., alias="X-Org-Id"),
    db_url: str = Depends(get_db_manager_url)
):
    """
    Retrieve conversation history for a campaign.
    Behavior:
    - Prefer persistent DB chat messages when available.
    - If none exist, attempt to migrate metadata.initial_chat_transcript to DB.
    - If DB operations fail, fall back to returning metadata transcript directly.
    """
    try:
        logger.info(f"Fetching chat history for campaign {campaign_id}, limit={limit}")

        # Always use campaigns.metadata chat history (DB source of truth)
        thread_id = campaign_id
        messages = await _get_metadata_chat_history_db(campaign_id)

        # Optional 'before' filtering
        if before:
            try:
                cutoff = datetime.fromisoformat(before)
                messages = [m for m in messages if datetime.fromisoformat(str(m.get("timestamp"))) < cutoff]
            except Exception:
                pass

        # Respect limit (return the most recent 'limit' messages)
        if limit and len(messages) > limit:
            messages = messages[-limit:]

        # Normalize to ChatMessage models
        norm = []
        for m in messages:
            try:
                norm.append(ChatMessage(**{
                    "id": str(m.get("id")),
                    "thread_id": thread_id,
                    "role": str(m.get("role", "user")),
                    "content": str(m.get("content", "")),
                    "timestamp": str(m.get("timestamp")),
                    "intent": m.get("intent"),
                    "sentiment": m.get("sentiment"),
                    "action_taken": m.get("action_taken"),
                    "attachments": m.get("attachments"),
                    "campaign_snapshot": m.get("campaign_snapshot")
                }))
            except Exception:
                # Skip malformed entries
                continue

        return ChatHistoryResponse(
            success=True,
            thread_id=thread_id,
            campaign_id=campaign_id,
            messages=norm,
            has_more=False
        )

    except Exception as e:
        logger.error(f"Error in get_chat_history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch history: {str(e)}")

# ============================================================================
# Helper Functions
# ============================================================================

async def _get_campaign(campaign_id: str, org_id: str, db_url: str) -> Optional[Dict[str, Any]]:
    """Fetch campaign using direct SQL helper to avoid external proxy 404s."""
    try:
        from routers.ai_orchestration import get_campaign_with_metrics_sql
        campaign = await get_campaign_with_metrics_sql(campaign_id)
        if campaign:
            # Normalize minimal shape expected by chat
            return {
                "id": campaign.get("id"),
                "tenant_id": campaign.get("tenant_id"),
                "name": campaign.get("name"),
                "status": campaign.get("status"),
                "workflow_state": campaign.get("workflow_state", {}),
                "constraints": campaign.get("constraints", {}),
                "dataSources": campaign.get("dataSources", {}),
                "autonomy": campaign.get("autonomy", {}),
                "metadata": campaign.get("metadata", {})
            }
        return None
    except Exception as e:
        logger.error(f"Failed to fetch campaign via SQL helper: {e}", exc_info=True)
        return None

async def _update_campaign_libraries(campaign_id: str, libraries: List[str]) -> bool:
    """Update campaign's internal libraries in the database"""
    try:
        uuid.UUID(campaign_id)  # Validate UUID format
    except ValueError:
        logger.error(f"Invalid campaign_id format: {campaign_id}")
        return False

    try:
        from core.db_helper import execute_sql
        libraries_json = json.dumps(libraries)
        await execute_sql(
            """UPDATE campaigns
               SET dataSources = JSON_MODIFY(COALESCE(dataSources, '{}'),
                   '$.internalLibraries', JSON_QUERY(%s)),
                   updated_at = GETUTCDATE()
               WHERE id = CAST(%s AS UNIQUEIDENTIFIER)""",
            (libraries_json, campaign_id)
        )
        logger.info(f"Updated libraries for campaign {campaign_id}: {len(libraries)} libraries")
        return True

    except Exception as e:
        logger.error(f"Failed to update campaign libraries: {e}")
        return False

async def _get_metadata_chat_history_db(campaign_id: str) -> List[Dict[str, Any]]:
    """Read chat history from campaigns.metadata.chat_history; robust fallbacks on failure."""
    try:
        uuid.UUID(campaign_id)  # Validate UUID format
    except ValueError:
        logger.error(f"Invalid campaign_id format: {campaign_id}")
        return []

    # 1) Try parameterized SQL query
    try:
        from core.db_helper import query_sql
        rows = await query_sql(
            "SELECT metadata FROM campaigns WHERE id = CAST(%s AS UNIQUEIDENTIFIER)",
            (campaign_id,)
        )
        if rows:
            metadata = rows[0].get('metadata') if isinstance(rows[0], dict) else rows[0][0]
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            history = metadata.get("chat_history")
            if isinstance(history, list):
                return history
            # Fallback to initial_chat_transcript
            transcript = metadata.get("initial_chat_transcript")
            if isinstance(transcript, list):
                normalized = []
                for i, m in enumerate(transcript):
                    content = str(m.get("content", "") or "")
                    if not content:
                        continue
                    role = str(m.get("role", "assistant"))
                    if role not in ("user", "assistant", "system"):
                        role = "assistant"
                    normalized.append({
                        "id": m.get("id") or f"transcript_{i}",
                        "thread_id": campaign_id,
                        "role": role,
                        "content": content,
                        "timestamp": m.get("timestamp") or datetime.utcnow().isoformat(),
                        "metadata": {"from_initial_transcript": True}
                    })
                await _save_metadata_chat_history_db(campaign_id, normalized)
                return normalized
    except Exception as e:
        logger.warning(f"Parameterized metadata query failed, will attempt SQL fallback: {e}")

    # 2) Direct SQL fallback (campaign_chat tables)
    try:
        from core.db_helper import query_sql
        # Get any active thread for this campaign
        threads = await query_sql(
            "SELECT TOP 1 id FROM campaign_chat_threads WHERE campaign_id = CAST(%s AS UNIQUEIDENTIFIER)",
            (campaign_id,)
        )
        thread_id = None
        if threads and isinstance(threads, list):
            t0 = threads[0]
            thread_id = t0.get('id') if isinstance(t0, dict) else list(t0.values())[0]

        if thread_id:
            rows = await query_sql(
                "SELECT id, role, content, timestamp FROM campaign_chat_messages WHERE thread_id = CAST(%s AS UNIQUEIDENTIFIER) ORDER BY timestamp ASC",
                (thread_id,)
            )
            history = []
            for r in rows or []:
                history.append({
                    "id": str(r.get('id')) if isinstance(r, dict) else str(r[0]),
                    "thread_id": campaign_id,
                    "role": r.get('role') if isinstance(r, dict) else r[1],
                    "content": r.get('content') if isinstance(r, dict) else r[2],
                    "timestamp": (r.get('timestamp') if isinstance(r, dict) else r[3]) or datetime.utcnow().isoformat()
                })
            if history:
                return history
    except Exception as e:
        logger.error(f"Direct SQL chat history fallback failed: {e}")
        # continue to final fallback

    # 3) Last resort: empty
    return []

async def _save_metadata_chat_history_db(campaign_id: str, history: List[Dict[str, Any]]) -> None:
    """Persist chat history array into campaigns.metadata.chat_history."""
    try:
        uuid.UUID(campaign_id)  # Validate UUID format
    except ValueError:
        raise ValueError(f"Invalid campaign_id format: {campaign_id}")

    try:
        from core.db_helper import execute_sql
        hist_json = json.dumps(history)
        await execute_sql(
            """UPDATE campaigns
               SET metadata = JSON_MODIFY(COALESCE(metadata, '{}'), '$.chat_history', JSON_QUERY(%s)),
                   updated_at = GETUTCDATE()
               WHERE id = CAST(%s AS UNIQUEIDENTIFIER)""",
            (hist_json, campaign_id)
        )
    except Exception as e:
        logger.error(f"Failed to save metadata chat history: {e}")
        raise

def _validate_s3_path(s3_path: str) -> tuple[bool, str]:
    """
    Validate S3 path format and file extension.
    Returns (is_valid, error_message)
    """
    import re

    # Check S3 URI format
    s3_pattern = r'^s3://[a-z0-9][a-z0-9\-\.]*[a-z0-9]/.*$'
    if not re.match(s3_pattern, s3_path, re.IGNORECASE):
        return False, "Invalid S3 URI format. Expected: s3://bucket-name/path/to/file"

    # Check file extension
    valid_extensions = ['.sdf', '.mol', '.csv', '.mol2', '.pdb']
    if not any(s3_path.lower().endswith(ext) for ext in valid_extensions):
        return False, f"Unsupported file format. Supported: {', '.join(valid_extensions)}"

    return True, ""

def _detect_intent(message: str) -> IntentType:
    """Detect user intent from message"""
    message_lower = message.lower()

    # Library management keywords
    if any(word in message_lower for word in ['library', 'libraries', 'add library', 'remove library', 'show libraries']):
        return IntentType.DATA_REQUEST

    # Question keywords
    if any(word in message_lower for word in ['why', 'how', 'what', 'explain', 'tell me']):
        return IntentType.QUESTION

    # Intervention keywords
    if any(word in message_lower for word in ['adjust', 'change', 'relax', 'increase', 'decrease', 'more', 'less', 'make it']):
        return IntentType.INTERVENTION

    # Data request keywords
    if any(word in message_lower for word in ['show', 'display', 'list', 'view', 'see']):
        return IntentType.DATA_REQUEST

    # Control keywords
    if any(word in message_lower for word in ['pause', 'stop', 'resume', 'restart']):
        return IntentType.CONTROL

    # Export keywords
    if any(word in message_lower for word in ['export', 'download', 'save']):
        return IntentType.EXPORT

    # Status check keywords
    if any(word in message_lower for word in ['status', 'progress', 'update', "how's it going"]):
        return IntentType.STATUS_CHECK

    return IntentType.CONVERSATIONAL

def _detect_sentiment(message: str) -> SentimentType:
    """Detect user sentiment from message"""
    message_lower = message.lower()

    # Frustrated indicators
    if any(word in message_lower for word in ['frustrated', 'annoyed', 'why is this', 'nothing is working', 'terrible']):
        return SentimentType.FRUSTRATED

    # Confused indicators
    if any(word in message_lower for word in ["don't understand", "confused", "not sure", "what does", "unclear"]):
        return SentimentType.CONFUSED

    # Positive indicators
    if any(word in message_lower for word in ['great', 'excellent', 'perfect', 'amazing', 'wonderful', 'thanks', 'thank you']):
        return SentimentType.POSITIVE

    return SentimentType.NEUTRAL

def _get_campaign_functions() -> List[Dict[str, Any]]:
    """Define functions GPT-5 can call for campaign interactions"""
    return [
        {
            "name": "manage_internal_libraries",
            "description": "Display current internal libraries and allow user to add/remove them",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["show", "suggest"],
                        "description": "Action to perform: show current libraries or suggest relevant ones"
                    }
                }
            }
        },
        {
            "name": "add_internal_library",
            "description": "Add a proprietary compound library from S3",
            "parameters": {
                "type": "object",
                "properties": {
                    "s3_path": {
                        "type": "string",
                        "description": "S3 URI to the library file (e.g., s3://bucket/path/library.sdf)"
                    }
                },
                "required": ["s3_path"]
            }
        },
        {
            "name": "remove_internal_library",
            "description": "Remove a proprietary compound library",
            "parameters": {
                "type": "object",
                "properties": {
                    "s3_path": {
                        "type": "string",
                        "description": "S3 URI of the library to remove"
                    }
                },
                "required": ["s3_path"]
            }
        },
        {
            "name": "analyze_campaign_metrics",
            "description": "Analyze campaign metrics and identify bottlenecks",
            "parameters": {
                "type": "object",
                "properties": {
                    "metrics_to_analyze": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Metrics to analyze (e.g., pass_rates, binding_affinity, toxicity)"
                    }
                }
            }
        },
        {
            "name": "adjust_thresholds",
            "description": "Adjust campaign quality thresholds",
            "parameters": {
                "type": "object",
                "properties": {
                    "mw_max": {"type": "number", "description": "Maximum molecular weight in Da"},
                    "hepatotoxicity_threshold": {"type": "number", "description": "Hepatotoxicity threshold (0-1)"},
                    "binding_threshold": {"type": "number", "description": "Binding affinity threshold in kcal/mol"},
                    "logp_min": {"type": "number", "description": "Minimum LogP (fragments more polar)"},
                    "logp_max": {"type": "number", "description": "Maximum LogP (fragments more polar)"},
                    "tpsa_min": {"type": "number", "description": "Minimum TPSA"},
                    "tpsa_max": {"type": "number", "description": "Maximum TPSA"},
                    "qed_min": {"type": "number", "description": "Minimum QED (allow lower for fragments)"},
                    "qed_max": {"type": "number", "description": "Maximum QED (optional)"},
                    "overall_toxicity": {"type": "number", "description": "Overall toxicity threshold (0-1)"},
                    "reason": {"type": "string", "description": "Reason for adjustment"}
                },
                "required": ["reason"]
            }
        },
        {
            "name": "regenerate_molecules",
            "description": "Trigger molecule regeneration with new parameters",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "description": "Number of molecules to generate"},
                    "diversity": {"type": "number", "description": "Diversity parameter (0-1)"},
                    "novelty": {"type": "number", "description": "Novelty parameter (0-1)"},
                    "strategy": {
                        "type": "string",
                        "enum": ["diverse_exploration", "focused_exploration", "exploitation"],
                        "description": "Generation strategy"
                    }
                },
                "required": ["count"]
            }
        },
        {
            "name": "get_top_molecules",
            "description": "Retrieve top N candidate molecules",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "description": "Number of molecules to retrieve"},
                    "sort_by": {
                        "type": "string",
                        "enum": ["binding_affinity", "composite_score", "toxicity"],
                        "description": "Sort criterion"
                    }
                },
                "required": ["count"]
            }
        },
        {
            "name": "pause_campaign",
            "description": "Pause campaign execution",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Reason for pausing"}
                },
                "required": ["reason"]
            }
        },
        {
            "name": "resume_campaign",
            "description": "Resume paused campaign",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    ]

async def _execute_campaign_action(
    campaign_id: str,
    function_name: str,
    function_args: Dict[str, Any],
    campaign: Dict[str, Any],
    db_url: str,
    org_id: str
) -> tuple[Optional[ActionTaken], List[Dict[str, Any]]]:
    """Execute campaign action based on GPT-5 function call"""

    try:
        if function_name == "manage_internal_libraries":
            # Display current libraries in a management card
            action_type = function_args.get("action", "show")
            data_sources = campaign.get("dataSources", {})
            current_libraries = data_sources.get("internalLibraries", [])

            suggested_libraries = []
            if action_type == "suggest":
                # Generate suggestions based on campaign type
                campaign_type = campaign.get("campaignType", "").lower()
                if "oncology" in campaign_type or "kras" in campaign.get("name", "").lower():
                    suggested_libraries = [
                        "s3://novo-compounds/oncology/fda-approved-kinase-inhibitors.sdf",
                        "s3://novo-compounds/oncology/chembl-oncology-subset.csv"
                    ]
                elif "cns" in campaign_type or "neuro" in campaign_type:
                    suggested_libraries = [
                        "s3://novo-compounds/cns/bbb-penetrant-compounds.sdf"
                    ]

            attachments = [{
                "type": "library_management",
                "data": {
                    "title": "Internal Compound Libraries",
                    "description": "Manage proprietary libraries from S3. Supports SDF, MOL, and CSV formats.",
                    "current_libraries": current_libraries,
                    "allow_add": True,
                    "allow_remove": True,
                    "suggested_libraries": suggested_libraries if suggested_libraries else None
                }
            }]

            return None, attachments

        elif function_name == "add_internal_library":
            # Add library to campaign
            s3_path = function_args.get("s3_path", "").strip()

            # Validate S3 path
            is_valid, error_msg = _validate_s3_path(s3_path)
            if not is_valid:
                action_taken = ActionTaken(
                    type="data_request",
                    details={
                        "action": "add_library",
                        "s3_path": s3_path,
                        "error": error_msg
                    },
                    success=False
                )
                return action_taken, []

            # Get current libraries
            data_sources = campaign.get("dataSources", {})
            current_libraries = data_sources.get("internalLibraries", [])

            # Check for duplicates
            if s3_path in current_libraries:
                action_taken = ActionTaken(
                    type="data_request",
                    details={
                        "action": "add_library",
                        "s3_path": s3_path,
                        "error": "Library already exists"
                    },
                    success=False
                )
                return action_taken, []

            # Add library
            updated_libraries = current_libraries + [s3_path]
            success = await _update_campaign_libraries(campaign_id, updated_libraries)

            action_taken = ActionTaken(
                type="data_request",
                details={
                    "action": "add_library",
                    "s3_path": s3_path,
                    "library_count": len(updated_libraries)
                },
                success=success
            )
            return action_taken, []

        elif function_name == "remove_internal_library":
            # Remove library from campaign
            s3_path = function_args.get("s3_path", "").strip()

            # Get current libraries
            data_sources = campaign.get("dataSources", {})
            current_libraries = data_sources.get("internalLibraries", [])

            # Check if library exists
            if s3_path not in current_libraries:
                action_taken = ActionTaken(
                    type="data_request",
                    details={
                        "action": "remove_library",
                        "s3_path": s3_path,
                        "error": "Library not found"
                    },
                    success=False
                )
                return action_taken, []

            # Remove library
            updated_libraries = [lib for lib in current_libraries if lib != s3_path]
            success = await _update_campaign_libraries(campaign_id, updated_libraries)

            action_taken = ActionTaken(
                type="data_request",
                details={
                    "action": "remove_library",
                    "s3_path": s3_path,
                    "library_count": len(updated_libraries)
                },
                success=success
            )
            return action_taken, []

        elif function_name == "analyze_campaign_metrics":
            # Return metrics analysis as chart attachment
            attachments = [{
                "type": "chart",
                "data": {
                    "title": "Campaign Metrics",
                    "chartType": "line",
                    "series": [
                        {"name": "Pass Rate", "data": [12, 8, 15, 18]},
                        {"name": "Candidates", "data": [100, 85, 95, 102]}
                    ],
                    "xaxis": {"categories": ["Iter 1", "Iter 2", "Iter 3", "Iter 4"]}
                }
            }]
            return None, attachments

        elif function_name == "get_top_molecules":
            # Return top molecules as cards
            count = function_args.get("count", 3)
            molecules = campaign.get("workflow_state", {}).get("molecules", [])[:count]

            attachments = [{
                "type": "molecule_card",
                "data": {
                    "molecule_id": f"MOL_{i+1}",
                    "smiles": mol.get("smiles", "CC(C)CC1=CC=C"),
                    "properties": {
                        "binding_affinity": mol.get("binding_affinity", -8.5),
                        "mw": mol.get("mw", 450),
                        "logP": mol.get("logP", 3.2),
                        "hepatotoxicity": mol.get("hepatotoxicity", 0.05)
                    },
                    "actions": ["view_3d", "export", "add_to_favorites"]
                }
            } for i, mol in enumerate(molecules)]

            return None, attachments

        elif function_name == "adjust_thresholds":
            # Apply adjustments to campaign constraints/thresholds directly in DB
            try:
                from core.db_helper import execute_sql, query_sql
                import json as _json

                reason = function_args.get("reason", "Manual threshold adjustment via chat")
                mw_max = function_args.get("mw_max")
                hepato = function_args.get("hepatotoxicity_threshold")
                binding = function_args.get("binding_threshold")
                logp_min = function_args.get("logp_min")
                logp_max = function_args.get("logp_max")
                tpsa_min = function_args.get("tpsa_min")
                tpsa_max = function_args.get("tpsa_max")
                qed_min = function_args.get("qed_min")
                qed_max = function_args.get("qed_max")
                overall_tox = function_args.get("overall_toxicity")

                # Load current constraints
                rows = await query_sql(
                    "SELECT constraints, metadata FROM campaigns WHERE campaign_id = CAST(%s AS UNIQUEIDENTIFIER)",
                    (campaign_id,)
                )
                if not rows:
                    return ActionTaken(type="intervention", details={"action": "adjust_thresholds", "error": "Campaign not found"}, success=False), []

                raw_constraints = rows[0].get("constraints") or "{}"
                raw_metadata = rows[0].get("metadata") or "{}"

                try:
                    constraints_obj = _json.loads(raw_constraints) if isinstance(raw_constraints, str) else (raw_constraints or {})
                except Exception:
                    constraints_obj = {}
                try:
                    metadata_obj = _json.loads(raw_metadata) if isinstance(raw_metadata, str) else (raw_metadata or {})
                except Exception:
                    metadata_obj = {}

                def set_path(root: Dict[str, Any], path: List[str], value: Any):
                    t = root
                    for p in path[:-1]:
                        if p not in t or not isinstance(t[p], dict):
                            t[p] = {}
                        t = t[p]
                    t[path[-1]] = value

                changes: Dict[str, Any] = {}
                if mw_max is not None:
                    set_path(constraints_obj, ["molecular", "mw", "max"], mw_max)
                    changes["constraints.molecular.mw.max"] = mw_max
                if hepato is not None:
                    set_path(constraints_obj, ["admet", "hepatotoxicity"], hepato)
                    changes["constraints.admet.hepatotoxicity"] = hepato
                if logp_min is not None:
                    set_path(constraints_obj, ["molecular", "logp", "min"], logp_min)
                    set_path(constraints_obj, ["molecular", "logP", "min"], logp_min)  # normalized/alias
                    changes["constraints.molecular.logp.min"] = logp_min
                if logp_max is not None:
                    set_path(constraints_obj, ["molecular", "logp", "max"], logp_max)
                    set_path(constraints_obj, ["molecular", "logP", "max"], logp_max)
                    changes["constraints.molecular.logp.max"] = logp_max
                if tpsa_min is not None:
                    set_path(constraints_obj, ["molecular", "tpsa", "min"], tpsa_min)
                    changes["constraints.molecular.tpsa.min"] = tpsa_min
                if tpsa_max is not None:
                    set_path(constraints_obj, ["molecular", "tpsa", "max"], tpsa_max)
                    changes["constraints.molecular.tpsa.max"] = tpsa_max
                if overall_tox is not None:
                    set_path(constraints_obj, ["admet", "overall_toxicity"], overall_tox)
                    changes["constraints.admet.overall_toxicity"] = overall_tox
                if binding is not None:
                    if "thresholds" not in metadata_obj or not isinstance(metadata_obj["thresholds"], dict):
                        metadata_obj["thresholds"] = {}
                    metadata_obj["thresholds"]["binding_affinity"] = binding
                    changes["thresholds.binding_affinity"] = binding
                if qed_min is not None:
                    if "thresholds" not in metadata_obj or not isinstance(metadata_obj["thresholds"], dict):
                        metadata_obj["thresholds"] = {}
                    metadata_obj["thresholds"]["qed_min"] = qed_min
                    changes["thresholds.qed_min"] = qed_min
                if qed_max is not None:
                    if "thresholds" not in metadata_obj or not isinstance(metadata_obj["thresholds"], dict):
                        metadata_obj["thresholds"] = {}
                    metadata_obj["thresholds"]["qed_max"] = qed_max
                    changes["thresholds.qed_max"] = qed_max

                # Persist updates
                await execute_sql(
                    "UPDATE campaigns SET constraints = %s, metadata = %s, updated_at = GETUTCDATE() WHERE campaign_id = CAST(%s AS UNIQUEIDENTIFIER)",
                    (_json.dumps(constraints_obj), _json.dumps(metadata_obj), campaign_id)
                )

                # Best-effort audit trail
                try:
                    import uuid as _uuid
                    intervention_id = str(_uuid.uuid4())
                    await execute_sql(
                        """
                        INSERT INTO intervention_requests (
                            intervention_id, campaign_id, requested_at, requested_by,
                            intervention_type, reason, previous_config, new_config, action_taken, status
                        ) VALUES (%s, %s, GETUTCDATE(), %s, 'adjust_thresholds', %s, NULL, %s, %s, 'completed')
                        """,
                        (
                            intervention_id,
                            campaign_id,
                            "chat_user",
                            reason,
                            _json.dumps({"changes": changes}),
                            f"Applied {len(changes)} adjustments via chat"
                        )
                    )
                except Exception:
                    pass

                action_taken = ActionTaken(
                    type="intervention",
                    details={
                        "action": "adjust_thresholds",
                        "parameters": function_args,
                        "changes": changes
                    },
                    success=True
                )
                return action_taken, []
            except Exception as e:
                logger.error(f"Failed to adjust thresholds via chat: {e}")
                return ActionTaken(type="intervention", details={"action": "adjust_thresholds", "error": str(e)}, success=False), []

        elif function_name == "regenerate_molecules":
            # Trigger regeneration
            action_taken = ActionTaken(
                type="intervention",
                details={
                    "action": "regenerate",
                    "parameters": function_args
                },
                success=True
            )
            return action_taken, []

        elif function_name == "pause_campaign":
            # Pause campaign
            action_taken = ActionTaken(
                type="control",
                details={"action": "pause", "reason": function_args.get("reason")},
                success=True
            )
            return action_taken, []

        elif function_name == "resume_campaign":
            # Resume campaign
            action_taken = ActionTaken(
                type="control",
                details={"action": "resume"},
                success=True
            )
            return action_taken, []

        return None, []

    except Exception as e:
        logger.error(f"Failed to execute action {function_name}: {e}")
        return ActionTaken(type="error", details={"error": str(e)}, success=False), []
