"""Boot-time update check against GitHub Releases.

Non-blocking, non-nagging, opt-out. The engine still boots cleanly on any
network failure, missing cache directory, malformed response, etc. — the
worst case is silence.

Cache: `~/.novomcp/update_check.json` (30-day TTL).
Opt-out: NOVOMCP_NO_UPDATE_CHECK=1

Also tracks the last-seen version in `~/.novomcp/last_seen_version` so the
first boot after an upgrade can log a pointer to the changelog.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

import httpx

from version import __version__

logger = logging.getLogger(__name__)

GITHUB_RELEASES_URL = "https://api.github.com/repos/NovoMCP/novomcp/releases/latest"
RELEASES_HTML_BASE = "https://github.com/NovoMCP/novomcp/releases/tag/"
CHANGELOG_URL = "https://github.com/NovoMCP/novomcp/blob/main/docs/changelog.md"
CACHE_TTL_SECONDS = 30 * 24 * 3600  # 30 days
HTTP_TIMEOUT_SECONDS = 3.0


def _cache_dir() -> Path:
    d = Path.home() / ".novomcp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _parse_semver(tag: str) -> Optional[tuple]:
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", tag.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _is_newer(latest: str, current: str) -> bool:
    lat = _parse_semver(latest)
    cur = _parse_semver(current)
    if lat is None or cur is None:
        return False
    return lat > cur


async def _fetch_latest_release() -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(
                GITHUB_RELEASES_URL,
                headers={"Accept": "application/vnd.github+json"},
            )
        if resp.status_code != 200:
            return None
        payload = resp.json()
        tag = payload.get("tag_name")
        if not tag:
            return None
        return {"tag": tag, "html_url": payload.get("html_url", RELEASES_HTML_BASE + tag)}
    except Exception:
        return None


def _load_cache() -> Optional[dict]:
    path = _cache_dir() / "update_check.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - payload.get("checked_at", 0) > CACHE_TTL_SECONDS:
            return None
        return payload
    except Exception:
        return None


def _save_cache(latest_tag: str, html_url: str) -> None:
    path = _cache_dir() / "update_check.json"
    try:
        path.write_text(
            json.dumps({
                "checked_at": int(time.time()),
                "latest_tag": latest_tag,
                "html_url": html_url,
            }),
            encoding="utf-8",
        )
    except Exception:
        pass


async def get_update_status() -> dict:
    """Return the current update status.

    Always returns a dict with `current_version` and `update_check_enabled`.
    When a check has run and returned a valid result, also includes
    `latest_version`, `is_newer`, and `release_url`.
    """
    status = {
        "current_version": __version__,
        "update_check_enabled": os.getenv("NOVOMCP_NO_UPDATE_CHECK", "").lower()
            not in ("1", "true", "yes"),
    }

    if not status["update_check_enabled"]:
        return status

    cached = _load_cache()
    if cached is None:
        latest = await _fetch_latest_release()
        if latest is None:
            return status
        _save_cache(latest["tag"], latest["html_url"])
        cached = {"latest_tag": latest["tag"], "html_url": latest["html_url"]}

    latest_tag = cached["latest_tag"]
    status["latest_version"] = latest_tag.lstrip("v")
    status["is_newer"] = _is_newer(latest_tag, __version__)
    status["release_url"] = cached["html_url"]
    return status


def _last_seen_path() -> Path:
    return _cache_dir() / "last_seen_version"


def check_upgrade_since_last_boot() -> Optional[str]:
    """Compare current version against the last-seen file. Returns the
    previous version string if it differs (i.e. user just upgraded), else
    None. Writes the current version regardless.
    """
    try:
        path = _last_seen_path()
        previous = path.read_text(encoding="utf-8").strip() if path.exists() else None
        path.write_text(__version__, encoding="utf-8")
        if previous and previous != __version__:
            return previous
    except Exception:
        pass
    return None


async def log_update_status_on_boot() -> None:
    """Called from lifespan startup. Logs at most two lines:
    - one for "you just upgraded" (if last_seen_version differs)
    - one for "newer version available" (if the GitHub check finds one)
    """
    upgraded_from = check_upgrade_since_last_boot()
    if upgraded_from:
        logger.info(
            "NovoMCP upgraded from v%s → v%s. Release notes: %s",
            upgraded_from, __version__, CHANGELOG_URL,
        )

    status = await get_update_status()
    if status.get("is_newer"):
        logger.info(
            "NovoMCP v%s running. v%s is available: %s",
            status["current_version"], status["latest_version"], status["release_url"],
        )
