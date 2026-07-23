"""
Input validation helpers for MCP tool executors.

Shared validators that return structured outcomes (valid / alias-with-suggestion /
unknown) so tools can fail loudly on unvalidated input with actionable diagnostics
instead of silently returning empty results.

Design principles:
- Every validator returns a dataclass with a `valid` flag and enough context to
  explain to the caller what went wrong and what to do next.
- Validators have a tier strategy: local data first (zero latency), cached data
  second (Redis), live API third (fallback on cold cache).
- Failures are never silent. An unknown input returns `valid=False` with a
  suggested next step, not an empty success.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


# =============================================================================
# Gene symbol validation (HGNC)
# =============================================================================


@dataclass
class GeneValidation:
    """Result of a gene symbol validation lookup."""
    valid: bool
    symbol: str  # The input symbol (uppercased, stripped)
    has_pgx_data: bool = False
    hgnc_id: Optional[str] = None
    gene_name: Optional[str] = None
    suggested_symbol: Optional[str] = None  # For alias/previous symbols
    source: str = "none"  # novomcp_pgx | hgnc_cache | hgnc_alias | ensembl | none
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "valid": self.valid,
            "symbol": self.symbol,
            "has_pgx_data": self.has_pgx_data,
            "source": self.source,
        }
        if self.hgnc_id:
            d["hgnc_id"] = self.hgnc_id
        if self.gene_name:
            d["gene_name"] = self.gene_name
        if self.suggested_symbol:
            d["suggested_symbol"] = self.suggested_symbol
        if self.message:
            d["message"] = self.message
        return d


# Pharmacogenes we have data for (56 in omics_pgx Cosmos collection).
# Loaded once at module import. This is the tier-1 validation layer.
PGX_GENES: set = set()


def _load_pgx_genes() -> set:
    """Load the 56 pharmacogenes we have omics data for.

    Source: the omics_pgx Cosmos collection partition keys (gene_symbol).
    Hardcoded as a fallback for zero-latency tier-1 validation. Update whenever
    the PGx panel expands.
    """
    # Core CYP, UGT, SLC, ABCB, and HLA pharmacogenes from CPIC/PharmGKB
    return {
        # CYP450
        "CYP1A2", "CYP2A6", "CYP2B6", "CYP2C8", "CYP2C9", "CYP2C19",
        "CYP2D6", "CYP2E1", "CYP3A4", "CYP3A5", "CYP4F2",
        # UGT / glucuronidation
        "UGT1A1", "UGT1A4", "UGT2B7", "UGT2B15",
        # Transporters
        "SLCO1B1", "SLCO1B3", "SLC22A1", "SLC22A2", "ABCB1", "ABCG2",
        # Drug targets with PGx guidance
        "VKORC1", "TPMT", "NUDT15", "DPYD", "G6PD", "HLA-B", "HLA-A",
        "HLA-C", "HLA-DRB1", "HMGCR", "F5", "F2", "MTHFR", "NAT2",
        "GSTP1", "GSTM1", "GSTT1", "COMT", "CFTR", "RYR1",
        "CACNA1S", "IFNL3", "IFNL4", "CYP2F1", "POR", "FMO3",
        "PTGS1", "PTGS2", "ITGB3", "ADRB1", "ADRB2", "APOE",
        "CES1", "EPHX1", "ALDH2", "ADH1B",
    }


PGX_GENES = _load_pgx_genes()


# In-memory HGNC cache. Lazily populated from local file or download.
# Structure:
#   _hgnc_symbols: {approved_symbol: {"hgnc_id": ..., "name": ...}}
#   _hgnc_aliases: {alias_or_prev_symbol: {"current_symbol": ..., "hgnc_id": ...}}
_hgnc_symbols: Dict[str, Dict[str, Any]] = {}
_hgnc_aliases: Dict[str, Dict[str, Any]] = {}
_hgnc_loaded: bool = False


def _hgnc_cache_path() -> Path:
    """Location of the local HGNC cache file.

    Bootstrap script writes to this path. Module loads from it at first use.
    """
    # Placed alongside the novomcp package so deploys can ship with it
    return Path(__file__).parent.parent / "data" / "hgnc_symbols.json"


def _load_hgnc_from_file() -> bool:
    """Load HGNC data from local cache file if present.

    Returns True on successful load, False if file missing or corrupt.
    """
    global _hgnc_symbols, _hgnc_aliases, _hgnc_loaded
    path = _hgnc_cache_path()
    if not path.exists():
        logger.info(f"HGNC cache not found at {path} — gene validation will use Ensembl fallback only")
        return False

    try:
        with open(path) as f:
            data = json.load(f)
        _hgnc_symbols = data.get("symbols", {})
        _hgnc_aliases = data.get("aliases", {})
        _hgnc_loaded = True
        logger.info(f"Loaded HGNC cache: {len(_hgnc_symbols)} approved symbols, {len(_hgnc_aliases)} aliases")
        return True
    except Exception as e:
        logger.warning(f"Failed to load HGNC cache from {path}: {e}")
        return False


async def _lookup_ensembl(symbol: str) -> Optional[Dict[str, Any]]:
    """Live lookup against Ensembl REST API as fallback when HGNC cache is cold.

    Returns {"hgnc_id": ..., "name": ...} if the gene exists, None otherwise.
    Ensembl's symbol lookup is the authoritative live source when the local
    cache isn't populated yet.
    """
    url = f"https://rest.ensembl.org/lookup/symbol/homo_sapiens/{symbol}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                url, headers={"Content-Type": "application/json"}
            )
            if response.status_code == 200:
                data = response.json()
                return {
                    "hgnc_id": data.get("id"),  # Ensembl ID, not HGNC but usable
                    "name": data.get("description", ""),
                }
            if response.status_code == 400:
                # Ensembl returns 400 for unknown symbols
                return None
            logger.warning(f"Ensembl returned HTTP {response.status_code} for {symbol}")
            return None
    except Exception as e:
        logger.warning(f"Ensembl lookup failed for {symbol}: {e}")
        return None


async def validate_gene_symbol(symbol: str) -> GeneValidation:
    """Validate a gene symbol against HGNC and the PGx panel.

    Three-tier lookup:
    1. Local PGx panel (56 pharmacogenes) — zero latency, tells us whether PGx data exists
    2. HGNC cache (~44K approved human symbols) — loaded from local file
    3. Ensembl REST API — live fallback when cache is cold

    Returns a GeneValidation dataclass with `valid`, `has_pgx_data`, and either
    `hgnc_id`/`gene_name` (valid) or `suggested_symbol`/`message` (alias/unknown).

    The caller should return early with a structured error response when valid=False.
    """
    if not symbol or not isinstance(symbol, str):
        return GeneValidation(
            valid=False,
            symbol=str(symbol) if symbol else "",
            source="none",
            message="Gene symbol is empty or not a string.",
        )

    # Normalize: uppercase, strip whitespace
    normalized = symbol.strip().upper()
    if not normalized:
        return GeneValidation(
            valid=False,
            symbol=symbol,
            source="none",
            message="Gene symbol is empty after stripping whitespace.",
        )

    # Tier 1: Is it a pharmacogene we have data for?
    if normalized in PGX_GENES:
        return GeneValidation(
            valid=True,
            symbol=normalized,
            has_pgx_data=True,
            source="novomcp_pgx",
        )

    # Lazy-load HGNC cache from local file on first call
    global _hgnc_loaded
    if not _hgnc_loaded:
        _load_hgnc_from_file()
        _hgnc_loaded = True  # Mark as attempted even if load failed

    # Tier 2a: HGNC approved symbols cache
    if normalized in _hgnc_symbols:
        entry = _hgnc_symbols[normalized]
        return GeneValidation(
            valid=True,
            symbol=normalized,
            has_pgx_data=False,
            hgnc_id=entry.get("hgnc_id"),
            gene_name=entry.get("name"),
            source="hgnc_cache",
        )

    # Tier 2b: HGNC alias/previous symbols
    if normalized in _hgnc_aliases:
        alias_entry = _hgnc_aliases[normalized]
        current = alias_entry.get("current_symbol")
        return GeneValidation(
            valid=False,
            symbol=normalized,
            suggested_symbol=current,
            hgnc_id=alias_entry.get("hgnc_id"),
            source="hgnc_alias",
            message=(
                f"'{normalized}' is an alias or previous symbol for '{current}'. "
                f"Retry with the current HGNC-approved symbol '{current}'."
            ),
        )

    # Tier 3: Live Ensembl fallback (only if local cache miss)
    ensembl_entry = await _lookup_ensembl(normalized)
    if ensembl_entry:
        return GeneValidation(
            valid=True,
            symbol=normalized,
            has_pgx_data=False,
            hgnc_id=ensembl_entry.get("hgnc_id"),
            gene_name=ensembl_entry.get("name"),
            source="ensembl",
        )

    # Not found anywhere — real unknown gene
    return GeneValidation(
        valid=False,
        symbol=normalized,
        source="none",
        message=(
            f"Gene symbol '{normalized}' not recognized as a valid HGNC-approved "
            f"human gene. Check spelling or look up the official symbol at "
            f"https://www.genenames.org/tools/search/#!/?query={normalized}"
        ),
    )


# =============================================================================
# PDB ID validation (RCSB)
# =============================================================================


@dataclass
class PdbValidation:
    """Result of a PDB ID validation lookup."""
    valid: bool
    pdb_id: str
    title: Optional[str] = None
    source: str = "none"  # format_check | rcsb_api | none
    message: Optional[str] = None
    suggested_id: Optional[str] = None


import re
_PDB_ID_PATTERN = re.compile(r"^[0-9][A-Za-z0-9]{3}$")


async def validate_pdb_id(pdb_id: str) -> PdbValidation:
    """Validate a PDB ID: format check + RCSB existence probe.

    PDB IDs are 4 characters: digit + 3 alphanumeric (e.g., '1CRN', '6OIM').
    If the format is valid, probes RCSB to confirm the entry exists.
    """
    if not pdb_id or not isinstance(pdb_id, str):
        return PdbValidation(
            valid=False, pdb_id=str(pdb_id or ""),
            message="PDB ID is empty.",
        )

    normalized = pdb_id.strip().upper()

    # Format check
    if not _PDB_ID_PATTERN.match(normalized):
        # Check if it looks like a gene name (all letters, >4 chars)
        if normalized.isalpha() and len(normalized) > 4:
            return PdbValidation(
                valid=False, pdb_id=normalized, source="format_check",
                message=(
                    f"'{normalized}' looks like a gene name, not a PDB ID. "
                    f"PDB IDs are 4 characters (e.g., '1CRN'). "
                    f"Use get_protein_structure with the gene name to find "
                    f"associated PDB structures."
                ),
            )
        return PdbValidation(
            valid=False, pdb_id=normalized, source="format_check",
            message=(
                f"'{normalized}' is not a valid PDB ID format. "
                f"PDB IDs are 4 characters: one digit followed by 3 "
                f"alphanumeric characters (e.g., '1CRN', '6OIM')."
            ),
        )

    # RCSB existence check
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://data.rcsb.org/rest/v1/core/entry/{normalized}"
            )
            if resp.status_code == 200:
                data = resp.json()
                title = data.get("struct", {}).get("title", "")
                return PdbValidation(
                    valid=True, pdb_id=normalized, title=title,
                    source="rcsb_api",
                )
            elif resp.status_code == 404:
                return PdbValidation(
                    valid=False, pdb_id=normalized, source="rcsb_api",
                    message=(
                        f"PDB entry '{normalized}' not found in RCSB. "
                        f"Check the ID at https://www.rcsb.org/search?q={normalized}"
                    ),
                )
    except Exception as e:
        logger.warning(f"RCSB API check failed for {normalized}: {e}")
        # If RCSB is unreachable, pass on format alone
        return PdbValidation(
            valid=True, pdb_id=normalized, source="format_check",
            message="PDB format is valid but RCSB availability could not be confirmed.",
        )

    return PdbValidation(
        valid=False, pdb_id=normalized, source="rcsb_api",
        message=f"Unexpected response from RCSB for '{normalized}'.",
    )


# =============================================================================
# ChEMBL ID validation
# =============================================================================


@dataclass
class ChemblValidation:
    """Result of a ChEMBL ID validation lookup."""
    valid: bool
    query: str
    is_chembl_id: bool = False  # True if input matches CHEMBL\d+ format
    chembl_id: Optional[str] = None
    pref_name: Optional[str] = None
    source: str = "none"  # format_check | chembl_api | none
    message: Optional[str] = None


_CHEMBL_ID_PATTERN = re.compile(r"^CHEMBL\d+$", re.IGNORECASE)


async def validate_chembl_query(query: str) -> ChemblValidation:
    """Validate a ChEMBL query: if it looks like a ChEMBL ID, verify it exists.

    For free-text queries (not ChEMBL IDs), always returns valid=True since
    ChEMBL search accepts any text. Only rejects explicit ChEMBL IDs that
    don't exist.
    """
    if not query or not isinstance(query, str):
        return ChemblValidation(
            valid=False, query=str(query or ""),
            message="Search query is empty.",
        )

    normalized = query.strip()

    # Check if it's a ChEMBL ID format
    if _CHEMBL_ID_PATTERN.match(normalized):
        chembl_id = normalized.upper()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"https://www.ebi.ac.uk/chembl/api/data/molecule/{chembl_id}.json"
                )
                if resp.status_code == 200:
                    data = resp.json()
                    pref_name = data.get("pref_name")
                    return ChemblValidation(
                        valid=True, query=normalized,
                        is_chembl_id=True, chembl_id=chembl_id,
                        pref_name=pref_name, source="chembl_api",
                    )
                elif resp.status_code == 404:
                    return ChemblValidation(
                        valid=False, query=normalized,
                        is_chembl_id=True, chembl_id=chembl_id,
                        source="chembl_api",
                        message=(
                            f"ChEMBL ID '{chembl_id}' not found. "
                            f"Check the ID at https://www.ebi.ac.uk/chembl/compound_report_card/{chembl_id}/"
                        ),
                    )
        except Exception as e:
            logger.warning(f"ChEMBL API check failed for {chembl_id}: {e}")
            # If ChEMBL is unreachable, pass on format alone
            return ChemblValidation(
                valid=True, query=normalized,
                is_chembl_id=True, chembl_id=chembl_id,
                source="format_check",
                message="ChEMBL ID format is valid but availability could not be confirmed.",
            )

    # Not a ChEMBL ID — free text query, always valid
    return ChemblValidation(
        valid=True, query=normalized,
        is_chembl_id=False, source="free_text",
    )
