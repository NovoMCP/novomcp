"""
Snowflake connector adapter for Connection Registry.

Uses snowflake-connector-python SDK for connection testing, schema discovery,
and data writing via parameterized INSERT or write_pandas().
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

# Snowflake type → normalized type mapping
_TYPE_MAP = {
    "NUMBER": NormalizedType.NUMBER,
    "DECIMAL": NormalizedType.NUMBER,
    "NUMERIC": NormalizedType.NUMBER,
    "INT": NormalizedType.NUMBER,
    "INTEGER": NormalizedType.NUMBER,
    "BIGINT": NormalizedType.NUMBER,
    "SMALLINT": NormalizedType.NUMBER,
    "TINYINT": NormalizedType.NUMBER,
    "FLOAT": NormalizedType.NUMBER,
    "FLOAT4": NormalizedType.NUMBER,
    "FLOAT8": NormalizedType.NUMBER,
    "DOUBLE": NormalizedType.NUMBER,
    "DOUBLE PRECISION": NormalizedType.NUMBER,
    "REAL": NormalizedType.NUMBER,
    "VARCHAR": NormalizedType.STRING,
    "CHAR": NormalizedType.STRING,
    "CHARACTER": NormalizedType.STRING,
    "STRING": NormalizedType.STRING,
    "TEXT": NormalizedType.STRING,
    "BINARY": NormalizedType.STRING,
    "VARBINARY": NormalizedType.STRING,
    "BOOLEAN": NormalizedType.BOOLEAN,
    "DATE": NormalizedType.DATETIME,
    "DATETIME": NormalizedType.DATETIME,
    "TIME": NormalizedType.DATETIME,
    "TIMESTAMP": NormalizedType.DATETIME,
    "TIMESTAMP_LTZ": NormalizedType.DATETIME,
    "TIMESTAMP_NTZ": NormalizedType.DATETIME,
    "TIMESTAMP_TZ": NormalizedType.DATETIME,
    "VARIANT": NormalizedType.JSON,
    "OBJECT": NormalizedType.JSON,
    "ARRAY": NormalizedType.JSON,
}


def _normalize_type(sf_type: str) -> NormalizedType:
    """Map Snowflake data type to normalized type."""
    # Strip precision/scale (e.g., "NUMBER(38,0)" → "NUMBER")
    base = sf_type.split("(")[0].upper().strip()
    return _TYPE_MAP.get(base, NormalizedType.STRING)


class SnowflakeConnector(BaseConnector):
    """Snowflake data warehouse connector."""

    connector_type = ConnectorType.SNOWFLAKE

    def __init__(self, config: Dict[str, Any], credentials: Dict[str, Any]):
        super().__init__(config, credentials)
        self._conn = None

    def _get_connection(self):
        """Create or return existing Snowflake connection."""
        if self._conn is not None:
            return self._conn

        import snowflake.connector

        conn_params = {
            "account": self.config["account"],
            "user": self.credentials["username"],
            "database": self.config.get("database"),
            "schema": self.config.get("schema"),
            "warehouse": self.config.get("warehouse"),
            "role": self.config.get("role"),
        }

        # Support password or key-pair auth
        if "password" in self.credentials:
            conn_params["password"] = self.credentials["password"]
        elif "private_key" in self.credentials:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.backends import default_backend

            p_key = serialization.load_pem_private_key(
                self.credentials["private_key"].encode(),
                password=(
                    self.credentials.get("private_key_passphrase", "").encode()
                    if self.credentials.get("private_key_passphrase")
                    else None
                ),
                backend=default_backend(),
            )
            conn_params["private_key"] = p_key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )

        self._conn = snowflake.connector.connect(**conn_params)
        return self._conn

    async def test_connection(self) -> Tuple[bool, Optional[str]]:
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("SELECT CURRENT_VERSION()")
            version = cur.fetchone()[0]
            cur.close()
            return True, None
        except Exception as e:
            return False, str(e)

    async def discover_schema(self) -> List[TargetSchema]:
        try:
            conn = self._get_connection()
            cur = conn.cursor()

            database = self.config.get("database")
            schema = self.config.get("schema", "PUBLIC")

            cur.execute(
                """
                SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, ORDINAL_POSITION
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = %s
                ORDER BY TABLE_NAME, ORDINAL_POSITION
                """,
                (schema,),
            )

            tables: Dict[str, List[SchemaColumn]] = {}
            for row in cur.fetchall():
                table_name, col_name, data_type, nullable, _ = row
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
                    location=f"{database}.{schema}.{table_name}",
                    metadata={"connector": "snowflake"},
                )
                for table_name, columns in tables.items()
            ]

        except Exception as e:
            logger.error(f"Snowflake schema discovery failed: {e}")
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
            database = validate_sql_identifier(self.config.get("database", ""), "database")
            schema = validate_sql_identifier(self.config.get("schema", "PUBLIC"), "schema")
            validate_sql_identifier(target, "target table")
            full_target = f"{database}.{schema}.{target}"

            if mode == WriteMode.REPLACE:
                cur.execute(f"TRUNCATE TABLE IF EXISTS {full_target}")

            # Get column names from first row
            columns = list(data[0].keys())
            for col in columns:
                validate_sql_identifier(col, "column")
            col_list = ", ".join(columns)
            placeholders = ", ".join(["%s"] * len(columns))

            rows_written = 0
            rows_failed = 0

            # Batch insert
            for row in data:
                try:
                    values = tuple(row.get(col) for col in columns)
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
                target_location=f"SNOWFLAKE:{full_target}",
            )

        except Exception as e:
            logger.error(f"Snowflake write failed: {e}")
            return ExportResult(
                success=False,
                error=str(e),
                target_location=f"SNOWFLAKE:{target}",
            )

    @staticmethod
    def get_config_schema() -> dict:
        return {
            "type": "object",
            "required": ["account"],
            "properties": {
                "account": {"type": "string", "description": "Snowflake account identifier (e.g., xy12345.us-east-1)"},
                "warehouse": {"type": "string", "description": "Compute warehouse name"},
                "database": {"type": "string", "description": "Database name"},
                "schema": {"type": "string", "description": "Schema name", "default": "PUBLIC"},
                "role": {"type": "string", "description": "Role to use"},
            },
        }

    @staticmethod
    def get_credentials_schema() -> dict:
        return {
            "type": "object",
            "required": ["username"],
            "properties": {
                "username": {"type": "string", "description": "Snowflake username"},
                "password": {"type": "string", "description": "Password (or use private_key)"},
                "private_key": {"type": "string", "description": "PEM-encoded private key"},
                "private_key_passphrase": {"type": "string", "description": "Private key passphrase"},
            },
        }

    async def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
