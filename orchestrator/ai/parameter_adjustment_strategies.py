"""
Parameter Adjustment Strategies for Quality Gate Failures

PHASE 3 FIX: Refactored from 283-line monolithic function to Strategy pattern.
Each gate failure type has its own strategy for parameter adjustment.

Original: workflow_engine.py::adjust_parameters_on_failure (lines 1211-1495)
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)


class ParameterAdjustmentStrategy(ABC):
    """Base class for parameter adjustment strategies"""

    def __init__(self, config: Dict[str, Any], constraints_meta: Dict[str, Any]):
        self.config = config
        self.constraints_meta = constraints_meta
        self.locks_meta = (constraints_meta or {}).get('locks') or {}

    @abstractmethod
    def adjust(self, gate_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Apply parameter adjustments for a failed gate.

        Args:
            gate_result: Quality gate evaluation result

        Returns:
            List of changes made: [{parameter, old_value, new_value, reason, adjustment}, ...]
        """
        pass

    def _is_locked(self, path: str) -> bool:
        """Check if a constraint path is locked by user"""
        parts = path.split('.')
        current = self.locks_meta
        for part in parts:
            if not isinstance(current, dict):
                return False
            current = current.get(part, {})
        return bool(current)

    def _get_constraint(self, path: str) -> Any:
        """Get constraint value by dot-separated path"""
        parts = path.split('.')
        current = self.config.get('constraints', {})
        for part in parts:
            if not isinstance(current, dict):
                return None
            current = current.get(part, {})
        return current

    def _set_constraint(self, path: str, value: Any):
        """Set constraint value by dot-separated path"""
        parts = path.split('.')
        current = self.config.setdefault('constraints', {})
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value


class MolecularConstraintsStrategy(ParameterAdjustmentStrategy):
    """Adjust molecular weight and LogP constraints"""

    def adjust(self, gate_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        changes = []

        # Detect strict fragment mode
        mol_cfg = self._get_constraint('molecular') or {}
        mw_cfg = mol_cfg.get('mw') or {}
        user_mw_max = mw_cfg.get('max')
        strict_fragment_mode = user_mw_max is not None and user_mw_max <= 200

        # Relax molecular weight (unless locked or strict fragment mode)
        if not self._is_locked('molecular.mw.max'):
            old_mw = mw_cfg.get('max', 500)
            if strict_fragment_mode:
                # Enforce user cap: do not increase above 200 for fragments
                new_mw = min(old_mw, 200)
            else:
                # Increase MW ceiling by 25%
                new_mw = min(old_mw * 1.25, 800)  # Cap at 800 Da

            if new_mw != old_mw:
                self._set_constraint('molecular.mw.max', new_mw)
                changes.append({
                    'parameter': 'constraints.molecular.mw.max',
                    'old_value': old_mw,
                    'new_value': new_mw,
                    'reason': 'Strict fragment mode: enforce MW max ≤ 200' if strict_fragment_mode else 'Relaxed MW to increase candidate pool',
                    'adjustment': 'clamped' if strict_fragment_mode else 'increased_25%'
                })
        else:
            logger.info("MW max is locked by user; skipping relaxation")

        # Relax LogP constraints
        if not self._is_locked('molecular.logp.max'):
            logp_cfg = mol_cfg.get('logp') or {}
            old_logp = logp_cfg.get('max', 5.0)
            new_logp = min(old_logp + 1.0, 7.0)  # Increase by 1, cap at 7

            if new_logp != old_logp:
                self._set_constraint('molecular.logp.max', new_logp)
                changes.append({
                    'parameter': 'constraints.molecular.logp.max',
                    'old_value': old_logp,
                    'new_value': new_logp,
                    'reason': 'Relaxed LogP to increase lipophilic candidates',
                    'adjustment': 'increased_1.0'
                })

        return changes


class ADMETFiltersStrategy(ParameterAdjustmentStrategy):
    """Adjust ADMET toxicity thresholds"""

    def adjust(self, gate_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        changes = []

        # Get current thresholds
        thresholds = self.config.get('thresholds', {}).get('admet', {})

        # Analyze failure breakdown to target most restrictive filter
        failures = gate_result.get('failures', [])
        violation_counts = {
            'overall_tox': 0,
            'hepatotoxicity': 0,
            'cardiotoxicity': 0,
            'respiratory': 0,
            'cyp450': 0
        }

        for f in failures:
            violations = f.get('violations', [])
            for v in violations:
                if 'Overall toxicity' in v:
                    violation_counts['overall_tox'] += 1
                elif 'Hepatotoxicity' in v:
                    violation_counts['hepatotoxicity'] += 1
                elif 'Cardiotoxicity' in v:
                    violation_counts['cardiotoxicity'] += 1
                elif 'Respiratory' in v:
                    violation_counts['respiratory'] += 1
                elif 'CYP450' in v:
                    violation_counts['cyp450'] += 1

        # Relax the most problematic threshold first
        max_violations = max(violation_counts.values()) if violation_counts else 0

        if max_violations > 0:
            # Find most problematic threshold
            for key, count in violation_counts.items():
                if count == max_violations:
                    if key == 'overall_tox':
                        old_val = thresholds.get('overall_toxicity', 0.75)
                        new_val = min(old_val + 0.05, 0.90)  # Relax by 5%, cap at 0.90
                        thresholds['overall_toxicity'] = new_val
                        changes.append({
                            'parameter': 'thresholds.admet.overall_toxicity',
                            'old_value': old_val,
                            'new_value': new_val,
                            'reason': f'Relaxed due to {count} overall toxicity failures',
                            'adjustment': 'increased_0.05'
                        })
                    elif key == 'hepatotoxicity':
                        old_val = thresholds.get('hepatotoxicity', 0.75)
                        new_val = min(old_val + 0.05, 0.90)
                        thresholds['hepatotoxicity'] = new_val
                        changes.append({
                            'parameter': 'thresholds.admet.hepatotoxicity',
                            'old_value': old_val,
                            'new_value': new_val,
                            'reason': f'Relaxed due to {count} hepatotoxicity failures',
                            'adjustment': 'increased_0.05'
                        })
                    elif key == 'cardiotoxicity':
                        old_val = thresholds.get('cardiotoxicity', 0.70)
                        new_val = min(old_val + 0.05, 0.85)
                        thresholds['cardiotoxicity'] = new_val
                        changes.append({
                            'parameter': 'thresholds.admet.cardiotoxicity',
                            'old_value': old_val,
                            'new_value': new_val,
                            'reason': f'Relaxed due to {count} cardiotoxicity failures',
                            'adjustment': 'increased_0.05'
                        })
                    break  # Only adjust one at a time

            # Update config
            self.config.setdefault('thresholds', {})['admet'] = thresholds

        return changes


class BindingAffinityStrategy(ParameterAdjustmentStrategy):
    """Adjust binding affinity threshold for docking"""

    def adjust(self, gate_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        changes = []

        # Relax binding affinity threshold (less negative = weaker binding OK)
        thresholds = self.config.setdefault('thresholds', {})
        old_threshold = thresholds.get('binding_affinity', -7.0)
        new_threshold = min(old_threshold + 0.5, -5.0)  # Relax by 0.5, cap at -5.0

        if new_threshold != old_threshold:
            thresholds['binding_affinity'] = new_threshold
            changes.append({
                'parameter': 'thresholds.binding_affinity',
                'old_value': old_threshold,
                'new_value': new_threshold,
                'reason': 'Relaxed binding affinity threshold to accept weaker binders',
                'adjustment': 'increased_0.5'
            })

        return changes


class ComplianceStrategy(ParameterAdjustmentStrategy):
    """Adjust compliance thresholds (FAVES)"""

    def adjust(self, gate_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        changes = []

        # Analyze compliance failures
        failures = gate_result.get('failures', [])
        ethical_failures = sum(1 for f in failures if 'Ethical score' in str(f.get('violations', [])))
        regulatory_failures = sum(1 for f in failures if 'Regulatory' in str(f.get('violations', [])))
        dual_use_failures = sum(1 for f in failures if 'Dual-use' in str(f.get('violations', [])))

        # Note: Compliance is generally NOT adjustable (safety critical)
        # Log warnings instead of relaxing
        if ethical_failures > 0:
            logger.warning(f"COMPLIANCE: {ethical_failures} ethical score failures - cannot auto-adjust (safety critical)")

        if regulatory_failures > 0:
            logger.warning(f"COMPLIANCE: {regulatory_failures} regulatory failures - cannot auto-adjust (safety critical)")

        if dual_use_failures > 0:
            logger.warning(f"COMPLIANCE: {dual_use_failures} dual-use risk failures - cannot auto-adjust (safety critical)")

        # For now, don't adjust compliance thresholds automatically
        # This is a safety-critical gate that should require human review
        return changes


class QuantumScoreStrategy(ParameterAdjustmentStrategy):
    """Adjust quantum validation threshold"""

    def adjust(self, gate_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        changes = []

        # Relax quantum score threshold
        thresholds = self.config.setdefault('thresholds', {})
        old_threshold = thresholds.get('quantum_score', 0.80)
        new_threshold = max(old_threshold - 0.05, 0.60)  # Relax by 5%, floor at 0.60

        if new_threshold != old_threshold:
            thresholds['quantum_score'] = new_threshold
            changes.append({
                'parameter': 'thresholds.quantum_score',
                'old_value': old_threshold,
                'new_value': new_threshold,
                'reason': 'Relaxed quantum validation threshold',
                'adjustment': 'decreased_0.05'
            })

        return changes


class MDStabilityStrategy(ParameterAdjustmentStrategy):
    """Adjust MD simulation stability thresholds"""

    def adjust(self, gate_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        changes = []

        # Analyze MD failures
        failures = gate_result.get('failures', [])
        rmsd_failures = sum(1 for f in failures if 'RMSD' in str(f.get('violations', [])))
        binding_energy_failures = sum(1 for f in failures if 'ΔG' in str(f.get('violations', [])))

        md_thresholds = self.config.setdefault('thresholds', {}).setdefault('md_stability', {})

        # Relax RMSD threshold if that's the main problem
        if rmsd_failures > binding_energy_failures:
            old_rmsd = md_thresholds.get('rmsd', 3.0)
            new_rmsd = min(old_rmsd + 0.5, 5.0)  # Relax by 0.5 Å, cap at 5.0 Å

            if new_rmsd != old_rmsd:
                md_thresholds['rmsd'] = new_rmsd
                changes.append({
                    'parameter': 'thresholds.md_stability.rmsd',
                    'old_value': old_rmsd,
                    'new_value': new_rmsd,
                    'reason': f'Relaxed RMSD threshold due to {rmsd_failures} stability failures',
                    'adjustment': 'increased_0.5'
                })

        # Relax binding free energy if that's the problem
        elif binding_energy_failures > 0:
            old_energy = md_thresholds.get('binding_free_energy', -5.0)
            new_energy = min(old_energy + 1.0, -3.0)  # Relax by 1 kcal/mol, cap at -3.0

            if new_energy != old_energy:
                md_thresholds['binding_free_energy'] = new_energy
                changes.append({
                    'parameter': 'thresholds.md_stability.binding_free_energy',
                    'old_value': old_energy,
                    'new_value': new_energy,
                    'reason': f'Relaxed binding energy threshold due to {binding_energy_failures} failures',
                    'adjustment': 'increased_1.0'
                })

        return changes


class OptimizationImprovementStrategy(ParameterAdjustmentStrategy):
    """Adjust optimization improvement threshold"""

    def adjust(self, gate_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        changes = []

        # Note: Optimization improvement is typically not adjustable
        # The requirement is >10% improvement - lowering this defeats the purpose
        # Instead, log that optimization is failing
        failures = gate_result.get('failures', [])
        improvement_failures = sum(1 for f in failures if 'Improvement' in str(f.get('violations', [])))

        if improvement_failures > 0:
            logger.warning(f"OPTIMIZATION: {improvement_failures} molecules failed to show >10% improvement")
            logger.warning("OPTIMIZATION: Consider adjusting optimization goals or allowing more optimization cycles")

        # Don't auto-adjust this threshold (defeats purpose of optimization)
        return changes


class ParameterAdjustmentStrategyFactory:
    """Factory for creating parameter adjustment strategies"""

    _strategies = {
        'molecular_constraints': MolecularConstraintsStrategy,
        'admet_filters': ADMETFiltersStrategy,
        'binding_affinity': BindingAffinityStrategy,
        'compliance_check': ComplianceStrategy,
        'quantum_score': QuantumScoreStrategy,
        'md_stability': MDStabilityStrategy,
        'optimization_improvement': OptimizationImprovementStrategy
    }

    @classmethod
    def get_strategy(
        cls,
        gate_id: str,
        config: Dict[str, Any],
        constraints_meta: Dict[str, Any]
    ) -> ParameterAdjustmentStrategy:
        """
        Get parameter adjustment strategy for a gate type.

        Args:
            gate_id: Quality gate identifier
            config: Campaign configuration
            constraints_meta: Constraints metadata (locks, sources)

        Returns:
            Strategy instance for this gate type
        """
        strategy_class = cls._strategies.get(gate_id)

        if not strategy_class:
            logger.warning(f"No strategy found for gate {gate_id}, using base strategy")
            # Return base no-op strategy
            return NoOpStrategy(config, constraints_meta)

        return strategy_class(config, constraints_meta)


class NoOpStrategy(ParameterAdjustmentStrategy):
    """No-op strategy for gates without adjustments"""

    def adjust(self, gate_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        logger.info(f"No adjustment strategy for {gate_result.get('gate_id')}")
        return []
