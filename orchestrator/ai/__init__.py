"""
AI Module for NovoMCP
Provides the LLM client facade used by the engine.
"""

from .azure_openai_client import AzureOpenAIClient

__all__ = [
    'AzureOpenAIClient',
]
