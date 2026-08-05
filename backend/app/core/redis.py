import json
import redis.asyncio as redis
from typing import Any, Optional
from app.core.config import settings
from loguru import logger

_redis_client: Optional[redis.Redis] = None

async def init_redis():
    """Initialize the Redis client."""
    global _redis_client
    _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    await _redis_client.ping()
    logger.info("Redis connection established")

def get_redis() -> redis.Redis:
    """Get the Redis client instance."""
    if _redis_client is None:
        raise RuntimeError("Redis client is not initialized.")
    return _redis_client

async def close_redis():
    """Close the Redis connection."""
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None

class RedisCache:
    """Redis cache wrapper."""
    
    async def get(self, key: str) -> Optional[str]:
        return await get_redis().get(key)
        
    async def set(self, key: str, value: str, expire: int = 3600) -> None:
        await get_redis().set(key, value, ex=expire)
        
    async def delete(self, key: str) -> None:
        await get_redis().delete(key)
        
    async def get_json(self, key: str) -> Optional[Any]:
        val = await self.get(key)
        if val:
            return json.loads(val)
        return None
        
    async def set_json(self, key: str, value: Any, expire: int = 3600) -> None:
        await self.set(key, json.dumps(value), expire)

class RedisStreams:
    """Redis streams wrapper for message queue."""
    
    async def publish(self, stream: str, message: dict) -> str:
        return await get_redis().xadd(stream, message)
        
    async def consume(self, stream: str, group: str, consumer: str, count: int = 10) -> list:
        try:
            return await get_redis().xreadgroup(group, consumer, {stream: ">"}, count=count)
        except redis.exceptions.ResponseError as e:
            if "NOGROUP" in str(e):
                await get_redis().xgroup_create(stream, group, mkstream=True)
                return await get_redis().xreadgroup(group, consumer, {stream: ">"}, count=count)
            raise e

cache = RedisCache()
streams = RedisStreams()
