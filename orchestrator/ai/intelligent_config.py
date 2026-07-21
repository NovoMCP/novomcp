"""
Intelligent Service Configuration Builder
Configures services based on campaign context, literature, and learning patterns
"""

import logging
from typing import Dict, Any, List, Optional
import json

logger = logging.getLogger(__name__)


class ServiceConfigBuilder:
    """Builds intelligent service configurations based on campaign context"""

    def __init__(self, campaign_config: Dict[str, Any], azure_client=None):
        self.campaign_id = campaign_config.get('id') or campaign_config.get('campaign_id')
        self.config = campaign_config

        # Normalize goal to dict (handle case where it's a JSON string)
        goal = campaign_config.get('goal') or {}
        if isinstance(goal, str):
            try:
                goal = json.loads(goal)
            except:
                goal = {'description': goal}  # If parsing fails, treat as description string
        self.goal = goal

        # For fragment detection: combine goal description AND campaign name
        # This ensures we detect fragments even if only the name contains "fragment"
        campaign_name = campaign_config.get('name', '').lower()
        goal_description = self.goal.get('description', '').lower()
        self.campaign_goal = f"{campaign_name} {goal_description}"  # Combine for fragment detection

        # Normalize constraints to dict (handle JSON string)
        constraints = campaign_config.get('constraints') or {}
        if isinstance(constraints, str):
            try:
                constraints = json.loads(constraints)
            except:
                constraints = {}
        self.constraints = constraints
        # Normalize molecular constraint keys: accept both 'logP' and 'logp'
        try:
            mol = self.constraints.get('molecular') or {}
            if isinstance(mol, dict):
                # If only one variant exists, mirror it to the other for consistency across readers
                if 'logP' in mol and 'logp' not in mol:
                    mol['logp'] = mol['logP']
                elif 'logp' in mol and 'logP' not in mol:
                    mol['logP'] = mol['logp']
                self.constraints['molecular'] = mol
        except Exception:
            pass

        # Normalize dataSources to dict
        data_sources = campaign_config.get('dataSources') or {}
        if isinstance(data_sources, str):
            try:
                data_sources = json.loads(data_sources)
            except:
                data_sources = {}
        self.data_sources = data_sources

        # Normalize autonomy to dict
        autonomy = campaign_config.get('autonomy') or {}
        if isinstance(autonomy, str):
            try:
                autonomy = json.loads(autonomy)
            except:
                autonomy = {}
        self.autonomy = autonomy

        self.azure_client = azure_client  # For AI-powered configuration

    async def build_generation_config(
        self,
        phase: str,
        iteration: int,
        failure_context: Optional[Dict[str, Any]] = None,
        seed_molecules: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Build molecular-intelligence generation configuration using AI for high-value parameters.

        PRODUCTION: molecular-intelligence (PubChem enriched - 115M molecules, 53 pre-calculated columns)
        - Drug-Like partition (mw_200_400): Initial generation (broad exploration)
        - Lead-Like partition (mw_0_200): Iterative refinement (optimization)
        - All molecules include: 11 PubChem + 3 Chem-Props + 39 ADMET columns

        AI tunes (high-value):
        - batch_size: Number of molecules to generate (up to 1000)
        - LogP/MW/TPSA/QED/toxicity ranges: Therapeutic area-specific tuning
        - Partition selection: Automatic based on seed_molecules presence

        Args:
            phase: Current workflow phase
            iteration: Phase iteration number
            failure_context: Optional gate failure context for reconfiguration
            seed_molecules: Top candidates from previous iteration (triggers Lead-Like partition)

        Returns:
            Dict with:
            - config: molecular-intelligence API request body (matches CompatibleGenerationRequest)
            - literature_context: Pinecone literature search results
            - ai_reasoning: GPT-5 strategic reasoning (for compliance logging)
        """
        # Get molecular constraints from wizard (Step 2 - Lipinski rules)
        molecular = self.constraints.get("molecular", {})

        # Load persistent locks from campaign metadata (if user locked constraints via API)
        metadata = self.config.get("metadata", {})
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except:
                metadata = {}
        persistent_locks = metadata.get("constraint_locks", {})
        logger.info(f"Campaign {self.campaign_id}: Loaded {len(persistent_locks)} persistent constraint lock(s) from database")

        # Query Pinecone for literature context
        literature_context = await self._get_literature_context()

        # Query successful patterns from Research DB (if reconfiguration)
        pattern_context = None
        if failure_context:
            pattern_context = await self._query_successful_patterns(failure_context)

        # Use AI to tune high-value parameters
        if self.azure_client:
            ai_tuning = await self._ai_tune_generation(
                phase=phase,
                iteration=iteration,
                molecular_constraints=molecular,
                literature_context=literature_context,
                pattern_context=pattern_context,
                failure_context=failure_context
            )
        else:
            # Fallback to rule-based tuning
            ai_tuning = self._fallback_tuning(phase, iteration)

        # CRITICAL: Respect user constraints when present (lock + source mapping)
        campaign_mw_min = (molecular.get("mw", {}) or {}).get("min")
        campaign_mw_max = (molecular.get("mw", {}) or {}).get("max")
        # Accept both 'logP' and 'logp' for user-provided values (normalized in __init__)
        user_logp = molecular.get("logP") or molecular.get("logp") or {}
        campaign_logp_min = user_logp.get("min") if isinstance(user_logp, dict) else None
        campaign_logp_max = user_logp.get("max") if isinstance(user_logp, dict) else None

        # Determine if this is a fragment campaign (MW < 300)
        is_fragment = (campaign_mw_max and campaign_mw_max <= 300) or \
                     any(kw in self.campaign_goal.lower() for kw in ['fragment', 'fbdd', 'fragment-based', 'hinge binder'])

        # Set appropriate defaults based on campaign type
        if is_fragment:
            default_mw_min = 100
            default_mw_max = 250
            default_logp_min = -0.5
            default_logp_max = 3.0
            logger.info(f"Campaign {self.campaign_id}: Fragment campaign detected - using fragment defaults (MW {campaign_mw_min or default_mw_min}-{campaign_mw_max or default_mw_max} Da)")
        else:
            default_mw_min = 200
            default_mw_max = 500
            default_logp_min = -0.4
            default_logp_max = 5.6

        # Effective constraints: user-specified values override AI tuning
        effective_mw_min = campaign_mw_min if campaign_mw_min is not None else ai_tuning.get("mw_min", default_mw_min)
        effective_mw_max = campaign_mw_max if campaign_mw_max is not None else ai_tuning.get("mw_max", default_mw_max)

        # Determine dataset partition based on effective MW constraints
        # Use strict partition boundaries: fragments for max <= 200 (mw_0_200)
        if is_fragment or (effective_mw_max is not None and effective_mw_max <= 200):
            dataset_preference = "fragments"  # Use mw_0_200 partition for fragments
            logger.info(f"Campaign {self.campaign_id}: Fragment campaign - will query mw_0_200 partition (max MW {effective_mw_max})")
        elif effective_mw_max is not None and effective_mw_max <= 400:
            dataset_preference = "drug-like"  # Use mw_200_400 partition
        elif effective_mw_max is not None and effective_mw_max <= 600:
            dataset_preference = "boutique"  # Use mw_400_600 partition
        else:
            dataset_preference = "auto"  # Let service decide

        # Build molecular-intelligence API request (matches CompatibleGenerationRequest)
        # Build sources + locks metadata
        source_mw_min = 'user' if campaign_mw_min is not None else ('ai' if ai_tuning.get('mw_min') is not None else 'default')
        source_mw_max = 'user' if campaign_mw_max is not None else ('ai' if ai_tuning.get('mw_max') is not None else 'default')
        source_logp_min = 'user' if campaign_logp_min is not None else ('ai' if ai_tuning.get('logp_min') is not None else 'default')
        source_logp_max = 'user' if campaign_logp_max is not None else ('ai' if ai_tuning.get('logp_max') is not None else 'default')

        # Build locks: Start with auto-locks (user provided value), then merge persistent locks from API
        locks = {
            'molecular': {
                'mw': {
                    'min': campaign_mw_min is not None,
                    'max': campaign_mw_max is not None
                },
                'logp': {
                    'min': campaign_logp_min is not None,
                    'max': campaign_logp_max is not None
                }
            }
        }

        # Merge persistent locks from database (API-set locks take precedence)
        # Persistent locks format: {"molecular.mw.max": true, "molecular.logp.min": false}
        for field_path, is_locked in persistent_locks.items():
            parts = field_path.split('.')
            if len(parts) == 3:  # e.g., "molecular.mw.max"
                category, constraint, bound = parts
                if category not in locks:
                    locks[category] = {}
                if constraint not in locks[category]:
                    locks[category][constraint] = {}
                locks[category][constraint][bound] = is_locked
                logger.info(f"Campaign {self.campaign_id}: Applied persistent lock {field_path} = {is_locked}")
            else:
                logger.warning(f"Campaign {self.campaign_id}: Invalid lock field path '{field_path}' (expected format: category.constraint.bound)")


        sources = {
            'molecular': {
                'mw': {'min': source_mw_min, 'max': source_mw_max},
                'logp': {'min': source_logp_min, 'max': source_logp_max}
            }
        }

        config = {
            # --- AI-tuned parameters ---
            "batch_size": ai_tuning.get("batch_size", 1000),  # PRODUCTION: 1000 molecules per call
            "algorithm": "pubchem-enriched-sampling",  # PubChem enriched data with 53 pre-calculated columns

            # --- CRITICAL: Partition selection for correct MW range ---
            "dataset_preference": dataset_preference,  # Routes to correct MW partition

            # --- Iterative refinement (triggers Lead-Like partition) ---
            "seed_molecules": seed_molecules if seed_molecules else None,  # Top candidates from previous iteration

            # --- Molecular constraints (molecular-intelligence format) ---
            "constraints": {
                "molecular": {
                    "mw": {
                        "min": effective_mw_min,
                        "max": effective_mw_max
                    },
                    "logp": {
                        # Respect user-provided logP if present; otherwise use AI/defaults
                        "min": campaign_logp_min if campaign_logp_min is not None else ai_tuning.get("logp_min", default_logp_min),
                        "max": campaign_logp_max if campaign_logp_max is not None else ai_tuning.get("logp_max", default_logp_max)
                    }
                },
                "force_druglike": not is_fragment,  # Disable drug-like filters for fragments
                "allowed_elements": ["C", "N", "O", "S", "F", "Cl", "Br"]
            },
            # --- Constraint provenance & locks for downstream logic ---
            "constraints_meta": {
                "sources": sources,
                "locks": locks
            }
        }

        logger.info(f"Built molecular-intelligence config for campaign {self.campaign_id}: "
                   f"batch_size={config['batch_size']}, algorithm=pubchem-enriched-sampling, "
                   f"seed_molecules={len(seed_molecules) if seed_molecules else 0}, "
                   f"dataset_preference={dataset_preference}, mw_range={config['constraints']['molecular']['mw']}")
        try:
            logger.info(f"Constraint sources: MW(min/max)={source_mw_min}/{source_mw_max}, LogP(min/max)={source_logp_min}/{source_logp_max}")
        except Exception:
            pass

        # Generate iteration-aware quality gate thresholds
        therapeutic_area = self.data_sources.get('therapeuticArea', 'General')
        thresholds = self._generate_iteration_aware_thresholds(iteration, therapeutic_area)

        # Add thresholds to config for quality gates
        config['thresholds'] = thresholds

        # Enhanced AI reasoning with threshold strategy
        ai_reasoning_with_thresholds = (
            f"{ai_tuning.get('reasoning', 'Configured for molecular generation')} "
            f"Quality gates configured for {thresholds['phase']} phase (iteration {iteration}): "
            f"ADMET toxicity max {thresholds['admet']['overall_toxicity']}, "
            f"binding affinity min {thresholds['binding_affinity']} kcal/mol, "
            f"quantum score min {thresholds['quantum_score']}."
        )

        logger.info(f"Generated iteration-aware thresholds for campaign {self.campaign_id}: "
                   f"phase={thresholds['phase']}, overall_tox={thresholds['admet']['overall_toxicity']}, "
                   f"binding={thresholds['binding_affinity']}, quantum={thresholds['quantum_score']}")

        return {
            "config": config,
            "literature_context": literature_context,
            "pattern_context": pattern_context,
            "ai_reasoning": ai_reasoning_with_thresholds,
            # Surface meta alongside config for chat/UI consumption
            "constraints_meta": config.get("constraints_meta", {})
        }

    async def _ai_tune_generation(
        self,
        phase: str,
        iteration: int,
        molecular_constraints: Dict[str, Any],
        literature_context: Dict[str, Any],
        pattern_context: Optional[Dict[str, Any]],
        failure_context: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Use AI to tune molecular-intelligence parameters for PubChem enriched data sampling.

        AI chooses:
        - batch_size: Number of molecules to generate (up to 1000)
        - mw_min/mw_max: Molecular weight constraints (therapeutic area-specific)
        - logp_min/logp_max: LogP constraints (BBB/lipophilicity optimization)
        - target_tpsa: Target TPSA value (CNS vs general drugs)

        NOTE: No MCTS parameters - molecular-intelligence uses direct PubChem enriched sampling
        """
        try:
            # Build concise literature summary
            top_papers = literature_context.get("top_papers", [])
            insights_text = "\n".join([
                f"- {paper.get('title', 'Untitled')[:80]}..."
                for paper in top_papers[:2]
            ]) if top_papers else "No literature matches"

            # Build failure context (if reconfiguration)
            failure_summary = ""
            if failure_context:
                gate_failures = failure_context.get("gate_failures", [])
                failure_types = [f.get('failure_type', 'unknown') for f in gate_failures]
                failure_summary = f"\nReconfiguration Trigger: {', '.join(set(failure_types))}"

            # Build successful pattern hints
            pattern_hint = ""
            if pattern_context and pattern_context.get("successful_patterns"):
                top_pattern = pattern_context["successful_patterns"][0]
                pattern_hint = f"\nSuccessful Pattern: {top_pattern.get('strategy')} ({top_pattern.get('success_rate'):.0%} success)"

            # Check if this is a fragment campaign
            goal_desc = self.campaign_goal or ""
            is_fragment_prompt = any(kw in goal_desc for kw in ['fragment', 'fbdd', 'fragment-based', 'hinge binder'])

            prompt = f"""Tune molecular generation parameters for PubChem enriched data sampling (115M molecules, 53 pre-calculated columns).

Therapeutic Area: {self.data_sources.get('therapeuticArea', 'General')}
Phase: {phase} (Iteration {iteration})
Literature: {literature_context.get('total_results', 0)} papers
Campaign Goal: {goal_desc[:100]}...
{insights_text}{pattern_hint}{failure_summary}

Current Constraints (from Campaign Config):
- MW: {molecular_constraints.get('mw', {}).get('min', 200)}-{molecular_constraints.get('mw', {}).get('max', 500)} Da
- LogP: {molecular_constraints.get('logP', {}).get('min', -0.4)}-{molecular_constraints.get('logP', {}).get('max', 5.6)}

{'⚠️ CRITICAL: This is a FRAGMENT SCREENING campaign. MUST use fragment MW ranges (100-250 Da), NOT drug-like ranges!' if is_fragment_prompt else ''}

TUNE THESE PARAMETERS FOR PUBCHEM ENRICHED SAMPLING:

1. batch_size: 500-1000 (molecules to generate per call, PRODUCTION: 1000)

2. Therapeutic area-specific constraints:
   {'- FRAGMENT CAMPAIGNS: mw_min=100-150, mw_max=200-250, logp_min=-0.5, logp_max=3.0' if is_fragment_prompt else ''}
   - mw_min: Adjust lower bound (e.g., CNS: 200-300, general: 200-400, FRAGMENTS: 100-150)
   - mw_max: Adjust upper bound (e.g., CNS: 400-450, general: 450-500, FRAGMENTS: 200-250)
   - logp_min: Adjust for lipophilicity (e.g., CNS: 1.0-2.0, general: -0.4-0.0, FRAGMENTS: -0.5)
   - logp_max: Adjust for lipophilicity (e.g., CNS: 3.0-4.0, general: 5.0-5.6, FRAGMENTS: 3.0)
   - target_tpsa: Target TPSA value (CNS: 60-90, cardiovascular: 90-120, fragments: <90, general: null)

Guidelines:
- First iteration: batch_size=1000, broad constraints for exploration
- Optimization: batch_size=1000, tighter constraints for refinement
- After ADMET failures: Tighten MW/LogP constraints to safer chemical space
- After binding failures: Relax constraints to explore diverse scaffolds
- FRAGMENT campaigns: MW 100-250 Da, LogP -0.5 to 3.0, TPSA <90
- CNS targets: MW<450, LogP 1-4, TPSA 60-90
- Cardiovascular: MW<500, LogP 0-5, TPSA 90-120
- IMPORTANT: NEVER override user-specified MW ranges! Stay within campaign constraints!

Respond in JSON:
{{
    "batch_size": 1000,
    "mw_min": 200,
    "mw_max": 500,
    "logp_min": -0.4,
    "logp_max": 5.6,
    "target_tpsa": null,
    "reasoning": "Brief scientific justification"
}}"""

            response = await self.azure_client.complete(
                prompt=prompt,
                system_prompt="You are a computational chemistry expert. Tune PubChem enriched data sampling parameters based on therapeutic area and phase. Be concise.",
                temperature=0.3,
                max_tokens=250
            )

            if response.get("success"):
                # Parse JSON response
                import re
                json_match = re.search(r'\{.*\}', response.get("response", ""), re.DOTALL)
                if json_match:
                    tuning = json.loads(json_match.group())
                    logger.info(f"AI tuned molecular-intelligence: batch_size={tuning.get('batch_size')}, "
                              f"mw={tuning.get('mw_min')}-{tuning.get('mw_max')}, "
                              f"logp={tuning.get('logp_min')}-{tuning.get('logp_max')}")
                    return tuning

            # Fallback if AI parsing fails
            return self._fallback_tuning(phase, iteration)

        except Exception as e:
            logger.error(f"AI tuning failed: {e}", exc_info=True)
            return self._fallback_tuning(phase, iteration)

    def _fallback_tuning(self, phase: str, iteration: int) -> Dict[str, Any]:
        """
        Fallback rule-based parameter tuning when AI unavailable.
        Returns molecular-intelligence sampling parameters (no MCTS).
        """
        if phase == "retrieval" and iteration == 0:
            return {
                "batch_size": 1000,
                "mw_min": 200,
                "mw_max": 500,
                "logp_min": -0.4,
                "logp_max": 5.6,
                "target_tpsa": None,
                "reasoning": "First iteration: broad PubChem sampling for diverse exploration"
            }
        elif phase == "optimization":
            return {
                "batch_size": 1000,
                "mw_min": 250,
                "mw_max": 450,
                "logp_min": 0.0,
                "logp_max": 5.0,
                "target_tpsa": None,
                "reasoning": "Optimization phase: tighter constraints for Lead-Like partition refinement"
            }
        else:
            return {
                "batch_size": 1000,
                "mw_min": 200,
                "mw_max": 500,
                "logp_min": -0.4,
                "logp_max": 5.6,
                "target_tpsa": None,
                "reasoning": "Subsequent generation: balanced PubChem enriched sampling"
            }

    def _generate_iteration_aware_thresholds(
        self,
        iteration: int,
        therapeutic_area: str = "General"
    ) -> Dict[str, Any]:
        """
        Generate quality gate thresholds that tighten over iterations.

        Strategy:
        - Iteration 1-3: Permissive (broad exploration, prioritize quantity)
        - Iteration 4-6: Moderate (refinement, balance quality/quantity)
        - Iteration 7+: Strict (optimization, prioritize quality)

        FRAGMENT CAMPAIGNS: Use fragment-appropriate thresholds (binding -3 to -5 kcal/mol)

        Args:
            iteration: Current iteration number (0-based)
            therapeutic_area: Therapeutic area for custom adjustments

        Returns:
            Dict with thresholds for all quality gates by phase
        """
        # Check if this is a fragment campaign
        goal_desc = self.campaign_goal or ""
        molecular = self.constraints.get("molecular", {})
        campaign_mw_max = (molecular.get("mw", {}) or {}).get("max")
        is_fragment = (campaign_mw_max and campaign_mw_max <= 300) or \
                     any(kw in goal_desc for kw in ['fragment', 'fbdd', 'fragment-based', 'hinge binder'])

        # Determine phase based on iteration
        if iteration <= 3:
            # Early iterations: explore broadly, tolerate higher risk
            phase = "exploration"
            admet = {
                'overall_toxicity': 0.75,      # 75% max toxicity (permissive)
                'hepatotoxicity': 0.75,         # 75% max liver toxicity
                'cardiotoxicity': 0.70,         # 70% max cardiotoxicity
                'respiratory_toxicity': 0.85,   # 85% max respiratory toxicity
                'cyp_inhibition': 0.80          # 80% max CYP450 inhibition
            }
            # FRAGMENT FIX: Fragments have much weaker binding (-3 to -5 kcal/mol)
            binding_affinity = -3.0 if is_fragment else -6.5
            quantum_score = 0.70 if is_fragment else 0.75
        elif iteration <= 6:
            # Middle iterations: refine candidates, balance safety/potency
            phase = "refinement"
            admet = {
                'overall_toxicity': 0.70,      # 70% max toxicity (moderate)
                'hepatotoxicity': 0.70,
                'cardiotoxicity': 0.65,
                'respiratory_toxicity': 0.80,
                'cyp_inhibition': 0.75
            }
            # FRAGMENT FIX: Moderate fragment threshold -4.0 kcal/mol (still fragment-appropriate)
            binding_affinity = -4.0 if is_fragment else -7.0
            quantum_score = 0.75 if is_fragment else 0.80
        else:
            # Late iterations: optimize top leads, strict safety
            phase = "optimization"
            admet = {
                'overall_toxicity': 0.65,      # 65% max toxicity (strict)
                'hepatotoxicity': 0.65,
                'cardiotoxicity': 0.60,
                'respiratory_toxicity': 0.75,
                'cyp_inhibition': 0.70
            }
            # FRAGMENT FIX: Fragments target -4.0 kcal/mol (realistic for high-quality fragments)
            # Note: -5.0 was too strict, causing endless validation loops
            binding_affinity = -4.0 if is_fragment else -7.5
            quantum_score = 0.80 if is_fragment else 0.85

        # Therapeutic area adjustments
        if therapeutic_area == "Oncology":
            # Cancer drugs can tolerate higher toxicity (trade-off for efficacy)
            admet['overall_toxicity'] += 0.05
            admet['hepatotoxicity'] += 0.05
            admet['cardiotoxicity'] += 0.05
        elif therapeutic_area == "CNS":
            # CNS drugs need stricter safety (blood-brain barrier concerns)
            admet['overall_toxicity'] -= 0.05
            admet['hepatotoxicity'] -= 0.05
        elif therapeutic_area == "Pediatrics":
            # Pediatric drugs need very strict safety
            admet['overall_toxicity'] -= 0.10
            admet['hepatotoxicity'] -= 0.10
            admet['cardiotoxicity'] -= 0.05

        if is_fragment:
            logger.info(f"Campaign {self.campaign_id}: Fragment thresholds applied - binding_affinity={binding_affinity} kcal/mol (iter {iteration})")

        return {
            'phase': phase,
            'iteration': iteration,
            'admet': admet,
            'binding_affinity': binding_affinity,
            'quantum_score': quantum_score,
            'md_stability': {
                'rmsd': 3.0,  # 3.0 Å max RMSD (constant across iterations)
                'binding_free_energy': -5.0  # -5.0 kcal/mol min (constant)
            }
        }

    def _extract_best_scaffold(self, literature_context: Dict[str, Any]) -> Optional[str]:
        """
        Extract single best scaffold SMILES from literature for similarity-based generation.

        Returns:
            Best scaffold SMILES string, or None if no scaffolds found
        """
        try:
            for paper in literature_context.get("top_papers", [])[:3]:
                metadata = paper.get("metadata", {})

                # Look for lead compounds in paper metadata
                if "lead_compounds" in metadata:
                    compounds = metadata.get("lead_compounds", [])
                    if compounds and "smiles" in compounds[0]:
                        best_scaffold = compounds[0]["smiles"]
                        logger.info(f"Extracted scaffold from literature: {best_scaffold[:50]}...")
                        return best_scaffold

                # Look for scaffold in structured data
                if "scaffold_smiles" in metadata:
                    scaffold = metadata.get("scaffold_smiles")
                    if scaffold:
                        logger.info(f"Extracted scaffold from metadata: {scaffold[:50]}...")
                        return scaffold

            logger.info("No scaffolds found in literature for similarity-based generation")
            return None

        except Exception as e:
            logger.error(f"Failed to extract scaffold from literature: {e}")
            return None

    async def _query_successful_patterns(self, failure_context: Dict[str, Any]) -> Dict[str, Any]:
        """Query Pinecone for successful patterns from similar campaigns"""
        try:
            from core.db_helper import query_sql

            # Query successful patterns from Research DB
            successful_patterns = await query_sql("""
                SELECT TOP 5
                    context,
                    pattern_type,
                    success_rate,
                    occurrence_count
                FROM learning_patterns
                WHERE success_rate > 0.7
                    AND pattern_type LIKE '%generation%'
                ORDER BY success_rate DESC, occurrence_count DESC
            """, ())

            patterns = []
            for pattern in successful_patterns:
                try:
                    context = json.loads(pattern.get("context", "{}"))
                    patterns.append({
                        "strategy": pattern.get("pattern_type", "unknown"),
                        "success_rate": pattern.get("success_rate", 0),
                        "usage_count": pattern.get("occurrence_count", 0),
                        "details": context
                    })
                except:
                    pass

            return {
                "successful_patterns": patterns,
                "total_found": len(patterns)
            }

        except Exception as e:
            logger.error(f"Failed to query successful patterns: {e}")
            return {
                "successful_patterns": [],
                "total_found": 0
            }


    async def build_admet_config(self, molecules: List[Dict]) -> Dict[str, Any]:
        """
        Build ADMET screening configuration

        Args:
            molecules: Molecules to screen

        Returns:
            ADMET screening parameters
        """
        admet_constraints = self.constraints.get("admet", {})
        therapeutic_area = self.data_sources.get("therapeuticArea", "")

        # Select ADMET models based on therapeutic area
        models = ["hepatotoxicity", "cyp450", "herg", "solubility"]

        # Add BBB models if CNS target
        if therapeutic_area.lower() in ["neurology", "cns", "neurological"]:
            models.extend(["bbb_penetration", "pgp_substrate"])

        # Add cardiotoxicity for cardiovascular
        if therapeutic_area.lower() in ["cardiology", "cardiovascular"]:
            models.extend(["cardiotoxicity", "qt_prolongation"])

        config = {
            "molecules": [{"smiles": m.get("smiles"), "id": m.get("id")} for m in molecules],
            "models": models,
            "filters": {
                "hepatotoxicity_threshold": 0.5 if admet_constraints.get("hepatotoxicity") else 1.0,
                "cyp450_threshold": 0.7 if admet_constraints.get("cyp450") else 1.0,
                "solubility_min": admet_constraints.get("solubility", -4),
                "bbb_required": admet_constraints.get("bbb", False)
            },
            "therapeutic_area": therapeutic_area
        }

        logger.info(f"Built ADMET config for campaign {self.campaign_id}: {len(models)} models, therapeutic_area={therapeutic_area}")
        return config

    async def build_compliance_config(self, molecules: List[Dict]) -> Dict[str, Any]:
        """
        Build FAVES compliance configuration

        Args:
            molecules: Molecules to validate

        Returns:
            Compliance check parameters
        """
        config = {
            "molecules": [{"smiles": m.get("smiles"), "id": m.get("id")} for m in molecules],
            "assessments": [
                "ethical",
                "regulatory",
                "dual_use_risk",
                "environmental_impact"
            ],
            "regulatory_frameworks": ["FDA", "EMA", "ICH"],
            "strict_mode": self.autonomy.get("level") == "full"  # Strict compliance for full autonomous
        }

        logger.info(f"Built compliance config for campaign {self.campaign_id}: {len(molecules)} molecules")
        return config

    async def build_validation_config(self, molecules: List[Dict], target_protein: str) -> Dict[str, Any]:
        """
        Build docking and MD validation configuration

        Args:
            molecules: Top candidates to validate
            target_protein: Target protein identifier

        Returns:
            Validation parameters
        """
        config = {
            "ligand_smiles_list": [m.get("smiles") for m in molecules],
            "protein_pdb_id": target_protein,
            "exhaustiveness": 16,  # High exhaustiveness for accuracy
            "num_poses": 9,
            "energy_range": 3,
            "auto_detect_binding_site": True,  # Auto-detect binding sites
            # Default center coordinates (will be overridden by auto-detection)
            "center_x": 0.0,
            "center_y": 0.0,
            "center_z": 0.0
        }

        logger.info(f"Built validation config for campaign {self.campaign_id}: {len(molecules)} molecules, target={target_protein}")
        return config

    async def build_optimization_config(self, leads: List[Dict], goal_properties: List[str]) -> Dict[str, Any]:
        """
        Build lead optimization configuration

        Args:
            leads: Lead molecules to optimize
            goal_properties: Target properties from campaign goal

        Returns:
            Optimization parameters
        """
        # Parse goal properties
        if not goal_properties:
            goal_properties = ["potency", "selectivity", "admet"]

        config = {
            "molecules": [{"smiles": m.get("smiles"), "id": m.get("id"), "parent_id": m.get("id")} for m in leads],
            "strategy": "multi_objective",
            "target_properties": goal_properties,
            "optimization_algorithm": "molmim",  # Use MolMIM optimizer
            "iterations": 5,
            "constraints": self.constraints.get("molecular", {}),
            "preserve_scaffold": True  # Maintain core structure
        }

        logger.info(f"Built optimization config for campaign {self.campaign_id}: {len(leads)} leads, properties={goal_properties}")
        return config

    async def _get_literature_context(self) -> Dict[str, Any]:
        """Query Pinecone for relevant literature"""
        try:
            from core.pinecone_client import get_pinecone_client

            pinecone_client = get_pinecone_client()

            # Build query from campaign data sources
            search_keywords = self.data_sources.get("searchKeywords", [])
            therapeutic_area = self.data_sources.get("therapeuticArea", "")
            modality = self.data_sources.get("modality", "")

            # Handle goal as either string or dict
            if isinstance(self.goal, dict):
                goal_description = self.goal.get("description", "")
            elif isinstance(self.goal, str):
                goal_description = self.goal
            else:
                goal_description = ""

            query_text = f"{goal_description} {therapeutic_area} {modality} {' '.join(search_keywords)}"

            logger.info(f"Querying Pinecone for campaign {self.campaign_id}: {query_text[:100]}...")

            # Query Pinecone using search_literature (which handles embeddings internally)
            papers = await pinecone_client.search_literature(
                query=query_text,
                filters={
                    "therapeutic_area": therapeutic_area,
                    "modality": modality
                },
                top_k=10
            )

            return {
                "total_results": len(papers),
                "top_papers": papers[:5],
                "query_text": query_text
            }

        except Exception as e:
            logger.error(f"Failed to get literature context: {e}")
            return {
                "total_results": 0,
                "top_papers": [],
                "error": str(e)
            }

    def apply_learning_patterns(self, service: str, base_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply successful learning patterns to service configuration

        Args:
            service: Service name
            base_config: Base configuration

        Returns:
            Enhanced configuration with learning patterns
        """
        # Placeholder for learning pattern application
        # Would query learning_patterns table for successful patterns

        logger.debug(f"Applying learning patterns for {service} (campaign {self.campaign_id})")

        # Example: adjust parameters based on past success
        # In future: query db-manager for learning patterns

        return base_config
