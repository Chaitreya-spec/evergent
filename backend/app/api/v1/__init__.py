"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.v1 import (
    agents,
    api_keys,
    artifacts,
    attachments,
    audit,
    auth,
    catalog,
    conversations,
    health,
    memories,
    projects,
    tasks,
    tools,
    workflows,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(workflows.router)
api_router.include_router(tasks.router)
api_router.include_router(artifacts.router)
api_router.include_router(attachments.router)
api_router.include_router(audit.router)
api_router.include_router(api_keys.router)
api_router.include_router(agents.router)
api_router.include_router(conversations.router)
api_router.include_router(memories.router)
api_router.include_router(tools.router)
api_router.include_router(catalog.router)

__all__ = ["api_router"]
