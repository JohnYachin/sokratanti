from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.core.database import get_db
from app.core.redis import get_redis

router = APIRouter()

@router.get("/")
async def health_detailed(db: AsyncSession = Depends(get_db)):
    """Detailed health check for all dependencies."""
    health_status = {"status": "ok", "db": "unknown", "redis": "unknown"}
    
    # Check DB
    try:
        await db.execute(text("SELECT 1"))
        health_status["db"] = "ok"
    except Exception as e:
        health_status["db"] = "error"
        health_status["status"] = "degraded"
        
    # Check Redis
    try:
        redis_client = get_redis()
        await redis_client.ping()
        health_status["redis"] = "ok"
    except Exception as e:
        health_status["redis"] = "error"
        health_status["status"] = "degraded"
        
    return health_status

@router.get("/live")
async def health_live():
    """Simple liveness probe."""
    return {"status": "alive"}

@router.get("/ready")
async def health_ready(db: AsyncSession = Depends(get_db)):
    """Readiness probe."""
    try:
        await db.execute(text("SELECT 1"))
        redis_client = get_redis()
        await redis_client.ping()
        return {"status": "ready"}
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Not ready")
