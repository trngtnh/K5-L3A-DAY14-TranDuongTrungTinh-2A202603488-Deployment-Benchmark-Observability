"""
Rate limiting (tiêu chí 7).

Sliding window 60 giây dùng Redis Sorted Set (ZREMRANGEBYSCORE + ZCARD + ZADD + EXPIRE)
để mọi worker / instance dùng chung state và giữ nguyên sau restart.
"""
import hashlib
import logging
import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import HTTPException
from redis.exceptions import RedisError

from app.config import settings
from app.redis_client import r

logger = logging.getLogger("agent")

WINDOW_SECONDS = 60

_SLIDING_WINDOW_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, tonumber(ARGV[1]))
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[2]) then
    return 0
end
redis.call('ZADD', KEYS[1], tonumber(ARGV[3]), ARGV[4])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[5]))
return 1
"""

_fallback_lock = threading.Lock()
_fallback_windows: dict[str, deque[float]] = defaultdict(deque)


def _key_id(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]


def _check_memory_fallback(bucket_id: str, limit: int, now: float) -> None:
    cutoff = now - WINDOW_SECONDS
    with _fallback_lock:
        window = _fallback_windows[bucket_id]
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) >= limit:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(WINDOW_SECONDS)},
            )
        window.append(now)


def check_rate_limit(key: str) -> None:
    """Kiểm tra giới hạn request/phút cho API key; vượt ngưỡng → HTTP 429."""
    limit = max(1, settings.rate_limit_per_minute)
    now = time.time()
    cutoff = now - WINDOW_SECONDS
    bucket_id = _key_id(key)
    redis_key = f"rate_limit:{bucket_id}"

    if r is None:
        _check_memory_fallback(bucket_id, limit, now)
        return

    member = f"{now:.6f}:{uuid.uuid4().hex}"
    try:
        allowed = r.eval(
            _SLIDING_WINDOW_LUA,
            1,
            redis_key,
            str(cutoff),
            str(limit),
            str(now),
            member,
            str(WINDOW_SECONDS + 5),
        )
    except RedisError as exc:
        logger.warning(
            "Redis unavailable for rate limiting; using local fallback",
            extra={"extra_fields": {"event": "rate_limit_redis_fallback", "error": type(exc).__name__}},
        )
        _check_memory_fallback(bucket_id, limit, now)
        return

    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(WINDOW_SECONDS)},
        )
