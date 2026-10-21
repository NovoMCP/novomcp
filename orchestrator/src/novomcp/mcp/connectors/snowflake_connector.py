"""
Snowflake connector adapter for Connection Registry.

Uses snowflake-connector-python SDK for connection testing, schema discovery,
and data writing via parameterized INSERT or write_pandas().
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from . import sql
from .base import (
    ConnectorType,
    NormalizedType,
    SchemaColumn,
    TargetSchema,
)
from .warehouse import WarehouseConnector

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


class SnowflakeConnector(WarehouseConnector):
    """Snowflake data warehouse connector. Reads and writes come from
    WarehouseConnector; this class connects and discovers tables."""

    connector_type = ConnectorType.SNOWFLAKE
    dialect = sql.SNOWFLAKE
    container_key = "database"
    default_container = None
    default_schema = "PUBLIC"
    location_label = "SNOWFLAKE"

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
            # Bound how long the (synchronous) driver may block on an unreachable
            # or firewalled host so the worker thread self-terminates well within
            # the caller's 45s deadline instead of hanging for the driver default.
            "login_timeout": 10,
            "network_timeout": 30,
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
