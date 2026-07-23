"""
PDB Structure Caching Utility
Fetches and caches PDB structures from RCSB for GROMACS and AutoDock
Enhanced with OpenFold3 structure prediction fallback
"""

import logging
import httpx
from typing import Dict, Optional, Tuple
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# In-memory cache for PDB structures (both fetched and predicted)
PDB_CACHE: Dict[str, str] = {}

# Cache metadata (source: 'rcsb' or 'openfold3')
PDB_CACHE_METADATA: Dict[str, Dict[str, str]] = {}

# Protein name to PDB ID mapping for common drug discovery targets
# Maps protein names/descriptions to curated high-quality PDB structures
PROTEIN_PDB_MAPPING = {
    # Kinases (Cancer, Inflammation)
    "CDK2": "1HCK",  # Cyclin-Dependent Kinase 2 with staurosporine (2.0 Å, hinge binder)
    "CYCLIN-DEPENDENT KINASE 2": "1HCK",
    "CDK2 HINGE": "4EK3",  # CDK2 with fragment in hinge region (2.1 Å)
    "CDK2 ATP": "1FIN",  # CDK2 with ATP analog (2.7 Å)
    "EGFR": "1M17",  # Epidermal Growth Factor Receptor with erlotinib
    "BRAF": "4XV2",  # BRAF V600E with vemurafenib
    "ALK": "2XP2",  # Anaplastic Lymphoma Kinase
    "JAK2": "3KRR",  # Janus Kinase 2
    "BTK": "5P9J",  # Bruton's Tyrosine Kinase with ibrutinib
    "ABL": "2HYY",  # ABL kinase with imatinib

    # GPCRs (Neuroscience, Metabolic)
    "BETA-2 ADRENERGIC RECEPTOR": "2RH1",
    "ADENOSINE A2A RECEPTOR": "3EML",
    "DOPAMINE D3 RECEPTOR": "3PBL",

    # Proteases (Infectious Disease, Cancer)
    "HIV PROTEASE": "1HXB",
    "THROMBIN": "1PPB",
    "FACTOR XA": "2P16",
    "SARS-COV-2 MAIN PROTEASE": "6LU7",  # COVID-19 Mpro
    "SARS-COV-2 MPRO": "6LU7",

    # Nuclear Receptors (Oncology, Metabolic)
    "ESTROGEN RECEPTOR": "3ERT",
    "ANDROGEN RECEPTOR": "2AMA",
    "PPAR GAMMA": "2PRG",

    # Epigenetic Targets
    "HDAC2": "4LXZ",  # Histone Deacetylase 2
    "BRD4": "3MXF",  # Bromodomain-containing protein 4

    # Ion Channels
    "KCNQ1": "6UZZ",  # Potassium channel (Cardiovascular)

    # Enzymes (Metabolic, Cancer)
    "PDE5": "1UDT",  # Phosphodiesterase 5 (Cardiovascular)
    "COX-2": "5KIR",  # Cyclooxygenase-2 (Inflammation)

    # Antibacterial
    "DNA GYRASE": "1KZN",
    "PENICILLIN-BINDING PROTEIN": "1CEF"
}


def extract_pdb_from_description(description: str) -> Optional[str]:
    """
    Extract a known PDB ID from a protein description.

    Handles common formats:
    - "CDK2" → "1HCK"
    - "Cyclin-Dependent Kinase 2 (CDK2) ATP-binding site hinge region" → "1HCK"
    - "EGFR kinase domain" → "1M17"

    Args:
        description: Protein description or name

    Returns:
        PDB ID if recognized, None otherwise
    """
    description_upper = description.strip().upper()

    # Strategy 1: Direct match (e.g., "CDK2" → "1HCK")
    if description_upper in PROTEIN_PDB_MAPPING:
        pdb_id = PROTEIN_PDB_MAPPING[description_upper]
        logger.info(f"Matched description '{description}' to PDB ID '{pdb_id}' (direct match)")
        return pdb_id

    # Strategy 2: Partial match (e.g., "CDK2 ATP-binding site" → "1HCK")
    for protein_name, pdb_id in PROTEIN_PDB_MAPPING.items():
        if protein_name in description_upper:
            logger.info(f"Matched description '{description}' to PDB ID '{pdb_id}' (found '{protein_name}')")
            return pdb_id

    # Strategy 3: Special case for hinge regions (prefer fragment-bound structures)
    if "HINGE" in description_upper or "ATP-BINDING SITE" in description_upper:
        for protein_name, pdb_id in PROTEIN_PDB_MAPPING.items():
            if "HINGE" in protein_name and any(key in description_upper for key in protein_name.split()):
                logger.info(f"Matched hinge description '{description}' to PDB ID '{pdb_id}'")
                return pdb_id

    logger.warning(f"Could not extract PDB ID from description: '{description}'")
    return None


async def get_pdb(pdb_id: str) -> str:
    """
    Fetch PDB structure with caching

    Args:
        pdb_id: PDB identifier (e.g., "6OIM")

    Returns:
        PDB file content as string

    Raises:
        HTTPException: If PDB not found or fetch fails
    """
    # Normalize PDB ID (uppercase, no whitespace)
    pdb_id = pdb_id.strip().upper()

    # Check cache first
    if pdb_id in PDB_CACHE:
        logger.info(f"PDB {pdb_id} retrieved from cache")
        return PDB_CACHE[pdb_id]

    # Fetch from RCSB
    logger.info(f"Fetching PDB {pdb_id} from RCSB")
    url = f"https://files.rcsb.org/download/{pdb_id}.pdb"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)

            if response.status_code == 200:
                pdb_content = response.text

                # Validate PDB content (basic check)
                if not pdb_content.startswith(("HEADER", "TITLE", "REMARK", "ATOM")):
                    raise HTTPException(
                        status_code=500,
                        detail=f"Invalid PDB content for {pdb_id}"
                    )

                # Cache the PDB
                PDB_CACHE[pdb_id] = pdb_content
                logger.info(f"PDB {pdb_id} fetched and cached ({len(pdb_content)} bytes)")

                return pdb_content

            elif response.status_code == 404:
                raise HTTPException(
                    status_code=404,
                    detail=f"PDB {pdb_id} not found in RCSB database"
                )
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to fetch PDB {pdb_id}: HTTP {response.status_code}"
                )

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail=f"Timeout fetching PDB {pdb_id} from RCSB"
        )
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=500,
            detail=f"HTTP error fetching PDB {pdb_id}: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Error fetching PDB {pdb_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching PDB {pdb_id}: {str(e)}"
        )


def get_cached_pdb(pdb_id: str) -> Optional[str]:
    """
    Get PDB from cache only (no fetch)

    Args:
        pdb_id: PDB identifier

    Returns:
        PDB content if cached, None otherwise
    """
    pdb_id = pdb_id.strip().upper()
    return PDB_CACHE.get(pdb_id)


def clear_cache():
    """Clear the entire PDB cache"""
    PDB_CACHE.clear()
    logger.info("PDB cache cleared")


def get_cache_stats() -> Dict[str, int]:
    """
    Get cache statistics

    Returns:
        Dict with cache stats (count, total_bytes)
    """
    total_bytes = sum(len(pdb) for pdb in PDB_CACHE.values())
    rcsb_count = sum(1 for meta in PDB_CACHE_METADATA.values() if meta.get("source") == "rcsb")
    openfold3_count = sum(1 for meta in PDB_CACHE_METADATA.values() if meta.get("source") == "openfold3")

    return {
        "cached_pdbs": len(PDB_CACHE),
        "total_bytes": total_bytes,
        "pdb_ids": list(PDB_CACHE.keys()),
        "rcsb_structures": rcsb_count,
        "openfold3_structures": openfold3_count
    }


async def get_or_predict_structure(
    target: str,
    sequence: Optional[str] = None,
    prefer_experimental: bool = True
) -> Tuple[str, str]:
    """
    Hybrid resolver: Get structure from PDB or predict with OpenFold3

    This is the main entry point for structure resolution with automatic fallback.

    Args:
        target: PDB ID or protein identifier
        sequence: Optional protein sequence (required if prediction needed)
        prefer_experimental: Try RCSB first before prediction (default: True)

    Returns:
        Tuple of (pdb_content, source) where source is 'rcsb' or 'openfold3'

    Raises:
        HTTPException: If both PDB fetch and prediction fail
    """
    # Normalize target
    target_original = target.strip()  # Keep original case for logging
    target = target_original.upper()

    # ROBUST FIX: Auto-resolve protein names/descriptions to PDB IDs
    resolved_pdb_id = None

    # Strategy 1: Direct lookup for short protein names (EGFR, CDK2, etc.)
    if target in PROTEIN_PDB_MAPPING:
        resolved_pdb_id = PROTEIN_PDB_MAPPING[target]
        logger.info(f"✓ Resolved protein name '{target}' → PDB ID '{resolved_pdb_id}' (direct match)")
        target = resolved_pdb_id

    # Strategy 2: For longer descriptions, try to extract known protein names
    elif len(target) > 10 or " " in target or "(" in target:
        logger.info(f"Target '{target_original}' appears to be a description, attempting auto-resolution")
        resolved_pdb_id = extract_pdb_from_description(target)

        if resolved_pdb_id:
            # Success! Use the resolved PDB ID
            logger.info(f"✓ Resolved description '{target_original}' → PDB ID '{resolved_pdb_id}'")
            target = resolved_pdb_id
            prefer_experimental = True  # Now we have a valid PDB ID, fetch from RCSB
        else:
            # Could not resolve - check if we have sequence for prediction
            logger.warning(f"Could not auto-resolve '{target_original}' to a known PDB ID")
            if not sequence:
                # Build helpful error message suggesting known proteins
                known_proteins = ", ".join(sorted(set([k.split()[0] for k in PROTEIN_PDB_MAPPING.keys()]))[:10])
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Target '{target_original}' is not a valid PDB ID and could not be auto-resolved. "
                        f"Please provide either:\n"
                        f"  1. A valid 4-character PDB ID (e.g., '1HCK')\n"
                        f"  2. A known protein name (e.g., {known_proteins}, ...)\n"
                        f"  3. A protein sequence for structure prediction"
                    )
                )
            # Have sequence - skip RCSB and go to prediction
            logger.info(f"Will predict structure for '{target_original}' using provided sequence")
            prefer_experimental = False

    # Check cache first (regardless of source)
    if target in PDB_CACHE:
        metadata = PDB_CACHE_METADATA.get(target, {})
        source = metadata.get("source", "unknown")
        logger.info(f"Structure {target} retrieved from cache (source: {source})")
        return PDB_CACHE[target], source

    if prefer_experimental:
        # Strategy 1: Try PDB first (fast, validated, free)
        try:
            pdb_content = await get_pdb(target)
            PDB_CACHE_METADATA[target] = {
                "source": "rcsb",
                "target": target,
                "retrieved_at": httpx.get.__module__  # Placeholder timestamp
            }
            return pdb_content, "rcsb"
        except HTTPException as e:
            if e.status_code == 404:
                logger.info(f"PDB {target} not found in RCSB, will try prediction")
            else:
                logger.warning(f"PDB fetch failed: {e.detail}, will try prediction")
        except Exception as e:
            logger.warning(f"Unexpected error fetching PDB: {e}, will try prediction")

    # Strategy 2: Predict with OpenFold3 (slower, predicted, but covers novel targets)
    if not sequence:
        raise HTTPException(
            status_code=400,
            detail=f"Structure {target} not found in PDB and no sequence provided for prediction"
        )

    logger.info(f"Predicting structure for {target} using OpenFold3")

    try:
        from ai.structure_manager import predict_protein_structure

        prediction_result = await predict_protein_structure(
            sequence=sequence,
            request_id=target,
            output_format="pdb"
        )

        pdb_content = prediction_result["structure"]

        # Cache the predicted structure
        PDB_CACHE[target] = pdb_content
        PDB_CACHE_METADATA[target] = {
            "source": "openfold3",
            "target": target,
            "sequence_length": len(sequence),
            "job_id": prediction_result.get("job_id"),
            "completed_at": prediction_result.get("completed_at"),
            "confidence_scores": str(prediction_result.get("confidence_scores", {}))
        }

        logger.info(f"Structure {target} predicted and cached (OpenFold3)")
        return pdb_content, "openfold3"

    except Exception as e:
        logger.error(f"OpenFold3 prediction failed for {target}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to obtain structure for {target}: PDB not found and prediction failed - {str(e)}"
        )


async def predict_and_cache_structure(
    target_id: str,
    sequence: str,
    msa_alignment: Optional[str] = None
) -> Tuple[str, Dict]:
    """
    Explicitly predict structure with OpenFold3 and cache it

    Use this when you want to force prediction (e.g., for mutations, novel proteins)

    Args:
        target_id: Identifier for the target (will be used as cache key)
        sequence: Protein amino acid sequence
        msa_alignment: Optional MSA alignment in CSV format

    Returns:
        Tuple of (pdb_content, metadata_dict)
    """
    target_id = target_id.strip().upper()

    logger.info(f"Explicitly predicting structure for {target_id}")

    try:
        from ai.structure_manager import predict_protein_structure

        prediction_result = await predict_protein_structure(
            sequence=sequence,
            request_id=target_id,
            msa_alignment=msa_alignment,
            output_format="pdb"
        )

        pdb_content = prediction_result["structure"]

        # Cache the result
        PDB_CACHE[target_id] = pdb_content
        metadata = {
            "source": "openfold3",
            "target": target_id,
            "sequence_length": len(sequence),
            "job_id": prediction_result.get("job_id"),
            "completed_at": prediction_result.get("completed_at"),
            "confidence_scores": prediction_result.get("confidence_scores", {})
        }
        PDB_CACHE_METADATA[target_id] = metadata

        logger.info(f"Structure {target_id} predicted and cached")
        return pdb_content, metadata

    except Exception as e:
        logger.error(f"Structure prediction failed for {target_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Structure prediction failed for {target_id}: {str(e)}"
        )


def get_structure_metadata(target: str) -> Optional[Dict]:
    """
    Get metadata for a cached structure

    Args:
        target: PDB ID or target identifier

    Returns:
        Metadata dict or None if not cached
    """
    target = target.strip().upper()
    return PDB_CACHE_METADATA.get(target)


def clear_pdb_cache() -> Dict[str, int]:
    """
    Clear all entries from the PDB cache.

    Returns:
        Dict with count of cleared entries
    """
    global PDB_CACHE, PDB_CACHE_METADATA

    cleared_count = len(PDB_CACHE)
    PDB_CACHE.clear()
    PDB_CACHE_METADATA.clear()

    logger.info(f"PDB cache cleared: {cleared_count} entries removed")

    return {
        "cleared": cleared_count,
        "remaining": 0
    }


def invalidate_pdb(pdb_id: str) -> Dict[str, bool]:
    """
    Remove a specific PDB entry from cache.

    Args:
        pdb_id: PDB identifier to invalidate

    Returns:
        Dict indicating if entry was found and removed
    """
    pdb_id = pdb_id.strip().upper()

    found = pdb_id in PDB_CACHE

    if found:
        del PDB_CACHE[pdb_id]
        if pdb_id in PDB_CACHE_METADATA:
            del PDB_CACHE_METADATA[pdb_id]
        logger.info(f"PDB cache entry invalidated: {pdb_id}")
    else:
        logger.info(f"PDB cache entry not found: {pdb_id}")

    return {
        "pdb_id": pdb_id,
        "found": found,
        "removed": found
    }


def get_cache_stats() -> Dict[str, any]:
    """
    Get statistics about the PDB cache.

    Returns:
        Dict with cache statistics
    """
    total_entries = len(PDB_CACHE)
    total_size = sum(len(content) for content in PDB_CACHE.values())

    # Count by source
    sources = {}
    for pdb_id, metadata in PDB_CACHE_METADATA.items():
        source = metadata.get("source", "unknown")
        sources[source] = sources.get(source, 0) + 1

    return {
        "total_entries": total_entries,
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "entries_by_source": sources,
        "cached_ids": list(PDB_CACHE.keys())
    }
