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
    "snowflake": "mcp.connectors.snowflake_connector.SnowflakeConnector",
    "google_sheets": "mcp.connectors.google_sheets_connector.GoogleSheetsConnector",
    "airtable": "mcp.connectors.airtable_connector.AirtableConnector",
    "benchling": "mcp.connectors.benchling_connector.BenchlingConnector",
    "databricks": "mcp.connectors.databricks_connector.DatabricksConnector",
}

# Minimum tier required for each connector type
CONNECTOR_TIER_REQUIREMENTS: Dict[str, str] = {
    "google_sheets": "pro",
    "airtable": "pro",
    "snowflake": "team",
    "databricks": "team",
    "benchling": "team",
}

# Tier hierarchy for comparison
TIER_HIERARCHY = {"free": 0, "pro": 1, "team": 2, "enterprise": 3}


def check_tier_access(user_tier: str, connector_type: str) -> bool:
    """Check if a user's tier grants access to a connector type."""
    required = CONNECTOR_TIER_REQUIREMENTS.get(connector_type, "team")
    return TIER_HIERARCHY.get(user_tier, 0) >= TIER_HIERARCHY.get(required, 2)


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
    "CONNECTOR_TIER_REQUIREMENTS",
    "check_tier_access",
    "get_connector",
]
