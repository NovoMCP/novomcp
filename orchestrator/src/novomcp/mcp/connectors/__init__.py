"""
Connection Registry — Connector adapters for Enterprise MCP exports.

Provides a registry of connector adapters (Snowflake, Databricks, Google Sheets,
Airtable, Benchling) and a factory function to instantiate them.
"""

from typing import Any, Dict

from .base import (
    BaseConnector,
    ConnectorType,
    ExportResult,
    NormalizedType,
    SchemaColumn,
    TargetSchema,
    WriteMode,
)

# Lazy imports to avoid loading all SDKs at startup
CONNECTOR_REGISTRY: Dict[str, str] = {
    "snowflake": "novomcp.mcp.connectors.snowflake_connector.SnowflakeConnector",
    "google_sheets": "novomcp.mcp.connectors.google_sheets_connector.GoogleSheetsConnector",
    "airtable": "novomcp.mcp.connectors.airtable_connector.AirtableConnector",
    "benchling": "novomcp.mcp.connectors.benchling_connector.BenchlingConnector",
    "databricks": "novomcp.mcp.connectors.databricks_connector.DatabricksConnector",
}


def get_connector(
    connector_type: str, config: Dict[str, Any], credentials: Dict[str, Any]
) -> BaseConnector:
    """
    Factory function to instantiate a connector adapter.

    Args:
        connector_type: One of snowflake, google_sheets, airtable, benchling, databricks
        config: Non-secret configuration dict
        credentials: Secret credentials from Azure Key Vault

    Returns:
        Instantiated BaseConnector subclass

    Raises:
        ValueError: If connector_type is not registered
        ImportError: If connector SDK is not installed
    """
    if connector_type not in CONNECTOR_REGISTRY:
        raise ValueError(
            f"Unknown connector type: {connector_type}. "
            f"Available: {list(CONNECTOR_REGISTRY.keys())}"
        )

    # Lazy import the connector class
    module_path, class_name = CONNECTOR_REGISTRY[connector_type].rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    connector_class = getattr(module, class_name)

    return connector_class(config=config, credentials=credentials)


__all__ = [
    "BaseConnector",
    "ConnectorType",
    "ExportResult",
    "NormalizedType",
    "SchemaColumn",
    "TargetSchema",
    "WriteMode",
    "CONNECTOR_REGISTRY",
    "get_connector",
]
