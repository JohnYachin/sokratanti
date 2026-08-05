import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.api.router import api_router

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    logger.info("Starting up CAIOS backend...")

    # Init Supabase
    try:
        from app.core.database import init_db
        await init_db()
        logger.info("Supabase connected")
    except Exception as e:
        logger.warning(f"DB init warning: {e}")

    # Init Redis (optional — skip if not available)
    try:
        from app.core.redis import init_redis, close_redis
        await init_redis()
        logger.info("Redis connected")
        app.state.redis_available = True
    except Exception as e:
        logger.warning(f"Redis not available (will run without cache): {e}")
        app.state.redis_available = False

    yield

    logger.info("Shutting down CAIOS backend...")
    try:
        from app.core.redis import close_redis
        await close_redis()
    except Exception:
        pass


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="CAIOS — Crypto AI Investment Operating System",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics
Instrumentator().instrument(app).expose(app)

# Routers
app.include_router(api_router, prefix="/api/v1")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "path": str(request.url)},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.get("/", tags=["root"])
async def root():
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "env": settings.APP_ENV,
        "status": "operational",
        "timestamp": time.time(),
        "docs": "/docs",
    }


@app.get("/health", tags=["health"])
async def health():
    """Quick liveness check."""
    return {"status": "ok", "timestamp": time.time()}
