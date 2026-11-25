"""
Cost guard (tiêu chí 8).

Theo dõi chi phí LLM trong Redis theo ngày/tháng; vượt budget → HTTP 402.
"""
import logging
import threading
from datetime import datetime, timezone

from fastapi import HTTPException
from redis.exceptions import RedisError

from app.config import settings
from app.redis_client import r

logger = logging.getLogger("agent")

PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006

_DAILY_TTL_SECONDS = 48 * 3600
_MONTHLY_TTL_SECONDS = 32 * 24 * 3600

_fallback_lock = threading.Lock()
_fallback_spend: dict[str, float] = {}


def _budget_keys() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return (
        f"budget:daily:{now.strftime('%Y-%m-%d')}",
        f"budget:monthly:{now.strftime('%Y-%m')}",
    )


def _get_spend(daily_key: str, monthly_key: str) -> tuple[float, float]:
    if r is None:
        with _fallback_lock:
            return _fallback_spend.get(daily_key, 0.0), _fallback_spend.get(monthly_key, 0.0)
    try:
        vals = r.mget(daily_key, monthly_key)
        daily = float(vals[0] or 0.0)
        monthly = float(vals[1] or 0.0)
        return daily, monthly
    except RedisError as exc:
        logger.warning(
            "Redis unavailable for budget check; using local fallback",
            extra={"extra_fields": {"event": "cost_guard_redis_fallback", "error": type(exc).__name__}},
        )
        with _fallback_lock:
            return _fallback_spend.get(daily_key, 0.0), _fallback_spend.get(monthly_key, 0.0)


def check_budget() -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget → raise HTTPException(status_code=402)."""
    daily_key, monthly_key = _budget_keys()
    daily_spend, monthly_spend = _get_spend(daily_key, monthly_key)

    if daily_spend >= settings.daily_budget_usd or monthly_spend >= settings.monthly_budget_usd:
        raise HTTPException(
            status_code=402,
            detail="Budget limit exceeded",
        )


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí request vào Redis, trả về chi phí request."""
    cost = (
        (max(0, input_tokens) / 1000.0) * PRICE_INPUT_PER_1K
        + (max(0, output_tokens) / 1000.0) * PRICE_OUTPUT_PER_1K
    )
    daily_key, monthly_key = _budget_keys()

    if r is None:
        with _fallback_lock:
            _fallback_spend[daily_key] = _fallback_spend.get(daily_key, 0.0) + cost
            _fallback_spend[monthly_key] = _fallback_spend.get(monthly_key, 0.0) + cost
        return cost

    try:
        pipe = r.pipeline()
        pipe.incrbyfloat(daily_key, cost)
        pipe.expire(daily_key, _DAILY_TTL_SECONDS)
        pipe.incrbyfloat(monthly_key, cost)
        pipe.expire(monthly_key, _MONTHLY_TTL_SECONDS)
        pipe.execute()
    except RedisError as exc:
        logger.warning(
            "Redis unavailable for recording cost; using local fallback",
            extra={"extra_fields": {"event": "cost_record_redis_fallback", "error": type(exc).__name__}},
        )
        with _fallback_lock:
            _fallback_spend[daily_key] = _fallback_spend.get(daily_key, 0.0) + cost
            _fallback_spend[monthly_key] = _fallback_spend.get(monthly_key, 0.0) + cost

    return cost
