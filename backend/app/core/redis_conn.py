# backend/app/core/redis_conn.py
from __future__ import annotations

import os
from typing import Optional

from redis.asyncio import Redis as AsyncRedis  # requires redis>=5
from redis import Redis as SyncRedis

# Use docker service name by default (works inside containers).
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Module-level singletons
_sync_client: Optional[SyncRedis] = None
_async_client: Optional[AsyncRedis] = None


def get_redis() -> SyncRedis:
    """
    Legacy alias — returns a synchronous Redis client.
    Matches existing imports in services (enqueue/dequeue).
    """
    return get_sync_redis()


def get_sync_redis() -> SyncRedis:
    """
    Return a singleton synchronous Redis client.
    """
    global _sync_client
    if _sync_client is None:
        _sync_client = SyncRedis.from_url(REDIS_URL, decode_responses=True)
    return _sync_client


def get_async_redis() -> AsyncRedis:
    """
    Return a singleton asynchronous Redis client.
    """
    global _async_client
    if _async_client is None:
        _async_client = AsyncRedis.from_url(REDIS_URL, decode_responses=True)
    return _async_client


def ping() -> bool:
    """
    Lightweight sync ping for health checks.
    """
    try:
        return bool(get_sync_redis().ping())
    except Exception:
        return False