"""
Orchestration Planner for NovoMCP
Plans optimal execution of multi-service workflows
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from .azure_openai_client import AzureOpenAIClient

logger = logging.getLogger(__name__)

@dataclass
class ServiceCall:
    """Represents a single service call in the execution plan"""
    service: str
    action: str
    params: Dict[str, Any]
    depends_on: List[int] = None
    parallel_group: int = 0
    timeout: int = 30
    required: bool = True
    sql: Optional[str] = None  # SQL query for dashboard-aggregator write operations

@dataclass
class ExecutionPlan:
    """Complete execution plan for a workflow"""
    steps: List[ServiceCall]
    estimated_time_ms: int
    parallelizable: bool
    cache_strategy: Dict[str, Any]
    fallback_plan: Optional[List[ServiceCall]] = None

class OrchestrationPlanner:
    """Plans optimal execution of complex workflows"""
    
    def __init__(self, ai_client: Optional[AzureOpenAIClient] = None):
        """Initialize with AI client"""
        self.ai_client = ai_client or AzureOpenAIClient()
        
        # Service dependencies and characteristics
        # NOTE: dashboard-aggregator is the UNIFIED data service (handles both READ and WRITE)
        # db-manager and dbschema-manager have been consolidated into dashboard-aggregator
        self.service_info = {
            "dashboard-aggregator": {
                "avg_latency_ms": 150,  # Fast for both read and write
                "cacheable": True,  # READ operations cacheable
                "batch_capable": True,
                "supports_read": True,
                "supports_write": True
            },
            "addie-models": {
                "avg_latency_ms": 2000,
                "cacheable": True,
                "batch_capable": True
            },
            "molecular-intelligence": {
                "avg_latency_ms": 5000,
                "cacheable": False,
                "batch_capable": True
            },
            "chem-props": {
                "avg_latency_ms": 300,
                "cacheable": True,
                "batch_capable": True
            },
            "faves-compliance": {
                "avg_latency_ms": 800,
                "cacheable": True,
                "batch_capable": True
            },
            "project-data": {
                "avg_latency_ms": 200,
                "cacheable": True,
                "batch_capable": False
            }
        }
        
        # Common workflow templates
        # NOTE: dashboard-aggregator handles BOTH read (query) AND write (save) operations
        self.workflow_templates = {
            "molecule_analysis": [
                {"service": "chem-props", "action": "calculate"},
                {"service": "addie-models", "action": "predict"},
                {"service": "faves-compliance", "action": "validate"}
            ],
            "project_creation": [
                {"service": "project-data", "action": "create"},
                {"service": "dashboard-aggregator", "action": "write"}  # Consolidated: was db-manager
            ],
            "molecule_search": [
                {"service": "dashboard-aggregator", "action": "query"},
                {"service": "addie-models", "action": "filter"}
            ],
            "data_save": [
                {"service": "dashboard-aggregator", "action": "write"}  # Unified write endpoint
            ]
        }
    
    async def plan(
        self, 
        intent: Dict[str, Any],
        context: Optional[Dict] = None,
        optimize_for: str = "speed"
    ) -> ExecutionPlan:
        """
        Create execution plan for intent
        
        Args:
            intent: Recognized intent with services and actions
            context: Execution context (user, constraints, etc.)
            optimize_for: "speed", "cost", or "reliability"
            
        Returns:
            Optimized execution plan
        """
        
        # First check if intent already has an execution_plan from AI intent recognition
        if "execution_plan" in intent and intent["execution_plan"]:
            # Use the execution plan from intent recognition (which includes SQL)
            steps = []
            for plan_step in intent["execution_plan"]:
                step = ServiceCall(
                    service=plan_step.get("service"),
                    action=plan_step.get("action", "execute"),
                    params=plan_step.get("params", {}),
                    parallel_group=0
                )
                # Add SQL if present
                if "sql" in plan_step:
                    step.sql = plan_step["sql"]
                steps.append(step)
            
            return ExecutionPlan(
                steps=steps,
                estimated_time_ms=1000,
                parallelizable=False,
                cache_strategy={}
            )
        
        # Check for template match
        template_plan = self._get_template_plan(intent)
        if template_plan:
            return self._optimize_plan(template_plan, optimize_for)
        
        # Use AI for complex planning if available
        if self.ai_client.available and self._is_complex_workflow(intent):
            ai_plan = await self._ai_plan_workflow(intent, context, optimize_for)
            if ai_plan:
                return ai_plan
        
        # Fallback to rule-based planning
        return self._rule_based_plan(intent, optimize_for)
    
    def _get_template_plan(self, intent: Dict) -> Optional[ExecutionPlan]:
        """Get plan from templates if available"""
        intent_name = intent.get("intent")
        
        if intent_name in ["analyze_molecules", "molecule_analysis"]:
            return self._create_plan_from_template("molecule_analysis", intent)
        elif intent_name in ["create_project"]:
            return self._create_plan_from_template("project_creation", intent)
        elif intent_name in ["search_molecules"]:
            return self._create_plan_from_template("molecule_search", intent)
        
        return None
    
    def _create_plan_from_template(self, template_name: str, intent: Dict) -> ExecutionPlan:
        """Create plan from template"""
        template = self.workflow_templates[template_name]
        
        steps = []
        for i, step_template in enumerate(template):
            step = ServiceCall(
                service=step_template["service"],
                action=step_template["action"],
                params=intent.get("entities", {}),
                parallel_group=0 if i == 0 else i  # First step alone, others can be parallel
            )
            steps.append(step)
        
        # Calculate estimated time
        estimated_time = self._estimate_execution_time(steps)
        
        return ExecutionPlan(
            steps=steps,
            estimated_time_ms=estimated_time,
            parallelizable=len(steps) > 1,
            cache_strategy={"enabled": True, "ttl": 300}
        )
    
    def _is_complex_workflow(self, intent: Dict) -> bool:
        """Check if workflow is complex enough for AI planning"""
        return (
            len(intent.get("services", [])) > 3 or
            intent.get("intent") == "complex_workflow" or
            len(intent.get("execution_plan", [])) > 5
        )
    
    async def _ai_plan_workflow(
        self, 
        intent: Dict, 
        context: Optional[Dict],
        optimize_for: str
    ) -> Optional[ExecutionPlan]:
        """Use AI to plan complex workflow"""
        
        system_prompt = f"""You are a workflow orchestration planner for a pharmaceutical research platform.
        Create an optimal execution plan for the given intent.
        
        Optimization goal: {optimize_for}
        - speed: Minimize total execution time through parallelization
        - cost: Minimize API calls and resource usage
        - reliability: Maximize success rate with fallbacks
        
        Service characteristics:
        {self.service_info}
        
        Return JSON format:
        {{
            "steps": [
                {{
                    "service": "service_name",
                    "action": "action_name",
                    "params": {{}},
                    "depends_on": [step_indices],
                    "parallel_group": group_number,
                    "timeout": seconds,
                    "required": true/false
                }}
            ],
            "estimated_time_ms": total_time,
            "parallelizable": true/false,
            "cache_strategy": {{
                "enabled": true/false,
                "ttl": seconds,
                "keys": ["cache_keys"]
            }},
            "optimization_notes": "explanation of optimization choices"
        }}
        """
        
        prompt = f"""Plan the optimal execution for this research workflow:
        
        Intent: {intent}
        Context: {context if context else "No additional context"}
        
        {self.ai_client.get_orchestration_context()}
        
        Create a detailed execution plan optimized for {optimize_for}.
        """
        
        result = await self.ai_client.complete(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        
        if result["success"] and result["response"]:
            parsed = await self.ai_client.parse_json_response(result["response"])
            if parsed:
                # Convert to ExecutionPlan
                steps = [
                    ServiceCall(**step) for step in parsed.get("steps", [])
                ]
                
                return ExecutionPlan(
                    steps=steps,
                    estimated_time_ms=parsed.get("estimated_time_ms", 1000),
                    parallelizable=parsed.get("parallelizable", False),
                    cache_strategy=parsed.get("cache_strategy", {})
                )
        
        return None
    
    def _rule_based_plan(self, intent: Dict, optimize_for: str) -> ExecutionPlan:
        """Create rule-based execution plan"""
        
        services = intent.get("services", [])
        steps = []
        
        # Group services by dependencies
        groups = self._group_services_by_dependency(services)
        
        for group_idx, group in enumerate(groups):
            for service in group:
                step = ServiceCall(
                    service=service,
                    action=self._get_default_action(service),
                    params=intent.get("entities", {}),
                    parallel_group=group_idx
                )
                steps.append(step)
        
        # Optimize based on preference
        if optimize_for == "speed":
            steps = self._optimize_for_speed(steps)
        elif optimize_for == "cost":
            steps = self._optimize_for_cost(steps)
        else:  # reliability
            steps = self._add_fallbacks(steps)
        
        estimated_time = self._estimate_execution_time(steps)
        
        return ExecutionPlan(
            steps=steps,
            estimated_time_ms=estimated_time,
            parallelizable=len(groups) > 1,
            cache_strategy={"enabled": True, "ttl": 300}
        )
    
    def _group_services_by_dependency(self, services: List[str]) -> List[List[str]]:
        """Group services that can run in parallel"""

        # Simple grouping - data services first, then processors, then validation
        # NOTE: dashboard-aggregator handles BOTH read AND write operations (consolidated service)
        data_services = ["dashboard-aggregator", "project-data"]  # Unified data layer
        processing_services = ["addie-models", "chem-props", "molecular-intelligence"]
        validation_services = ["faves-compliance", "negative-data"]

        groups = []

        # Group 1: Data operations (dashboard-aggregator handles both read and write)
        group1_data = [s for s in services if s in data_services]
        if group1_data:
            groups.append(group1_data)

        # Group 2: Processing
        group2 = [s for s in services if s in processing_services]
        if group2:
            groups.append(group2)

        # Group 3: Validation
        group3 = [s for s in services if s in validation_services]
        if group3:
            groups.append(group3)

        # Remaining services
        remaining = [s for s in services if s not in group1_data + group2 + group3]
        if remaining:
            groups.append(remaining)

        return groups if groups else [services]
    
    def _get_default_action(self, service: str) -> str:
        """Get default action for service"""
        # NOTE: dashboard-aggregator handles both "query" (read) and "write" operations
        default_actions = {
            "dashboard-aggregator": "query",  # Default to read; use "write" for writes
            "addie-models": "predict",
            "molecular-intelligence": "generate",
            "chem-props": "calculate",
            "project-data": "get",
            "faves-compliance": "validate"
        }
        return default_actions.get(service, "process")
    
    def _optimize_for_speed(self, steps: List[ServiceCall]) -> List[ServiceCall]:
        """Optimize steps for speed through parallelization"""
        
        # Mark all independent steps as parallel
        for i, step in enumerate(steps):
            if not step.depends_on:
                step.parallel_group = 0
            else:
                # Put in next group after dependencies
                max_dep_group = max(steps[dep].parallel_group for dep in step.depends_on)
                step.parallel_group = max_dep_group + 1
        
        return steps
    
    def _optimize_for_cost(self, steps: List[ServiceCall]) -> List[ServiceCall]:
        """Optimize for cost by reducing redundant calls"""
        
        # Mark cacheable services
        for step in steps:
            if self.service_info.get(step.service, {}).get("cacheable"):
                step.params["use_cache"] = True
        
        # Batch capable services
        batch_services = {}
        for step in steps:
            if self.service_info.get(step.service, {}).get("batch_capable"):
                if step.service not in batch_services:
                    batch_services[step.service] = []
                batch_services[step.service].append(step)
        
        # Mark batched steps
        for service, service_steps in batch_services.items():
            if len(service_steps) > 1:
                for step in service_steps[1:]:
                    step.params["batch_with"] = service_steps[0]
        
        return steps
    
    def _add_fallbacks(self, steps: List[ServiceCall]) -> List[ServiceCall]:
        """Add fallback options for reliability"""

        # Mark critical steps as required
        # dashboard-aggregator is critical for both read and write operations
        critical_services = ["dashboard-aggregator", "project-data"]
        for step in steps:
            step.required = step.service in critical_services

        # Increase timeouts for complex services
        complex_services = ["molecular-intelligence", "addie-models"]
        for step in steps:
            if step.service in complex_services:
                step.timeout = 60

        return steps
    
    def _estimate_execution_time(self, steps: List[ServiceCall]) -> int:
        """Estimate total execution time"""
        
        # Group by parallel groups
        groups = {}
        for step in steps:
            if step.parallel_group not in groups:
                groups[step.parallel_group] = []
            groups[step.parallel_group].append(step)
        
        total_time = 0
        for group in groups.values():
            # Parallel group time is the max of all steps
            group_time = max(
                self.service_info.get(step.service, {}).get("avg_latency_ms", 1000)
                for step in group
            )
            total_time += group_time
        
        return total_time
    
    def validate_plan(self, plan: ExecutionPlan) -> Tuple[bool, List[str]]:
        """Validate execution plan for correctness"""
        
        issues = []
        
        # Check for circular dependencies
        for i, step in enumerate(plan.steps):
            if step.depends_on and i in step.depends_on:
                issues.append(f"Step {i} has circular dependency")
        
        # Check service availability
        for step in plan.steps:
            if step.service not in self.service_info:
                logger.warning(f"Unknown service in plan: {step.service}")
        
        # Check timeout values
        for step in plan.steps:
            if step.timeout < 1 or step.timeout > 300:
                issues.append(f"Invalid timeout for {step.service}: {step.timeout}")
        
        return len(issues) == 0, issues