"""FastAPI entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import conversations, database, developer, health, memories, uploads
from app.container import build_container
from app.core.logging import configure_beijing_logging
from app.api.routes import unified_evaluation


configure_beijing_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    container = build_container()
    app.state.container = container
    await container.initialize()
    try:
        yield
    finally:
        await container.close()


app = FastAPI(
    title="青岛科技大学校园生活助手 API",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    swagger_ui_oauth2_redirect_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health.router)
app.include_router(conversations.router, prefix="/api")
app.include_router(uploads.router, prefix="/api")
app.include_router(memories.router, prefix="/api")
app.include_router(developer.router, prefix="/api")
app.include_router(database.router, prefix="/api")
app.include_router(unified_evaluation.router, prefix="/api")
