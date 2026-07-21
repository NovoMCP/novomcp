"""
NovoMCP - Model Context Protocol Server for Molecular Intelligence

Exposes molecular analysis tools to Claude and other MCP-compatible AI assistants.
Supports OAuth 2.0 for Claude Web integration.
"""

from .tools import MCP_TOOLS, MCPToolExecutor
from .auth import MCPAuthManager, MCPUser
from .rate_limiter import MCPRateLimiter
from .oauth import setup_oauth

__all__ = [
    "MCP_TOOLS",
    "MCPToolExecutor",
    "MCPAuthManager",
    "MCPUser",
    "MCPRateLimiter",
    "setup_oauth",
]
