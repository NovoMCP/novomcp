"""
Intent Recognizer for NovoMCP
Parses natural language requests and identifies service routing
"""

import logging
import re
from typing import Dict, Any, List, Optional
from .azure_openai_client import AzureOpenAIClient

logger = logging.getLogger(__name__)

class IntentRecognizer:
    """Recognizes user intent from natural language queries"""
    
    def __init__(self, ai_client: Optional[AzureOpenAIClient] = None):
        """Initialize with AI client"""
        self.ai_client = ai_client or AzureOpenAIClient()
        
        # Define intent patterns for quick matching
        self.intent_patterns = {
            "search_molecules": [
                r"find.*molecules?",
                r"search.*molecules?",
                r"show.*molecules?",
                r"list.*molecules?",
                r"get.*molecules?"
            ],
            "generate_molecules": [
                r"generate.*molecules?",
                r"create.*molecules?",
                r"design.*molecules?",
                r"make.*molecules?"
            ],
            "analyze_molecules": [
                r"analyze.*molecules?",
                r"score.*molecules?",
                r"evaluate.*molecules?",
                r"assess.*molecules?"
            ],
            "create_project": [
                r"create.*project",
                r"new.*project",
                r"start.*project",
                r"initialize.*project"
            ],
            "search_projects": [
                r"find.*projects?",
                r"search.*projects?",
                r"show.*projects?",
                r"list.*projects?"
            ],
            "calculate_properties": [
                r"calculate.*propert",
                r"compute.*propert",
                r"get.*propert",
                r"predict.*propert"
            ],
            "check_compliance": [
                r"check.*compliance",
                r"validate.*compliance",
                r"faves.*check",
                r"safety.*assessment"
            ]
        }
        
        # Service mapping - dashboard-aggregator handles BOTH read AND write operations
        # (db-manager and dbschema-manager have been consolidated into dashboard-aggregator)
        self.intent_to_services = {
            "search_molecules": ["dashboard-aggregator"],  # READ operation
            "generate_molecules": ["molecular-intelligence", "molmim-optimizer"],
            "analyze_molecules": ["addie-models", "chem-props"],
            "create_project": ["project-data", "dashboard-aggregator"],  # WRITE operation (consolidated)
            "search_projects": ["dashboard-aggregator"],  # READ operation
            "calculate_properties": ["chem-props"],
            "check_compliance": ["faves-compliance", "negative-data"],
            "query_data": ["dashboard-aggregator"],  # READ operation
            "fetch_data": ["dashboard-aggregator"],  # READ operation
            "get_data": ["dashboard-aggregator"],  # READ operation
            "write_data": ["dashboard-aggregator"],  # WRITE operation (consolidated)
            "update_data": ["dashboard-aggregator"],  # WRITE operation (consolidated)
            "delete_data": ["dashboard-aggregator"]  # WRITE operation (consolidated)
        }
    
    async def recognize(self, query: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Recognize intent from natural language query
        
        Args:
            query: Natural language query
            context: Optional context (user, project, etc.)
            
        Returns:
            Dict with intent, entities, and routing information
        """
        
        # First try pattern matching for common intents
        quick_match = self._quick_match_intent(query)
        
        if quick_match and not self._is_complex_query(query):
            # Simple query, use pattern matching
            entities = self._extract_entities(query)
            return {
                "intent": quick_match,
                "confidence": 0.85,
                "entities": entities,
                "services": self.intent_to_services.get(quick_match, []),
                "method": "pattern_matching"
            }
        
        # Use AI for complex queries
        if self.ai_client.available:
            return await self._ai_recognize_intent(query, context)
        
        # Fallback to basic parsing
        return self._fallback_recognize(query)
    
    def _quick_match_intent(self, query: str) -> Optional[str]:
        """Quick pattern matching for common intents"""
        query_lower = query.lower()
        
        for intent, patterns in self.intent_patterns.items():
            for pattern in patterns:
                if re.search(pattern, query_lower):
                    return intent
        
        return None
    
    def _is_complex_query(self, query: str) -> bool:
        """Check if query is complex and needs AI"""
        # Complex if:
        # - Multiple sentences
        # - Contains 'and', 'or', 'then'
        # - Has specific conditions
        # - References multiple entities
        
        complexity_indicators = [
            len(query.split('.')) > 1,
            ' and ' in query.lower(),
            ' or ' in query.lower(),
            ' then ' in query.lower(),
            ' with ' in query.lower(),
            ' where ' in query.lower(),
            ' having ' in query.lower(),
            len(re.findall(r'\b(molecules?|projects?|compounds?|drugs?)\b', query.lower())) > 1
        ]
        
        return sum(complexity_indicators) >= 2
    
    def _extract_entities(self, query: str) -> Dict[str, List[str]]:
        """Extract entities from query"""
        entities = {
            "molecules": [],
            "projects": [],
            "properties": [],
            "filters": {}
        }
        
        # Extract SMILES patterns
        smiles_pattern = r'[A-Z][A-Za-z0-9@+\-\[\]()=#%]+' 
        smiles_matches = re.findall(smiles_pattern, query)
        if smiles_matches:
            entities["molecules"] = smiles_matches
        
        # Extract property names
        properties = ["logp", "mw", "qed", "tpsa", "hba", "hbd", "rotatable", "lipinski"]
        for prop in properties:
            if prop in query.lower():
                entities["properties"].append(prop)
        
        # Extract numeric filters
        numeric_pattern = r'(>|<|>=|<=|=)\s*(\d+(?:\.\d+)?)'
        numeric_matches = re.findall(numeric_pattern, query)
        for op, value in numeric_matches:
            entities["filters"]["numeric"] = {"operator": op, "value": float(value)}
        
        # Extract therapeutic areas
        therapeutic_areas = ["cancer", "oncology", "diabetes", "cardiovascular", "cns", "infectious"]
        for area in therapeutic_areas:
            if area in query.lower():
                entities["filters"]["therapeutic_area"] = area
        
        return entities
    
    async def _ai_recognize_intent(self, query: str, context: Optional[Dict]) -> Dict[str, Any]:
        """Use AI to recognize complex intent"""

        system_prompt = """You are an intent recognition system for a pharmaceutical research platform.
        Analyze the user's query and identify:
        1. Primary intent (what they want to do)
        2. Entities (molecules, projects, properties, etc.)
        3. Required services
        4. Execution order

        CRITICAL SERVICE ROUTING RULES:
        - dashboard-aggregator is the UNIFIED data service for BOTH read AND write operations
        - For READ operations (SELECT, search, fetch, get, query, list): Use "dashboard-aggregator"
        - For WRITE operations (INSERT, UPDATE, DELETE, create, modify): Use "dashboard-aggregator"
        - The old db-manager and dbschema-manager services have been consolidated into dashboard-aggregator

        IMPORTANT: When using dashboard-aggregator for WRITE operations:
        - MUST include "sql" field with the actual SQL query
        - Use proper SQL Server syntax (e.g., GETUTCDATE() for timestamps)
        - Example: {"service": "dashboard-aggregator", "action": "update user", "sql": "UPDATE users SET last_name = 'Smith' WHERE id = '123'"}

        Available intents:
        - search_molecules: Find existing molecules (READ - use dashboard-aggregator)
        - generate_molecules: Create new molecules
        - analyze_molecules: Score or evaluate molecules
        - create_project: Start new research project (WRITE - use dashboard-aggregator)
        - search_projects: Find existing projects (READ - use dashboard-aggregator)
        - calculate_properties: Compute chemical properties
        - check_compliance: Validate safety/compliance
        - query_data: Retrieve data from database (READ - use dashboard-aggregator)
        - write_data: Write data to database (WRITE - use dashboard-aggregator)
        - complex_workflow: Multi-step operation

        Return JSON format:
        {
            "intent": "primary_intent",
            "sub_intents": ["additional_intents"],
            "entities": {
                "molecules": [],
                "projects": [],
                "properties": [],
                "filters": {}
            },
            "services": ["service1", "service2"],
            "execution_plan": [
                {"step": 1, "service": "service1", "action": "action1", "sql": "SQL query if needed"},
                {"step": 2, "service": "service2", "action": "action2"}
            ],
            "confidence": 0.95
        }
        """
        
        from core.prompt_sanitizer import sanitize_for_prompt
        _query = sanitize_for_prompt(query, "query", 2000)
        _context = sanitize_for_prompt(str(context) if context else "No additional context", "context", 2000)
        prompt = f"""Analyze this research query and identify the intent:

        Query: "{_query}"

        Context: {_context}
        
        {self.ai_client.get_service_context()}
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
                parsed["method"] = "ai_recognition"
                parsed["ai_tokens"] = result.get("tokens")
                return parsed
        
        # Fallback if AI fails
        return self._fallback_recognize(query)
    
    def _fallback_recognize(self, query: str) -> Dict[str, Any]:
        """Fallback recognition without AI"""
        
        # Basic intent from keywords
        intent = self._quick_match_intent(query) or "unknown"
        entities = self._extract_entities(query)
        
        return {
            "intent": intent,
            "confidence": 0.5,
            "entities": entities,
            "services": self.intent_to_services.get(intent, ["dashboard-aggregator"]),  # Default to READ service
            "method": "fallback",
            "message": "Using basic pattern matching"
        }
    
    async def clarify_intent(self, query: str, options: List[str]) -> str:
        """
        Clarify ambiguous intent with user options
        
        Args:
            query: Original query
            options: Possible interpretations
            
        Returns:
            Clarification message for user
        """
        
        if len(options) == 1:
            return f"I understand you want to: {options[0]}"
        
        clarification = f"Your query '{query}' could mean:\n"
        for i, option in enumerate(options, 1):
            clarification += f"{i}. {option}\n"
        clarification += "\nPlease specify which action you'd like to perform."
        
        return clarification