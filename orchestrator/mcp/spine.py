"""
Auth, credit-metering, and audit interfaces.

Three protocols — `AuthGate`, `CreditMeter`, `AuditSink` — plus default
implementations that let the engine run standalone with no external
services. Each interface can also be swapped for a custom implementation
selected via environment variable.

`CreditMeter` and `AuditSink` are intentionally separate contracts: audit
is useful without metering (self-hosters get real audit logging with no
credit accounting), and metering without audit is unusual.

Environment configuration (all default to local when unset):

  NOVO_AUTH        = local | custom   (default: local)
  NOVO_METER       = local | custom   (default: local)
  NOVO_AUDIT       = local | custom   (default: local)
  NOVO_AUDIT_PATH  = /path/to/audit.jsonl (target for the local file sink)

Setting any of the above to `custom` loads implementations from a
`spine_custom` module — write your own against the protocols below.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------

@dataclass
class User:
    """Represents an authenticated caller.

    A minimal, framework-agnostic identity returned by auth gates. Custom
    implementations can carry richer metadata (tier, credits, org info)
    in the `extra` slot.
    """

    user_id: str
    email: str = ""
    tier: str = "unlimited"        # local default; hosted values: free/core/team/enterprise
    org_id: Optional[str] = None
    org_name: Optional[str] = None

    # Optional metadata slot for custom implementations (credits, limits,
    # JWT claims, etc.). The default local implementation leaves it empty.
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Interfaces (protocols)
# ---------------------------------------------------------------------------

class AuthGate(Protocol):
    """Authenticate the incoming request. Returns a User or None."""

    async def validate(
        self, credential: Optional[str], mode: str = "core"
    ) -> Optional[User]:
        """Validate an API key, JWT, or None.

        Args:
            credential: The raw bearer token / API key / JWT. May be None
                for the local auth-less case.
            mode: "core" or "compute" — advisory hint for implementations
                that route to different validation endpoints.

        Returns:
            A User on success; None on failure.
        """
        ...


class CreditMeter(Protocol):
    """Record a tool call and (optionally) deduct credits.

    Separate from AuditSink by design — audit and metering are independent
    concerns and can be satisfied by different backends.
    """

    async def record(
        self,
        user: User,
        tool: str,
        cost_credits: int,
        meta: Optional[dict] = None,
    ) -> "RecordResult":
        """Record a completed tool call and deduct credits if applicable.

        Must never raise for the local case. Custom implementations may
        raise on unreachable backends; callers decide whether that's a
        hard error or a warning.
        """
        ...


class AuditSink(Protocol):
    """Emit an audit event. Fire-and-forget from the caller's perspective."""

    async def emit(
        self,
        event_type: str,
        payload: dict,
        user: Optional[User] = None,
    ) -> None:
        """Emit an audit event. Must not block or raise for the local case."""
        ...


@dataclass
class RecordResult:
    """Result of a CreditMeter.record() call."""

    success: bool
    remaining_credits: Optional[float] = None
    reason: Optional[str] = None  # populated when success=False


# ---------------------------------------------------------------------------
# Local default implementations
# ---------------------------------------------------------------------------

class LocalAuthGate:
    """Pass-through auth for local runs.

    Returns a single 'local' user regardless of what credential is
    presented (including None). No network calls; no external state.

    Tier is set to "enterprise" (the highest tier in the ToolTier enum)
    so all tools are accessible in local mode. OSS local users get
    unmetered access to everything by default — no tier gating, no
    credit accounting (NoopMeter handles that side).
    """

    _LOCAL_USER = User(
        user_id="local",
        email="local@localhost",
        tier="enterprise",
        org_id="local",
        org_name="local",
    )

    async def validate(
        self, credential: Optional[str], mode: str = "core"
    ) -> Optional[User]:
        return self._LOCAL_USER


class NoopMeter:
    """Meter that records nothing and always succeeds.

    Every tool call returns success without a credit deduction and without
    any network I/O. Pair with `FileAuditSink` for local observability.
    """

    async def record(
        self,
        user: User,
        tool: str,
        cost_credits: int,
        meta: Optional[dict] = None,
    ) -> RecordResult:
        return RecordResult(success=True, remaining_credits=None)


class FileAuditSink:
    """Append audit events as JSON-lines to a local file.

    Default path: `~/.novo/audit.jsonl` (override with `NOVO_AUDIT_PATH`).
    Atomic append per event; no buffering; no rotation. Users rotate however
    they want (logrotate, cron, whatever).
    """

    def __init__(self, path: Optional[str] = None):
        if path is None:
            path = os.getenv(
                "NOVO_AUDIT_PATH",
                str(Path.home() / ".novo" / "audit.jsonl"),
            )
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def emit(
        self,
        event_type: str,
        payload: dict,
        user: Optional[User] = None,
    ) -> None:
        record = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "event": event_type,
            "user": user.user_id if user else None,
            "org": user.org_id if user else None,
            "payload": payload,
        }
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except OSError as e:
            # Audit must not break the tool call — log and move on.
            logger.warning("FileAuditSink write failed: %s", e)


# ---------------------------------------------------------------------------
# Spine container + factory
# ---------------------------------------------------------------------------

@dataclass
class Spine:
    """The three interfaces bundled together.

    Set on `app.state.spine` at startup; passed through requests via
    `RequestContext`. Code that needs auth / credit / audit reads it
    from here explicitly.
    """

    auth: AuthGate
    meter: CreditMeter
    audit: AuditSink


@dataclass
class RequestContext:
    """Per-request context threaded through tool execution.

    Tools call `ctx.meter.record(...)` and `ctx.audit.emit(...)` directly.
    `funnel_id` is the audit correlation key.
    """

    spine: Spine
    user: User
    funnel_id: Optional[str] = None

    @property
    def auth(self) -> AuthGate:
        return self.spine.auth

    @property
    def meter(self) -> CreditMeter:
        return self.spine.meter

    @property
    def audit(self) -> AuditSink:
        return self.spine.audit


def build_spine() -> Spine:
    """Assemble a Spine from environment configuration.

    Default: local implementations for all three interfaces. The engine
    runs with no external services required.

    Setting any of NOVO_AUTH / NOVO_METER / NOVO_AUDIT to `custom` loads
    the corresponding implementation from a `spine_custom` module. Write
    your own module against the protocols above and place it on the
    import path.
    """

    auth: AuthGate = LocalAuthGate()
    meter: CreditMeter = NoopMeter()
    audit: AuditSink = FileAuditSink()

    want_custom_auth = os.getenv("NOVO_AUTH", "local").lower() == "custom"
    want_custom_meter = os.getenv("NOVO_METER", "local").lower() == "custom"
    want_custom_audit = os.getenv("NOVO_AUDIT", "local").lower() == "custom"

    if want_custom_auth or want_custom_meter or want_custom_audit:
        try:
            from . import spine_custom  # type: ignore[attr-defined]
        except ImportError as e:
            raise RuntimeError(
                "NOVO_AUTH/METER/AUDIT=custom requested but no "
                "`spine_custom` module is available. Provide one that "
                "implements AuthGate / CreditMeter / AuditSink, or unset "
                "the env vars to use the local defaults."
            ) from e

        if want_custom_auth:
            auth = spine_custom.AuthGate()  # type: ignore[assignment]
        if want_custom_meter:
            meter = spine_custom.CreditMeter()  # type: ignore[assignment]
        if want_custom_audit:
            audit = spine_custom.AuditSink()  # type: ignore[assignment]

    logger.info(
        "Spine assembled: auth=%s meter=%s audit=%s",
        type(auth).__name__,
        type(meter).__name__,
        type(audit).__name__,
    )
    return Spine(auth=auth, meter=meter, audit=audit)
