"""
Molecule Models

Pydantic models for molecular data structures.
Replaces Dict[str, Any] molecule representations.
"""

from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field, validator


class MolecularProperties(BaseModel):
    """
    Core molecular properties

    Calculated during generation or from PubChem enrichment
    """
    # Basic properties
    molecular_weight: Optional[float] = Field(default=None, ge=0, description="Molecular weight (g/mol)")
    logp: Optional[float] = Field(default=None, description="Partition coefficient (octanol/water)")
    tpsa: Optional[float] = Field(default=None, ge=0, description="Topological polar surface area (Ų)")

    # Hydrogen bonding
    h_bond_donors: Optional[int] = Field(default=None, ge=0, description="Number of H-bond donors")
    h_bond_acceptors: Optional[int] = Field(default=None, ge=0, description="Number of H-bond acceptors")

    # Rotatable bonds
    rotatable_bonds: Optional[int] = Field(default=None, ge=0, description="Number of rotatable bonds")

    # Ring systems
    aromatic_rings: Optional[int] = Field(default=None, ge=0, description="Number of aromatic rings")

    # Drug-likeness scores
    qed: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Quantitative Estimate of Drug-likeness")
    sa_score: Optional[float] = Field(default=None, ge=1.0, le=10.0, description="Synthetic Accessibility score (1-10)")

    # Lipinski's Rule of Five compliance
    lipinski_violations: Optional[int] = Field(default=None, ge=0, le=4, description="Number of Lipinski violations")

    @property
    def is_drug_like(self) -> bool:
        """Check if molecule passes Lipinski's Rule of Five"""
        if self.lipinski_violations is None:
            return True  # Unknown, assume true
        return self.lipinski_violations <= 1  # Allow 1 violation

    class Config:
        json_schema_extra = {
            "example": {
                "molecular_weight": 342.4,
                "logp": 2.5,
                "tpsa": 68.4,
                "h_bond_donors": 2,
                "h_bond_acceptors": 4,
                "qed": 0.78,
                "lipinski_violations": 0
            }
        }


class ADMETProperties(BaseModel):
    """
    ADMET (Absorption, Distribution, Metabolism, Excretion, Toxicity) properties

    Calculated during ADMET screening phase
    """
    # Absorption
    caco2_permeability: Optional[float] = Field(default=None, description="Caco-2 permeability (log cm/s)")
    hia: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Human Intestinal Absorption probability")

    # Distribution
    plasma_protein_binding: Optional[float] = Field(default=None, ge=0.0, le=100.0, description="Plasma protein binding (%)")
    bbb_permeability: Optional[float] = Field(default=None, description="Blood-brain barrier permeability")

    # Metabolism
    cyp_substrate: Optional[Dict[str, bool]] = Field(default=None, description="CYP enzyme substrate predictions")
    cyp_inhibitor: Optional[Dict[str, bool]] = Field(default=None, description="CYP enzyme inhibitor predictions")

    # Excretion
    clearance: Optional[float] = Field(default=None, ge=0, description="Clearance rate (mL/min/kg)")
    half_life: Optional[float] = Field(default=None, ge=0, description="Half-life (hours)")

    # Toxicity
    toxicity: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Overall toxicity score")
    hepatotoxicity: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Hepatotoxicity score")
    cardiotoxicity: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Cardiotoxicity score (hERG)")
    mutagenicity: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Mutagenicity score (Ames)")

    class Config:
        json_schema_extra = {
            "example": {
                "caco2_permeability": -5.2,
                "hia": 0.92,
                "toxicity": 0.15,
                "hepatotoxicity": 0.08,
                "cardiotoxicity": 0.22
            }
        }


class DockingResult(BaseModel):
    """
    Molecular docking result

    Result from autodock-gpu service
    """
    # Binding affinity
    binding_affinity: float = Field(..., description="Binding affinity (kcal/mol)")
    binding_energy: float = Field(..., description="Binding energy (kcal/mol)")

    # Binding modes
    num_binding_modes: int = Field(default=1, ge=1, description="Number of binding modes found")
    best_mode_index: int = Field(default=0, ge=0, description="Index of best binding mode")

    # Coordinates
    binding_pose: Optional[Dict[str, Any]] = Field(default=None, description="3D coordinates of binding pose")

    # Interactions
    hydrogen_bonds: Optional[int] = Field(default=None, ge=0, description="Number of hydrogen bonds")
    hydrophobic_contacts: Optional[int] = Field(default=None, ge=0, description="Number of hydrophobic contacts")

    # Quality
    rmsd: Optional[float] = Field(default=None, ge=0, description="RMSD from reference (if available)")

    class Config:
        json_schema_extra = {
            "example": {
                "binding_affinity": -8.5,
                "binding_energy": -9.2,
                "num_binding_modes": 9,
                "best_mode_index": 0,
                "hydrogen_bonds": 3
            }
        }


class QuantumResult(BaseModel):
    """
    Quantum validation result

    Result from quantum chemistry service (novo-quantum / novomcp-qm)
    """
    # Energies
    homo_energy: Optional[float] = Field(default=None, description="HOMO energy (eV)")
    lumo_energy: Optional[float] = Field(default=None, description="LUMO energy (eV)")
    homo_lumo_gap: Optional[float] = Field(default=None, description="HOMO-LUMO gap (eV)")

    # Binding
    binding_energy: Optional[float] = Field(default=None, description="Quantum binding energy (kcal/mol)")

    # Electronic properties
    dipole_moment: Optional[float] = Field(default=None, ge=0, description="Dipole moment (Debye)")
    polarizability: Optional[float] = Field(default=None, ge=0, description="Polarizability (Ų)")

    # Job metadata
    backend: Optional[str] = Field(default=None, description="Quantum backend used")
    basis_set: Optional[str] = Field(default=None, description="Basis set used")
    job_id: Optional[str] = Field(default=None, description="AWS Braket job ID")

    class Config:
        json_schema_extra = {
            "example": {
                "homo_energy": -6.2,
                "lumo_energy": -1.8,
                "homo_lumo_gap": 4.4,
                "binding_energy": -12.3,
                "backend": "simulator"
            }
        }


class Molecule(BaseModel):
    """
    Complete molecule data structure

    Replaces: Dict[str, Any] molecule representations throughout codebase
    """
    # Identifiers
    id: str = Field(..., description="Unique molecule identifier (generated by system)")
    smiles: str = Field(..., description="SMILES string")
    inchi: Optional[str] = Field(default=None, description="InChI identifier")
    inchi_key: Optional[str] = Field(default=None, description="InChI key")

    # Metadata
    campaign_id: str = Field(..., description="Campaign identifier")
    iteration_number: int = Field(..., ge=0, description="Iteration in which molecule was generated")

    # Source
    source: Optional[str] = Field(default=None, description="Source: 'pubchem', 'generated', 'optimized'")
    pubchem_cid: Optional[int] = Field(default=None, description="PubChem compound ID")

    # Properties
    properties: Optional[MolecularProperties] = Field(default=None, description="Molecular properties")
    admet: Optional[ADMETProperties] = Field(default=None, description="ADMET properties")

    # Validation results
    docking: Optional[DockingResult] = Field(default=None, description="Docking result")
    quantum: Optional[QuantumResult] = Field(default=None, description="Quantum validation result")

    # Compliance
    compliance_passed: Optional[bool] = Field(default=None, description="FAVES compliance status")
    compliance_issues: List[str] = Field(default_factory=list, description="Compliance issues (if any)")

    # Optimization
    optimized_from: Optional[str] = Field(default=None, description="Molecule ID if this is an optimization")
    optimization_score: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Optimization improvement score")

    # Literature
    literature_references: List[str] = Field(default_factory=list, description="PubMed IDs or DOIs")
    known_targets: List[str] = Field(default_factory=list, description="Known protein targets")

    # Quality gates
    gates_passed: List[str] = Field(default_factory=list, description="Quality gates passed")
    gates_failed: List[str] = Field(default_factory=list, description="Quality gates failed")

    # Discovery status
    is_discovery: bool = Field(default=False, description="High-quality discovery flag")
    discovery_score: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Overall discovery score")

    # Timestamps
    created_at: Optional[datetime] = Field(default=None, description="Creation timestamp")
    updated_at: Optional[datetime] = Field(default=None, description="Last update timestamp")

    @validator('smiles')
    def validate_smiles(cls, v):
        """Basic SMILES validation"""
        if not v or len(v) == 0:
            raise ValueError("SMILES cannot be empty")
        # Basic character validation (can be enhanced)
        allowed_chars = set("CNOPSFClBrI[]()=@#+-123456789cnops ")
        if not all(c in allowed_chars for c in v):
            raise ValueError(f"Invalid characters in SMILES: {v}")
        return v

    @property
    def passed_all_gates(self) -> bool:
        """Check if molecule passed all evaluated gates"""
        return len(self.gates_failed) == 0

    @property
    def binding_affinity(self) -> Optional[float]:
        """Get binding affinity (from docking or quantum)"""
        if self.docking:
            return self.docking.binding_affinity
        elif self.quantum:
            return self.quantum.binding_energy
        return None

    class Config:
        json_schema_extra = {
            "example": {
                "id": "mol-abc-123",
                "smiles": "CCO",
                "campaign_id": "campaign-abc-123",
                "iteration_number": 5,
                "source": "pubchem",
                "properties": {
                    "molecular_weight": 46.07,
                    "logp": -0.31,
                    "qed": 0.41
                },
                "admet": {
                    "toxicity": 0.15,
                    "hepatotoxicity": 0.08
                },
                "docking": {
                    "binding_affinity": -6.5
                },
                "gates_passed": ["molecular_constraints", "admet_filters"],
                "is_discovery": True,
                "discovery_score": 0.85
            }
        }


class MoleculeList(BaseModel):
    """
    Container for a list of molecules with metadata

    Used for batch operations and API responses
    """
    molecules: List[Molecule] = Field(..., description="List of molecules")
    total_count: int = Field(..., ge=0, description="Total number of molecules")

    # Metadata
    campaign_id: str = Field(..., description="Campaign identifier")
    iteration_number: int = Field(..., ge=0, description="Iteration number")
    phase: Optional[str] = Field(default=None, description="Phase these molecules are in")

    # Summary statistics
    avg_discovery_score: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Average discovery score")
    discoveries_count: int = Field(default=0, ge=0, description="Number of discoveries")

    @property
    def discovery_rate(self) -> float:
        """Calculate discovery rate"""
        if self.total_count == 0:
            return 0.0
        return self.discoveries_count / self.total_count

    class Config:
        json_schema_extra = {
            "example": {
                "molecules": [
                    {
                        "id": "mol-1",
                        "smiles": "CCO",
                        "campaign_id": "campaign-abc-123",
                        "iteration_number": 5,
                        "is_discovery": True
                    }
                ],
                "total_count": 1,
                "campaign_id": "campaign-abc-123",
                "iteration_number": 5,
                "discoveries_count": 1,
                "avg_discovery_score": 0.85
            }
        }
