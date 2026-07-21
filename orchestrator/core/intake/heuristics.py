"""Donor-atom fallback classifier (doc 07 Option B).

Used when MetalPDB has no hit for a structure (user-uploaded PDB,
AlphaFold model, unreleased entry) OR when MetalPDB returns a hit but
neither Pfam nor EC number gives us a usable role. Refuses by default
for anything outside the explicit rules — "refuse when in doubt" is
the design principle per docs 01 and 07.
"""

from __future__ import annotations

from typing import List, Tuple

from .models import FunctionalRole


def classify_by_donors(
    element: str, ligating_residues: List[str]
) -> Tuple[FunctionalRole, str]:
    """Classify a metal from its coordination sphere donor pattern.

    Args:
        element: Metal element symbol (uppercase, e.g. "ZN", "MG", "CA").
        ligating_residues: List of "RESNAME:RESID:ATOM" strings from the
            local MDAnalysis scan.

    Returns:
        (role, reason). Role is one of: "structural", "catalytic",
        "electron", "transport", "unknown". "unknown" means no rule
        matched — the caller should treat this as refuse-worthy.
    """
    donors = [lig.split(":")[-1] for lig in ligating_residues]
    resnames = [lig.split(":")[0] for lig in ligating_residues]
    n_sg_cys = sum(1 for d, r in zip(donors, resnames) if d == "SG" and r == "CYS")
    n_his = sum(1 for r in resnames if r == "HIS")
    n_asp_glu = sum(1 for r in resnames if r in ("ASP", "GLU"))
    n_phosphate = sum(
        1 for r in resnames if r in ("GDP", "GTP", "ADP", "ATP", "GNP", "GSP")
    )

    if element == "ZN":
        # Structural zinc fingers: Zn4Cys (TFIIIA-type variants),
        # Cys2His2 (dominant C2H2), or Cys3His (GATA-type).
        if n_sg_cys >= 4:
            return "structural", "Zn4Cys zinc finger pattern"
        if n_sg_cys >= 2 and n_his >= 2:
            return "structural", "Zn Cys2His2 zinc finger pattern (C2H2)"
        if n_sg_cys >= 3 and n_his >= 1:
            return "structural", "Zn Cys3His zinc finger pattern"
        # Catalytic hydrolase patterns.
        if n_his >= 3:
            return "catalytic", "Zn 3xHis pattern (carbonic-anhydrase / MMP-like)"
        if n_his >= 2 and n_asp_glu >= 1:
            return "catalytic", "Zn His/Asp/Glu hydrolase-like pattern"

    if element == "CA" and n_asp_glu >= 3:
        return "structural", "Ca with >=3 Asp/Glu donors (EF-hand-like)"

    if element == "MG":
        if n_phosphate >= 1:
            return "catalytic", "Mg coordinating nucleotide phosphate (kinase/GTPase-like)"
        return "catalytic", "Mg default to catalytic (usually phosphate-bound)"

    if element in ("FE", "CU", "MN", "NI", "CO"):
        return "unknown", f"{element} default refuse pending ML/MM branch"

    return "unknown", f"{element} coordination pattern not in heuristic table"
