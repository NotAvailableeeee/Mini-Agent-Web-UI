"""FastAPI application factory.

Creates the FastAPI app, registers middleware, includes routers, and
serves the built frontend (if present) as static files.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .routes import chat, init_trace, memory, showcase, workspace


# Web frontend dev server (Vite) origin — only used during development.
# Production builds are served from the same origin as the API.
DEV_FRONTEND_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def create_app() -> FastAPI:
    app = FastAPI(
        title="Mini-Agent Web UI",
        version="0.1.0",
        description="Browser-based frontend for Mini-Agent — showcase + chat.",
    )

    # CORS: allow Vite dev server during development.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_FRONTEND_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routes
    app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
    app.include_router(showcase.router, prefix="/api/showcase", tags=["showcase"])
    app.include_router(workspace.router, prefix="/api/workspace", tags=["workspace"])
    app.include_router(memory.router, prefix="/api/memory", tags=["memory"])
    app.include_router(init_trace.router, prefix="/api/init-trace", tags=["meta"])

    @app.get("/api/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/info", tags=["meta"])
    async def info() -> JSONResponse:
        """Return runtime info (model, workspace, tools count).

        Non-blocking: if the agent hasn't been initialised yet, returns
        ``{"ready": false}`` instead of kicking off init. The frontend's
        ``/api/init-trace/state`` polling is the only thing that should
        trigger init, so this endpoint must never preempt it.
        """
        from .agent_runner import get_runner

        runner = get_runner()
        try:
            meta = await runner.describe()
        except Exception as exc:  # pragma: no cover - defensive
            meta = {"ready": False, "error": str(exc)}
        return JSONResponse(meta)

    # Serve built frontend (production). Path: web/frontend/dist/
    frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    return app


# Convenience for `uvicorn web.backend.app:app`
app = create_app()