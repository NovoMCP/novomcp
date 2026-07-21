"""
WebSocket Router for Real-Time Campaign Updates
Phase 1, Week 3-4, Task 3.1: Extracted from ai_orchestration.py

Provides WebSocket endpoints for:
- Campaign-specific updates (/ws/campaign/{campaign_id})
- Global dashboard updates (/ws/global)
- Cross-ECS task broadcasting via Redis pub/sub
"""

import logging
import json
from datetime import datetime
from typing import Dict, Any, List, Set
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSocket"])


# =============================================================================
# WebSocket Connection Managers
# =============================================================================

class ConnectionManager:
    """
    Manages WebSocket connections for campaign-specific updates.
    Maintains separate connection pools per campaign_id.
    """

    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, campaign_id: str):
        """Accept WebSocket connection and register it for a campaign"""
        await websocket.accept()
        if campaign_id not in self.active_connections:
            self.active_connections[campaign_id] = set()
        self.active_connections[campaign_id].add(websocket)

    def disconnect(self, websocket: WebSocket, campaign_id: str = None):
        """Remove WebSocket connection from campaign pool"""
        if campaign_id and campaign_id in self.active_connections:
            self.active_connections[campaign_id].discard(websocket)

    async def send_update(self, campaign_id: str, message: dict):
        """
        Send update to all connections subscribed to a campaign.
        Automatically removes disconnected websockets.
        """
        if campaign_id in self.active_connections:
            disconnected = set()
            for connection in self.active_connections[campaign_id]:
                try:
                    await connection.send_json(message)
                except:
                    disconnected.add(connection)
            # Remove disconnected websockets
            self.active_connections[campaign_id] -= disconnected


class GlobalConnectionManager:
    """
    Manages WebSocket connections for dashboard-wide updates.
    Supports subscription filtering by campaign_id and event types.
    """

    def __init__(self):
        # Track all active global websocket connections
        self.connections: Set[WebSocket] = set()
        # Optional per-connection subscriptions (campaign_ids or event types)
        self.subscriptions: Dict[WebSocket, Dict[str, Any]] = {}

    async def connect(self, websocket: WebSocket):
        """Accept WebSocket connection and set default subscriptions"""
        await websocket.accept()
        self.connections.add(websocket)
        # Default subscription is subscribe_all
        self.subscriptions[websocket] = {
            "all": True,
            "campaign_ids": set(),
            "events": set(["all"])  # event type filtering (optional)
        }

    def disconnect(self, websocket: WebSocket):
        """Remove WebSocket connection and its subscriptions"""
        self.connections.discard(websocket)
        if websocket in self.subscriptions:
            del self.subscriptions[websocket]

    def subscribe_all(self, websocket: WebSocket):
        """Subscribe to all campaigns and events"""
        if websocket in self.subscriptions:
            self.subscriptions[websocket]["all"] = True
            self.subscriptions[websocket]["campaign_ids"] = set()

    def subscribe_campaigns(self, websocket: WebSocket, campaign_ids: List[str]):
        """Subscribe to specific campaigns only"""
        if websocket in self.subscriptions:
            self.subscriptions[websocket]["all"] = False
            self.subscriptions[websocket]["campaign_ids"] = set(campaign_ids or [])

    def subscribe_events(self, websocket: WebSocket, events: List[str]):
        """Subscribe to specific event types only"""
        if websocket in self.subscriptions:
            self.subscriptions[websocket]["events"] = set(events or ["all"]) or set(["all"])

    async def broadcast(self, message: Dict[str, Any]):
        """
        Broadcast message to all global connections respecting campaign filters.
        Automatically removes disconnected websockets.
        """
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


# =============================================================================
# Global Manager Instances
# =============================================================================

# Campaign-specific connection manager
manager = ConnectionManager()

# Global dashboard connection manager
global_ws_manager = GlobalConnectionManager()


# =============================================================================
# WebSocket Endpoints
# =============================================================================

@router.websocket("/campaign/{campaign_id}")
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


@router.websocket("/global")
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


# =============================================================================
# Helper Functions for Broadcasting
# =============================================================================

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


# =============================================================================
# Exports for Other Modules
# =============================================================================

def get_connection_manager() -> ConnectionManager:
    """Get the campaign-specific connection manager instance"""
    return manager


def get_global_connection_manager() -> GlobalConnectionManager:
    """Get the global connection manager instance"""
    return global_ws_manager
