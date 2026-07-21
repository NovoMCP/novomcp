"""
Routers for NovoMCP
"""
from .proxy import router as proxy_router
from .ai_orchestration import router as ai_router
from .control_center import router as control_center_router
from .campaigns import router as campaigns_router

# Create placeholder routers for now
from fastapi import APIRouter

# Create basic routers
health = type('health', (), {'router': APIRouter()})()
orchestration = type('orchestration', (), {'router': ai_router})()  # Use AI orchestration router
analytics = type('analytics', (), {'router': APIRouter()})()
jobs = type('jobs', (), {'router': APIRouter()})()
projects = type('projects', (), {'router': APIRouter()})()
conversations = type('conversations', (), {'router': APIRouter()})()
rbac = type('rbac', (), {'router': APIRouter()})()
proxy = type('proxy', (), {'router': proxy_router})()
control_center = type('control_center', (), {'router': control_center_router})()
ai_orchestration = type('ai_orchestration', (), {'router': ai_router})()
campaigns = type('campaigns', (), {'router': campaigns_router})()

# Add basic health endpoint
@health.router.get("/")
async def health_check():
    return {"status": "healthy", "service": "novomcp"}

# Add basic orchestration endpoint
@orchestration.router.post("/orchestrate")
async def orchestrate(request: dict):
    return {"message": "Orchestration endpoint", "request": request}

__all__ = ['health', 'orchestration', 'analytics', 'jobs', 'projects', 'conversations', 'rbac', 'proxy', 'control_center', 'ai_orchestration', 'campaigns']
