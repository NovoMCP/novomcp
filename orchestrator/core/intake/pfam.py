"""Pfam family -> functional role lookup, loaded from YAML at startup.

Matching is case-insensitive and ignores underscores/hyphens so that
MetalPDB variants like "efhand", "EF_hand", "EF-hand" all resolve to
the same entry. See pfam_roles.yaml for the data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import yaml

from .models import FunctionalRole


_YAML_PATH = Path(__file__).parent / "pfam_roles.yaml"


def _normalize(s: str) -> str:
    """Canonical form for Pfam keys: lowercase, underscores + hyphens stripped."""
    return s.strip().lower().replace("_", "").replace("-", "")


def _load_table() -> Dict[str, FunctionalRole]:
    """Load the raw YAML and build a normalized lookup table.

    Called once at import. If the YAML is malformed, raise loudly — we
    prefer a startup failure to a silent empty table that would refuse
    every metalloprotein.
    """
    with _YAML_PATH.open("r") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"pfam_roles.yaml must be a top-level dict of family -> role, "
            f"got {type(raw).__name__}"
        )
    valid_roles = {"structural", "catalytic", "electron", "transport"}
    normalized: Dict[str, FunctionalRole] = {}
    for family, role in raw.items():
        if role not in valid_roles:
            raise ValueError(
                f"pfam_roles.yaml: invalid role {role!r} for family {family!r}. "
                f"Valid roles: {sorted(valid_roles)}"
            )
        normalized[_normalize(str(family))] = role  # type: ignore[assignment]
    return normalized


_TABLE: Dict[str, FunctionalRole] = _load_table()


def lookup_role(pfam: Optional[str]) -> Optional[FunctionalRole]:
    """Return "structural" / "catalytic" / "electron" / "transport" or None.

    Lookup priority:
      1. Normalized direct hit
      2. Suffix trim (e.g. "Peptidase_M10A" -> "Peptidase_M10")
      3. Substring fallback on normalized keys
    """
    if not pfam:
        return None
    raw = pfam.strip().lower()
    key = _normalize(pfam)

    # 1. Direct normalized hit
    if key in _TABLE:
        return _TABLE[key]

    # 2. Suffix trim
    if "_" in raw:
        base = raw.rsplit("_", 1)[0]
        base_norm = _normalize(base)
        if base_norm in _TABLE:
            return _TABLE[base_norm]

    # 3. Substring fallback on normalized keys
    for known_key, role in _TABLE.items():
        if known_key in key or key in known_key:
            return role
    return None


def table_size() -> int:
    """Return the number of loaded Pfam entries. Useful for startup logs."""
    return len(_TABLE)
