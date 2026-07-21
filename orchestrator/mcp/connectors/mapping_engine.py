"""
Field Mapping Engine for Connection Registry.

Provides three mapping strategies:
1. auto_map — Name matching with normalization and alias tables
2. template_map — Pre-defined mappings for common tool→connector combinations
3. ai_assisted_map — Azure OpenAI-powered mapping with confidence scores

Also provides apply_mapping() to transform tool output using resolved mappings.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .base import NormalizedType, SchemaColumn, TargetSchema

logger = logging.getLogger(__name__)


@dataclass
class FieldMapping:
    """A single field mapping from source to target."""
    source_field: str                     # Dot-notation path (e.g., "properties.molecular_weight")
    target_field: str                     # Target column/field name
    transform: Optional[str] = None       # Transform function name (round_2, to_upper, etc.)
    default_value: Optional[Any] = None   # Default if source is missing
    confidence: float = 1.0               # Mapping confidence (0-1)


# Alias table for common molecular property names
FIELD_ALIASES: Dict[str, List[str]] = {
    "molecular_weight": ["mw", "mol_wt", "molwt", "mol_weight", "molecularweight"],
    "logp": ["xlogp", "clogp", "alogp", "logp_value", "partition_coefficient"],
    "smiles": ["canonical_smiles", "smi", "smiles_string", "molecule_smiles"],
    "molecular_formula": ["formula", "mol_formula", "chemical_formula"],
    "num_atoms": ["atom_count", "natoms", "n_atoms", "heavy_atom_count"],
    "num_bonds": ["bond_count", "nbonds", "n_bonds"],
    "num_rings": ["ring_count", "nrings", "n_rings"],
    "num_rotatable_bonds": ["rotatable_bonds", "n_rotatable", "rot_bonds"],
    "num_h_acceptors": ["hba", "h_acceptors", "hydrogen_bond_acceptors", "hb_acceptors"],
    "num_h_donors": ["hbd", "h_donors", "hydrogen_bond_donors", "hb_donors"],
    "tpsa": ["polar_surface_area", "topological_polar_surface_area"],
    "qed": ["qed_score", "drug_likeness", "quantitative_estimate_of_druglikeness"],
    "sa_score": ["synthetic_accessibility", "sa", "synth_accessibility"],
    "lipinski_violations": ["lipinski", "ro5_violations", "rule_of_five"],
    "compound_name": ["name", "molecule_name", "drug_name", "compound"],
    "inchi": ["inchi_string", "standard_inchi"],
    "inchi_key": ["inchikey", "standard_inchi_key"],
}

# Build reverse alias lookup
_REVERSE_ALIASES: Dict[str, str] = {}
for canonical, aliases in FIELD_ALIASES.items():
    for alias in aliases:
        _REVERSE_ALIASES[alias.lower()] = canonical
    _REVERSE_ALIASES[canonical.lower()] = canonical


def _normalize_name(name: str) -> str:
    """Normalize a field name for matching: lowercase, strip underscores/camelCase."""
    # camelCase → snake_case
    name = re.sub(r"([a-z])([A-Z])", r"\1_\2", name)
    # Remove non-alphanumeric, lowercase
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _canonical_name(name: str) -> str:
    """Get canonical name via alias lookup."""
    lower = name.lower().strip()
    if lower in _REVERSE_ALIASES:
        return _REVERSE_ALIASES[lower]
    # Try normalized
    normalized = _normalize_name(name)
    if normalized in _REVERSE_ALIASES:
        return _REVERSE_ALIASES[normalized]
    return lower


# Available transform functions
TRANSFORMS = {
    "round_2": lambda v: round(float(v), 2) if v is not None else None,
    "round_3": lambda v: round(float(v), 3) if v is not None else None,
    "round_4": lambda v: round(float(v), 4) if v is not None else None,
    "to_upper": lambda v: str(v).upper() if v is not None else None,
    "to_lower": lambda v: str(v).lower() if v is not None else None,
    "to_string": lambda v: str(v) if v is not None else None,
    "to_int": lambda v: int(float(v)) if v is not None else None,
    "to_float": lambda v: float(v) if v is not None else None,
    "json_stringify": lambda v: json.dumps(v) if v is not None else None,
    "iso_datetime": lambda v: str(v) if v is not None else None,
    "boolean_yn": lambda v: "Y" if v else "N",
    "boolean_10": lambda v: 1 if v else 0,
}


def auto_map(
    source_fields: List[str],
    target_schema: TargetSchema,
) -> List[FieldMapping]:
    """
    Auto-map source fields to target schema using name matching and aliases.

    Strategy:
    1. Exact match (case-insensitive)
    2. Normalized match (strip underscores, camelCase)
    3. Alias table match

    Args:
        source_fields: List of source field names (dot-notation)
        target_schema: Target schema with column definitions

    Returns:
        List of FieldMapping with confidence scores
    """
    mappings = []
    target_columns = {col.name: col for col in target_schema.columns}

    # Build target lookup tables
    target_by_lower = {col.name.lower(): col.name for col in target_schema.columns}
    target_by_normalized = {_normalize_name(col.name): col.name for col in target_schema.columns}
    target_by_canonical = {}
    for col in target_schema.columns:
        canon = _canonical_name(col.name)
        target_by_canonical[canon] = col.name

    mapped_targets = set()

    for src in source_fields:
        src_leaf = src.split(".")[-1]  # Get leaf name for dot-notation paths
        src_lower = src_leaf.lower()
        src_normalized = _normalize_name(src_leaf)
        src_canonical = _canonical_name(src_leaf)

        # 1. Exact match (case-insensitive)
        if src_lower in target_by_lower and target_by_lower[src_lower] not in mapped_targets:
            target_name = target_by_lower[src_lower]
            mappings.append(FieldMapping(
                source_field=src,
                target_field=target_name,
                confidence=1.0,
            ))
            mapped_targets.add(target_name)
            continue

        # 2. Normalized match
        if src_normalized in target_by_normalized and target_by_normalized[src_normalized] not in mapped_targets:
            target_name = target_by_normalized[src_normalized]
            mappings.append(FieldMapping(
                source_field=src,
                target_field=target_name,
                confidence=0.9,
            ))
            mapped_targets.add(target_name)
            continue

        # 3. Alias match
        if src_canonical in target_by_canonical and target_by_canonical[src_canonical] not in mapped_targets:
            target_name = target_by_canonical[src_canonical]
            mappings.append(FieldMapping(
                source_field=src,
                target_field=target_name,
                confidence=0.8,
            ))
            mapped_targets.add(target_name)
            continue

    return mappings


def template_map(source_tool: str, connector_type: str) -> Optional[List[FieldMapping]]:
    """
    Get pre-defined template mapping for a tool→connector combination.

    Args:
        source_tool: MCP tool name (e.g., "get_molecule_profile")
        connector_type: Connector type (e.g., "snowflake")

    Returns:
        List of FieldMapping or None if no template exists
    """
    from .mapping_templates import MAPPING_TEMPLATES

    key = f"{source_tool}:{connector_type}"
    template = MAPPING_TEMPLATES.get(key)
    if template is None:
        # Try generic (connector-agnostic) template
        key = f"{source_tool}:*"
        template = MAPPING_TEMPLATES.get(key)

    if template is None:
        return None

    return [
        FieldMapping(
            source_field=m["source"],
            target_field=m["target"],
            transform=m.get("transform"),
            default_value=m.get("default"),
            confidence=1.0,
        )
        for m in template
    ]


async def ai_assisted_map(
    source_fields: List[Dict[str, Any]],
    target_schema: TargetSchema,
    sample_data: Optional[Dict[str, Any]] = None,
) -> List[FieldMapping]:
    """
    Use Azure OpenAI to generate field mappings with confidence scores.

    Costs 5 credits per invocation.

    Args:
        source_fields: List of {name, type, sample_value} dicts
        target_schema: Target schema with column definitions
        sample_data: Optional sample row from source data

    Returns:
        List of FieldMapping with AI-assigned confidence scores
    """
    try:
        from ai.azure_openai_client import AzureOpenAIClient
    except ImportError:
        logger.error("Azure OpenAI client not available for AI-assisted mapping")
        return []

    client = AzureOpenAIClient()
    if not client.available:
        logger.error("Azure OpenAI client not configured")
        return []

    # Build prompt
    source_desc = json.dumps(source_fields, indent=2)
    target_desc = json.dumps(
        [
            {
                "name": col.name,
                "type": col.native_type,
                "normalized_type": col.data_type.value,
                "nullable": col.nullable,
            }
            for col in target_schema.columns
        ],
        indent=2,
    )

    prompt = f"""Map source fields to target columns for data export.

Source fields:
{source_desc}

Target columns ({target_schema.name}):
{target_desc}

{f"Sample source data: {json.dumps(sample_data, indent=2)}" if sample_data else ""}

Return a JSON array of mappings. Each mapping should have:
- "source": source field name (exact match from source fields)
- "target": target column name (exact match from target columns)
- "transform": optional transform (one of: round_2, round_3, to_upper, to_lower, to_string, to_int, to_float, json_stringify, boolean_yn, boolean_10) or null
- "confidence": float 0-1 indicating mapping confidence

Only include mappings where you are reasonably confident (>0.5).
Return ONLY the JSON array, no other text."""

    try:
        response = client.client.chat.completions.create(
            model=client.deployment_name,
            messages=[
                {"role": "system", "content": "You are a data mapping assistant. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=2000,
        )

        content = response.choices[0].message.content.strip()
        # Extract JSON from potential markdown code blocks
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        mapping_data = json.loads(content)

        return [
            FieldMapping(
                source_field=m["source"],
                target_field=m["target"],
                transform=m.get("transform"),
                confidence=float(m.get("confidence", 0.7)),
            )
            for m in mapping_data
            if isinstance(m, dict) and "source" in m and "target" in m
        ]

    except Exception as e:
        logger.error(f"AI-assisted mapping failed: {e}")
        return []


def apply_mapping(
    data: Dict[str, Any],
    mappings: List[FieldMapping],
) -> Dict[str, Any]:
    """
    Apply field mappings to transform a single data row.

    Resolves dot-notation source paths, applies transforms, fills defaults.

    Args:
        data: Source data dict (possibly nested)
        mappings: List of FieldMapping to apply

    Returns:
        Flat dict with target field names as keys
    """
    result = {}

    for mapping in mappings:
        # Resolve dot-notation path
        value = _resolve_path(data, mapping.source_field)

        # Apply default if missing
        if value is None and mapping.default_value is not None:
            value = mapping.default_value

        # Apply transform
        if value is not None and mapping.transform and mapping.transform in TRANSFORMS:
            try:
                value = TRANSFORMS[mapping.transform](value)
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Transform {mapping.transform} failed for {mapping.source_field}: {e}"
                )

        result[mapping.target_field] = value

    return result


def apply_mapping_batch(
    data: List[Dict[str, Any]],
    mappings: List[FieldMapping],
) -> List[Dict[str, Any]]:
    """Apply mappings to a list of data rows."""
    return [apply_mapping(row, mappings) for row in data]


def _resolve_path(data: Dict[str, Any], path: str) -> Any:
    """Resolve a dot-notation path in nested data."""
    parts = path.split(".")
    current = data

    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            idx = int(part)
            current = current[idx] if idx < len(current) else None
        else:
            return None

        if current is None:
            return None

    return current


async def resolve_mapping(
    connection_id: str,
    source_tool: str,
    connector_type: str,
    target_schema: Optional[TargetSchema],
    source_fields: Optional[List[str]],
    mapping_id: Optional[str] = None,
    dashboard_url: Optional[str] = None,
    org_id: Optional[str] = None,
) -> List[FieldMapping]:
    """
    Resolve the best mapping for an export operation.

    Priority:
    1. Explicit mapping_id (user specified)
    2. Org's default mapping for this tool+connection
    3. Template mapping
    4. Auto-map

    Args:
        connection_id: Connection to export to
        source_tool: MCP tool that produced the data
        connector_type: Type of connector
        target_schema: Target schema (for auto-map fallback)
        source_fields: Source field names (for auto-map fallback)
        mapping_id: Explicit mapping ID (optional)
        dashboard_url: Dashboard aggregator URL for DB lookups
        org_id: Organization ID

    Returns:
        List of FieldMapping
    """
    import httpx

    # 1. Explicit mapping_id
    if mapping_id and dashboard_url:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{dashboard_url}/mcp/connections/{connection_id}/mappings",
                    params={"org_id": org_id},
                )
                if resp.status_code == 200:
                    mappings_data = resp.json()
                    for m in mappings_data.get("mappings", []):
                        if m.get("mapping_id") == mapping_id:
                            return _parse_stored_mappings(m.get("field_mappings_json", "[]"))
        except Exception as e:
            logger.warning(f"Failed to fetch explicit mapping {mapping_id}: {e}")

    # 2. Org's default mapping
    if dashboard_url and org_id:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{dashboard_url}/mcp/connections/{connection_id}/mappings",
                    params={"org_id": org_id, "source_tool": source_tool, "is_default": True},
                )
                if resp.status_code == 200:
                    mappings_data = resp.json()
                    defaults = [m for m in mappings_data.get("mappings", []) if m.get("is_default")]
                    if defaults:
                        return _parse_stored_mappings(defaults[0].get("field_mappings_json", "[]"))
        except Exception as e:
            logger.warning(f"Failed to fetch default mapping: {e}")

    # 3. Template mapping
    template = template_map(source_tool, connector_type)
    if template:
        return template

    # 4. Auto-map
    if target_schema and source_fields:
        return auto_map(source_fields, target_schema)

    return []


def _parse_stored_mappings(field_mappings_json: str) -> List[FieldMapping]:
    """Parse stored field mappings JSON into FieldMapping objects."""
    try:
        data = json.loads(field_mappings_json) if isinstance(field_mappings_json, str) else field_mappings_json
        return [
            FieldMapping(
                source_field=m["source"],
                target_field=m["target"],
                transform=m.get("transform"),
                default_value=m.get("default"),
                confidence=float(m.get("confidence", 1.0)),
            )
            for m in data
        ]
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to parse stored mappings: {e}")
        return []
