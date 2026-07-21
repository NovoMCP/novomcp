"""
Export Manager — Central orchestrator for Connection Registry exports.

Handles the full export lifecycle:
1. Fetch connection from dashboard-aggregator
2. Validate org access and tier
3. Retrieve credentials from Azure Key Vault
4. Instantiate connector
5. Resolve and apply field mappings
6. Write data to destination
7. Record export audit
"""

import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx

from .base import ExportResult, TargetSchema, WriteMode
from . import check_tier_access, get_connector
from .mapping_engine import (
    FieldMapping,
    apply_mapping_batch,
    resolve_mapping,
)
from .vault_client import get_vault_client

logger = logging.getLogger(__name__)


class ExportManager:
    """Orchestrates data export from MCP tools to external destinations."""

    def __init__(self):
        self.dashboard_url = os.environ.get("DASHBOARD_AGGREGATOR_URL", "")
        self.dashboard_api_key = os.environ.get("DASHBOARD_AGGREGATOR_API_KEY", "")

    async def execute_export(
        self,
        connection_id: str,
        data: Any,
        source_tool: str,
        mapping_id: Optional[str] = None,
        write_mode: str = "append",
        target_override: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> ExportResult:
        """
        Execute a full export operation.

        Args:
            connection_id: Connection to export to
            data: Tool output data (dict or list of dicts)
            source_tool: MCP tool that produced the data
            mapping_id: Explicit mapping ID (optional)
            write_mode: append, replace, or upsert
            target_override: Override target table/sheet name
            context: Auth context (org_id, user_id, user_tier)

        Returns:
            ExportResult with write metrics
        """
        context = context or {}
        org_id = context.get("org_id", "")
        user_id = context.get("user_id", "")
        export_id = f"exp_{uuid.uuid4().hex[:16]}"
        start_time = time.time()

        try:
            # 1. Fetch connection from dashboard-aggregator
            connection = await self._fetch_connection(connection_id, org_id)
            if not connection:
                return ExportResult(
                    success=False,
                    error=f"Connection {connection_id} not found or not accessible",
                )

            # 2. Validate org access
            if connection.get("org_id") != org_id:
                return ExportResult(
                    success=False,
                    error="Access denied: connection belongs to different organization",
                )

            connector_type = connection["connector_type"]

            # 3. Check tier
            user_tier = context.get("user_tier", "free")
            if not check_tier_access(user_tier, connector_type):
                return ExportResult(
                    success=False,
                    error=f"Tier '{user_tier}' does not have access to {connector_type} connector. Upgrade required.",
                )

            # 4. Retrieve credentials from Azure Key Vault
            vault_secret_name = connection.get("vault_secret_name")
            if not vault_secret_name:
                return ExportResult(
                    success=False,
                    error="Connection has no configured credentials",
                )

            vault = get_vault_client()
            credentials = await vault.get_credentials(vault_secret_name)
            if not credentials:
                return ExportResult(
                    success=False,
                    error="Failed to retrieve credentials from vault",
                )

            # 5. Instantiate connector
            config = connection.get("config_json", {})
            if isinstance(config, str):
                import json
                config = json.loads(config)

            connector = get_connector(connector_type, config, credentials)

            try:
                # 6. Normalize data to list of dicts
                data_rows = self._normalize_data(data)
                if not data_rows:
                    return ExportResult(
                        success=True,
                        rows_written=0,
                        target_location="",
                        details={"message": "No data to export"},
                    )

                # 7. Resolve mapping
                source_fields = list(data_rows[0].keys()) if data_rows else []

                # Get target schema for auto-mapping fallback
                target_schema = None
                if not mapping_id:
                    try:
                        schemas = await connector.discover_schema()
                        target_name = target_override or self._default_target(source_tool)
                        target_schema = next(
                            (s for s in schemas if s.name.lower() == target_name.lower()),
                            schemas[0] if schemas else None,
                        )
                    except Exception:
                        pass

                mappings = await resolve_mapping(
                    connection_id=connection_id,
                    source_tool=source_tool,
                    connector_type=connector_type,
                    target_schema=target_schema,
                    source_fields=source_fields,
                    mapping_id=mapping_id,
                    dashboard_url=self.dashboard_url,
                    org_id=org_id,
                )

                # 8. Apply mapping
                if mappings:
                    mapped_data = apply_mapping_batch(data_rows, mappings)
                else:
                    # No mapping — pass through raw data
                    mapped_data = data_rows

                # 9. Write data
                target_name = target_override or self._default_target(source_tool)
                mode = WriteMode(write_mode) if write_mode in WriteMode.__members__.values() else WriteMode.APPEND

                result = await connector.write_data(target_name, mapped_data, mode)

                # 10. Record export audit
                execution_time_ms = int((time.time() - start_time) * 1000)
                await self._record_export(
                    export_id=export_id,
                    org_id=org_id,
                    user_id=user_id,
                    connection_id=connection_id,
                    mapping_id=mapping_id,
                    source_tool=source_tool,
                    connector_type=connector_type,
                    target_location=result.target_location,
                    rows_exported=result.rows_written,
                    fields_mapped=len(mappings) if mappings else len(mapped_data[0]) if mapped_data else 0,
                    credit_cost=10.0,  # Base cost for export_results tool
                    status="success" if result.success else ("partial" if result.rows_written > 0 else "failed"),
                    error_message=result.error,
                    execution_time_ms=execution_time_ms,
                )

                result.details["export_id"] = export_id
                result.details["fields_mapped"] = len(mappings) if mappings else 0
                result.details["mapping_type"] = "explicit" if mapping_id else "auto"

                return result

            finally:
                await connector.close()

        except Exception as e:
            logger.error(f"Export failed: {e}")
            execution_time_ms = int((time.time() - start_time) * 1000)

            # Record failed export
            await self._record_export(
                export_id=export_id,
                org_id=org_id,
                user_id=user_id,
                connection_id=connection_id,
                mapping_id=mapping_id,
                source_tool=source_tool,
                connector_type="unknown",
                target_location="",
                rows_exported=0,
                fields_mapped=0,
                credit_cost=0,
                status="failed",
                error_message=str(e)[:1000],
                execution_time_ms=execution_time_ms,
            )

            return ExportResult(success=False, error=str(e))

    async def _fetch_connection(self, connection_id: str, org_id: str) -> Optional[Dict]:
        """Fetch connection details from dashboard-aggregator (internal endpoint)."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.dashboard_url}/mcp/connections/{connection_id}",
                    params={"org_id": org_id},
                    headers={"X-API-Key": self.dashboard_api_key},
                )
                if resp.status_code == 200:
                    return resp.json()
                return None
        except Exception as e:
            logger.error(f"Failed to fetch connection {connection_id}: {e}")
            return None

    async def _record_export(self, **kwargs):
        """Record export in audit trail via dashboard-aggregator."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(
                    f"{self.dashboard_url}/mcp/record-export",
                    json=kwargs,
                    headers={"X-API-Key": self.dashboard_api_key},
                )
        except Exception as e:
            # Non-blocking — don't fail the export because of audit
            logger.warning(f"Failed to record export audit: {e}")

    @staticmethod
    def _normalize_data(data: Any) -> List[Dict[str, Any]]:
        """Normalize tool output to a flat list of dicts."""
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            # Single result → wrap in list
            return [data]
        return []

    @staticmethod
    def _default_target(source_tool: str) -> str:
        """Generate a default target name from the source tool."""
        return f"novomcp_{source_tool}"


# Singleton
_export_manager: Optional[ExportManager] = None


def get_export_manager() -> ExportManager:
    global _export_manager
    if _export_manager is None:
        _export_manager = ExportManager()
    return _export_manager
