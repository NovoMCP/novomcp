"""
Local data bridge: warehouse reads and writes for a self-hosted engine.

The hosted platform ran connector work in a separate bridge service, with
connections and credentials held by the managed backend. A self-hosted engine
does the same work in-process instead, against the connections saved on the
dashboard's Connections page (~/.novo/connectors.json, or NOVO_CONNECTORS_PATH).

`handle(path, request)` takes the bridge's request payloads and returns its
response shapes, so push_to_destination and pull_from_source run unchanged on
either. Nothing here is metered: the destination bills its own usage.
"""

import asyncio
import json
import logging
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# The warehouse drivers are synchronous. Run them on a small dedicated pool so
# a hung connection can't tie up the event loop or the default thread pool.
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="local-bridge")


def connections_path() -> str:
    return os.getenv("NOVO_CONNECTORS_PATH") or os.path.expanduser("~/.novo/connectors.json")


def load_connections() -> List[Dict[str, Any]]:
    """Saved connections of a type this engine has an adapter for."""
    from . import CONNECTOR_REGISTRY

    try:
        with open(connections_path(), "r", encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(saved, list):
        return []
    return [c for c in saved if isinstance(c, dict) and str(c.get("type", "")).lower() in CONNECTOR_REGISTRY]


def available() -> bool:
    """True on a self-hosted engine with at least one data connection saved."""
    if os.getenv("NOVO_AUTH", "local").lower() == "hosted":
        return False
    return bool(load_connections())


def describe(conn: Dict[str, Any]) -> Dict[str, Any]:
    """A connection without its credentials."""
    return {
        "connection_id": conn.get("id"),
        "connector_type": str(conn.get("type", "")).lower(),
        "display_name": conn.get("displayName") or conn.get("type"),
        "created_at": conn.get("createdAt"),
        "config": {k: v for k, v in (conn.get("config") or {}).items() if not re.search(r"(?i)secret|token|password|key", k)},
    }


def find_connection(ref: Optional[str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """A connection by id, by name, or by type when only one of that type exists.
    With no ref and exactly one connection saved, that one."""
    conns = load_connections()
    if not conns:
        return None, "No data connections saved. Add Snowflake or Databricks on the dashboard's Connections page."
    names = ", ".join(f"{c.get('displayName') or c.get('type')} ({c.get('id')})" for c in conns)
    if not ref:
        if len(conns) == 1:
            return conns[0], None
        return None, f"connection_id is required. Saved connections: {names}"
    ref_l = str(ref).strip().lower()
    for match in (
        lambda c: str(c.get("id", "")).lower() == ref_l,
        lambda c: str(c.get("displayName", "")).strip().lower() == ref_l,
    ):
        hits = [c for c in conns if match(c)]
        if hits:
            return hits[0], None
    by_type = [c for c in conns if str(c.get("type", "")).lower() == ref_l]
    if len(by_type) == 1:
        return by_type[0], None
    return None, f"No saved connection matches {ref!r}. Saved connections: {names}"


def _connector(conn: Dict[str, Any]):
    from . import get_connector

    return get_connector(str(conn.get("type", "")).lower(), conn.get("config") or {}, conn.get("credentials") or {})


async def _with_connector(conn: Dict[str, Any], work: Callable[[Any], Awaitable[Any]], timeout: float) -> Any:
    """Run `work(connector)` on the bridge pool, closing the connector after."""

    async def _run():
        connector = _connector(conn)
        try:
            return await work(connector)
        finally:
            try:
                await connector.close()
            except Exception:
                pass

    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(loop.run_in_executor(_POOL, lambda: asyncio.run(_run())), timeout=timeout)


def normalize_rows(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


def safe_columns(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rename keys a warehouse can't take as column names (spaces, dashes,
    leading digits) to ones it can, consistently across rows."""
    renamed: Dict[str, str] = {}
    taken: set = set()
    for row in rows:
        for key in row:
            if key in renamed:
                continue
            name = re.sub(r"[^A-Za-z0-9_]", "_", str(key))[:128] or "column"
            if not re.match(r"[A-Za-z_]", name):
                name = f"_{name}"[:128]
            base, n = name, 2
            while name.lower() in taken:
                name = f"{base[:120]}_{n}"
                n += 1
            taken.add(name.lower())
            renamed[key] = name
    return [{renamed[k]: v for k, v in row.items()} for row in rows]


def _schema_dict(s) -> Dict[str, Any]:
    cols = [
        {"name": c.name, "type": c.data_type.value, "native_type": c.native_type, "nullable": c.nullable}
        for c in (s.columns or [])
    ]
    return {"name": s.name, "location": s.location, "columns": cols, "column_count": len(cols), "metadata": s.metadata or {}}


# ── operations (bridge request → bridge response) ────────────────────────────


async def discover_schema(req: Dict[str, Any]) -> Dict[str, Any]:
    conn, err = find_connection(req.get("connection_id"))
    if not conn:
        return {"connection_id": req.get("connection_id"), "connector_type": "unknown", "schemas": [], "schema_count": 0, "error": err}
    schemas = await _with_connector(conn, lambda c: c.discover_schema(), timeout=60)
    filt = (req.get("target_filter") or "").lower()
    if filt:
        schemas = [s for s in schemas if filt in s.name.lower()]
    out = [_schema_dict(s) for s in schemas]
    return {"connection_id": conn["id"], "connector_type": describe(conn)["connector_type"], "schemas": out, "schema_count": len(out)}


async def _mappings(conn: Dict[str, Any], connector, rows, source_tool: str, target: str):
    from .mapping_engine import resolve_mapping

    target_schema = None
    try:
        target_schema = await connector.discover_target_schema(target)
    except Exception as e:
        logger.info(f"No target schema for {target!r}: {e}")
    return await resolve_mapping(
        connection_id=conn["id"],
        source_tool=source_tool,
        connector_type=describe(conn)["connector_type"],
        target_schema=target_schema,
        source_fields=list(rows[0].keys()) if rows else [],
    )


def default_target(source_tool: str) -> str:
    return f"novomcp_{re.sub(r'[^A-Za-z0-9_]', '_', source_tool or 'results')}"


async def preview_mapping(req: Dict[str, Any]) -> Dict[str, Any]:
    from .mapping_engine import _resolve_path, apply_mapping

    conn, err = find_connection(req.get("connection_id"))
    source_tool = req.get("source_tool") or "results"
    rows = normalize_rows(req.get("data"))
    if not conn:
        return {"connection_id": req.get("connection_id"), "connector_type": "unknown", "source_tool": source_tool,
                "mapping_count": 0, "mappings": [], "unmapped_source_fields": [], "sample_output": {}, "error": err}
    target = req.get("target") or default_target(source_tool)

    async def work(connector):
        return await _mappings(conn, connector, rows, source_tool, target)

    mappings = await _with_connector(conn, work, timeout=60)
    first = rows[0] if rows else {}
    sample = apply_mapping(first, mappings) if (first and mappings) else dict(safe_columns([first])[0]) if first else {}
    mapped = {m.source_field for m in mappings}
    return {
        "connection_id": conn["id"],
        "connector_type": describe(conn)["connector_type"],
        "source_tool": source_tool,
        "target": target,
        "mapping_count": len(mappings),
        "mappings": [
            {"source_field": m.source_field, "target_field": m.target_field, "transform": m.transform,
             "confidence": m.confidence, "sample_source_value": _resolve_path(first, m.source_field) if first else None,
             "sample_target_value": sample.get(m.target_field)}
            for m in mappings
        ],
        "unmapped_source_fields": [f for f in first.keys() if f not in mapped] if mappings else [],
        "sample_output": sample,
        "note": None if mappings else "No mapping for this data: every field is written as its own column.",
    }


async def export(req: Dict[str, Any]) -> Dict[str, Any]:
    from .base import ExportIdentity, WriteMode
    from .mapping_engine import apply_mapping_batch
    from .warehouse import WarehouseConnector

    conn, err = find_connection(req.get("connection_id"))
    if not conn:
        return {"success": False, "rows_written": 0, "rows_failed": 0, "target_location": "", "error": err, "details": {}}
    rows = normalize_rows(req.get("data"))
    if not rows:
        return {"success": True, "rows_written": 0, "rows_failed": 0, "target_location": "", "error": None,
                "details": {"message": "No data to export"}}
    source_tool = req.get("source_tool") or "results"
    target = req.get("target") or default_target(source_tool)
    try:
        mode = WriteMode(req.get("write_mode") or "append")
    except ValueError:
        mode = WriteMode.APPEND
    export_id = f"exp_{uuid.uuid4().hex[:16]}"
    identity = ExportIdentity(
        user_id=req.get("user_id") or "local",
        user_email=req.get("user_email") or "",
        org_id=req.get("org_id") or "local",
        export_id=export_id,
        source_tool=source_tool,
        exported_at=datetime.now(timezone.utc).isoformat(),
    )

    async def work(connector):
        mappings = await _mappings(conn, connector, rows, source_tool, target)
        out = apply_mapping_batch(rows, mappings) if mappings else rows
        out = safe_columns(out)
        if isinstance(connector, WarehouseConnector):
            result = await connector.write_data(target, out, mode, identity=identity)
        else:
            result = await connector.write_data(target, out, mode)
        return result, len(mappings)

    try:
        result, fields_mapped = await _with_connector(conn, work, timeout=300)
    except asyncio.TimeoutError:
        return {"success": False, "rows_written": 0, "rows_failed": 0, "target_location": "",
                "error": "The destination did not respond in time.", "details": {"export_id": export_id}}
    except Exception as e:
        return {"success": False, "rows_written": 0, "rows_failed": 0, "target_location": "", "error": str(e),
                "details": {"export_id": export_id}}
    details = dict(result.details or {})
    details.update({"export_id": export_id, "fields_mapped": fields_mapped, "mapping_type": "auto" if fields_mapped else "as_is",
                    "connection_id": conn["id"], "connector_type": describe(conn)["connector_type"]})
    return {"success": result.success, "rows_written": result.rows_written, "rows_failed": result.rows_failed,
            "target_location": result.target_location or "", "error": result.error, "details": details}


async def read_data(req: Dict[str, Any]) -> Dict[str, Any]:
    conn, err = find_connection(req.get("connection_id"))
    table = req.get("table")
    if not conn:
        return {"success": False, "connection_id": req.get("connection_id"), "table": table, "columns": [], "rows": [],
                "row_count": 0, "total_available": None, "truncated": False, "error": err}
    limit = int(req.get("limit") or 100)
    offset = int(req.get("offset") or 0)

    async def work(connector):
        return await connector.read_data(table=table, columns=req.get("columns"), filters=req.get("filters"),
                                         limit=limit, offset=offset)

    try:
        r = await _with_connector(conn, work, timeout=150)
    except NotImplementedError as e:
        return {"success": False, "connection_id": conn["id"], "table": table, "columns": [], "rows": [], "row_count": 0,
                "total_available": None, "truncated": False, "error": str(e)}
    rows = [{k: _jsonable(v) for k, v in row.items()} for row in r.rows]
    return {"success": r.success, "connection_id": conn["id"], "table": table, "columns": r.columns, "rows": rows,
            "row_count": r.row_count, "total_available": r.total_available, "truncated": r.truncated, "error": r.error}


async def count_rows(req: Dict[str, Any]) -> Dict[str, Any]:
    conn, err = find_connection(req.get("connection_id"))
    table = req.get("table")
    if not conn:
        return {"success": False, "connection_id": req.get("connection_id"), "table": table, "row_count": 0, "error": err}
    try:
        n = await _with_connector(conn, lambda c: c.count_rows(table=table, filters=req.get("filters")), timeout=150)
        return {"success": True, "connection_id": conn["id"], "table": table, "row_count": n}
    except Exception as e:
        return {"success": False, "connection_id": conn["id"], "table": table, "row_count": 0, "error": str(e)}


def _jsonable(v: Any) -> Any:
    """Warehouse values (Decimal, datetime, bytes) as JSON-safe values."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    try:
        from decimal import Decimal

        if isinstance(v, Decimal):
            return float(v)
    except Exception:
        pass
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, (list, dict)):
        return json.loads(json.dumps(v, default=str))
    return str(v)


ROUTES: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {
    "/discover-schema": discover_schema,
    "/preview-mapping": preview_mapping,
    "/export": export,
    "/read-data": read_data,
    "/count-rows": count_rows,
}


async def handle(path: str, req: Dict[str, Any]) -> Dict[str, Any]:
    op = ROUTES.get(path)
    if op is None:
        raise ValueError(f"Unknown bridge operation {path!r}")
    return await op(req or {})
