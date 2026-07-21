"""
Redis Pub/Sub for Cross-Task WebSocket Broadcasting
Enables WebSocket broadcasts to reach all ECS tasks behind the load balancer
"""

import asyncio
import json
import logging
import uuid
from typing import Dict, Any, Callable, Optional
import redis.asyncio as redis
from datetime import datetime

logger = logging.getLogger(__name__)

class RedisPubSubManager:
    """
    Manages Redis pub/sub for cross-task WebSocket broadcasting.
    Each ECS task subscribes to ws.global channel and rebroadcasts to its local WebSocket connections.
    """

    def __init__(self, redis_url: str, key_prefix: str = "novomcp"):
        self.redis_url = redis_url
        self.key_prefix = key_prefix
        self.channel_name = f"{key_prefix}:ws:global"
        self.task_id = str(uuid.uuid4())[:8]  # Unique ID for this ECS task

        self.redis_client: Optional[redis.Redis] = None
        self.pubsub: Optional[redis.client.PubSub] = None
        self.subscriber_task: Optional[asyncio.Task] = None
        self.message_handler: Optional[Callable] = None
        self.is_running = False

        logger.info(f"RedisPubSubManager initialized for task {self.task_id}")

    async def connect(self):
        """Connect to Redis and set up pub/sub"""
        try:
            self.redis_client = await redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True
            )

            # Test connection
            await self.redis_client.ping()
            logger.info(f"Task {self.task_id}: Connected to Redis for pub/sub")

            # Set up pub/sub
            self.pubsub = self.redis_client.pubsub()
            await self.pubsub.subscribe(self.channel_name)
            logger.info(f"Task {self.task_id}: Subscribed to channel {self.channel_name}")

            return True
        except Exception as e:
            logger.error(f"Task {self.task_id}: Failed to connect to Redis: {e}")
            return False

    async def start_subscriber(self, message_handler: Callable[[Dict[str, Any]], None]):
        """
        Start the Redis subscriber loop that listens for messages and rebroadcasts locally.

        Args:
            message_handler: Async function that receives message dict and broadcasts to local WebSockets
        """
        if self.is_running:
            logger.warning(f"Task {self.task_id}: Subscriber already running")
            return

        self.message_handler = message_handler
        self.is_running = True

        # Start background task
        self.subscriber_task = asyncio.create_task(self._subscriber_loop())
        logger.info(f"Task {self.task_id}: Started Redis subscriber loop")

    async def _subscriber_loop(self):
        """Background loop that processes Redis messages"""
        logger.info(f"Task {self.task_id}: Entering subscriber loop")

        try:
            while self.is_running:
                try:
                    message = await self.pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)

                    if message and message['type'] == 'message':
                        try:
                            payload = json.loads(message['data'])

                            # Skip messages from this task (prevent loop)
                            origin_task_id = payload.get('_origin_task_id')
                            if origin_task_id == self.task_id:
                                logger.debug(f"Task {self.task_id}: Skipping own message")
                                continue

                            # Remove internal metadata before rebroadcasting
                            if '_origin_task_id' in payload:
                                del payload['_origin_task_id']

                            logger.info(f"Task {self.task_id}: Received broadcast: {payload.get('type')} from task {origin_task_id}")

                            # Rebroadcast to local WebSocket connections
                            if self.message_handler:
                                await self.message_handler(payload)

                        except json.JSONDecodeError as e:
                            logger.error(f"Task {self.task_id}: Failed to decode message: {e}")
                        except Exception as e:
                            logger.error(f"Task {self.task_id}: Error handling message: {e}")

                    # Small sleep to prevent tight loop
                    await asyncio.sleep(0.01)

                except asyncio.TimeoutError:
                    # Timeout is normal, continue loop
                    continue
                except Exception as e:
                    logger.error(f"Task {self.task_id}: Error in subscriber loop: {e}")
                    await asyncio.sleep(1)  # Back off on error

        except asyncio.CancelledError:
            logger.info(f"Task {self.task_id}: Subscriber loop cancelled")
        finally:
            logger.info(f"Task {self.task_id}: Exited subscriber loop")

    async def publish(self, event_type: str, data: Dict[str, Any]):
        """
        Publish a message to Redis for all tasks to receive.

        Args:
            event_type: Type of event (campaign_paused, campaign_resumed, etc.)
            data: Event payload
        """
        if not self.redis_client:
            logger.warning(f"Task {self.task_id}: Redis client not connected, skipping publish")
            return

        try:
            # Add origin task ID to prevent loops
            payload = {
                "type": event_type,
                "data": data,
                "timestamp": datetime.utcnow().isoformat(),
                "_origin_task_id": self.task_id
            }

            # Publish to Redis channel (default=str to handle UUID, datetime)
            await self.redis_client.publish(self.channel_name, json.dumps(payload, default=str))
            logger.info(f"Task {self.task_id}: Published {event_type} to Redis")

        except Exception as e:
            logger.error(f"Task {self.task_id}: Failed to publish to Redis: {e}")

    async def stop(self):
        """Stop the subscriber and disconnect from Redis"""
        logger.info(f"Task {self.task_id}: Stopping pub/sub manager")
        self.is_running = False

        if self.subscriber_task:
            self.subscriber_task.cancel()
            try:
                await self.subscriber_task
            except asyncio.CancelledError:
                pass

        if self.pubsub:
            await self.pubsub.unsubscribe(self.channel_name)
            await self.pubsub.close()

        if self.redis_client:
            await self.redis_client.close()

        logger.info(f"Task {self.task_id}: Pub/sub manager stopped")


# Global instance (will be initialized in main.py)
redis_pubsub_manager: Optional[RedisPubSubManager] = None


def get_redis_pubsub_manager() -> Optional[RedisPubSubManager]:
    """Get the global Redis pub/sub manager instance"""
    return redis_pubsub_manager
