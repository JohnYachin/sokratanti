from fastapi import APIRouter
from app.api.v1 import health, coins, agents, signals

api_router = APIRouter()

# Health — routes are: /, /live, /ready → become /api/v1/health, /api/v1/health/live, /api/v1/health/ready
api_router.include_router(health.router, prefix="/health", tags=["health"])

# Coins — routes already have full paths, include without extra prefix
api_router.include_router(coins.router, tags=["coins"])

# Agents
api_router.include_router(agents.router, tags=["agents"])

# Signals
api_router.include_router(signals.router, tags=["signals"])
