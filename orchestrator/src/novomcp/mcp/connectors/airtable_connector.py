"""
Airtable connector adapter for Connection Registry.

Uses pyairtable SDK for connection testing, schema discovery
via Metadata API, and batch create/update operations.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

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

# Airtable field type → normalized type
_TYPE_MAP = {
    "singleLineText": NormalizedType.STRING,
    "multilineText": NormalizedType.STRING,
    "email": NormalizedType.STRING,
    "url": NormalizedType.STRING,
    "richText": NormalizedType.STRING,
    "phoneNumber": NormalizedType.STRING,
    "singleSelect": NormalizedType.STRING,
    "multipleSelects": NormalizedType.JSON,
    "number": NormalizedType.NUMBER,
    "currency": NormalizedType.NUMBER,
    "percent": NormalizedType.NUMBER,
    "duration": NormalizedType.NUMBER,
    "rating": NormalizedType.NUMBER,
    "autoNumber": NormalizedType.NUMBER,
    "count": NormalizedType.NUMBER,
    "checkbox": NormalizedType.BOOLEAN,
    "date": NormalizedType.DATETIME,
    "dateTime": NormalizedType.DATETIME,
    "createdTime": NormalizedType.DATETIME,
    "lastModifiedTime": NormalizedType.DATETIME,
    "multipleAttachments": NormalizedType.JSON,
    "multipleRecordLinks": NormalizedType.JSON,
    "multipleLookupValues": NormalizedType.JSON,
    "formula": NormalizedType.STRING,
    "rollup": NormalizedType.STRING,
    "barcode": NormalizedType.STRING,
    "button": NormalizedType.STRING,
    "externalSyncSource": NormalizedType.STRING,
}


class AirtableConnector(BaseConnector):
    """Airtable base connector via Personal Access Token."""

    connector_type = ConnectorType.AIRTABLE

    def __init__(self, config: Dict[str, Any], credentials: Dict[str, Any]):
        super().__init__(config, credentials)
        self._api = None

    def _get_api(self):
        if self._api is not None:
            return self._api

        from pyairtable import Api

        self._api = Api(self.credentials["api_key"])
        return self._api

    def _get_table(self, table_name: Optional[str] = None):
        api = self._get_api()
        base_id = self.config["base_id"]
        target = table_name or self.config.get("table_name")
        return api.table(base_id, target)

    async def test_connection(self) -> Tuple[bool, Optional[str]]:
        try:
            table = self._get_table()
            # Fetch one record to validate access
            table.first()
            return True, None
        except Exception as e:
            error_msg = str(e)
            if "NOT_FOUND" in error_msg:
                return False, "Base or table not found. Check base_id and table_name."
            if "AUTHENTICATION_REQUIRED" in error_msg or "401" in error_msg:
                return False, "Authentication failed. Check your API key."
            return False, error_msg

    async def discover_schema(self) -> List[TargetSchema]:
        try:
            import httpx

            base_id = self.config["base_id"]
            api_key = self.credentials["api_key"]

            # Use Airtable Metadata API
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://api.airtable.com/v0/meta/bases/{base_id}/tables",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=15,
                )
                resp.raise_for_status()
                tables_data = resp.json()

            schemas = []
            for table in tables_data.get("tables", []):
                columns = []
                for field in table.get("fields", []):
                    field_type = field.get("type", "singleLineText")
                    columns.append(
                        SchemaColumn(
                            name=field["name"],
                            data_type=_TYPE_MAP.get(field_type, NormalizedType.STRING),
                            native_type=field_type,
                            nullable=True,
                            description=field.get("description"),
                        )
                    )

                schemas.append(
                    TargetSchema(
                        name=table["name"],
                        columns=columns,
                        location=f"AIRTABLE:{base_id}:{table['name']}",
                        metadata={
                            "connector": "airtable",
                            "table_id": table.get("id"),
                        },
                    )
                )

            return schemas

        except Exception as e:
            logger.error(f"Airtable schema discovery failed: {e}")
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
            table = self._get_table(target)

            if mode == WriteMode.REPLACE:
                # Delete all existing records first
                existing = table.all()
                if existing:
                    record_ids = [r["id"] for r in existing]
                    table.batch_delete(record_ids)

            # Prepare records — Airtable uses {"fields": {...}} format
            records = [{"fields": row} for row in data]

            # Batch create (pyairtable handles 10-record batches internally)
            created = table.batch_create([r["fields"] for r in records])

            location = f"AIRTABLE:{self.config['base_id']}:{target}"
            return ExportResult(
                success=True,
                rows_written=len(created),
                target_location=location,
                details={"base_id": self.config["base_id"], "table": target},
            )

        except Exception as e:
            logger.error(f"Airtable write failed: {e}")
            return ExportResult(
                success=False,
                error=str(e),
                target_location=f"AIRTABLE:{target}",
            )

    @staticmethod
    def get_config_schema() -> dict:
        return {
            "type": "object",
            "required": ["base_id"],
            "properties": {
                "base_id": {"type": "string", "description": "Airtable base ID (starts with 'app')"},
                "table_name": {"type": "string", "description": "Default table name"},
            },
        }

    @staticmethod
    def get_credentials_schema() -> dict:
        return {
            "type": "object",
            "required": ["api_key"],
            "properties": {
                "api_key": {"type": "string", "description": "Airtable Personal Access Token (PAT)"},
            },
        }

    async def close(self):
        self._api = None
