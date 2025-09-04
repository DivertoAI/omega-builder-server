from __future__ import annotations
import os
from typing import Optional
from redis.asyncio import Redis  # pip install redis>=5
from redis import Redis as SyncRedis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

_sync_client: Optional[SyncRedis] = None
_async_client: Optional[Redis] = None

def get_sync_redis() -> SyncRedis:
    global _sync_client
    if _sync_client is None:
        _sync_client = SyncRedis.from_url(REDIS_URL, decode_responses=True)
    return _sync_client

def get_async_redis() -> Redis:
    global _async_client
    if _async_client is None:
        _async_client = Redis.from_url(REDIS_URL, decode_responses=True)
    return _async_client