"""
Base connector classes and data models for the Connection Registry.

Defines the abstract BaseConnector interface that all connector adapters
(Snowflake, Databricks, Google Sheets, Airtable, Benchling) must implement.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

_SAFE_ID = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')


def validate_sql_identifier(name: str, label: str = "identifier") -> str:
    """Validate a SQL identifier (table name, column name, schema, etc.)."""
    if not _SAFE_ID.match(name):
        raise ValueError(f"Invalid SQL {label}: {name!r}")
    return name


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

    def __init__(self, config: Dict[str, Any], credentials: Dict[str, Any]):
        """
        Initialize connector with non-secret config and credentials.

        Args:
            config: Non-secret configuration (account, warehouse, spreadsheet_id, etc.)
            credentials: Secret credentials from Azure Key Vault
        """
        self.config = config
        self.credentials = credentials

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
    ) -> ExportResult:
        """
        Write data to a specific target.

        Args:
            target: Target identifier (table name, sheet name, entity type)
            data: List of row dicts to write
            mode: Write mode (append, replace, upsert)

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

    async def close(self):
        """Close any open connections. Override in subclasses as needed."""
        pass
