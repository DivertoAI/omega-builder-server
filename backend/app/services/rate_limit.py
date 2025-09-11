from __future__ import annotations
import time
import redis
from app.config import settings

r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

def allow(ip: str) -> bool:
    # Simple token bucket in Redis
    now = int(time.time())
    key_tokens = f"ratelimit:{ip}:tokens"
    key_ts = f"ratelimit:{ip}:ts"

    last = r.get(key_ts)
    tokens = float(r.get(key_tokens) or settings.RATE_LIMIT_BURST)

    if last is None:
        r.set(key_ts, now)
        r.set(key_tokens, settings.RATE_LIMIT_BURST - 1)
        return True

    elapsed = max(0, now - int(last))
    tokens = min(settings.RATE_LIMIT_BURST, tokens + elapsed * settings.RATE_LIMIT_RPS)
    if tokens >= 1:
        tokens -= 1
        pipe = r.pipeline()
        pipe.set(key_ts, now)
        pipe.set(key_tokens, tokens)
        pipe.execute()
        return True
    else:
        r.set(key_ts, now)
        r.set(key_tokens, tokens)
        return False