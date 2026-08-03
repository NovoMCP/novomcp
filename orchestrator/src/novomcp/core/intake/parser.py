"""PDB parser and local structure detection.

Uses MDAnalysis as the primary parser with Bio.PDB scaffolded as a
future fallback for structures MDA chokes on. Synchronous by design —
these functions do CPU-bound structural analysis. Callers should wrap
them in `loop.run_in_executor(None, ...)` when invoked from async code.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

import MDAnalysis as mda

logger = logging.getLogger(__name__)


# First coordination shell cutoff (Angstrom). MetalPDB uses ~2.8 for
# donors; we pad slightly to catch borderline geometries.
COORD_CUTOFF = 3.0

# Biological metals we care about. Keep conservative — exotic metals
# route to refuse by default.
METAL_ELEMENTS: Set[str] = {
    "ZN", "MG", "CA", "FE", "CU", "MN", "NI", "CO",
    "NA", "K", "MO", "W", "V", "CD",
}

# Heme cofactor residue codes. Detected at HETATM audit → refuse.
HEME_RESIDUES: Set[str] = {"HEM", "HEC", "HEB", "HEA", "HEO", "HNI", "HDM"}

# Iron-sulfur cluster residue codes. Detected at HETATM audit → refuse.
FES_CLUSTER_RESIDUES: Set[str] = {"SF4", "FES", "F3S", "FE2", "F4S"}

# Standard amino acid residue names (for hetatm inventory filtering).
STANDARD_AA: Set[str] = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS",
    "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP",
    "TYR", "VAL",
}


@dataclass
class CoordinationShell:
    """Locally-computed first coordination shell for one metal atom."""
    metal_element: str
    metal_resid: int
    metal_chain: str
    ligating_residues: List[str]   # ["HIS:94:NE2", "CYS:12:SG", ...]
    coord_number: int

    def fingerprint(self) -> str:
        """Short signature: 'Zn: 4xSG(Cys)'."""
        by_atom: dict = {}
        for lig in self.ligating_residues:
            parts = lig.split(":")
            if len(parts) == 3:
                resname, _, atom = parts
                key = f"{atom}({resname.capitalize()})"
                by_atom[key] = by_atom.get(key, 0) + 1
        pieces = [f"{n}x{k}" for k, n in sorted(by_atom.items())]
        return f"{self.metal_element}: " + " + ".join(pieces)


@dataclass
class ParsedStructure:
    """Everything the parser extracts from one PDB, packaged for the classifier."""
    universe: Optional[mda.Universe]
    parser_used: str                       # "mdanalysis" | "biopython" | "failed"
    metal_shells: List[CoordinationShell] = field(default_factory=list)
    hetatm_inventory: List[str] = field(default_factory=list)
    heme_residues: List[str] = field(default_factory=list)
    fes_clusters: dict = field(default_factory=dict)   # {"SF4": 2}
    warnings: List[str] = field(default_factory=list)


def parse_pdb_content(pdb_content: str) -> ParsedStructure:
    """Parse a PDB blob and extract the classifier's local features.

    Best-effort: returns a ParsedStructure with `parser_used="failed"`
    and an empty universe on hard parse errors rather than raising. The
    caller decides whether to refuse or retry.
    """
    u = _load_universe(pdb_content)
    if u is None:
        return ParsedStructure(
            universe=None,
            parser_used="failed",
            warnings=["structure could not be parsed by MDAnalysis"],
        )

    hetatm_inventory, heme_residues, fes_clusters = _audit_hetatms(u)
    metal_shells = _detect_metals(u)

    return ParsedStructure(
        universe=u,
        parser_used="mdanalysis",
        metal_shells=metal_shells,
        hetatm_inventory=hetatm_inventory,
        heme_residues=heme_residues,
        fes_clusters=fes_clusters,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _load_universe(pdb_content: str) -> Optional[mda.Universe]:
    """Parse PDB content from a string. MDAnalysis primary, Bio.PDB fallback TBD."""
    try:
        # MDAnalysis can load from a StringIO when we set format="PDB".
        u = mda.Universe(io.StringIO(pdb_content), format="PDB")
        return u
    except Exception as e:
        logger.warning(f"MDAnalysis parse failed: {e}")
        # TODO: Bio.PDB fallback for structures MDA chokes on.
        # Scaffolded; not wired in v0 because all validation test cases
        # parse cleanly with MDA. Add when a real user-uploaded PDB
        # triggers this path.
        return None


def _atom_chain(atom) -> str:
    """Best-effort chain identifier for an MDAnalysis atom.

    Handles both `chainID` (newer MDA) and `segid` (older MDA with
    PDB chain promoted to segment). Filters known garbage values.
    """
    for attr in ("chainID", "segid"):
        val = getattr(atom, attr, None)
        if val:
            s = str(val).strip()
            if s and s not in ("SYSTEM", "0"):
                return s
    return ""


def _audit_hetatms(u: mda.Universe) -> Tuple[List[str], List[str], dict]:
    """Walk residues and catalog HETATM-style non-standard residues.

    Returns:
        (hetatm_inventory, heme_residues, fes_cluster_counts).
    """
    inventory: Set[str] = set()
    heme: List[str] = []
    fes_counts: dict = {}
    for res in u.residues:
        name = res.resname.strip().upper()
        if name in STANDARD_AA:
            continue
        if name in ("HOH", "WAT", "SOL"):
            continue  # ordinary waters — not worth inventorying
        inventory.add(name)
        if name in HEME_RESIDUES and name not in heme:
            heme.append(name)
        if name in FES_CLUSTER_RESIDUES:
            fes_counts[name] = fes_counts.get(name, 0) + 1
    return sorted(inventory), heme, fes_counts


def _detect_metals(u: mda.Universe) -> List[CoordinationShell]:
    """Find free metal ion HETATMs and compute each one's coordination sphere.

    Uses the metal atom's unique global index to scope the `around`
    selection — required for homo-oligomers where multiple chains share
    residue numbering. Matching on `resid+resname` would collapse all
    copies into a single oversized coordination sphere (bug found via
    2SOD stress test, doc 09).
    """
    shells: List[CoordinationShell] = []
    for res in u.residues:
        resname = res.resname.strip().upper()
        if resname not in METAL_ELEMENTS:
            continue
        metal_atoms = res.atoms
        if len(metal_atoms) != 1:
            continue
        metal = metal_atoms[0]
        chain = _atom_chain(metal)
        try:
            near = u.select_atoms(f"(around {COORD_CUTOFF} index {metal.index})")
        except Exception as e:
            logger.warning(f"coordination selection failed for {resname}@{res.resid}: {e}")
            continue

        ligands: List[str] = []
        for atom in near:
            if atom.resname.strip().upper() in METAL_ELEMENTS:
                continue
            # Only polar donors (N, O, S). Carbon contacts at 3 A are
            # van der Waals, not coordination.
            elem = (
                atom.element.strip().upper()
                if getattr(atom, "element", "")
                else atom.name[0].upper()
            )
            if elem not in ("N", "O", "S"):
                continue
            ligands.append(
                f"{atom.resname.strip()}:{atom.resid}:{atom.name.strip()}"
            )

        shells.append(
            CoordinationShell(
                metal_element=resname,
                metal_resid=int(res.resid),
                metal_chain=chain,
                ligating_residues=ligands,
                coord_number=len(ligands),
            )
        )
    return shells
