"""
Workflow Orchestration Engine for Drug Discovery Campaigns
Implements structured 5-phase workflow with quality gates and circuit breaking

RECURSION BUG FIX: Uses ServiceProxy for direct HTTP calls instead of orchestrate_func
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from enum import Enum
import json
import uuid
import time
import asyncio

from .service_proxy import ServiceProxy

logger = logging.getLogger(__name__)


class WorkflowPhase(Enum):
    """5-phase drug discovery workflow"""
    RETRIEVAL = "retrieval"  # molecular-intelligence: QUERIES enriched PubChem (115M molecules, 53 pre-calculated columns)
    ADMET_SCREENING = "admet_screening"  # FILTERS pre-calculated ADMET data (39 columns from molecular-intelligence)
    COMPLIANCE = "compliance"  # FAVES (ethics/safety/regulatory) → Knowledge-Graph → TDC
    VALIDATION = "validation"  # AutoDock-GPU (docking) → AWS Braket (quantum) → GROMACS-MD (simulation)
    OPTIMIZATION = "optimization"  # Lead-Optimization → MolMIM


class PhaseAction(Enum):
    """Actions after quality gate evaluation"""
    PROCEED = "proceed"  # Continue to next phase
    LOOP_BACK = "loop_back"  # Return to earlier phase with adjusted params
    HUMAN_INTERVENTION = "human_intervention"  # Request human decision
    HALT = "halt"  # Stop campaign (circuit breaker open)
    DISCOVERY = "discovery"  # Discovery made, campaign complete


def convert_enums_to_values(obj: Any) -> Any:
    """
    Recursively convert Enum objects to their values for JSON serialization.

    Args:
        obj: Object that may contain Enum values (dict, list, or Enum)

    Returns:
        Same structure with Enums converted to their .value strings
    """
    if isinstance(obj, Enum):
        return obj.value
    elif isinstance(obj, dict):
        return {k: convert_enums_to_values(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_enums_to_values(item) for item in obj]
    return obj


class WorkflowState:
    """Tracks campaign workflow progression"""

    def __init__(self, campaign_id: str, initial_phase: WorkflowPhase = WorkflowPhase.RETRIEVAL):
        self.campaign_id = campaign_id
        self.current_phase = initial_phase
        self.phase_iteration = {phase: 0 for phase in WorkflowPhase}
        self.total_iterations = 0
        self.phase_history: List[Dict[str, Any]] = []
        self.molecules_in_pipeline: List[Dict] = []
        self.top_candidates: List[Dict] = []

    def advance_phase(self, next_phase: WorkflowPhase):
        """Move to next workflow phase"""
        self.phase_history.append({
            "from_phase": self.current_phase.value,
            "to_phase": next_phase.value,
            "timestamp": datetime.utcnow().isoformat(),
            "iteration": self.phase_iteration[self.current_phase]
        })
        self.current_phase = next_phase
        self.phase_iteration[next_phase] += 1
        self.total_iterations += 1

    def loop_back(self, target_phase: WorkflowPhase, reason: str):
        """Loop back to earlier phase"""
        logger.info(f"Campaign {self.campaign_id} looping back from {self.current_phase.value} to {target_phase.value}: {reason}")
        self.phase_history.append({
            "from_phase": self.current_phase.value,
            "to_phase": target_phase.value,
            "timestamp": datetime.utcnow().isoformat(),
            "loop_back": True,
            "reason": reason
        })
        self.current_phase = target_phase
        self.phase_iteration[target_phase] += 1
        self.total_iterations += 1

    def to_dict(self) -> Dict[str, Any]:
        """Serialize workflow state"""
        return {
            "current_phase": self.current_phase.value,
            "phase_iteration": {p.value: count for p, count in self.phase_iteration.items()},
            "total_iterations": self.total_iterations,
            "phase_history": self.phase_history,
            "molecules_in_pipeline": self.molecules_in_pipeline,  # CRITICAL: Save actual molecules, not just count!
            "top_candidates": self.top_candidates,
            "molecules_count": len(self.molecules_in_pipeline),  # Keep for backward compat
            "candidates_count": len(self.top_candidates)
        }

    @classmethod
    def from_dict(cls, campaign_id: str, data: Dict[str, Any]) -> 'WorkflowState':
        """Deserialize workflow state"""
        state = cls(campaign_id)

        # BACKWARD COMPATIBILITY: Map old "generation" phase to new "retrieval" phase
        phase_value = data.get("current_phase", "retrieval")
        if phase_value == "generation":
            phase_value = "retrieval"  # Migration: generation → retrieval

        state.current_phase = WorkflowPhase(phase_value)
        state.total_iterations = data.get("total_iterations", 0)
        state.phase_history = data.get("phase_history", [])

        # CRITICAL: Restore molecules_in_pipeline and top_candidates from serialized state
        state.molecules_in_pipeline = data.get("molecules_in_pipeline", [])
        state.top_candidates = data.get("top_candidates", [])

        # Reconstruct phase_iteration
        phase_iter_data = data.get("phase_iteration", {})

        # Handle legacy format: phase_iteration stored as int instead of dict
        if isinstance(phase_iter_data, int):
            # Old format: single integer, assign to current phase
            logger.warning(f"Campaign {campaign_id}: Legacy phase_iteration format detected (int), migrating to dict")
            state.phase_iteration[state.current_phase] = phase_iter_data
        elif isinstance(phase_iter_data, dict):
            # BACKWARD COMPATIBILITY: Migrate old "generation" phase to "retrieval"
            if "generation" in phase_iter_data and "retrieval" not in phase_iter_data:
                phase_iter_data["retrieval"] = phase_iter_data["generation"]
                logger.info(f"Campaign {campaign_id}: Migrated phase_iteration 'generation' → 'retrieval' (count: {phase_iter_data['generation']})")

            # New format: dict mapping phase names to iteration counts
            for phase in WorkflowPhase:
                state.phase_iteration[phase] = phase_iter_data.get(phase.value, 0)
        else:
            # Unknown format, log warning and continue with defaults (all zeros)
            logger.warning(f"Campaign {campaign_id}: Unknown phase_iteration format: {type(phase_iter_data)}, using defaults")

        return state


class WorkflowEngine:
    """
    Orchestrates drug discovery workflow with quality gates and intelligent phase transitions
    """

    def __init__(self, campaign_config: Dict[str, Any], azure_client=None, iteration_id: Optional[str] = None):
        self.campaign_id = campaign_config.get('id') or campaign_config.get('campaign_id')
        self.config = campaign_config
        self.azure_client = azure_client
        self.iteration_id = iteration_id  # CRITICAL FIX: Track iteration_id for indexed database updates

        # CRITICAL FIX: Normalize goal to dict (handle JSON string from database)
        # Issue: goal is stored as nvarchar JSON in DB, but code expects dict
        goal = self.config.get('goal') or {}
        if isinstance(goal, str):
            try:
                import json
                goal = json.loads(goal)
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"Failed to parse goal as JSON, treating as description: {goal}")
                goal = {'description': goal}
        self.config['goal'] = goal  # Store normalized version back

        # Merge persisted metadata.thresholds into runtime thresholds (if present)
        try:
            metadata = self.config.get('metadata') or {}
            if isinstance(metadata, str):
                import json as _json
                try:
                    metadata = _json.loads(metadata)
                except Exception:
                    metadata = {}
            meta_thresholds = (metadata or {}).get('thresholds') or {}
            if isinstance(meta_thresholds, dict) and meta_thresholds:
                rt_thresholds = self.config.get('thresholds') or {}
                # Shallow merge is sufficient for current gate keys
                merged = {**rt_thresholds, **meta_thresholds}
                self.config['thresholds'] = merged
        except Exception as _e:
            logger.warning(f"Failed to merge metadata.thresholds into config: {_e}")

        # Initialize workflow state
        workflow_state_data = campaign_config.get('workflow_state', {})
        if workflow_state_data:
            self.state = WorkflowState.from_dict(self.campaign_id, workflow_state_data)
        else:
            self.state = WorkflowState(self.campaign_id)

        # Track current iteration number (set from CampaignLoopManager)
        self.iteration_number: Optional[int] = campaign_config.get('iteration_number')

        # Initialize DecisionLogger for audit trail
        if azure_client:
            from .decision_logger import DecisionLogger
            self.decision_logger = DecisionLogger(azure_client, self.campaign_id)
        else:
            self.decision_logger = None
            logger.warning(f"WorkflowEngine initialized without azure_client - decision logging disabled")

        # Phase definitions with service sequences
        self.phase_definitions = self._build_phase_definitions()

    def _build_phase_definitions(self) -> Dict[WorkflowPhase, Dict[str, Any]]:
        """Define service sequences and parameters for each phase"""
        return {
            WorkflowPhase.RETRIEVAL: {
                "services": ["molecular-intelligence"],  # PubChem enriched (115M molecules, 53 columns)
                "quality_gate": "molecular_constraints",
                "next_phase": WorkflowPhase.ADMET_SCREENING,
                "description": "Filter enriched PubChem data → 1000 molecules (instant PyArrow query)"
            },
            WorkflowPhase.ADMET_SCREENING: {
                "services": [],  # No service calls - validation only (enriched ADMET data)
                "quality_gates": ["admet_filters", "safety_screening"],
                "next_phase": WorkflowPhase.COMPLIANCE,
                "description": "Validate pre-calculated ADMET scores meet thresholds (<1 second)"
            },
            WorkflowPhase.COMPLIANCE: {
                "services": ["faves-compliance", "knowledge-graph", "tdc-integration"],
                "quality_gate": "compliance_check",
                "next_phase": WorkflowPhase.VALIDATION,
                "description": "Compliance, knowledge enrichment, benchmarking"
            },
            WorkflowPhase.VALIDATION: {
                "services": ["autodock-gpu", "quantum-validation", "gromacs-md"],
                "quality_gates": ["binding_affinity", "quantum_score", "md_stability"],
                "next_phase": WorkflowPhase.OPTIMIZATION,
                "description": "Docking → Quantum → MD validation",
                "conditions": {
                    "skip_if_no_target": True,  # Skip if no target protein
                    "top_candidates_only": 20,  # Top 20 for quantum filtering
                    "quantum_filter_to": 10  # Filter to 10 after quantum validation
                }
            },
            WorkflowPhase.OPTIMIZATION: {
                "services": ["molmim-optimizer", "lead-optimization"],
                "quality_gate": "optimization_improvement",
                "next_phase": WorkflowPhase.RETRIEVAL,  # Loop back for next iteration
                "description": "Lead optimization and improvement verification",
                "decision_point": True  # Check if discovery made or continue
            }
        }

    async def execute_current_phase(self, orchestrate_func=None) -> Dict[str, Any]:
        """
        Execute current workflow phase

        RECURSION BUG FIX: No longer uses orchestrate_func - makes direct HTTP calls via ServiceProxy

        Args:
            orchestrate_func: DEPRECATED - kept for backward compatibility but NOT used

        Returns:
            Phase execution result with molecules, scores, and metrics
        """
        phase_def = self.phase_definitions[self.state.current_phase]
        phase_name = self.state.current_phase.value

        logger.info(f"Campaign {self.campaign_id} executing phase: {phase_name} (iteration {self.state.phase_iteration[self.state.current_phase]})")

        try:
            # Import intelligent config for service configuration
            from .intelligent_config import ServiceConfigBuilder

            config_builder = ServiceConfigBuilder(self.config, self.azure_client)

            # Initialize ServiceProxy for direct HTTP calls (NO recursion)
            service_proxy = ServiceProxy()

            # Execute phase-specific logic
            if self.state.current_phase == WorkflowPhase.RETRIEVAL:
                result = await self._execute_retrieval_phase(service_proxy, config_builder)
            elif self.state.current_phase == WorkflowPhase.ADMET_SCREENING:
                result = await self._execute_admet_phase(service_proxy, config_builder)
            elif self.state.current_phase == WorkflowPhase.COMPLIANCE:
                result = await self._execute_compliance_phase(service_proxy, config_builder)
            elif self.state.current_phase == WorkflowPhase.VALIDATION:
                result = await self._execute_validation_phase(service_proxy, config_builder)
            elif self.state.current_phase == WorkflowPhase.OPTIMIZATION:
                result = await self._execute_optimization_phase(service_proxy, config_builder)
            else:
                result = {"status": "error", "message": f"Unknown phase: {phase_name}"}

            result["phase"] = phase_name
            result["iteration"] = self.state.phase_iteration[self.state.current_phase]
            return result

        except Exception as e:
            logger.error(f"Error executing phase {phase_name}: {e}", exc_info=True)
            return {
                "status": "error",
                "phase": phase_name,
                "message": str(e),
                "iteration": self.state.phase_iteration[self.state.current_phase]
            }

    async def _execute_retrieval_phase(self, service_proxy: ServiceProxy, config_builder) -> Dict[str, Any]:
        """
        Phase 1: QUERY/SELECT molecules from enriched PubChem via molecular-intelligence

        IMPORTANT: This does NOT generate new molecules - it QUERIES enriched PubChem dataset
        - 115M molecules with 53 pre-calculated columns (11 PubChem + 3 Chem-Props + 39 ADMET)
        - Uses PyArrow predicate pushdown filtering on S3 Parquet files
        - Smart dataset selection: Drug-Like (5.5M), Lead-Like (8.6M), Fragments, Boutique

        Funnel design: 1000 selected → 100 filtered → 20 validated → 10 optimized → 5+ leads
        """
        logger.info(f"Campaign {self.campaign_id}: Starting RETRIEVAL phase - querying enriched PubChem (iteration {self.state.phase_iteration[WorkflowPhase.RETRIEVAL]})")

        # Extract seed molecules from previous iteration's top candidates (for loop-back)
        seed_molecules = None
        current_iteration = self.state.phase_iteration[WorkflowPhase.RETRIEVAL]
        if current_iteration > 0 and self.state.top_candidates:
            # Loop-back iteration: Use top 10 candidates as MCTS seeds for iterative refinement
            seed_molecules = [mol.get("smiles") for mol in self.state.top_candidates[:10] if mol.get("smiles")]
            logger.info(f"Campaign {self.campaign_id}: Loop-back iteration {current_iteration} - using {len(seed_molecules)} seed molecules from previous iteration")
        else:
            logger.info(f"Campaign {self.campaign_id}: Initial iteration {current_iteration} - using preset molecule library")

        # PRODUCTION: molecular-intelligence QUERIES enriched PubChem (NOT generation)
        # DrugSynthMC removed - all campaigns query molecular-intelligence enriched data
        selection_service = 'molecular-intelligence'
        logger.info(f"Campaign {self.campaign_id}: Querying molecular-intelligence enriched PubChem dataset")

        # Build selection config with AI + Pinecone literature analysis + seed molecules
        # Returns: {config, literature_context, pattern_context, ai_reasoning, constraints_meta}
        config_result = await config_builder.build_generation_config(
            phase="retrieval",
            iteration=self.state.phase_iteration[WorkflowPhase.RETRIEVAL],
            seed_molecules=seed_molecules
        )

        # Add selection_service to config for molecular-worker routing
        config_result['config']['generation_service'] = selection_service  # Keep key name for backward compat

        # Extract config and metadata
        base_config = config_result.get("config", config_result)  # Backward compat if old format
        literature_context = config_result.get("literature_context", {})
        ai_reasoning = config_result.get("ai_reasoning", "Configured for molecular selection from enriched PubChem")
        constraints_meta = config_result.get("constraints_meta", {})

        # Store constraints_meta in self.config for use by adjust_parameters_on_failure
        if constraints_meta:
            self.config['constraints_meta'] = constraints_meta
            logger.info(f"Campaign {self.campaign_id}: Stored constraints_meta with locks for loop-back enforcement")

        # CRITICAL FIX: Propagate iteration-aware thresholds to campaign config for quality gates
        # Quality gates read from self.config['thresholds'], but intelligent_config generates them in base_config
        if 'thresholds' in base_config:
            self.config['thresholds'] = base_config['thresholds']
            logger.info(f"Campaign {self.campaign_id}: Propagated iteration-aware thresholds to campaign config for quality gates: {base_config['thresholds']}")

        # Notify UI of any AI/default-assumed constraints so users can lock or edit
        try:
            from .message_formatter import MessageFormatter
            from routers.ai_orchestration import broadcast_global_update
            from core.db_helper import query_sql, execute_sql
            assumed_msg = MessageFormatter.format_assumed_constraints(constraints_meta, base_config.get('constraints', {}))
            if assumed_msg:
                await broadcast_global_update('assumed_constraints', {
                    'campaign_id': self.campaign_id,
                    'phase': 'retrieval',
                    'message': assumed_msg,
                    'constraints': base_config.get('constraints', {}),
                    'constraints_meta': constraints_meta,
                    'actions': {
                        'lockable': ['constraints.molecular.mw.min', 'constraints.molecular.mw.max', 'constraints.molecular.logp.min', 'constraints.molecular.logp.max'],
                        'editable': ['constraints.molecular']
                    }
                })

                # Also persist as a chat message in the campaign thread for auditability
                try:
                    rows = await query_sql(
                        "SELECT TOP 1 id FROM campaign_chat_threads WHERE campaign_id = CAST(%s AS UNIQUEIDENTIFIER)",
                        (self.campaign_id,)
                    )
                    thread_id = None
                    if rows and isinstance(rows, list):
                        r0 = rows[0]
                        thread_id = r0.get('id') if isinstance(r0, dict) else list(r0.values())[0]
                    if not thread_id:
                        # Create a thread if missing
                        import uuid as _uuid
                        thread_id = str(_uuid.uuid4())
                        await execute_sql(
                            "INSERT INTO campaign_chat_threads (id, campaign_id, status) VALUES (CAST(%s AS UNIQUEIDENTIFIER), CAST(%s AS UNIQUEIDENTIFIER), %s)",
                            (thread_id, self.campaign_id, 'active')
                        )
                    # Insert the assistant message
                    import uuid as _uuid
                    message_id = str(_uuid.uuid4())
                    await execute_sql(
                        (
                            "INSERT INTO campaign_chat_messages (id, thread_id, role, content, timestamp, "
                            "intent, sentiment, user_id, action_type, action_details, action_success, attachments, "
                            "campaign_iteration, campaign_candidates_count, campaign_discoveries_count, campaign_status) "
                            "VALUES (CAST(%s AS UNIQUEIDENTIFIER), CAST(%s AS UNIQUEIDENTIFIER), %s, %s, GETUTCDATE(), "
                            "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)"
                        ),
                        (message_id, thread_id, 'assistant', assumed_msg)
                    )
                except Exception as e:
                    logger.debug(f"Failed to persist assumed-constraints chat message: {e}")
        except Exception as e:
            logger.debug(f"Assumed-constraints notification skipped: {e}")

        # PRODUCTION: Single call with 1000 molecules (10x faster than old DrugSynthMC parallel pattern)
        base_config['batch_size'] = 1000  # PRODUCTION: 1000 molecules in 1 call
        configs = [base_config]  # Single call - no parallel needed!

        # Determine dataset label from dataset_preference or seed_molecules fallback
        ds_pref = base_config.get('dataset_preference')
        ds_label_map = {
            'fragments': 'Fragments (mw_0_200)',
            'drug-like': 'Drug-Like (mw_200_400)',
            'boutique': 'Boutique (mw_400_600)',
            'auto': 'Auto (service-selected)'
        }
        dataset_label = ds_label_map.get(ds_pref) if ds_pref else (
            'Lead-Like (mw_0_200)' if seed_molecules else 'Drug-Like (mw_200_400)'
        )

        logger.info(f"Campaign {self.campaign_id}: molecular-intelligence config: "
                   f"batch_size={base_config['batch_size']}, "
                   f"algorithm=zinc12-sampling, "
                   f"seed_molecules={len(seed_molecules) if seed_molecules else 0}, "
                   f"dataset={dataset_label}")
        logger.info(f"Campaign {self.campaign_id}: AI reasoning: {ai_reasoning}")

        # Execute molecular-intelligence QUERY (RECURSION BUG FIX: Direct HTTP call)
        logger.info(f"Campaign {self.campaign_id}: Querying molecular-intelligence for {base_config['batch_size']} molecules")

        result = await service_proxy.call_molecular_intelligence(base_config)

        # Process result
        all_molecules = []

        if isinstance(result, Exception):
            logger.error(f"Campaign {self.campaign_id}: molecular-intelligence query failed: {result}")
            raise result

        if result.get("status") == "success":
            # Try new format (molecules at top level) first, fall back to old nested format
            molecules = result.get("molecules", []) or result.get("results", {}).get("molecules", [])
            all_molecules.extend(molecules)
            logger.info(f"Campaign {self.campaign_id}: molecular-intelligence returned {len(molecules)} molecules with enriched data (53 columns)")
        else:
            error_msg = f"Selection query failed with status: {result.get('status')}"
            logger.error(f"Campaign {self.campaign_id}: {error_msg}")
            raise Exception(error_msg)

        logger.info(f"Campaign {self.campaign_id}: Selection complete - {len(all_molecules)} total molecules from enriched PubChem")

        # Store molecules in pipeline
        self.state.molecules_in_pipeline = all_molecules

        # Return result
        return {
            "status": "success",
            "results": {
                "molecules": all_molecules,
                "total_selected": len(all_molecules),  # Changed from total_generated
                "service": "molecular-intelligence",
                "dataset": dataset_label,
                "enriched_columns": 53  # 11 PubChem + 3 Chem-Props + 39 ADMET
            },
            "literature_context": literature_context,
            "ai_reasoning": ai_reasoning,
            "selection_strategy": "molecular-intelligence enriched PubChem query (PyArrow filtering)"
        }

    def _generate_config_variations(self, base_config: Dict[str, Any], num_variations: int = 10) -> List[Dict[str, Any]]:
        """
        Generate multiple config variations for diversity in molecular generation.

        Varies temperature and exploration_constant to explore different regions of chemical space
        while keeping other parameters (algorithm, constraints, targets) consistent.

        Args:
            base_config: Base DrugSynthMC config from intelligent_config
            num_variations: Number of variations to generate (default: 10)

        Returns:
            List of config dicts with parameter variations
        """
        import copy

        configs = []

        # Temperature range: 0.8 to 1.5 (diversity in sampling)
        # Exploration constant range: base ± 0.3 (diversity in MCTS exploration)
        base_temp = base_config.get("temperature", 1.0)
        base_exploration = base_config.get("exploration_constant", 1.4)

        temperature_values = [
            0.8, 0.9, 1.0, 1.0, 1.1, 1.1, 1.2, 1.3, 1.4, 1.5
        ]

        exploration_offsets = [
            -0.3, -0.2, -0.1, 0.0, 0.0, 0.1, 0.1, 0.2, 0.2, 0.3
        ]

        for i in range(num_variations):
            config = copy.deepcopy(base_config)

            # Vary temperature (affects sampling diversity)
            config["temperature"] = temperature_values[i]

            # Vary exploration constant (affects MCTS exploration)
            config["exploration_constant"] = max(0.5, min(3.0, base_exploration + exploration_offsets[i]))

            # TEST CONFIG: Reduce batch_size to 5 for continuous pipeline testing
            # Production: 100 (DrugSynthMC max)
            config["batch_size"] = 5

            configs.append(config)

        logger.info(f"Generated {len(configs)} config variations: "
                   f"temperature range [{min(temperature_values)}, {max(temperature_values)}], "
                   f"exploration_constant range [{min(exploration_offsets) + base_exploration:.1f}, {max(exploration_offsets) + base_exploration:.1f}]")

        return configs

    async def _execute_admet_phase(self, service_proxy: ServiceProxy, config_builder) -> Dict[str, Any]:
        """
        Phase 2: ADMET Filtering using Pre-Calculated Values from Enriched PubChem

        NO SERVICE CALLS - molecules already have 39 ADMET columns from enrichment jobs
        This phase filters molecules based on pre-calculated toxicity/safety thresholds
        """
        molecules = self.state.molecules_in_pipeline

        logger.info(f"[ADMET_DEBUG] Starting ADMET phase with {len(molecules) if molecules else 0} molecules in pipeline")

        if not molecules:
            logger.error("[ADMET_DEBUG] No molecules in pipeline - cannot proceed with ADMET filtering!")
            return {"status": "error", "message": "No molecules to filter"}

        # DEBUG: Log first molecule structure
        sample = molecules[0]
        logger.info(f"[ADMET_DEBUG] Sample molecule type: {type(sample).__name__}")
        if isinstance(sample, dict):
            logger.info(f"[ADMET_DEBUG] Sample dict keys (first 15): {list(sample.keys())[:15]}")
            logger.info(f"[ADMET_DEBUG] Sample ADMET values: overall_tox={sample.get('overall_toxicity_score')}, hepato={sample.get('hepatotoxicity_probability')}, cardio={sample.get('cardiotoxicity_max_probability')}")
        else:
            logger.info(f"[ADMET_DEBUG] Molecule is NOT a dict - attempting conversion from Pydantic")
            # Convert Pydantic objects to dicts
            try:
                molecules = [mol.model_dump() if hasattr(mol, 'model_dump') else mol.dict() for mol in molecules]
                logger.info(f"[ADMET_DEBUG] Successfully converted {len(molecules)} Pydantic objects to dicts")
                sample = molecules[0]
                logger.info(f"[ADMET_DEBUG] After conversion - sample keys (first 15): {list(sample.keys())[:15]}")
                logger.info(f"[ADMET_DEBUG] After conversion - ADMET values: overall_tox={sample.get('overall_toxicity_score')}, hepato={sample.get('hepatotoxicity_probability')}")
            except Exception as e:
                logger.error(f"[ADMET_DEBUG] Failed to convert molecules to dicts: {e}")
                return {"status": "error", "message": f"Failed to convert molecules: {e}"}

        # Filter molecules that have ADMET data present
        molecules_with_admet = []
        molecules_without_admet = 0

        for mol in molecules:
            # Check if molecule has overall_toxicity_score (primary ADMET indicator)
            if mol.get("overall_toxicity_score") is not None:
                molecules_with_admet.append(mol)
            else:
                molecules_without_admet += 1

        logger.info(
            f"[ADMET_DEBUG] ADMET data availability: "
            f"{len(molecules_with_admet)} molecules WITH ADMET, "
            f"{molecules_without_admet} molecules WITHOUT ADMET"
        )

        if len(molecules_with_admet) == 0:
            logger.error("[ADMET_DEBUG] CRITICAL: ALL molecules missing overall_toxicity_score!")
            logger.error(f"[ADMET_DEBUG] Sample molecule keys: {list(sample.keys())}")
            return {
                "status": "error",
                "message": f"All {len(molecules)} molecules missing ADMET data (overall_toxicity_score is None)"
            }

        # Build ADMET predictions dict for quality gates
        admet_predictions = {}
        safety_analysis = {}

        for mol in molecules_with_admet:
            # Use SMILES as molecule ID (more reliable than 'id' field which may not exist)
            mol_id = mol.get("smiles", f"mol_{hash(str(mol))}")

            admet_predictions[mol_id] = {
                "overall_toxicity_score": mol.get("overall_toxicity_score", 0),
                "hepatotoxicity_probability": mol.get("hepatotoxicity_probability", 0),
                "cardiotoxicity_max_probability": mol.get("cardiotoxicity_max_probability", 0),
                "cyp_inhibition_risk_score": mol.get("cyp_inhibition_risk_score", 0),
                "respiratory_toxicity_probability": mol.get("respiratory_toxicity_probability", 0),
                "logp": mol.get("logp") or mol.get("xlogp", 0)
            }

            # Safety analysis - empty for pre-enriched data (structural alerts not in dataset)
            safety_analysis[mol_id] = {
                "pains_alerts": [],
                "reactive_groups": [],
                "failure_pattern_match": False
            }

        logger.info(
            f"Campaign {self.campaign_id}: ADMET phase complete - "
            f"{len(molecules_with_admet)}/{len(molecules)} molecules with valid ADMET data "
            f"(source: enriched PubChem, NO service calls)"
        )

        return {
            "status": "success",
            "results": {
                "admet_predictions": admet_predictions,
                "safety_analysis": safety_analysis,
                "molecules_screened": len(molecules_with_admet),
                "molecules_skipped": molecules_without_admet,
                "source": "enriched_pubchem",
                "runtime_ms": 50  # Instant filtering - no ML inference
            },
            "service_calls": 0  # Zero service calls - all data pre-calculated!
        }

    async def _execute_compliance_phase(self, service_proxy: ServiceProxy, config_builder) -> Dict[str, Any]:
        """
        Phase 3: Ethics, Safety & Regulatory Compliance Validation

        FAVES = Fairness, Accountability, Validity, Ethics, Safety
        - Validates ETHICS and REGULATORY compliance (NOT ADMET pharmacokinetics)
        - Checks: toxic substances, explosives, controlled substances, DEA schedules
        - Detects: structural alerts, reactive groups, PAINS filters
        - Assesses: AI fairness, bias detection, misuse risk

        IMPORTANT: FAVES does NOT predict ADMET (absorption, metabolism, etc.)
        - ADMET data already pre-calculated in molecules from Phase 1
        - FAVES focuses on safety/ethics at the compliance level
        """
        molecules = self.state.molecules_in_pipeline

        compliance_config = await config_builder.build_compliance_config(molecules)

        # Execute FAVES compliance check (RECURSION BUG FIX: Direct HTTP call)
        logger.info(f"Campaign {self.campaign_id}: Calling FAVES for ethics/safety/regulatory compliance validation")
        result = await service_proxy.call_faves_compliance(compliance_config)

        return result

    async def _execute_validation_phase(self, service_proxy: ServiceProxy, config_builder) -> Dict[str, Any]:
        """Phase 4: Docking → Quantum → MD validation with hybrid PDB/OpenFold3 structure resolution"""
        # Check if target protein available
        target_protein = self.config.get("target_protein")
        if not target_protein:
            logger.info("Skipping validation phase - no target protein defined")
            return {"status": "skipped", "reason": "no_target_protein"}

        # PRODUCTION: Select top 20 for docking validation (increased from 5 for better success rate)
        molecules = self.state.top_candidates[:20] if self.state.top_candidates else self.state.molecules_in_pipeline[:20]

        # Step 0: Resolve protein structure (PDB-first, OpenFold3-fallback)
        target_sequence = self.config.get("target_sequence")  # Optional protein sequence
        structure_source = "unknown"

        try:
            from utils.pdb_cache import get_or_predict_structure

            logger.info(f"Resolving structure for target: {target_protein}")
            pdb_content, structure_source = await get_or_predict_structure(
                target=target_protein,
                sequence=target_sequence,
                prefer_experimental=True  # Try PDB first
            )

            logger.info(f"Structure resolved from {structure_source} (size: {len(pdb_content)} bytes)")

            # Store structure info in config for later use
            context_with_structure = {
                **self.config,
                "use_workflow_engine": False,
                "structure_source": structure_source,
                "structure_size": len(pdb_content)
            }

        except Exception as e:
            logger.error(f"Failed to resolve structure for {target_protein}: {e}")
            return {
                "status": "error",
                "error": f"Structure resolution failed: {str(e)}",
                "target_protein": target_protein
            }

        # Step 1: Docking validation (RECURSION BUG FIX: Direct HTTP call)
        validation_config = await config_builder.build_validation_config(molecules, target_protein)
        docking_result = await service_proxy.call_autodock_gpu(validation_config)

        # Step 2: Quantum validation (if enabled)
        if self.config.get("quantum_enabled"):
            logger.info(f"Quantum enabled for campaign {self.campaign_id}, executing quantum validation")
            # PRODUCTION: Quantum on top 20 molecules
            quantum_result = await self._execute_quantum_validation(molecules[:20], self.iteration_number)

            # Filter molecules by quantum_score > 0.80
            # PRODUCTION: Take top 10 after quantum filtering
            molecules = [m for m in molecules if m.get("quantum_score", 0) > 0.80][:10]
            logger.info(f"Quantum validation complete: {len(molecules)} molecules passed quantum gate")
        else:
            # PRODUCTION: Skip quantum, take top 10 by docking score (increased from 3)
            molecules = molecules[:10]
            logger.info(f"Quantum disabled, skipping to MD with top 10 molecules")

        # Update workflow state with filtered molecules
        self.state.top_candidates = molecules

        # Step 3: MD simulation (with quantum-filtered molecules if enabled)
        # Note: MD execution handled by existing validate_candidates action
        # Return combined result
        return {
            "status": "success",
            "docking_result": docking_result,
            "quantum_enabled": self.config.get("quantum_enabled", False),
            "molecules_after_quantum": len(molecules),
            "structure_source": structure_source,
            "target_protein": target_protein
        }

    async def _execute_quantum_validation(self, molecules: List[Dict], iteration_number: Optional[int] = None) -> Dict[str, Any]:
        """Execute quantum validation sub-phase"""
        logger.info(f"Executing quantum validation for {len(molecules)} molecules")

        # Import db helper
        from core.db_helper import execute_sql, query_sql

        # Insert quantum jobs into database
        job_ids = []
        for molecule in molecules:
            job_id = str(uuid.uuid4())
            job_ids.append((job_id, molecule.get("id")))

            # INSERT quantum job with iteration_number
            await execute_sql("""
                INSERT INTO quantum_jobs (
                    job_id, campaign_id, molecule_id, smiles,
                    quantum_backend, basis, max_iterations, status, submitted_at, iteration_number
                ) VALUES (%s, %s, %s, %s, 'sv1', 'sto-3g', 100, 'pending', GETUTCDATE(), %s)
            """, (job_id, self.campaign_id, molecule.get("id"), molecule.get("smiles"), iteration_number))

        logger.info(f"Inserted {len(job_ids)} quantum jobs for campaign {self.campaign_id}")

        # Poll for completion (with timeout: 30 minutes)
        await self._poll_quantum_jobs([jid for jid, _ in job_ids], timeout=1800)

        # Fetch results and enrich molecules
        return await self._fetch_quantum_results(job_ids, molecules)

    async def _poll_quantum_jobs(self, job_ids: List[str], timeout: int = 1800):
        """Poll quantum_jobs table until all jobs complete or timeout"""
        from core.db_helper import query_sql

        start_time = time.time()
        logger.info(f"Polling {len(job_ids)} quantum jobs (timeout: {timeout}s)")

        while time.time() - start_time < timeout:
            # Check completion status
            placeholders = ",".join(["%s" for _ in job_ids])
            completed_result = await query_sql(f"""
                SELECT COUNT(*) as done
                FROM quantum_jobs
                WHERE job_id IN ({placeholders}) AND status IN ('completed', 'failed')
            """, tuple(job_ids))

            completed_count = completed_result[0]['done'] if completed_result else 0

            if completed_count == len(job_ids):
                logger.info(f"All {len(job_ids)} quantum jobs completed")
                return

            logger.debug(f"Quantum jobs: {completed_count}/{len(job_ids)} completed")
            await asyncio.sleep(10)  # Poll every 10 seconds

        raise TimeoutError(f"Quantum jobs did not complete within {timeout}s")

    async def _fetch_quantum_results(self, job_ids: List[Tuple[str, str]], molecules: List[Dict]):
        """Fetch quantum results and enrich molecule data"""
        from core.db_helper import query_sql

        # Query quantum results
        placeholders = ",".join(["%s" for jid, _ in job_ids])
        results = await query_sql(f"""
            SELECT job_id, molecule_id, ground_state_energy, quantum_score,
                   compute_path, gpu_runtime_seconds
            FROM quantum_jobs
            WHERE job_id IN ({placeholders})
        """, tuple([jid for jid, _ in job_ids]))

        # Enrich molecules with quantum scores
        result_map = {r['molecule_id']: r for r in results}
        for mol in molecules:
            quantum_data = result_map.get(mol.get("id"), {})
            mol['quantum_score'] = quantum_data.get('quantum_score', 0)
            mol['ground_state_energy'] = quantum_data.get('ground_state_energy')
            mol['quantum_compute_path'] = quantum_data.get('compute_path')

        logger.info(f"Enriched {len(molecules)} molecules with quantum results")

        return {"status": "success", "molecules": molecules}

    async def _update_iteration_phase(
        self,
        phase_name: str,
        input_count: int,
        output_count: int,
        iteration_id: Optional[str] = None
    ) -> None:
        """
        Update phase funnel metrics in campaign_iterations table (Research DB).

        Args:
            phase_name: 'phase_1', 'phase_2', 'phase_3', 'phase_4'
            input_count: Number of molecules entering this phase
            output_count: Number of molecules passing quality gate
            iteration_id: Optional iteration_id to update (if None, queries by campaign_id + latest)
        """
        VALID_PHASE_COLUMNS = {"phase_1", "phase_2", "phase_3", "phase_4", "phase_5"}
        if phase_name not in VALID_PHASE_COLUMNS:
            raise ValueError(f"Invalid phase_name: {phase_name}")

        try:
            from core.db_helper import execute_sql

            pass_rate = output_count / input_count if input_count > 0 else 0.0

            if iteration_id:
                # Update specific iteration by ID
                await execute_sql(f"""
                    UPDATE campaign_iterations
                    SET {phase_name}_input = %s,
                        {phase_name}_output = %s,
                        {phase_name}_pass_rate = %s
                    WHERE iteration_id = %s
                """, (input_count, output_count, pass_rate, iteration_id))
            else:
                # Update latest iteration for this campaign
                await execute_sql(f"""
                    UPDATE campaign_iterations
                    SET {phase_name}_input = %s,
                        {phase_name}_output = %s,
                        {phase_name}_pass_rate = %s
                    WHERE campaign_id = %s AND status = 'running'
                """, (input_count, output_count, pass_rate, self.campaign_id))

            logger.info(f"Updated {phase_name} metrics for campaign {self.campaign_id}: {input_count} -> {output_count} (pass rate: {pass_rate:.2%})")

        except Exception as e:
            logger.error(f"Failed to update iteration phase metrics: {e}", exc_info=True)

    async def _execute_optimization_phase(self, service_proxy: ServiceProxy, config_builder) -> Dict[str, Any]:
        """Phase 5: Lead optimization and improvement verification
        TEST CONFIG: Optimize top 3 leads (reduced from 5)
        """
        # TEST CONFIG: Top 3 leads (reduced from 5)
        top_leads = self.state.top_candidates[:3] if self.state.top_candidates else self.state.molecules_in_pipeline[:3]

        if not top_leads:
            return {"status": "error", "message": "No leads to optimize"}

        opt_config = await config_builder.build_optimization_config(
            leads=top_leads,
            goal_properties=self.config.get("goal", {}).get("successMetrics", {}).get("targetProperties", [])
        )

        # Lead-Optimization service expects top-level 'smiles' (not 'molecules')
        # Choose the first available lead SMILES as the seed for optimization
        lead_smiles = None
        for lead in top_leads:
            s = None
            if isinstance(lead, dict):
                s = lead.get("smiles") or lead.get("SMILES")
            if isinstance(lead, str):
                s = lead
            if s:
                lead_smiles = s
                break

        if not lead_smiles:
            return {"status": "error", "message": "No SMILES found in top leads for optimization"}

        # Minimal, schema-compatible payload for the service
        # Keep it lean: service will use defaults for objectives if not provided
        opt_request = {
            "smiles": lead_smiles,
            "n_candidates": max(1, int(opt_config.get("n_candidates", 16)))
        }

        logger.info(
            f"Optimization payload prepared (smiles only)",
            extra={"smiles_len": len(lead_smiles), "n_candidates": opt_request["n_candidates"]}
        )

        # Execute lead optimization (direct HTTP call via ServiceProxy)
        result = await service_proxy.call_lead_optimization(opt_request)

        # Enrich generated variants with chem-props (SA/properties) and addie-models (ADMET)
        variants = result.get("variants", [])
        if variants:
            logger.info(f"Enriching {len(variants)} variants with chem-props and addie-models")
            enriched = await service_proxy.enrich_variants(variants)
            result["variants"] = enriched
            logger.info(f"Enrichment complete for {len(enriched)} variants")

        return result

    async def evaluate_phase_gate(self, phase_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate quality gate(s) for current phase

        Returns:
            {
                "gate_id": str,
                "passed": bool,
                "failures": [...],
                "action": PhaseAction,
                "target_phase": WorkflowPhase (if loop_back),
                "metrics": {...}
            }
        """
        from .quality_gates import QualityGateFactory

        phase_def = self.phase_definitions[self.state.current_phase]
        gate_ids = phase_def.get("quality_gates", [phase_def.get("quality_gate")])

        if not gate_ids or gate_ids == [None]:
            # No quality gate for this phase, proceed
            return {
                "passed": True,
                "action": PhaseAction.PROCEED,
                "next_phase": phase_def.get("next_phase")
            }

        # Evaluate all gates for this phase
        gate_results = []
        overall_passed = True

        for gate_id in gate_ids:
            gate = QualityGateFactory.create(
                gate_id=gate_id,
                campaign_config=self.config
            )

            gate_result = await gate.evaluate(phase_result, self.state)
            gate_results.append(gate_result)

            # Persist gate evaluation to Research DB
            await self._store_gate_evaluation(gate_id, gate_result)

            if not gate_result["passed"]:
                overall_passed = False
                logger.warning(f"Quality gate {gate_id} failed: {gate_result.get('failures')}")

        # Update funnel metrics in campaign_iterations table
        # Map workflow phases to database phase columns (phase_1, phase_2, etc.)
        phase_mapping = {
            WorkflowPhase.RETRIEVAL: "phase_1",
            WorkflowPhase.ADMET_SCREENING: "phase_2",
            WorkflowPhase.COMPLIANCE: "phase_3",
            WorkflowPhase.VALIDATION: "phase_4",
            WorkflowPhase.OPTIMIZATION: "phase_5"
        }

        if self.state.current_phase in phase_mapping and gate_results:
            phase_column = phase_mapping[self.state.current_phase]
            # Aggregate metrics from all gates for this phase
            total_evaluated = sum(gr.get("metrics", {}).get("molecules_evaluated", 0) for gr in gate_results)
            total_passed = sum(gr.get("metrics", {}).get("molecules_passed", 0) for gr in gate_results)

            # Update funnel metrics (use max to avoid double-counting multi-gate phases)
            input_count = max([gr.get("metrics", {}).get("molecules_evaluated", 0) for gr in gate_results] + [0])
            output_count = min([gr.get("metrics", {}).get("molecules_passed", 0) for gr in gate_results] + [input_count])

            await self._update_iteration_phase(phase_column, input_count, output_count, self.iteration_id)

        # Determine action based on gate results
        if overall_passed:
            action = PhaseAction.PROCEED
            next_phase = phase_def.get("next_phase")
            adjustments = None

            # Log phase completion (compliance-safe audit trail)
            if self.decision_logger:
                try:
                    await self.decision_logger.log_phase_completion(
                        phase=self.state.current_phase.value,
                        metrics={
                            "molecules_evaluated": sum(gr.get("metrics", {}).get("molecules_evaluated", 0) for gr in gate_results),
                            "molecules_passed": sum(gr.get("metrics", {}).get("molecules_passed", 0) for gr in gate_results),
                            "pass_rate": sum(gr.get("metrics", {}).get("pass_rate", 0) for gr in gate_results) / len(gate_results) if gate_results else 0
                        },
                        gate_passed=True
                    )
                except Exception as e:
                    logger.error(f"Failed to log phase completion: {e}")

            # SPECIAL CASE: Optimization phase - check for therapeutic discovery
            if self.state.current_phase == WorkflowPhase.OPTIMIZATION:
                discovery_result = await self._check_therapeutic_discovery()
                if discovery_result["discovery_made"]:
                    # Sufficient high-quality candidates found - campaign complete!
                    action = PhaseAction.DISCOVERY
                    next_phase = None
                    logger.info(f"Campaign {self.campaign_id} therapeutic discovery complete: {discovery_result['candidates_count']} high-quality leads identified")

                    # Log therapeutic discovery
                    if self.decision_logger:
                        try:
                            await self.decision_logger.log_therapeutic_discovery(
                                final_candidates=self.state.top_candidates[:5],
                                discovery_summary={
                                    "avg_score": discovery_result.get("high_quality_count", 0) / max(1, len(self.state.top_candidates)),
                                    "threshold": self.config.get('constraints', {}).get('therapeutic_score_threshold', 0.85)
                                }
                            )
                        except Exception as e:
                            logger.error(f"Failed to log therapeutic discovery: {e}")
                else:
                    # Not enough candidates, loop back to Generation with adjusted parameters
                    action = PhaseAction.LOOP_BACK
                    next_phase = WorkflowPhase.RETRIEVAL
                    logger.info(f"Campaign {self.campaign_id} optimization complete but therapeutic criteria not met: {discovery_result['reason']} - looping back to Generation")
        else:
            # ADAPTIVE LEARNING: Adjust parameters based on failures before determining action
            adjustments = await self.adjust_parameters_on_failure(gate_results)

            # Check failure threshold and determine action
            action, next_phase = await self._determine_failure_action(gate_results)

            logger.info(f"Gate failures detected for campaign {self.campaign_id}: made {len(adjustments.get('changes', []))} parameter adjustments")

            # Log gate failure and reconfiguration decision (compliance-safe)
            # Only log if actual parameter changes were made
            if self.decision_logger and len(adjustments.get('changes', [])) > 0:
                try:
                    await self.decision_logger.log_gate_failure_reconfiguration(
                        phase=self.state.current_phase.value,
                        gate_failures=gate_results,
                        literature_context={},  # Will be populated on next generation
                        parameter_adjustments=adjustments
                    )
                except Exception as e:
                    logger.error(f"Failed to log gate failure: {e}")
            elif self.decision_logger:
                # Log that gate failed but no adjustments were possible
                logger.info(f"Gate failure in {self.state.current_phase.value} - no auto-adjustable parameters (may require human review)")

        return {
            "gate_results": gate_results,
            "passed": overall_passed,
            "action": action,
            "next_phase": next_phase,
            "phase": self.state.current_phase.value,
            "parameter_adjustments": adjustments
        }

    async def _check_therapeutic_discovery(self) -> Dict[str, Any]:
        """
        Check if therapeutic discovery criteria are met after Optimization phase.
        TEST CONFIG: Need ≥2 high-quality final candidates for testing
        Production: Need ≥5 high-quality final candidates

        Returns:
            {
                "discovery_made": bool,
                "candidates_count": int,
                "high_quality_count": int,
                "reason": str
            }
        """
        try:
            # Get final candidates from workflow state
            final_candidates = self.state.top_candidates

            # Define therapeutic quality thresholds
            THERAPEUTIC_THRESHOLD = self.config.get('constraints', {}).get('therapeutic_score_threshold', 0.85)
            # TEST CONFIG: 2 candidates required (for 50-molecule funnel)
            # Production: 5 candidates required
            MIN_CANDIDATES_REQUIRED = 2

            if len(final_candidates) < MIN_CANDIDATES_REQUIRED:
                return {
                    "discovery_made": False,
                    "candidates_count": len(final_candidates),
                    "high_quality_count": 0,
                    "reason": f"Only {len(final_candidates)} candidates survived (need {MIN_CANDIDATES_REQUIRED})"
                }

            # Filter candidates by therapeutic quality threshold
            high_quality_leads = [
                c for c in final_candidates
                if c.get('composite_score', 0) >= THERAPEUTIC_THRESHOLD
            ]

            if len(high_quality_leads) >= MIN_CANDIDATES_REQUIRED:
                # SUCCESS: Campaign discovered therapeutic candidates!
                logger.info(f"Campaign {self.campaign_id} DISCOVERY: {len(high_quality_leads)} therapeutic-quality leads identified")

                # Store top leads as discoveries
                for lead in high_quality_leads[:MIN_CANDIDATES_REQUIRED]:
                    await self._store_final_discovery(lead)

                return {
                    "discovery_made": True,
                    "candidates_count": len(final_candidates),
                    "high_quality_count": len(high_quality_leads),
                    "reason": f"Success: {len(high_quality_leads)} candidates exceed therapeutic threshold ({THERAPEUTIC_THRESHOLD})"
                }
            else:
                # Not enough high-quality leads, need to loop back
                return {
                    "discovery_made": False,
                    "candidates_count": len(final_candidates),
                    "high_quality_count": len(high_quality_leads),
                    "reason": f"Only {len(high_quality_leads)} candidates exceed threshold {THERAPEUTIC_THRESHOLD} (need {MIN_CANDIDATES_REQUIRED})"
                }

        except Exception as e:
            logger.error(f"Error checking therapeutic discovery: {e}", exc_info=True)
            return {
                "discovery_made": False,
                "candidates_count": 0,
                "high_quality_count": 0,
                "reason": f"Error checking discovery: {str(e)}"
            }

    async def _store_final_discovery(self, lead: Dict[str, Any]) -> None:
        """
        Store final therapeutic discovery to Research DB with 'therapeutic' type.

        Args:
            lead: Final lead candidate with all validation scores
        """
        try:
            from core.db_helper import execute_sql

            discovery_id = str(uuid.uuid4())

            await execute_sql("""
                INSERT INTO campaign_discoveries (
                    discovery_id, campaign_id, iteration_number, discovered_at,
                    smiles, significance, discovery_type,
                    binding_affinity, quantum_score, admet_score,
                    properties, validation_results
                ) VALUES (%s, %s, %s, GETUTCDATE(), %s, %s, 'therapeutic', %s, %s, %s, %s, %s)
            """, (
                discovery_id,
                self.campaign_id,
                self.iteration_number,
                lead.get('smiles', ''),
                lead.get('composite_score', 0),
                lead.get('binding_affinity'),
                lead.get('quantum_score'),
                lead.get('admet_score'),
                json.dumps(lead.get('properties', {})),
                json.dumps(lead.get('validation_results', {}))
            ))

            logger.info(f"Stored therapeutic discovery: {lead.get('smiles', '')[:50]} (score: {lead.get('composite_score', 0):.3f})")

        except Exception as e:
            logger.error(f"Failed to store final discovery: {e}", exc_info=True)

    async def _store_gate_evaluation(self, gate_id: str, gate_result: Dict[str, Any]) -> None:
        """
        Store quality gate evaluation results to Research DB.

        Args:
            gate_id: Gate identifier (e.g., 'molecular_constraints', 'admet_filters')
            gate_result: Gate evaluation result from QualityGate.evaluate()
        """
        try:
            from core.db_helper import execute_sql

            evaluation_id = str(uuid.uuid4())
            metrics = gate_result.get('metrics', {})
            failures_json = json.dumps(gate_result.get('failures', []))

            # Insert quality gate evaluation (use 'id' not 'evaluation_id')
            await execute_sql("""
                INSERT INTO quality_gate_evaluations (
                    id, campaign_id, gate_id, phase, evaluation_timestamp, passed,
                    failures, metrics, molecules_evaluated, molecules_passed
                ) VALUES (%s, %s, %s, %s, GETUTCDATE(), %s, %s, %s, %s, %s)
            """, (
                evaluation_id,
                self.campaign_id,
                gate_id,
                self.state.current_phase.value,
                1 if gate_result.get('passed') else 0,
                failures_json,
                json.dumps(metrics),
                metrics.get('molecules_evaluated', 0),
                metrics.get('molecules_passed', 0)
            ))

            logger.info(f"Stored quality gate evaluation: {gate_id} for campaign {self.campaign_id} (iteration {self.iteration_number})")

        except Exception as e:
            logger.error(f"Failed to store quality gate evaluation: {e}", exc_info=True)

    async def adjust_parameters_on_failure(self, gate_results: List[Dict]) -> Dict[str, Any]:
        """
        Adaptively adjust campaign parameters based on quality gate failures.
        Learns from failures to improve success rates in subsequent iterations.

        Args:
            gate_results: List of quality gate evaluation results

        Returns:
            Dict of parameter adjustments made with rationale
        """
        adjustments = {
            'timestamp': datetime.utcnow().isoformat(),
            'trigger': 'quality_gate_failure',
            'changes': []
        }

        try:
            # Detect strict fragment mode: enforce user MW upper bound if <= 200
            mol_cfg = (self.config.get('constraints') or {}).get('molecular') or {}
            mw_cfg = mol_cfg.get('mw') or {}
            user_mw_max = mw_cfg.get('max')
            strict_fragment_mode = user_mw_max is not None and user_mw_max <= 200

            # Locks: prevent changes to user-locked fields
            locks_meta = (self.config.get('constraints_meta') or {}).get('locks') or {}
            lock_mw = (locks_meta.get('molecular') or {}).get('mw') or {}
            lock_logp = (locks_meta.get('molecular') or {}).get('logp') or {}
            for gate_result in gate_results:
                if gate_result.get('passed'):
                    continue  # Only adjust on failures

                gate_id = gate_result.get('gate_id', 'unknown')
                failure_type = gate_result.get('failure_type', '')
                failures = gate_result.get('failures', [])
                severity = gate_result.get('severity', 'medium')

                logger.info(f"Adjusting parameters for {gate_id} failure (type: {failure_type}, severity: {severity})")

                # 1. Molecular constraints failures → Relax MW/LogP constraints aggressively (25%)
                if gate_id == 'molecular_constraints' or 'molecular_properties' in failure_type:
                    if 'constraints' not in self.config:
                        self.config['constraints'] = {}
                    if 'molecular' not in self.config['constraints']:
                        self.config['constraints']['molecular'] = {}

                    mol_constraints = self.config['constraints']['molecular']

                    # Relax molecular weight (unless strict fragment mode with user cap ≤ 200)
                    if 'mw' in mol_constraints and 'max' in mol_constraints['mw']:
                        if lock_mw.get('max'):
                            logger.info("MW max is locked by user; skipping relaxation")
                            pass
                        else:
                            old_mw = mol_constraints['mw']['max']
                            if strict_fragment_mode:
                                # Enforce user cap: do not increase above 200 for fragments
                                new_mw = min(old_mw, 200)
                                if new_mw != old_mw:
                                    mol_constraints['mw']['max'] = new_mw
                                    adjustments['changes'].append({
                                        'parameter': 'constraints.molecular.mw.max',
                                        'old_value': old_mw,
                                        'new_value': new_mw,
                                        'reason': 'Strict fragment mode: enforce MW max ≤ 200',
                                        'adjustment': 'clamped'
                                    })
                                    logger.info(f"Enforced fragment MW cap: {old_mw} -> {new_mw}")
                            else:
                                new_mw = old_mw * 1.25  # 25% increase
                                mol_constraints['mw']['max'] = new_mw
                                adjustments['changes'].append({
                                    'parameter': 'constraints.molecular.mw.max',
                                    'old_value': old_mw,
                                    'new_value': new_mw,
                                    'reason': f'Molecular weight constraint too strict (gate: {gate_id})',
                                    'adjustment': '+25%'
                                })
                                logger.info(f"Relaxed MW max: {old_mw} -> {new_mw}")

                    # Relax LogP (unless strict fragment mode; cap to ≤ 3.0 typical fragment ceiling)
                    if 'logp' in mol_constraints and 'max' in mol_constraints['logp']:
                        if lock_logp.get('max'):
                            logger.info("LogP max is locked by user; skipping relaxation")
                        else:
                            old_logp = mol_constraints['logp']['max']
                            if strict_fragment_mode:
                                new_logp = min(old_logp, 3.0)
                                if new_logp != old_logp:
                                    mol_constraints['logp']['max'] = new_logp
                                    adjustments['changes'].append({
                                        'parameter': 'constraints.molecular.logp.max',
                                        'old_value': old_logp,
                                        'new_value': new_logp,
                                        'reason': 'Strict fragment mode: enforce LogP max ≤ 3.0',
                                        'adjustment': 'clamped'
                                    })
                                    logger.info(f"Enforced fragment LogP cap: {old_logp} -> {new_logp}")
                            else:
                                new_logp = old_logp * 1.25
                                mol_constraints['logp']['max'] = new_logp
                                adjustments['changes'].append({
                                    'parameter': 'constraints.molecular.logp.max',
                                    'old_value': old_logp,
                                    'new_value': new_logp,
                                    'reason': f'LogP constraint too strict (gate: {gate_id})',
                                    'adjustment': '+25%'
                                })
                                logger.info(f"Relaxed LogP max: {old_logp} -> {new_logp}")

                # 2. ADMET failures → Increase diversity, lower novelty
                elif gate_id in ('admet_filters', 'safety_screening') or failure_type in ('toxicity', 'admet'):
                    # Adjust retrieval strategy to prioritize safety
                    if 'retrieval' not in self.config:
                        self.config['retrieval'] = {}

                    gen_config = self.config['retrieval']

                    # Increase diversity to explore safer chemical space
                    old_diversity = gen_config.get('diversity', 0.5)
                    new_diversity = min(0.9, old_diversity + 0.2)
                    gen_config['diversity'] = new_diversity
                    adjustments['changes'].append({
                        'parameter': 'generation.diversity',
                        'old_value': old_diversity,
                        'new_value': new_diversity,
                        'reason': f'ADMET failures - increase chemical space exploration (gate: {gate_id})',
                        'adjustment': '+0.2 (capped at 0.9)'
                    })
                    logger.info(f"Increased diversity: {old_diversity} -> {new_diversity}")

                    # Decrease novelty to stay in known-safe space
                    old_novelty = gen_config.get('novelty', 0.5)
                    new_novelty = max(0.2, old_novelty - 0.2)
                    gen_config['novelty'] = new_novelty
                    adjustments['changes'].append({
                        'parameter': 'generation.novelty',
                        'old_value': old_novelty,
                        'new_value': new_novelty,
                        'reason': f'ADMET failures - prioritize known-safe chemical space (gate: {gate_id})',
                        'adjustment': '-0.2 (floor at 0.2)'
                    })
                    logger.info(f"Decreased novelty: {old_novelty} -> {new_novelty}")

                    # Relax ADMET thresholds (adaptive learning from failures)
                    if 'thresholds' not in self.config:
                        self.config['thresholds'] = {}
                    if 'admet' not in self.config['thresholds']:
                        self.config['thresholds']['admet'] = {}

                    admet_thresholds = self.config['thresholds']['admet']

                    # Relax overall toxicity by 5% (capped at 0.85)
                    old_tox = admet_thresholds.get('overall_toxicity', 0.70)
                    new_tox = min(0.85, old_tox + 0.05)
                    admet_thresholds['overall_toxicity'] = new_tox
                    adjustments['changes'].append({
                        'parameter': 'thresholds.admet.overall_toxicity',
                        'old_value': old_tox,
                        'new_value': new_tox,
                        'reason': f'ADMET failures - relaxing toxicity threshold (gate: {gate_id})',
                        'adjustment': '+5% (capped at 0.85)'
                    })
                    logger.info(f"Relaxed overall toxicity: {old_tox} -> {new_tox}")

                    # Relax hepatotoxicity by 5% (capped at 0.85)
                    old_hepato = admet_thresholds.get('hepatotoxicity', 0.70)
                    new_hepato = min(0.85, old_hepato + 0.05)
                    admet_thresholds['hepatotoxicity'] = new_hepato
                    adjustments['changes'].append({
                        'parameter': 'thresholds.admet.hepatotoxicity',
                        'old_value': old_hepato,
                        'new_value': new_hepato,
                        'reason': f'ADMET failures - relaxing hepatotoxicity threshold (gate: {gate_id})',
                        'adjustment': '+5% (capped at 0.85)'
                    })
                    logger.info(f"Relaxed hepatotoxicity: {old_hepato} -> {new_hepato}")

                    # Relax cardiotoxicity by 5% (capped at 0.80)
                    old_cardio = admet_thresholds.get('cardiotoxicity', 0.65)
                    new_cardio = min(0.80, old_cardio + 0.05)
                    admet_thresholds['cardiotoxicity'] = new_cardio
                    adjustments['changes'].append({
                        'parameter': 'thresholds.admet.cardiotoxicity',
                        'old_value': old_cardio,
                        'new_value': new_cardio,
                        'reason': f'ADMET failures - relaxing cardiotoxicity threshold (gate: {gate_id})',
                        'adjustment': '+5% (capped at 0.80)'
                    })
                    logger.info(f"Relaxed cardiotoxicity: {old_cardio} -> {new_cardio}")

                # 3. Binding affinity failures → Adjust docking parameters
                elif gate_id == 'binding_affinity' or 'binding' in failure_type:
                    if 'docking' not in self.config:
                        self.config['docking'] = {}

                    docking_config = self.config['docking']

                    # Increase exhaustiveness for more thorough docking
                    old_exhaustiveness = docking_config.get('exhaustiveness', 8)
                    new_exhaustiveness = min(32, old_exhaustiveness + 4)
                    docking_config['exhaustiveness'] = new_exhaustiveness
                    adjustments['changes'].append({
                        'parameter': 'docking.exhaustiveness',
                        'old_value': old_exhaustiveness,
                        'new_value': new_exhaustiveness,
                        'reason': f'Binding affinity failures - increase docking thoroughness (gate: {gate_id})',
                        'adjustment': '+4 (capped at 32)'
                    })
                    logger.info(f"Increased docking exhaustiveness: {old_exhaustiveness} -> {new_exhaustiveness}")

                    # Relax binding affinity threshold slightly
                    if 'thresholds' not in self.config:
                        self.config['thresholds'] = {}

                    # FRAGMENT FIX: Use fragment-appropriate default (-4.0) instead of drug-like (-8.0)
                    # Detect fragment campaign from name/goal
                    campaign_name = (self.config.get('name') or '').lower()
                    goal = self.config.get('goal') or {}
                    if isinstance(goal, str):
                        try:
                            goal = json.loads(goal)
                        except:
                            goal = {'description': goal}
                    goal_desc = goal.get('description', '').lower() if isinstance(goal, dict) else ''
                    combined_text = f"{campaign_name} {goal_desc}"
                    is_fragment_campaign = any(kw in combined_text for kw in ['fragment', 'fbdd', 'fragment-based', 'hinge'])

                    # Use appropriate default: -4.0 for fragments, -7.0 for drug-like
                    default_threshold = -4.0 if is_fragment_campaign else -7.0
                    old_binding_threshold = self.config['thresholds'].get('binding_affinity', default_threshold)

                    # Relax by 10% (less strict) - but floor at -3.0 for fragments
                    new_binding_threshold = old_binding_threshold * 0.9
                    if is_fragment_campaign:
                        new_binding_threshold = max(new_binding_threshold, -3.5)  # Don't go below -3.5 for fragments

                    self.config['thresholds']['binding_affinity'] = new_binding_threshold
                    adjustments['changes'].append({
                        'parameter': 'thresholds.binding_affinity',
                        'old_value': old_binding_threshold,
                        'new_value': new_binding_threshold,
                        'reason': f'Binding affinity threshold may be too strict (gate: {gate_id}, fragment={is_fragment_campaign})',
                        'adjustment': '10% less strict'
                    })
                    logger.info(f"Relaxed binding threshold: {old_binding_threshold} -> {new_binding_threshold} (fragment={is_fragment_campaign})")

                # 4. Compliance failures → Add stricter safety filters
                elif gate_id == 'compliance_check' or 'compliance' in failure_type:
                    if 'constraints' not in self.config:
                        self.config['constraints'] = {}
                    if 'safety' not in self.config['constraints']:
                        self.config['constraints']['safety'] = {}

                    safety_constraints = self.config['constraints']['safety']

                    # Enable all safety filters
                    safety_constraints['pains_filter'] = True
                    safety_constraints['aggregators_filter'] = True
                    safety_constraints['frequent_hitters_filter'] = True

                    adjustments['changes'].append({
                        'parameter': 'constraints.safety.filters',
                        'old_value': 'partial',
                        'new_value': 'all_enabled',
                        'reason': f'Compliance failures - enable all safety filters (gate: {gate_id})',
                        'adjustment': 'enabled PAINS, aggregators, frequent hitters'
                    })
                    logger.info(f"Enabled all safety filters for compliance")

                # 5. MD stability failures → Adjust MD parameters
                elif gate_id == 'md_stability' or 'stability' in failure_type:
                    if 'md_simulation' not in self.config:
                        self.config['md_simulation'] = {}

                    md_config = self.config['md_simulation']

                    # Increase simulation time for better stability assessment
                    old_time = md_config.get('simulation_time_ns', 5)
                    new_time = min(20, old_time + 5)
                    md_config['simulation_time_ns'] = new_time
                    adjustments['changes'].append({
                        'parameter': 'md_simulation.simulation_time_ns',
                        'old_value': old_time,
                        'new_value': new_time,
                        'reason': f'MD stability failures - longer simulation for better assessment (gate: {gate_id})',
                        'adjustment': '+5ns (capped at 20ns)'
                    })
                    logger.info(f"Increased MD simulation time: {old_time}ns -> {new_time}ns")

            # Store adjustments in database for audit trail
            if adjustments['changes']:
                await self._store_parameter_adjustments(adjustments)

            return adjustments

        except Exception as e:
            logger.error(f"Failed to adjust parameters: {e}", exc_info=True)
            return {
                'timestamp': datetime.utcnow().isoformat(),
                'trigger': 'quality_gate_failure',
                'changes': [],
                'error': str(e)
            }

    async def _store_parameter_adjustments(self, adjustments: Dict[str, Any]) -> None:
        """
        Store parameter adjustments to Research DB for audit trail and analysis.

        Args:
            adjustments: Dictionary of parameter changes
        """
        try:
            from core.db_helper import execute_sql

            adjustments_json = json.dumps(adjustments)

            # Update current running iteration with adjustments_made
            # CRITICAL FIX: Use iteration_id for indexed UPDATE instead of campaign_id scan
            if self.iteration_id:
                await execute_sql("""
                    UPDATE campaign_iterations
                    SET adjustments_made = %s
                    WHERE iteration_id = %s
                """, (adjustments_json, self.iteration_id))
            else:
                # Fallback if iteration_id not available (shouldn't happen)
                await execute_sql("""
                    UPDATE campaign_iterations
                    SET adjustments_made = %s
                    WHERE campaign_id = %s AND status = 'running'
                """, (adjustments_json, self.campaign_id))

            logger.info(f"Stored {len(adjustments['changes'])} parameter adjustments for campaign {self.campaign_id}")

        except Exception as e:
            logger.error(f"Failed to store parameter adjustments: {e}", exc_info=True)

    async def _determine_failure_action(self, gate_results: List[Dict]) -> Tuple[PhaseAction, Optional[WorkflowPhase]]:
        """Determine action based on quality gate failures"""
        # Check if human intervention required (based on autonomy level)
        # Defensive: handle case where self.config or autonomy may be None
        autonomy = ((self.config or {}).get("autonomy") or {}).get("level", "full")

        if autonomy == "supervised":
            return PhaseAction.HUMAN_INTERVENTION, None

        # Check failure severity
        critical_failures = [r for r in gate_results if r.get("severity") == "critical"]
        if critical_failures:
            # Critical failure in guided/full mode
            if autonomy == "guided":
                return PhaseAction.HUMAN_INTERVENTION, None
            else:
                # Full autonomous: loop back to generation with adjusted params
                return PhaseAction.LOOP_BACK, WorkflowPhase.RETRIEVAL

        # Non-critical failures: determine loop-back target
        failure_types = [r.get("failure_type") for r in gate_results if not r["passed"]]

        if "toxicity" in failure_types or "safety" in failure_types:
            # Safety issues: loop back to generation
            return PhaseAction.LOOP_BACK, WorkflowPhase.RETRIEVAL
        elif "compliance" in failure_types:
            # Compliance issues: check autonomy
            if autonomy == "full":
                return PhaseAction.LOOP_BACK, WorkflowPhase.RETRIEVAL
            else:
                return PhaseAction.HUMAN_INTERVENTION, None
        elif "binding" in failure_types or "stability" in failure_types:
            # Structural issues: loop back to optimization
            return PhaseAction.LOOP_BACK, WorkflowPhase.OPTIMIZATION
        else:
            # Generic failure: loop back to previous phase
            return PhaseAction.LOOP_BACK, WorkflowPhase.RETRIEVAL

    async def transition_phase(self, action: PhaseAction, next_phase: Optional[WorkflowPhase], reason: str = ""):
        """Execute phase transition based on quality gate action"""
        if action == PhaseAction.PROCEED:
            if next_phase:
                self.state.advance_phase(next_phase)
                logger.info(f"Campaign {self.campaign_id} proceeding to {next_phase.value}")
        elif action == PhaseAction.LOOP_BACK:
            if next_phase:
                self.state.loop_back(next_phase, reason)
        elif action == PhaseAction.DISCOVERY:
            logger.info(f"Campaign {self.campaign_id} discovery made!")
            # Campaign completion handled externally
        elif action == PhaseAction.HALT:
            logger.warning(f"Campaign {self.campaign_id} halted: {reason}")
            # Circuit breaker handled externally

    def get_workflow_state(self) -> Dict[str, Any]:
        """Get current workflow state for persistence"""
        return self.state.to_dict()
