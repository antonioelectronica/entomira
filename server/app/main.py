"""Aplicación FastAPI de ENTOMIRA: API v1 + web estática."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import (auth, backup, client, demo, disco, firmware, greenhouses, health,
                  image_params, images, inference, ingest, live, logs_trampa, perfil, stats, stream,
                  tarjetas, telemetry, traps, users, worker)
from .core import config
from .core.auth import BasicAuthMiddleware
from .core.db import init_db

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    malas = config.claves_inseguras()
    if malas and not config.MODO_LOCAL:
        raise RuntimeError("claves sin configurar (%s): con la API en internet no se arranca "
                           "sin ellas; ENTOMIRA_MODO_LOCAL=1 solo para desarrollo" % ", ".join(malas))
    init_db()
    yield


# Sin /docs, /redoc ni /openapi.json en internet: son el inventario completo de la API.
_docs = {} if config.MODO_LOCAL else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="ENTOMIRA API", version="0.1.0", lifespan=lifespan, **_docs)

# Basic Auth para el panel público (se añade antes que CORS para que envuelva todo).
app.add_middleware(BasicAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ORIGENES,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (ingest.router, traps.router, greenhouses.router, images.router,
          inference.router, demo.router, stream.router, health.router,
          stats.router, client.router, worker.router, auth.router, users.router,
          image_params.router, telemetry.router, live.router, firmware.router, perfil.router, backup.router,
          tarjetas.router, logs_trampa.router, disco.router):
    app.include_router(r)


# --- Frontend estático (SPA) -------------------------------------------------
if (FRONTEND_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")


@app.middleware("http")
async def no_cache_web(request, call_next):
    """Evita que el navegador sirva versiones cacheadas del HTML/JS/CSS."""
    resp = await call_next(request)
    ct = resp.headers.get("content-type", "")
    if any(t in ct for t in ("text/html", "javascript", "text/css")):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
    return resp


@app.get("/")
def index():
    idx = FRONTEND_DIR / "index.html"
    if idx.exists():
        return FileResponse(idx)
    return {"message": "ENTOMIRA API. Frontend no desplegado aún."}


@app.get("/{path:path}")
def spa_fallback(path: str):
    """Rutas del SPA → index.html (para navegación de una sola página)."""
    try:
        candidate = (FRONTEND_DIR / path).resolve()
        # nunca servir nada fuera de frontend/ (evita path traversal con ../)
        if candidate.is_file() and candidate.is_relative_to(FRONTEND_DIR.resolve()):
            return FileResponse(candidate)
    except (OSError, ValueError):
        pass
    idx = FRONTEND_DIR / "index.html"
    if idx.exists():
        return FileResponse(idx)
    return {"message": "ENTOMIRA API."}
