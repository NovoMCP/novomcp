"""
Project Enricher for NovoMCP
Automatically enriches projects with tags, metadata, and classifications
"""

import logging
import re
from typing import Dict, Any, List, Optional
from .azure_openai_client import AzureOpenAIClient

logger = logging.getLogger(__name__)

class ProjectEnricher:
    """Enriches research projects with AI-generated metadata"""
    
    def __init__(self, ai_client: Optional[AzureOpenAIClient] = None):
        """Initialize with AI client"""
        self.ai_client = ai_client or AzureOpenAIClient()
        
        # Predefined categories for quick matching
        self.therapeutic_areas = {
            "oncology": ["cancer", "tumor", "carcinoma", "lymphoma", "leukemia", "kras", "braf", "egfr"],
            "neurology": ["alzheimer", "parkinson", "epilepsy", "cns", "brain", "neurodegeneration", "amyloid"],
            "cardiovascular": ["heart", "cardiac", "hypertension", "atherosclerosis", "cholesterol", "ldl"],
            "metabolic": ["diabetes", "obesity", "nafld", "insulin", "glucose", "metabolic"],
            "infectious": ["antibiotic", "antiviral", "antimicrobial", "bacteria", "virus", "infection"],
            "immunology": ["immune", "inflammation", "autoimmune", "cytokine", "antibody", "car-t"],
            "respiratory": ["asthma", "copd", "lung", "pulmonary", "respiratory", "cystic fibrosis"],
            "rare_diseases": ["orphan", "rare disease", "genetic disorder", "inherited"]
        }
        
        self.drug_modalities = {
            "small_molecule": ["small molecule", "compound", "inhibitor", "agonist", "antagonist"],
            "antibody": ["antibody", "mab", "monoclonal", "bispecific"],
            "protein": ["protein", "peptide", "enzyme", "fusion protein"],
            "gene_therapy": ["gene therapy", "crispr", "aav", "gene editing"],
            "cell_therapy": ["car-t", "cell therapy", "stem cell", "tcr"],
            "rna": ["rna", "sirna", "mirna", "antisense", "mrna"],
            "vaccine": ["vaccine", "immunization", "prophylactic"]
        }
        
        self.development_stages = {
            "discovery": ["discovery", "early stage", "hit identification", "lead optimization"],
            "preclinical": ["preclinical", "in vitro", "in vivo", "animal model"],
            "clinical": ["clinical", "phase", "trial", "patient", "human"],
            "approved": ["approved", "marketed", "commercial", "fda approved"]
        }
    
    async def enrich(self, project_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enrich project with AI-generated metadata
        
        Args:
            project_data: Basic project information
            
        Returns:
            Enriched project data with tags and metadata
        """
        
        # Start with original data
        enriched = project_data.copy()
        
        # Quick enrichment from patterns
        quick_tags = self._extract_quick_tags(project_data)
        enriched["tags"] = quick_tags
        
        # Determine therapeutic area
        therapeutic_area = self._identify_therapeutic_area(project_data)
        if therapeutic_area:
            enriched["therapeutic_area"] = therapeutic_area
        
        # Identify drug modality
        modality = self._identify_drug_modality(project_data)
        if modality:
            enriched["drug_modality"] = modality
        
        # Development stage
        stage = self._identify_development_stage(project_data)
        if stage:
            enriched["development_stage"] = stage
        
        # Use AI for comprehensive enrichment if available
        if self.ai_client.available and self._needs_ai_enrichment(project_data):
            ai_enrichment = await self._ai_enrich_project(project_data)
            if ai_enrichment:
                # Merge AI enrichment
                enriched = self._merge_enrichments(enriched, ai_enrichment)
        
        # Add metadata
        enriched["metadata"] = self._generate_metadata(enriched)
        
        return enriched
    
    def _extract_quick_tags(self, project_data: Dict[str, Any]) -> List[str]:
        """Extract tags from project name and description"""
        tags = set()
        
        text = f"{project_data.get('name', '')} {project_data.get('description', '')}".lower()
        
        # Extract protein/gene names (uppercase sequences)
        protein_pattern = r'\b[A-Z]{2,}[0-9]*[A-Z]*\b'
        proteins = re.findall(protein_pattern, project_data.get('description', ''))
        tags.update(proteins)
        
        # Extract disease names
        diseases = ["cancer", "diabetes", "alzheimer", "parkinson", "covid", "influenza"]
        for disease in diseases:
            if disease in text:
                tags.add(disease)
        
        # Extract drug types
        drug_types = ["inhibitor", "agonist", "antagonist", "antibody", "vaccine"]
        for drug_type in drug_types:
            if drug_type in text:
                tags.add(drug_type)
        
        # Extract mutation patterns
        mutation_pattern = r'[A-Z]\d+[A-Z]'
        mutations = re.findall(mutation_pattern, project_data.get('description', ''))
        tags.update(mutations)
        
        return list(tags)
    
    def _identify_therapeutic_area(self, project_data: Dict[str, Any]) -> Optional[str]:
        """Identify therapeutic area from project data"""
        text = f"{project_data.get('name', '')} {project_data.get('description', '')}".lower()
        
        scores = {}
        for area, keywords in self.therapeutic_areas.items():
            score = sum(1 for keyword in keywords if keyword in text)
            if score > 0:
                scores[area] = score
        
        if scores:
            return max(scores, key=scores.get)
        
        return None
    
    def _identify_drug_modality(self, project_data: Dict[str, Any]) -> Optional[str]:
        """Identify drug modality from project data"""
        text = f"{project_data.get('name', '')} {project_data.get('description', '')}".lower()
        
        for modality, keywords in self.drug_modalities.items():
            for keyword in keywords:
                if keyword in text:
                    return modality
        
        return "small_molecule"  # Default
    
    def _identify_development_stage(self, project_data: Dict[str, Any]) -> Optional[str]:
        """Identify development stage from project data"""
        text = f"{project_data.get('name', '')} {project_data.get('description', '')}".lower()
        
        for stage, keywords in self.development_stages.items():
            for keyword in keywords:
                if keyword in text:
                    return stage
        
        return "discovery"  # Default for new projects
    
    def _needs_ai_enrichment(self, project_data: Dict[str, Any]) -> bool:
        """Check if project needs AI enrichment"""
        # Use AI if:
        # - Description is long and complex
        # - No therapeutic area identified
        # - User requests comprehensive analysis
        
        description = project_data.get('description', '')
        return (
            len(description) > 100 or
            'therapeutic_area' not in project_data or
            project_data.get('comprehensive_analysis', False)
        )
    
    async def _ai_enrich_project(self, project_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Use AI to comprehensively enrich project"""
        
        system_prompt = """You are a pharmaceutical research expert analyzing research projects.
        Extract and generate comprehensive metadata for the project.
        
        Return JSON format:
        {
            "tags": ["list", "of", "relevant", "tags"],
            "therapeutic_area": "primary therapeutic area",
            "secondary_therapeutic_areas": ["other", "relevant", "areas"],
            "target_proteins": ["PROTEIN1", "PROTEIN2"],
            "disease_areas": ["specific diseases"],
            "drug_modality": "small_molecule/antibody/protein/etc",
            "development_stage": "discovery/preclinical/clinical",
            "key_mutations": ["G12C", "V600E"],
            "biomarkers": ["biomarker1", "biomarker2"],
            "related_pathways": ["pathway1", "pathway2"],
            "potential_indications": ["indication1", "indication2"],
            "research_approach": "brief description of approach",
            "innovation_score": 0.85,
            "complexity_score": 0.75,
            "success_probability": 0.65
        }
        """
        
        from core.prompt_sanitizer import sanitize_for_prompt
        _proj_name = sanitize_for_prompt(project_data.get('name', 'Unnamed'), 'project.name', 200)
        _proj_desc = sanitize_for_prompt(project_data.get('description', 'No description'), 'project.description', 2000)
        _proj_info = sanitize_for_prompt(project_data.get('additional_info', 'None'), 'project.additional_info', 2000)
        prompt = f"""Analyze this pharmaceutical research project and extract comprehensive metadata:

        Project Name: {_proj_name}
        Description: {_proj_desc}
        Additional Info: {_proj_info}
        
        Provide detailed tags, classifications, and metadata for this project.
        """
        
        result = await self.ai_client.complete(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.3,
            response_format={"type": "json_object"}
        )
        
        if result["success"] and result["response"]:
            parsed = await self.ai_client.parse_json_response(result["response"])
            if parsed:
                parsed["ai_generated"] = True
                parsed["ai_tokens"] = result.get("tokens")
                return parsed
        
        return None
    
    def _merge_enrichments(self, base: Dict, ai_enrichment: Dict) -> Dict:
        """Merge AI enrichment with base enrichment"""
        merged = base.copy()
        
        # Merge tags (union)
        base_tags = set(base.get("tags", []))
        ai_tags = set(ai_enrichment.get("tags", []))
        merged["tags"] = list(base_tags.union(ai_tags))
        
        # Take AI values for fields not already set
        for field in ["therapeutic_area", "drug_modality", "development_stage"]:
            if field in ai_enrichment and field not in base:
                merged[field] = ai_enrichment[field]
        
        # Add AI-specific fields
        ai_fields = [
            "secondary_therapeutic_areas", "target_proteins", "disease_areas",
            "key_mutations", "biomarkers", "related_pathways", "potential_indications",
            "research_approach", "innovation_score", "complexity_score", "success_probability"
        ]
        
        for field in ai_fields:
            if field in ai_enrichment:
                merged[field] = ai_enrichment[field]
        
        # Mark as AI-enriched
        merged["ai_enriched"] = True
        
        return merged
    
    def _generate_metadata(self, enriched_project: Dict) -> Dict[str, Any]:
        """Generate additional metadata"""
        metadata = {
            "enrichment_version": "1.0",
            "enrichment_method": "ai" if enriched_project.get("ai_enriched") else "pattern_matching",
            "tag_count": len(enriched_project.get("tags", [])),
            "has_therapeutic_area": "therapeutic_area" in enriched_project,
            "has_target_proteins": "target_proteins" in enriched_project,
            "completeness_score": self._calculate_completeness(enriched_project)
        }
        
        # Add risk assessment
        if enriched_project.get("drug_modality") == "small_molecule":
            metadata["typical_timeline"] = "10-15 years"
            metadata["typical_success_rate"] = "5-10%"
        elif enriched_project.get("drug_modality") == "antibody":
            metadata["typical_timeline"] = "8-12 years"
            metadata["typical_success_rate"] = "15-20%"
        
        return metadata
    
    def _calculate_completeness(self, project: Dict) -> float:
        """Calculate how complete the project enrichment is"""
        required_fields = [
            "name", "description", "tags", "therapeutic_area",
            "drug_modality", "development_stage"
        ]
        
        optional_fields = [
            "target_proteins", "disease_areas", "biomarkers",
            "related_pathways", "potential_indications"
        ]
        
        # Required fields worth 60%
        required_score = sum(1 for field in required_fields if field in project) / len(required_fields) * 0.6
        
        # Optional fields worth 40%
        optional_score = sum(1 for field in optional_fields if field in project) / len(optional_fields) * 0.4
        
        return round(required_score + optional_score, 2)
    
    async def suggest_similar_projects(self, project_data: Dict) -> List[str]:
        """Suggest similar projects based on enriched data"""
        suggestions = []
        
        # This would query the database for similar projects
        # For now, return placeholder suggestions
        if project_data.get("therapeutic_area") == "oncology":
            suggestions = [
                "KRAS G12C inhibitor development",
                "PD-L1 checkpoint inhibitor optimization",
                "CAR-T cell therapy for solid tumors"
            ]
        
        return suggestions