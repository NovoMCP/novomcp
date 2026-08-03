"""GET /v1/events — server-initiated notification stream (Streamable HTTP).

Implements the server-to-client notification half of the MCP Streamable HTTP
transport (spec 2025-03-26) for Studio's events.listen subscribers. Anthropic
deprecated the standalone HTTP+SSE transport (separate /messages POST and /sse
GET endpoints) in favor of Streamable HTTP, which uses a single endpoint URL
where:

  GET  /v1/events      → opens a long-lived stream of server-initiated
                          notifications. Body of the stream is SSE-framed.
                          Used here.

  POST /v1/events      → reserved for future client→server JSON-RPC messages
                          on the same endpoint. Not implemented in this
                          revision — Studio doesn't need it yet. Returns 405
                          so a future MCP-client cutover can light it up
                          without changing the URL.

Auth + org scoping: reuses `get_mcp_user` (the same dependency the agent
endpoint and /v1/tools/* use). Connections are bound to the user's org_id
on accept; the connection manager dispatches each Redis pubsub event only
to the connections whose org_id matches.

Resumability: clients may send `Last-Event-ID` on reconnect; we accept the
header and resume from the next event id. The session id (`Mcp-Session-Id`
response header) lets the client correlate reconnects to the same logical
session if/when the backend grows session-scoped state.

Heartbeat: emits a comment line (`: hb\n\n`) every 25s so intermediate
proxies / ALB idle-timeouts don't close the connection. Comments are
ignored by EventSource per the SSE spec.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import AsyncIterator, Dict, Optional, Set

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from . import router as _router  # get_mcp_user
from .auth import MCPUser

logger = logging.getLogger(__name__)

events_router = APIRouter(prefix="/v1/events", tags=["NovoMCP Events"])

# Heartbeat keeps the connection through ALB idle timeout (default 60s) and
# any HTTP/1.1 proxy keep-alive limits. SSE comments (lines starting with
# `:`) are dropped by EventSource per the spec, so this costs ~5 bytes per
# heartbeat per connection.
HEARTBEAT_INTERVAL_SECONDS = 25

# Per-connection queue depth. Bounded so a slow/disconnected client can't
# unboundedly buffer in memory if the server-side dispatch can't drain.
# If the queue is full when we try to enqueue, we drop the oldest event
# (FIFO eviction) — better to lose one event than block the dispatch
# loop or OOM the process.
CONNECTION_QUEUE_MAXSIZE = 256


class _EventConnection:
    """One active /v1/events stream. Holds the asyncio.Queue the dispatcher
    pushes into and the metadata (org_id, last_event_id) the manager needs
    for routing and resumability decisions."""

    __slots__ = ("connection_id", "org_id", "user_id", "queue", "next_event_id")

    def __init__(self, org_id: str, user_id: str):
        self.connection_id = str(uuid.uuid4())
        self.org_id = org_id
        self.user_id = user_id
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=CONNECTION_QUEUE_MAXSIZE)
        self.next_event_id = 1


class EventStreamManager:
    """Tracks active /v1/events connections and dispatches events from the
    Redis pubsub message_handler (set in main_https.py:438) to the
    connections whose org_id matches the event's target_org_id.

    Thread-safety: all mutations of `connections` happen from the asyncio
    event loop. The Redis subscriber loop posts via `dispatch()` which is
    `async` and runs in the same loop, so a single-task tasklet model holds.
    """

    def __init__(self):
        self.connections_by_org: Dict[str, Set[_EventConnection]] = {}

    async def connect(self, org_id: str, user_id: str) -> _EventConnection:
        conn = _EventConnection(org_id=org_id, user_id=user_id)
        self.connections_by_org.setdefault(org_id, set()).add(conn)
        logger.info(
            "events: client connected conn=%s org=%s user=%s (total_for_org=%d)",
            conn.connection_id, org_id, user_id, len(self.connections_by_org[org_id]),
        )
        return conn

    def disconnect(self, conn: _EventConnection) -> None:
        bucket = self.connections_by_org.get(conn.org_id)
        if bucket and conn in bucket:
            bucket.discard(conn)
            if not bucket:
                self.connections_by_org.pop(conn.org_id, None)
        logger.info("events: client disconnected conn=%s org=%s", conn.connection_id, conn.org_id)

    async def dispatch(self, message: Dict) -> None:
        """Called from the global Redis pubsub rebroadcast handler. Routes
        the event to connections whose org_id matches the event's target_org
        (read from `message["data"]["org_id"]`). Events without a target
        org_id are dispatched to no one — we don't broadcast org data
        cross-tenant, ever.
        """
        data = message.get("data") if isinstance(message.get("data"), dict) else {}
        target_org_id = data.get("org_id") or message.get("org_id")
        if not target_org_id:
            return

        bucket = self.connections_by_org.get(str(target_org_id))
        if not bucket:
            return

        # Render once, fan out. Each connection's queue is bounded; if full,
        # drop the oldest event (preserving the new one) rather than block
        # the dispatch loop.
        payload = json.dumps(message, default=str)
        for conn in list(bucket):
            try:
                conn.queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop oldest, retry once.
                try:
                    conn.queue.get_nowait()
                    conn.queue.put_nowait(payload)
                except Exception:
                    pass


# Module-level singleton wired in main_https.py at startup.
event_stream_manager = EventStreamManager()


def _format_sse(event_id: int, event_type: str, data: str) -> str:
    """Format an SSE frame. Spec: id + event + data lines, then blank line."""
    # data may contain newlines; each must be prefixed with `data: ` per RFC.
    data_lines = "\n".join(f"data: {line}" for line in data.split("\n"))
    return f"id: {event_id}\nevent: {event_type}\n{data_lines}\n\n"


@events_router.get("")
async def events_stream(
    request: Request,
    user: MCPUser = Depends(_router.get_mcp_user),
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    mcp_session_id: Optional[str] = Header(None, alias="Mcp-Session-Id"),
):
    """Open a server-to-client notification stream for the calling user's
    org. Body is SSE-framed; events have `id`, `event`, and `data` fields.

    Response headers:
      Content-Type: text/event-stream
      Cache-Control: no-cache, no-transform
      X-Accel-Buffering: no   (nginx: disable response buffering)
      Connection: keep-alive
      Mcp-Session-Id: <uuid>  (per Streamable HTTP — clients echo on reconnect)
    """
    if user.is_trial_blocked:
        raise HTTPException(
            status_code=402,
            detail={
                "error": getattr(user, "trial_block_reason", None) or "credits_exhausted",
                "message": "Your credits are depleted or your trial has expired.",
            },
        )

    # Honor an incoming session id (resumption) or mint a new one.
    session_id = mcp_session_id or str(uuid.uuid4())

    conn = await event_stream_manager.connect(org_id=user.org_id, user_id=user.user_id)

    # Best-effort: respect Last-Event-ID so a reconnecting client starts
    # numbering from the right place. We don't replay events; just advance
    # the counter so the client's de-dup-by-id logic stays consistent.
    if last_event_id:
        try:
            conn.next_event_id = int(last_event_id) + 1
        except ValueError:
            pass

    async def stream() -> AsyncIterator[str]:
        # Immediate hello + the heartbeat baseline so the client knows the
        # stream is alive before any real event arrives.
        try:
            hello = {
                "type": "stream-open",
                "data": {
                    "session_id": session_id,
                    "org_id": user.org_id,
                    "user_id": user.user_id,
                    "server_time": datetime.utcnow().isoformat() + "Z",
                },
            }
            yield _format_sse(conn.next_event_id, "stream-open", json.dumps(hello, default=str))
            conn.next_event_id += 1

            last_heartbeat = asyncio.get_event_loop().time()
            while True:
                if await request.is_disconnected():
                    break

                # Wait for either an event or the heartbeat deadline.
                try:
                    payload = await asyncio.wait_for(
                        conn.queue.get(), timeout=HEARTBEAT_INTERVAL_SECONDS
                    )
                except asyncio.TimeoutError:
                    payload = None

                now = asyncio.get_event_loop().time()
                if payload is None or now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                    # Comment line — SSE clients (including EventSource) drop
                    # this silently. Keeps the TCP connection live through
                    # proxies + ALB idle timeout.
                    yield ": hb\n\n"
                    last_heartbeat = now

                if payload is not None:
                    # message["type"] is the event name; fall back to "message".
                    try:
                        parsed = json.loads(payload)
                        event_type = parsed.get("type") or "message"
                    except Exception:
                        event_type = "message"
                    yield _format_sse(conn.next_event_id, event_type, payload)
                    conn.next_event_id += 1
        except asyncio.CancelledError:
            # Client disconnected mid-yield.
            raise
        finally:
            event_stream_manager.disconnect(conn)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
            "Mcp-Session-Id": session_id,
        },
    )


@events_router.post("")
async def events_post_reserved():
    """Reserved for future client→server JSON-RPC messages on the same
    Streamable HTTP endpoint. Returns 405 today; the URL is the same the
    eventual MCP-client cutover will use, so we own it now.
    """
    raise HTTPException(
        status_code=405,
        detail={
            "error": "method_not_allowed",
            "message": "POST /v1/events is reserved for future client→server "
                       "messages. Studio uses GET only for server-initiated "
                       "notifications. See Streamable HTTP transport spec.",
        },
    )
