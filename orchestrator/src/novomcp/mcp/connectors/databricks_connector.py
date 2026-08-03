"""
Databricks connector adapter for Connection Registry.

Uses databricks-sql-connector SDK for connection testing, schema discovery
via Unity Catalog, and data writing via parameterized INSERT.
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

# Databricks/Spark SQL type → normalized type
_TYPE_MAP = {
    "STRING": NormalizedType.STRING,
    "VARCHAR": NormalizedType.STRING,
    "CHAR": NormalizedType.STRING,
    "BINARY": NormalizedType.STRING,
    "INT": NormalizedType.NUMBER,
    "INTEGER": NormalizedType.NUMBER,
    "BIGINT": NormalizedType.NUMBER,
    "SMALLINT": NormalizedType.NUMBER,
    "TINYINT": NormalizedType.NUMBER,
    "FLOAT": NormalizedType.NUMBER,
    "DOUBLE": NormalizedType.NUMBER,
    "DECIMAL": NormalizedType.NUMBER,
    "NUMERIC": NormalizedType.NUMBER,
    "LONG": NormalizedType.NUMBER,
    "SHORT": NormalizedType.NUMBER,
    "BOOLEAN": NormalizedType.BOOLEAN,
    "DATE": NormalizedType.DATETIME,
    "TIMESTAMP": NormalizedType.DATETIME,
    "TIMESTAMP_NTZ": NormalizedType.DATETIME,
    "MAP": NormalizedType.JSON,
    "ARRAY": NormalizedType.JSON,
    "STRUCT": NormalizedType.JSON,
}


def _normalize_type(db_type: str) -> NormalizedType:
    base = db_type.split("(")[0].split("<")[0].upper().strip()
    return _TYPE_MAP.get(base, NormalizedType.STRING)


class DatabricksConnector(BaseConnector):
    """Databricks SQL warehouse / Unity Catalog connector."""

    connector_type = ConnectorType.DATABRICKS

    def __init__(self, config: Dict[str, Any], credentials: Dict[str, Any]):
        super().__init__(config, credentials)
        self._conn = None

    def _get_connection(self):
        if self._conn is not None:
            return self._conn

        from databricks import sql

        self._conn = sql.connect(
            server_hostname=self.config["server_hostname"],
            http_path=self.config["http_path"],
            access_token=self.credentials["access_token"],
            catalog=self.config.get("catalog"),
            schema=self.config.get("schema"),
        )
        return self._conn

    async def test_connection(self) -> Tuple[bool, Optional[str]]:
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
            return True, None
        except Exception as e:
            return False, str(e)

    async def discover_schema(self) -> List[TargetSchema]:
        try:
            from .base import validate_sql_identifier
            conn = self._get_connection()
            cur = conn.cursor()

            catalog = validate_sql_identifier(self.config.get("catalog", "main"), "catalog")
            schema = validate_sql_identifier(self.config.get("schema", "default"), "schema")

            cur.execute(
                f"""
                SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE
                FROM {catalog}.INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = '{schema}'
                ORDER BY TABLE_NAME, ORDINAL_POSITION
                """
            )

            tables: Dict[str, List[SchemaColumn]] = {}
            for row in cur.fetchall():
                table_name, col_name, data_type, nullable = row
                if table_name not in tables:
                    tables[table_name] = []
                tables[table_name].append(
                    SchemaColumn(
                        name=col_name,
                        data_type=_normalize_type(data_type),
                        native_type=data_type,
                        nullable=(nullable == "YES"),
                    )
                )

            cur.close()

            return [
                TargetSchema(
                    name=table_name,
                    columns=columns,
                    location=f"{catalog}.{schema}.{table_name}",
                    metadata={"connector": "databricks"},
                )
                for table_name, columns in tables.items()
            ]

        except Exception as e:
            logger.error(f"Databricks schema discovery failed: {e}")
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
            from .base import validate_sql_identifier
            conn = self._get_connection()
            cur = conn.cursor()
            catalog = validate_sql_identifier(self.config.get("catalog", "main"), "catalog")
            schema = validate_sql_identifier(self.config.get("schema", "default"), "schema")
            validate_sql_identifier(target, "target table")
            full_target = f"{catalog}.{schema}.{target}"

            if mode == WriteMode.REPLACE:
                cur.execute(f"TRUNCATE TABLE {full_target}")

            columns = list(data[0].keys())
            for col in columns:
                validate_sql_identifier(col, "column")
            col_list = ", ".join(columns)

            rows_written = 0
            rows_failed = 0

            for row in data:
                try:
                    values = [row.get(col) for col in columns]
                    placeholders = ", ".join(["%s"] * len(columns))
                    cur.execute(
                        f"INSERT INTO {full_target} ({col_list}) VALUES ({placeholders})",
                        values,
                    )
                    rows_written += 1
                except Exception as e:
                    logger.warning(f"Row insert failed: {e}")
                    rows_failed += 1

            cur.close()

            return ExportResult(
                success=rows_failed == 0,
                rows_written=rows_written,
                rows_failed=rows_failed,
                target_location=f"DATABRICKS:{full_target}",
            )

        except Exception as e:
            logger.error(f"Databricks write failed: {e}")
            return ExportResult(
                success=False,
                error=str(e),
                target_location=f"DATABRICKS:{target}",
            )

    @staticmethod
    def get_config_schema() -> dict:
        return {
            "type": "object",
            "required": ["server_hostname", "http_path"],
            "properties": {
                "server_hostname": {"type": "string", "description": "Databricks workspace hostname"},
                "http_path": {"type": "string", "description": "SQL warehouse HTTP path"},
                "catalog": {"type": "string", "description": "Unity Catalog name", "default": "main"},
                "schema": {"type": "string", "description": "Schema name", "default": "default"},
            },
        }

    @staticmethod
    def get_credentials_schema() -> dict:
        return {
            "type": "object",
            "required": ["access_token"],
            "properties": {
                "access_token": {"type": "string", "description": "Databricks personal access token or service principal token"},
            },
        }

    async def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
