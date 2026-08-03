"""
Pre-defined field mapping templates for common tool→connector combinations.

Template key format: "{source_tool}:{connector_type}" or "{source_tool}:*" for generic.
Each template is a list of mapping dicts with source, target, transform, and default fields.
"""

# Generic templates work across all connector types
# Connector-specific templates override column names for that system

MAPPING_TEMPLATES = {
    # =========================================================================
    # get_molecule_profile → Generic (works for all connectors)
    # =========================================================================
    "get_molecule_profile:*": [
        {"source": "smiles", "target": "smiles"},
        {"source": "properties.molecular_weight", "target": "molecular_weight", "transform": "round_2"},
        {"source": "properties.molecular_formula", "target": "molecular_formula"},
        {"source": "properties.logp", "target": "logp", "transform": "round_3"},
        {"source": "properties.tpsa", "target": "tpsa", "transform": "round_2"},
        {"source": "properties.num_h_acceptors", "target": "h_bond_acceptors", "transform": "to_int"},
        {"source": "properties.num_h_donors", "target": "h_bond_donors", "transform": "to_int"},
        {"source": "properties.num_rotatable_bonds", "target": "rotatable_bonds", "transform": "to_int"},
        {"source": "properties.num_rings", "target": "num_rings", "transform": "to_int"},
        {"source": "properties.qed", "target": "qed_score", "transform": "round_3"},
        {"source": "properties.sa_score", "target": "sa_score", "transform": "round_2"},
        {"source": "properties.lipinski_violations", "target": "lipinski_violations", "transform": "to_int"},
        {"source": "compliance.overall_status", "target": "compliance_status"},
        {"source": "compliance.flags", "target": "compliance_flags", "transform": "json_stringify"},
    ],

    # get_molecule_profile → Snowflake (uppercase column convention)
    "get_molecule_profile:snowflake": [
        {"source": "smiles", "target": "SMILES"},
        {"source": "properties.molecular_weight", "target": "MOLECULAR_WEIGHT", "transform": "round_2"},
        {"source": "properties.molecular_formula", "target": "MOLECULAR_FORMULA"},
        {"source": "properties.logp", "target": "LOGP", "transform": "round_3"},
        {"source": "properties.tpsa", "target": "TPSA", "transform": "round_2"},
        {"source": "properties.num_h_acceptors", "target": "H_BOND_ACCEPTORS", "transform": "to_int"},
        {"source": "properties.num_h_donors", "target": "H_BOND_DONORS", "transform": "to_int"},
        {"source": "properties.num_rotatable_bonds", "target": "ROTATABLE_BONDS", "transform": "to_int"},
        {"source": "properties.num_rings", "target": "NUM_RINGS", "transform": "to_int"},
        {"source": "properties.qed", "target": "QED_SCORE", "transform": "round_3"},
        {"source": "properties.sa_score", "target": "SA_SCORE", "transform": "round_2"},
        {"source": "properties.lipinski_violations", "target": "LIPINSKI_VIOLATIONS", "transform": "to_int"},
        {"source": "compliance.overall_status", "target": "COMPLIANCE_STATUS"},
        {"source": "compliance.flags", "target": "COMPLIANCE_FLAGS", "transform": "json_stringify"},
    ],

    # get_molecule_profile → Databricks (snake_case convention)
    "get_molecule_profile:databricks": [
        {"source": "smiles", "target": "smiles"},
        {"source": "properties.molecular_weight", "target": "molecular_weight", "transform": "round_2"},
        {"source": "properties.molecular_formula", "target": "molecular_formula"},
        {"source": "properties.logp", "target": "logp", "transform": "round_3"},
        {"source": "properties.tpsa", "target": "tpsa", "transform": "round_2"},
        {"source": "properties.num_h_acceptors", "target": "h_bond_acceptors", "transform": "to_int"},
        {"source": "properties.num_h_donors", "target": "h_bond_donors", "transform": "to_int"},
        {"source": "properties.num_rotatable_bonds", "target": "rotatable_bonds", "transform": "to_int"},
        {"source": "properties.num_rings", "target": "num_rings", "transform": "to_int"},
        {"source": "properties.qed", "target": "qed_score", "transform": "round_3"},
        {"source": "properties.sa_score", "target": "sa_score", "transform": "round_2"},
        {"source": "properties.lipinski_violations", "target": "lipinski_violations", "transform": "to_int"},
        {"source": "compliance.overall_status", "target": "compliance_status"},
        {"source": "compliance.flags", "target": "compliance_flags", "transform": "json_stringify"},
    ],

    # =========================================================================
    # predict_admet → Generic
    # =========================================================================
    "predict_admet:*": [
        {"source": "smiles", "target": "smiles"},
        {"source": "predictions.absorption.hia", "target": "hia_probability", "transform": "round_3"},
        {"source": "predictions.absorption.caco2", "target": "caco2_permeability", "transform": "round_3"},
        {"source": "predictions.absorption.pgp_substrate", "target": "pgp_substrate", "transform": "round_3"},
        {"source": "predictions.absorption.bioavailability", "target": "bioavailability", "transform": "round_3"},
        {"source": "predictions.distribution.vdss", "target": "vdss", "transform": "round_3"},
        {"source": "predictions.distribution.bbb", "target": "bbb_permeability", "transform": "round_3"},
        {"source": "predictions.distribution.ppb", "target": "plasma_protein_binding", "transform": "round_3"},
        {"source": "predictions.metabolism.cyp2d6_inhibitor", "target": "cyp2d6_inhibitor", "transform": "round_3"},
        {"source": "predictions.metabolism.cyp3a4_inhibitor", "target": "cyp3a4_inhibitor", "transform": "round_3"},
        {"source": "predictions.metabolism.cyp2c9_inhibitor", "target": "cyp2c9_inhibitor", "transform": "round_3"},
        {"source": "predictions.excretion.clearance", "target": "clearance", "transform": "round_3"},
        {"source": "predictions.excretion.half_life", "target": "half_life", "transform": "round_3"},
        {"source": "predictions.toxicity.herg", "target": "herg_inhibition", "transform": "round_3"},
        {"source": "predictions.toxicity.ames", "target": "ames_mutagenicity", "transform": "round_3"},
        {"source": "predictions.toxicity.ld50", "target": "ld50", "transform": "round_3"},
        {"source": "predictions.toxicity.hepatotoxicity", "target": "hepatotoxicity", "transform": "round_3"},
    ],

    # predict_admet → Snowflake
    "predict_admet:snowflake": [
        {"source": "smiles", "target": "SMILES"},
        {"source": "predictions.absorption.hia", "target": "HIA_PROBABILITY", "transform": "round_3"},
        {"source": "predictions.absorption.caco2", "target": "CACO2_PERMEABILITY", "transform": "round_3"},
        {"source": "predictions.absorption.bioavailability", "target": "BIOAVAILABILITY", "transform": "round_3"},
        {"source": "predictions.distribution.vdss", "target": "VDSS", "transform": "round_3"},
        {"source": "predictions.distribution.bbb", "target": "BBB_PERMEABILITY", "transform": "round_3"},
        {"source": "predictions.distribution.ppb", "target": "PLASMA_PROTEIN_BINDING", "transform": "round_3"},
        {"source": "predictions.metabolism.cyp2d6_inhibitor", "target": "CYP2D6_INHIBITOR", "transform": "round_3"},
        {"source": "predictions.metabolism.cyp3a4_inhibitor", "target": "CYP3A4_INHIBITOR", "transform": "round_3"},
        {"source": "predictions.excretion.clearance", "target": "CLEARANCE", "transform": "round_3"},
        {"source": "predictions.excretion.half_life", "target": "HALF_LIFE", "transform": "round_3"},
        {"source": "predictions.toxicity.herg", "target": "HERG_INHIBITION", "transform": "round_3"},
        {"source": "predictions.toxicity.ames", "target": "AMES_MUTAGENICITY", "transform": "round_3"},
        {"source": "predictions.toxicity.hepatotoxicity", "target": "HEPATOTOXICITY", "transform": "round_3"},
    ],

    # =========================================================================
    # optimize_molecule → Generic
    # =========================================================================
    "optimize_molecule:*": [
        {"source": "smiles", "target": "original_smiles"},
        {"source": "variant_smiles", "target": "variant_smiles"},
        {"source": "variant_index", "target": "variant_index", "transform": "to_int"},
        {"source": "qed", "target": "qed_score", "transform": "round_3"},
        {"source": "logp", "target": "logp", "transform": "round_3"},
        {"source": "sa_score", "target": "sa_score", "transform": "round_2"},
        {"source": "similarity", "target": "tanimoto_similarity", "transform": "round_3"},
        {"source": "molecular_weight", "target": "molecular_weight", "transform": "round_2"},
        {"source": "compliance_status", "target": "compliance_status"},
    ],

    # optimize_molecule → Snowflake
    "optimize_molecule:snowflake": [
        {"source": "smiles", "target": "ORIGINAL_SMILES"},
        {"source": "variant_smiles", "target": "VARIANT_SMILES"},
        {"source": "variant_index", "target": "VARIANT_INDEX", "transform": "to_int"},
        {"source": "qed", "target": "QED_SCORE", "transform": "round_3"},
        {"source": "logp", "target": "LOGP", "transform": "round_3"},
        {"source": "sa_score", "target": "SA_SCORE", "transform": "round_2"},
        {"source": "similarity", "target": "TANIMOTO_SIMILARITY", "transform": "round_3"},
        {"source": "molecular_weight", "target": "MOLECULAR_WEIGHT", "transform": "round_2"},
        {"source": "compliance_status", "target": "COMPLIANCE_STATUS"},
    ],

    # =========================================================================
    # search_similar → Generic
    # =========================================================================
    "search_similar:*": [
        {"source": "query_smiles", "target": "query_smiles"},
        {"source": "smiles", "target": "similar_smiles"},
        {"source": "similarity", "target": "tanimoto_similarity", "transform": "round_3"},
        {"source": "molecular_weight", "target": "molecular_weight", "transform": "round_2"},
        {"source": "logp", "target": "logp", "transform": "round_3"},
        {"source": "qed", "target": "qed_score", "transform": "round_3"},
    ],

    # =========================================================================
    # filter_molecules → Generic
    # =========================================================================
    "filter_molecules:*": [
        {"source": "smiles", "target": "smiles"},
        {"source": "molecular_weight", "target": "molecular_weight", "transform": "round_2"},
        {"source": "logp", "target": "logp", "transform": "round_3"},
        {"source": "qed", "target": "qed_score", "transform": "round_3"},
        {"source": "sa_score", "target": "sa_score", "transform": "round_2"},
        {"source": "tpsa", "target": "tpsa", "transform": "round_2"},
        {"source": "num_h_acceptors", "target": "h_bond_acceptors", "transform": "to_int"},
        {"source": "num_h_donors", "target": "h_bond_donors", "transform": "to_int"},
    ],

    # =========================================================================
    # check_compliance → Generic
    # =========================================================================
    "check_compliance:*": [
        {"source": "smiles", "target": "smiles"},
        {"source": "overall_status", "target": "compliance_status"},
        {"source": "dea_status", "target": "dea_status"},
        {"source": "fda_status", "target": "fda_status"},
        {"source": "cwc_status", "target": "cwc_status"},
        {"source": "epa_status", "target": "epa_status"},
        {"source": "eu_reach_status", "target": "eu_reach_status"},
        {"source": "risk_level", "target": "risk_level"},
        {"source": "flags", "target": "flags", "transform": "json_stringify"},
    ],

    # =========================================================================
    # search_literature → Generic
    # =========================================================================
    "search_literature:*": [
        {"source": "title", "target": "title"},
        {"source": "authors", "target": "authors", "transform": "json_stringify"},
        {"source": "abstract", "target": "abstract"},
        {"source": "year", "target": "publication_year", "transform": "to_int"},
        {"source": "doi", "target": "doi"},
        {"source": "relevance_score", "target": "relevance_score", "transform": "round_3"},
        {"source": "source", "target": "source"},
    ],

    # =========================================================================
    # Benchling-specific templates (entity-oriented)
    # =========================================================================
    "get_molecule_profile:benchling": [
        {"source": "smiles", "target": "SMILES"},
        {"source": "properties.molecular_weight", "target": "Molecular Weight", "transform": "round_2"},
        {"source": "properties.molecular_formula", "target": "Molecular Formula"},
        {"source": "properties.logp", "target": "LogP", "transform": "round_3"},
        {"source": "properties.tpsa", "target": "TPSA", "transform": "round_2"},
        {"source": "properties.qed", "target": "QED Score", "transform": "round_3"},
        {"source": "properties.sa_score", "target": "SA Score", "transform": "round_2"},
        {"source": "properties.lipinski_violations", "target": "Lipinski Violations", "transform": "to_int"},
        {"source": "compliance.overall_status", "target": "Compliance Status"},
    ],
}
