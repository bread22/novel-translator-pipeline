from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from translator.web.routes.books import router as books_router
from translator.web.routes.events import router as events_router
from translator.web.routes.knowledge import router as knowledge_router
from translator.web.routes.queue import router as queue_router
from translator.web.routes.system import router as system_router
from translator.web.routes.tasks import router as tasks_router


logger = logging.getLogger("translator.web")


def create_app(static_dir: Path | None = None) -> FastAPI:
    app = FastAPI(
        title="Novel Translator Studio API",
        description="Universal AI Novel Translation and Consistency Review Pipeline API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # 1. Enable CORS for all local/intranet origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. Register API v1 routers
    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(books_router)
    api_v1.include_router(queue_router)
    api_v1.include_router(tasks_router)
    api_v1.include_router(knowledge_router)
    api_v1.include_router(system_router)
    api_v1.include_router(events_router)

    app.include_router(api_v1)

    # 3. Basic health check
    @app.get("/health")
    def health_check() -> dict[str, str]:
        return {"status": "ok", "app": "novel-translator-studio", "version": "0.1.0"}

    # 4. Mount Frontend Static Files if available
    dist_path = static_dir or (Path(__file__).resolve().parents[2] / "frontend" / "dist")
    if dist_path.exists() and (dist_path / "index.html").exists():
        if (dist_path / "assets").exists():
            app.mount("/assets", StaticFiles(directory=str(dist_path / "assets")), name="assets")

        @app.get("/{full_path:path}")
        async def serve_spa(request: Request, full_path: str):
            if full_path.startswith("api/") or full_path == "docs" or full_path == "redoc" or full_path == "openapi.json":
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            file_candidate = dist_path / full_path
            if file_candidate.exists() and file_candidate.is_file():
                return FileResponse(file_candidate)
            return FileResponse(dist_path / "index.html")
    else:
        @app.get("/")
        def root_redirect():
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url="/docs")

    return app


app = create_app()

