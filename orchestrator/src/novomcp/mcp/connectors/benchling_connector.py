"""
Benchling connector adapter for Connection Registry.

Uses Benchling API v2 for connection testing, schema discovery
via entity schemas, and custom entity creation.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .base import (
    BaseConnector,
    ConnectorType,
    ExportResult,
    NormalizedType,
    SchemaColumn,
    TargetSchema,
    WriteMode,
)

logger = logging.getLogger(__name__)

# Benchling field type → normalized type
_TYPE_MAP = {
    "text": NormalizedType.STRING,
    "long_text": NormalizedType.STRING,
    "dropdown": NormalizedType.STRING,
    "entity_link": NormalizedType.STRING,
    "storage_link": NormalizedType.STRING,
    "part_link": NormalizedType.STRING,
    "batch_link": NormalizedType.STRING,
    "integer": NormalizedType.NUMBER,
    "float": NormalizedType.NUMBER,
    "decimal": NormalizedType.NUMBER,
    "boolean": NormalizedType.BOOLEAN,
    "date": NormalizedType.DATETIME,
    "datetime": NormalizedType.DATETIME,
    "json": NormalizedType.JSON,
    "blob_link": NormalizedType.STRING,
}


class BenchlingConnector(BaseConnector):
    """Benchling LIMS connector via API v2."""

    connector_type = ConnectorType.BENCHLING

    def __init__(self, config: Dict[str, Any], credentials: Dict[str, Any]):
        super().__init__(config, credentials)
        self._base_url = config["tenant_url"].rstrip("/")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.credentials['api_key']}",
            "Content-Type": "application/json",
        }

    async def test_connection(self) -> Tuple[bool, Optional[str]]:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self._base_url}/api/v2/registries",
                    headers=self._headers(),
                )
                if resp.status_code == 200:
                    return True, None
                elif resp.status_code == 401:
                    return False, "Authentication failed. Check your API key."
                else:
                    return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            return False, str(e)

    async def discover_schema(self) -> List[TargetSchema]:
        try:
            schemas = []

            async with httpx.AsyncClient(timeout=15) as client:
                # Fetch entity schemas
                resp = await client.get(
                    f"{self._base_url}/api/v2/entity-schemas",
                    headers=self._headers(),
                    params={"pageSize": 100},
                )
                resp.raise_for_status()
                schema_data = resp.json()

            for entity_schema in schema_data.get("entitySchemas", []):
                columns = []

                # Built-in fields
                columns.append(SchemaColumn(name="name", data_type=NormalizedType.STRING, native_type="text", nullable=False))
                columns.append(SchemaColumn(name="entityRegistryId", data_type=NormalizedType.STRING, native_type="text", nullable=True))

                # Custom fields from schema
                for field_def in entity_schema.get("fieldDefinitions", []):
                    field_type = field_def.get("type", "text")
                    columns.append(
                        SchemaColumn(
                            name=field_def["name"],
                            data_type=_TYPE_MAP.get(field_type, NormalizedType.STRING),
                            native_type=field_type,
                            nullable=not field_def.get("isRequired", False),
                            description=field_def.get("description"),
                        )
                    )

                schemas.append(
                    TargetSchema(
                        name=entity_schema["name"],
                        columns=columns,
                        location=f"BENCHLING:{self._base_url}:{entity_schema['id']}",
                        metadata={
                            "connector": "benchling",
                            "schema_id": entity_schema["id"],
                            "entity_type": entity_schema.get("type", "custom_entity"),
                        },
                    )
                )

            return schemas

        except Exception as e:
            logger.error(f"Benchling schema discovery failed: {e}")
            return []

    async def write_data(
        self,
        target: str,
        data: List[Dict[str, Any]],
        mode: WriteMode = WriteMode.APPEND,
    ) -> ExportResult:
        if not data:
            return ExportResult(success=True, rows_written=0, target_location=target)

        try:
            schema_id = self.config.get("schema_id", target)
            folder_id = self.config.get("folder_id")
            registry_id = self.config.get("registry_id")

            rows_written = 0
            rows_failed = 0
            errors = []

            async with httpx.AsyncClient(timeout=30) as client:
                for row in data:
                    try:
                        # Separate built-in fields from custom fields
                        entity_name = row.pop("name", row.get("smiles", f"Entity_{rows_written + 1}"))
                        custom_fields = {}
                        for key, value in row.items():
                            if key not in ("name", "entityRegistryId"):
                                custom_fields[key] = {"value": value}

                        payload = {
                            "name": entity_name,
                            "schemaId": schema_id,
                            "fields": custom_fields,
                        }
                        if folder_id:
                            payload["folderId"] = folder_id
                        if registry_id:
                            payload["registryId"] = registry_id

                        resp = await client.post(
                            f"{self._base_url}/api/v2/custom-entities",
                            headers=self._headers(),
                            json=payload,
                        )

                        if resp.status_code in (200, 201):
                            rows_written += 1
                        else:
                            rows_failed += 1
                            errors.append(f"Row {rows_written + rows_failed}: {resp.text[:100]}")

                    except Exception as e:
                        rows_failed += 1
                        errors.append(str(e))

            location = f"BENCHLING:{self._base_url}:{schema_id}"
            return ExportResult(
                success=rows_failed == 0,
                rows_written=rows_written,
                rows_failed=rows_failed,
                target_location=location,
                error="; ".join(errors[:3]) if errors else None,
                details={"schema_id": schema_id, "folder_id": folder_id},
            )

        except Exception as e:
            logger.error(f"Benchling write failed: {e}")
            return ExportResult(
                success=False,
                error=str(e),
                target_location=f"BENCHLING:{target}",
            )

    @staticmethod
    def get_config_schema() -> dict:
        return {
            "type": "object",
            "required": ["tenant_url"],
            "properties": {
                "tenant_url": {"type": "string", "description": "Benchling tenant URL (e.g., https://myorg.benchling.com)"},
                "folder_id": {"type": "string", "description": "Default folder ID for new entities"},
                "schema_id": {"type": "string", "description": "Default entity schema ID"},
                "registry_id": {"type": "string", "description": "Registry ID for auto-registration"},
            },
        }

    @staticmethod
    def get_credentials_schema() -> dict:
        return {
            "type": "object",
            "required": ["api_key"],
            "properties": {
                "api_key": {"type": "string", "description": "Benchling API key"},
            },
        }

    async def close(self):
        pass
