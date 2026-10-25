"""存活与就绪探针。"""

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.api.dependencies import get_container
from app.api.schemas import HealthResult
from app.container import AppContainer


router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResult)
async def live() -> HealthResult:
    return HealthResult(status="ok")


@router.get("/health/ready", response_model=HealthResult)
async def ready(container: AppContainer = Depends(get_container)) -> HealthResult:
    details = {"postgres": False, "redis": False, "ollama": False}
    try:
        async with container.database.session() as session:
            await session.execute(text("SELECT 1"))
        details["postgres"] = True
    except Exception:
        pass
    try:
        details["redis"] = bool(await container.redis.client.ping())
    except Exception:
        pass
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            response = await client.get("%s/api/tags" % container.settings.ollama_base_url.rstrip("/"))
            details["ollama"] = response.is_success
    except Exception:
        pass
    retrieval_config = container.retrieval_config
    warmup = container.intent_embedding_warmup
    details["intent_embeddings"] = warmup.get("status") == "ready"
    return HealthResult(
        status="ready" if all(details.values()) else "degraded",
        details={
            **details,
            "intent_embedding_warmup": warmup,
            "agents": {"recursion_limit": container.agent_recursion_limit},
            "retrieval": {
                "route_top_k": int(retrieval_config["route_top_k"]),
                "rrf_candidate_k": int(retrieval_config["rrf_candidate_k"]),
                "output_top_k": min(int(retrieval_config["rrf_candidate_k"]), 20),
            },
        },
    )
