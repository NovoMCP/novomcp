"""
Base connector classes and data models for the Connection Registry.

Defines the abstract BaseConnector interface that all connector adapters
(Snowflake, Databricks, Google Sheets, Airtable, Benchling) must implement.
"""

import asyncio
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Dict, List, Optional, Tuple, TypeVar

_SAFE_ID = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')


def validate_sql_identifier(name: str, label: str = "identifier") -> str:
    """Validate a SQL identifier (table name, column name, schema, etc.)."""
    if not _SAFE_ID.match(name):
        raise ValueError(f"Invalid SQL {label}: {name!r}")
    return name


T = TypeVar("T")


class ConnectorTimeout(Exception):
    """A connector call ran past its time budget."""

    def __init__(self, connector_type: str, operation: str, timeout: float):
        self.connector_type, self.operation, self.timeout = connector_type, operation, timeout
        super().__init__(f"{connector_type} {operation} took longer than {timeout:g}s")


class ConnectorType(str, Enum):
    SNOWFLAKE = "snowflake"
    GOOGLE_SHEETS = "google_sheets"
    AIRTABLE = "airtable"
    BENCHLING = "benchling"
    DATABRICKS = "databricks"


class WriteMode(str, Enum):
    APPEND = "append"       # Add rows to existing data
    REPLACE = "replace"     # Replace all data in target
    UPSERT = "upsert"      # Update existing, insert new (requires key)


class NormalizedType(str, Enum):
    """Normalized data types across all connectors."""
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    JSON = "json"


@dataclass
class SchemaColumn:
    """A column/field in a target schema."""
    name: str
    data_type: NormalizedType
    native_type: str          # Original type from the system (e.g., "VARCHAR(255)", "Number")
    nullable: bool = True
    description: Optional[str] = None


@dataclass
class TargetSchema:
    """Schema of a target table/sheet/entity."""
    name: str                           # Table name, sheet name, entity type
    columns: List[SchemaColumn]
    location: str                       # Fully qualified location (e.g., "DB.SCHEMA.TABLE")
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExportIdentity:
    """Who exported a set of rows. Written into each row so the destination
    table itself records where its rows came from."""
    user_id: str = ""
    user_email: str = ""
    org_id: str = ""
    export_id: str = ""
    source_tool: str = ""
    exported_at: str = ""

    @staticmethod
    def inject_columns(rows: List[Dict[str, Any]], identity: "ExportIdentity") -> List[Dict[str, Any]]:
        for row in rows:
            row["_exported_by"] = identity.user_email or identity.user_id
            row["_export_id"] = identity.export_id
            row["_exported_at"] = identity.exported_at
        return rows


@dataclass
class ReadResult:
    """Rows read from a source table."""
    success: bool
    columns: List[str] = field(default_factory=list)
    rows: List[Dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    total_available: Optional[int] = None
    truncated: bool = False
    error: Optional[str] = None


@dataclass
class ExportResult:
    """Result of a data export operation."""
    success: bool
    rows_written: int = 0
    rows_failed: int = 0
    target_location: str = ""
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class BaseConnector(ABC):
    """
    Abstract base class for all connector adapters.

    Each connector implements connection testing, schema discovery,
    and data writing for a specific destination system.
    """

    connector_type: ConnectorType

    # Time budgets (seconds) for each kind of call.
    DISCOVER_TIMEOUT = 30
    READ_TIMEOUT = 120
    WRITE_TIMEOUT = 300

    def __init__(self, config: Dict[str, Any], credentials: Dict[str, Any]):
        """
        Initialize connector with non-secret config and credentials.

        Args:
            config: Non-secret configuration (account, warehouse, spreadsheet_id, etc.)
            credentials: Secret credentials from the configured secret store
        """
        self.config = config or {}
        self.credentials = credentials or {}

    async def _with_timeout(self, coro: Awaitable[T], timeout: float, operation: str) -> T:
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError as e:
            raise ConnectorTimeout(self.connector_type.value, operation, timeout) from e

    @abstractmethod
    async def test_connection(self) -> Tuple[bool, Optional[str]]:
        """
        Test connectivity to the destination system.

        Returns:
            Tuple of (success, error_message). error_message is None on success.
        """
        ...

    @abstractmethod
    async def discover_schema(self) -> List[TargetSchema]:
        """
        Discover available target schemas (tables, sheets, entities).

        Returns:
            List of TargetSchema objects describing available destinations.
        """
        ...

    @abstractmethod
    async def write_data(
        self,
        target: str,
        data: List[Dict[str, Any]],
        mode: WriteMode = WriteMode.APPEND,
        identity: Optional[ExportIdentity] = None,
    ) -> ExportResult:
        """
        Write data to a specific target.

        Args:
            target: Target identifier (table name, sheet name, entity type)
            data: List of row dicts to write
            mode: Write mode (append, replace, upsert)
            identity: Who is exporting; connectors that support it add it to each row

        Returns:
            ExportResult with write metrics
        """
        ...

    @staticmethod
    @abstractmethod
    def get_config_schema() -> dict:
        """
        Return JSON Schema for non-secret configuration fields.

        Used to validate config_json in mcp_connections.
        """
        ...

    @staticmethod
    @abstractmethod
    def get_credentials_schema() -> dict:
        """
        Return JSON Schema for credential fields.

        Used to validate credentials before storing in Key Vault.
        """
        ...

    async def read_data(
        self,
        table: str,
        columns: Optional[List[str]] = None,
        filters: Optional[List[Dict[str, Any]]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> ReadResult:
        """Read rows from a table. Only warehouse connectors can read."""
        raise NotImplementedError(f"The {self.connector_type.value} connector can't read data")

    async def count_rows(self, table: str, filters: Optional[List[Dict[str, Any]]] = None) -> int:
        """Count rows in a table. Only warehouse connectors can read."""
        raise NotImplementedError(f"The {self.connector_type.value} connector can't read data")

    async def discover_target_schema(self, target: str) -> Optional[TargetSchema]:
        """The schema of one target, if it exists."""
        for s in await self.discover_schema():
            if s.name.lower() == target.lower():
                return s
        return None

    async def close(self):
        """Close any open connections. Override in subclasses as needed."""
        pass
