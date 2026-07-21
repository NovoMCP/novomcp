"""
User-Friendly Message Formatter
Translates internal technical details into business-appropriate messages
Protects proprietary IP and algorithm details
"""

from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)


class MessageFormatter:
    """Formats technical system messages for external users"""

    # Map technical parameter names to user-friendly descriptions
    PARAMETER_LABELS = {
        'constraints.molecular.mw.max': 'Molecular Weight Upper Limit',
        'constraints.molecular.mw.min': 'Molecular Weight Lower Limit',
        'constraints.molecular.logp.max': 'Lipophilicity Upper Limit',
        'constraints.molecular.logp.min': 'Lipophilicity Lower Limit',
        'constraints.molecular.hbd.max': 'Hydrogen Bond Donors Limit',
        'constraints.molecular.hba.max': 'Hydrogen Bond Acceptors Limit',
        'constraints.admet.hepatotoxicity': 'Hepatotoxicity Threshold',
        'constraints.admet.cardiotoxicity': 'Cardiotoxicity Threshold',
        'constraints.admet.bioavailability': 'Oral Bioavailability Requirement',
        'constraints.admet.clearance': 'Drug Clearance Threshold',
        'generation.diversity': 'Chemical Space Exploration',
        'generation.novelty': 'Novel Structure Generation',
        'generation.count': 'Molecules Per Iteration'
    }

    # Map gate IDs to user-friendly names
    GATE_LABELS = {
        'molecular_constraints': 'Molecular Property Filters',
        'admet_filters': 'Drug Safety Screening',
        'safety_screening': 'Toxicity Assessment',
        'compliance_check': 'Regulatory Compliance',
        'binding_affinity': 'Target Binding Analysis',
        'md_stability': 'Molecular Stability Test',
        'optimization_improvement': 'Lead Optimization'
    }

    @classmethod
    def format_loop_back_message(cls, adjustments: Dict[str, Any]) -> str:
        """
        Format a loop-back (parameter adjustment) message for users.

        Args:
            adjustments: Technical adjustment details from quality gate

        Returns:
            User-friendly message describing what changed and why
        """
        try:
            changes = adjustments.get('changes', [])
            if not changes:
                return "Campaign adjusted search parameters to improve results"

            # Group changes by gate/reason
            gate_groups = {}
            for change in changes:
                gate_id = cls._extract_gate_from_reason(change.get('reason', ''))
                if gate_id not in gate_groups:
                    gate_groups[gate_id] = []
                gate_groups[gate_id].append(change)

            # Build user-friendly summary
            if len(gate_groups) == 1:
                # Single gate affected
                gate_id = list(gate_groups.keys())[0]
                gate_name = cls.GATE_LABELS.get(gate_id, 'Search Parameters')
                change_summary = cls._summarize_changes(gate_groups[gate_id])

                return f"Campaign adjusted: {gate_name}\n\n{change_summary}\n\nReason: Previous parameters were too restrictive"

            else:
                # Multiple gates affected
                summaries = []
                for gate_id, gate_changes in gate_groups.items():
                    gate_name = cls.GATE_LABELS.get(gate_id, 'Parameters')
                    change_count = len(gate_changes)
                    summaries.append(f"• {gate_name} ({change_count} adjustment{'s' if change_count > 1 else ''})")

                return f"Campaign adjusted multiple search parameters:\n" + "\n".join(summaries) + "\n\nReason: Expanding search to improve success rate"

        except Exception as e:
            logger.error(f"Error formatting loop-back message: {e}")
            return "Campaign automatically adjusted search parameters to improve results"

    @classmethod
    def format_assumed_constraints(cls, constraints_meta: Dict[str, Any], constraints: Dict[str, Any]) -> str:
        """
        Summarize which constraint values were assumed by AI or defaults.

        Args:
            constraints_meta: Dict containing 'sources' and optional 'locks'
            constraints: Resolved constraints dict

        Returns:
            Short, user-friendly message or empty string if nothing assumed
        """
        try:
            sources = (constraints_meta or {}).get('sources') or {}
            mol_sources = (sources.get('molecular') or {})
            assumed = []

            def src_label(src: str) -> str:
                return {'user': 'you', 'ai': 'AI', 'default': 'default'}.get(src, src)

            mw_src = mol_sources.get('mw') or {}
            logp_src = mol_sources.get('logp') or {}

            # Only mention fields not provided by user
            if mw_src.get('min') in ['ai', 'default']:
                val = ((constraints or {}).get('molecular') or {}).get('mw', {}).get('min')
                assumed.append(f"MW min {val} ({src_label(mw_src.get('min'))})")
            if mw_src.get('max') in ['ai', 'default']:
                val = ((constraints or {}).get('molecular') or {}).get('mw', {}).get('max')
                assumed.append(f"MW max {val} ({src_label(mw_src.get('max'))})")

            if logp_src.get('min') in ['ai', 'default']:
                val = ((constraints or {}).get('molecular') or {}).get('logp', {}).get('min')
                assumed.append(f"LogP min {val} ({src_label(logp_src.get('min'))})")
            if logp_src.get('max') in ['ai', 'default']:
                val = ((constraints or {}).get('molecular') or {}).get('logp', {}).get('max')
                assumed.append(f"LogP max {val} ({src_label(logp_src.get('max'))})")

            if not assumed:
                return ""

            return "Assumed constraints: " + ", ".join(assumed)
        except Exception as e:
            logger.debug(f"format_assumed_constraints error: {e}")
            return ""

    @classmethod
    def _extract_gate_from_reason(cls, reason: str) -> str:
        """Extract gate ID from reason string"""
        if 'gate:' in reason:
            # Format: "... (gate: molecular_constraints)"
            try:
                return reason.split('gate:')[1].strip().rstrip(')')
            except:
                pass

        # Fallback: extract from reason text
        for gate_id in cls.GATE_LABELS.keys():
            if gate_id.replace('_', ' ') in reason.lower():
                return gate_id

        return 'unknown'

    @classmethod
    def _summarize_changes(cls, changes: List[Dict[str, Any]]) -> str:
        """
        Create a concise summary of parameter changes.

        Args:
            changes: List of parameter adjustment dicts

        Returns:
            User-friendly summary string
        """
        summaries = []

        for change in changes[:3]:  # Show max 3 specific changes
            param = change.get('parameter', '')
            old_val = change.get('old_value')
            new_val = change.get('new_value')

            # Get user-friendly label
            label = cls.PARAMETER_LABELS.get(param, param.split('.')[-1].replace('_', ' ').title())

            # Format value change (hide exact algorithm if adjustment percentage)
            if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)):
                # Round to readable precision
                old_str = cls._format_number(old_val)
                new_str = cls._format_number(new_val)

                direction = "expanded" if new_val > old_val else "tightened"
                summaries.append(f"{label}: {direction} from {old_str} to {new_str}")
            else:
                summaries.append(f"{label}: adjusted")

        if len(changes) > 3:
            summaries.append(f"...and {len(changes) - 3} other parameter{'s' if len(changes) - 3 > 1 else ''}")

        return "\n".join(summaries)

    @classmethod
    def _format_number(cls, value: float) -> str:
        """Format number to readable precision"""
        if abs(value) < 0.001:
            return f"{value:.2e}"  # Scientific notation for very small
        elif abs(value) > 1000:
            return f"{int(value):,}"  # Comma separator for large
        elif abs(value) >= 10:
            return f"{value:.1f}"  # One decimal for medium
        else:
            return f"{value:.2f}"  # Two decimals for small

    @classmethod
    def format_intervention_message(cls, gate_results: List[Dict[str, Any]]) -> str:
        """
        Format human intervention request message.

        Args:
            gate_results: Quality gate evaluation results

        Returns:
            User-friendly message explaining what needs attention
        """
        try:
            if not gate_results:
                return "Campaign requires review - automated adjustments insufficient"

            failed_gates = [g for g in gate_results if not g.get('passed')]

            if len(failed_gates) == 1:
                gate_id = failed_gates[0].get('gate_id', 'unknown')
                gate_name = cls.GATE_LABELS.get(gate_id, 'Quality Check')
                return f"Review Required: {gate_name}\n\nMultiple iterations have not met requirements. Manual parameter adjustment recommended."

            else:
                gate_names = [cls.GATE_LABELS.get(g.get('gate_id', ''), 'Check') for g in failed_gates[:3]]
                return f"Review Required: Multiple Checks Failed\n\n" + "\n".join(f"• {name}" for name in gate_names) + "\n\nManual review recommended to adjust search strategy."

        except Exception as e:
            logger.error(f"Error formatting intervention message: {e}")
            return "Campaign requires manual review"

    @classmethod
    def format_discovery_message(cls, discovery_type: str, molecule_count: int) -> str:
        """
        Format discovery notification message.

        Args:
            discovery_type: Type of discovery (lead, hit, breakthrough)
            molecule_count: Number of candidates found

        Returns:
            User-friendly celebration message
        """
        if discovery_type == 'breakthrough':
            return f"🎉 Breakthrough Discovery! Campaign identified {molecule_count} exceptional candidate{'s' if molecule_count > 1 else ''}"
        elif discovery_type == 'lead':
            return f"✅ Lead Candidates Found! Campaign discovered {molecule_count} promising lead{'s' if molecule_count > 1 else ''}"
        else:
            return f"New Candidates: Campaign generated {molecule_count} molecule{'s' if molecule_count > 1 else ''} meeting initial criteria"
