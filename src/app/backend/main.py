"""PAVE — Platform Asset Vending Engine. FastAPI backend entrypoint."""
import logging
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .exceptions import PaveError
from .routers import approvals, assist, finops, governance, meta, ownership, registry, requests

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("pave")

app = FastAPI(
    title="PAVE — Platform Asset Vending Engine",
    description="Governed self-service resource provisioning on Databricks.",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    """Force the SPA shell + assets to revalidate (ETag still yields fast 304s) so a
    redeploy is picked up immediately instead of serving a stale cached bundle."""
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.startswith("/assets"):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


@app.exception_handler(PaveError)
async def pave_error_handler(request: Request, exc: PaveError):
    logger.warning("%s on %s: %s", exc.error_code, request.url.path, exc.message)
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


# ---- routers ----
app.include_router(meta.router)
app.include_router(assist.router)
app.include_router(requests.router)
app.include_router(approvals.router)
app.include_router(registry.router)
app.include_router(ownership.router)
app.include_router(governance.router)
app.include_router(finops.router)


@app.on_event("startup")
async def startup():
    logger.info("=" * 70)
    logger.info("PAVE — Platform Asset Vending Engine — starting up")
    logger.info("=" * 70)
    try:
        from .database import db
        await db.init_schema()
        h = await db.health()
        logger.info("Lakebase: %s", h)
    except Exception as e:  # noqa: BLE001
        logger.error("Schema init failed (continuing in demo mode): %s", e)


# ---- health ----
@app.get("/api/health")
async def health():
    from . import config
    return {"status": "healthy", "service": "PAVE", "provision_mode": config.PROVISION_MODE}


@app.get("/api/health/database")
async def health_db():
    from .database import db
    return await db.health()


# ---- static SPA ----
_static = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_index = os.path.join(_static, "index.html")
if os.path.isdir(os.path.join(_static, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(_static, "assets")), name="assets")
    logger.info("Mounted static assets from %s", _static)


@app.get("/")
async def root():
    if os.path.exists(_index):
        return FileResponse(_index)
    return {"service": "PAVE", "frontend": "not built", "api_docs": "/api/docs"}


@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail=f"API route /{full_path} not found")
    if os.path.exists(_index):
        return FileResponse(_index)
    raise HTTPException(status_code=404, detail="frontend not built")
