"""Async client for the MetalPDB REST API + response flattening.

Primary data source for per-metal annotation (Pfam family, EC number,
coordination geometry, motif pattern). The API exposes a single
endpoint that returns a list of sites, each containing a `metals` list,
each containing a `ligands` list with `donors`. We flatten the nested
schema into one `MetalPDBAnnotation` per physical metal atom for
consumption by the classifier.

See doc 08 for the schema correction history — the `site_type` field
is nuclearity, not functional role, and the real functional-role tag
is NOT exposed via the public API. We work around this in the
classifier by reasoning from Pfam + EC + coordination pattern.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

METALPDB_URL = "http://metalpdb.cerm.unifi.it/api"
METALPDB_TIMEOUT_S = 15.0
METALPDB_CACHE_TTL_S = 30 * 24 * 3600  # 30 days


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, max=5),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
    reraise=True,
)
async def _metalpdb_fetch(http_client: httpx.AsyncClient, url: str, params: dict):
    """HTTP GET with automatic retry on transient network errors."""
    return await http_client.get(url, params=params, timeout=METALPDB_TIMEOUT_S)

_REDIS_KEY_PREFIX = "intake:metalpdb:"


@dataclass
class MetalPDBAnnotation:
    """Flattened per-metal view of one entry from the MetalPDB response."""
    metal_symbol: str
    nuclearity: Optional[str] = None         # "Mononuclear" / "Dinuclear" / ...
    geometry: Optional[str] = None           # "tetrahedron (regular)" / ...
    coord_number: Optional[int] = None
    pattern: Optional[str] = None            # e.g. "HX(1)HX(22)H"
    pfam: Optional[str] = None
    ec_number: Optional[str] = None
    scop: Optional[str] = None
    uniprot: Optional[str] = None
    molecule: Optional[str] = None
    site_id: Optional[str] = None
    residue_pdb_number: Optional[int] = None
    chain: Optional[str] = None              # inferred from first ligand's chain field
    ligand_residues: List[str] = field(default_factory=list)


async def fetch_sites(
    pdb_id: Optional[str],
    *,
    http_client: httpx.AsyncClient,
    redis_client=None,
) -> tuple[List[MetalPDBAnnotation], Optional[str]]:
    """Fetch and flatten MetalPDB annotations for a PDB.

    Returns:
        (annotations, warning). annotations is a list of per-metal
        MetalPDBAnnotation records (empty on miss). warning is set
        when the API was unreachable and we fell back to empty — the
        caller should add it to SystemProfile.warnings so the user
        knows classification degraded.
    """
    if not pdb_id:
        return [], None
    pdb_id = pdb_id.strip().lower()

    # 1. Cache check
    raw: Optional[List[Dict[str, Any]]] = None
    if redis_client is not None:
        try:
            cached = await redis_client.get(_REDIS_KEY_PREFIX + pdb_id)
            if cached:
                raw = json.loads(cached)
        except Exception as e:  # pragma: no cover
            logger.warning(f"MetalPDB cache read failed for {pdb_id}: {e}")

    warning: Optional[str] = None

    # 2. API fetch if not cached
    if raw is None:
        try:
            resp = await _metalpdb_fetch(
                http_client, METALPDB_URL, {"query": f"pdb:{pdb_id}"}
            )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError) as e:
            warning = (
                f"MetalPDB API unreachable ({type(e).__name__}); "
                f"falling back to heuristic classification"
            )
            logger.warning(f"{warning} for {pdb_id}")
            return [], warning

        if resp.status_code != 200:
            if resp.status_code == 404:
                await _cache_set(redis_client, pdb_id, [])
                return [], None
            warning = (
                f"MetalPDB API returned HTTP {resp.status_code}; "
                f"falling back to heuristic"
            )
            logger.warning(f"{warning} for {pdb_id}")
            return [], warning

        try:
            parsed = resp.json()
        except (json.JSONDecodeError, ValueError):
            warning = "MetalPDB API returned non-JSON; falling back to heuristic"
            logger.warning(f"{warning} for {pdb_id}")
            return [], warning

        # Normalize into a list of site dicts.
        if isinstance(parsed, dict):
            raw = parsed.get("sites") or parsed.get("results") or [parsed]
        elif isinstance(parsed, list):
            raw = parsed
        else:
            raw = []

        await _cache_set(redis_client, pdb_id, raw)

    # 3. Flatten nested schema into per-metal annotations
    return _iter_metal_annotations(raw), None


def _iter_metal_annotations(sites: List[Dict[str, Any]]) -> List[MetalPDBAnnotation]:
    """Walk the nested sites[].metals[].ligands[] schema and produce one
    annotation per physical metal atom.

    Handles the real MetalPDB schema: each site has a `metals` array,
    each metal has a `ligands` array, each ligand has a `donors` array
    and a `chain` field that we use to infer the metal's chain. See
    doc 08 for a full example response.
    """
    annotations: List[MetalPDBAnnotation] = []
    for site in sites or []:
        if not isinstance(site, dict):
            continue
        site_pfam = site.get("pfam")
        site_ec = site.get("ec_number")
        site_scop = site.get("scop")
        site_uniprot = site.get("uniprot")
        site_mol = site.get("molecule")
        site_id = site.get("site")
        site_nuc = site.get("site_type")  # nuclearity, NOT functional role
        metals = site.get("metals", [])
        if not isinstance(metals, list):
            continue
        for metal in metals:
            if not isinstance(metal, dict):
                continue
            symbol = metal.get("symbol") or metal.get("metal") or ""
            if not symbol:
                continue
            cn_raw = metal.get("coordination")
            try:
                cn = int(cn_raw) if cn_raw is not None else None
            except (TypeError, ValueError):
                cn = None
            resno_raw = metal.get("residue_pdb_number")
            try:
                resno = int(resno_raw) if resno_raw is not None else None
            except (TypeError, ValueError):
                resno = None
            ligand_list: List[str] = []
            site_chain: Optional[str] = None
            for lig in metal.get("ligands", []) or []:
                if not isinstance(lig, dict):
                    continue
                lig_resname = lig.get("residue", "")
                lig_resno = lig.get("residue_pdb_number", "")
                if site_chain is None and lig.get("chain"):
                    site_chain = str(lig["chain"]).strip()
                if lig_resname:
                    ligand_list.append(f"{lig_resname}:{lig_resno}")
            annotations.append(
                MetalPDBAnnotation(
                    metal_symbol=str(symbol),
                    nuclearity=str(site_nuc) if site_nuc else None,
                    geometry=metal.get("geometry"),
                    coord_number=cn,
                    pattern=metal.get("pattern"),
                    pfam=str(site_pfam) if site_pfam else None,
                    ec_number=str(site_ec) if site_ec else None,
                    scop=str(site_scop) if site_scop else None,
                    uniprot=str(site_uniprot) if site_uniprot else None,
                    molecule=str(site_mol) if site_mol else None,
                    site_id=str(site_id) if site_id else None,
                    residue_pdb_number=resno,
                    chain=site_chain,
                    ligand_residues=ligand_list,
                )
            )
    return annotations


def match_annotation(
    element: str,
    chain: Optional[str],
    resid: int,
    annotations: List[MetalPDBAnnotation],
    consumed: set,
) -> Optional[MetalPDBAnnotation]:
    """Pair a locally-detected metal with a MetalPDB annotation.

    Three-pass match:
      1. (element, chain, resid) — tightest; required for homo-oligomers
         where multiple chains share residue numbering.
      2. (element, resid) — fallback when chain is missing on either side.
      3. (element only) — last resort.

    `consumed` tracks annotations already paired so subsequent metals
    don't all collapse onto the first match.
    """
    target_elem = element.strip().lower()
    target_chain = (chain or "").strip()

    def take(i: int) -> MetalPDBAnnotation:
        consumed.add(i)
        return annotations[i]

    # Pass 1: element + chain + resid
    for i, ann in enumerate(annotations):
        if i in consumed:
            continue
        if ann.metal_symbol.strip().lower() != target_elem:
            continue
        if ann.residue_pdb_number != resid:
            continue
        if target_chain and ann.chain and ann.chain.strip() != target_chain:
            continue
        return take(i)
    # Pass 2: element + resid
    for i, ann in enumerate(annotations):
        if i in consumed:
            continue
        if (
            ann.metal_symbol.strip().lower() == target_elem
            and ann.residue_pdb_number == resid
        ):
            return take(i)
    # Pass 3: element only
    for i, ann in enumerate(annotations):
        if i in consumed:
            continue
        if ann.metal_symbol.strip().lower() == target_elem:
            return take(i)
    return None


async def _cache_set(redis_client, pdb_id: str, sites: List[Dict[str, Any]]) -> None:
    if redis_client is None:
        return
    try:
        await redis_client.set(
            _REDIS_KEY_PREFIX + pdb_id,
            json.dumps(sites),
            ex=METALPDB_CACHE_TTL_S,
        )
    except Exception as e:  # pragma: no cover
        logger.warning(f"MetalPDB cache write failed for {pdb_id}: {e}")
