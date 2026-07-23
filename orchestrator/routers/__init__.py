"""
Routers for NovoMCP
"""
from .proxy import router as proxy_router

# Create placeholder routers for now
from fastapi import APIRouter

# Create basic routers
health = type('health', (), {'router': APIRouter()})()
analytics = type('analytics', (), {'router': APIRouter()})()
jobs = type('jobs', (), {'router': APIRouter()})()
projects = type('projects', (), {'router': APIRouter()})()
conversations = type('conversations', (), {'router': APIRouter()})()
rbac = type('rbac', (), {'router': APIRouter()})()
proxy = type('proxy', (), {'router': proxy_router})()

# Add basic health endpoint
@health.router.get("/")
async def health_check():
    return {"status": "healthy", "service": "novomcp"}

__all__ = ['health', 'analytics', 'jobs', 'projects', 'conversations', 'rbac', 'proxy']
