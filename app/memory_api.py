from fastapi import APIRouter

from app import memory

router = APIRouter()


@router.get("/api/memory/graph")
async def memory_graph(request: str | None = None):
    return await memory.graph(request)
