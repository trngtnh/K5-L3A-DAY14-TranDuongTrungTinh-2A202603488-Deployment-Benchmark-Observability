"""
Production AI Agent — Part 6 Final Project.
"""
import json
import logging
import signal
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import redis_healthy
from utils.mock_llm import ask as llm_ask


class JsonFormatter(logging.Formatter):
    """Định dạng mỗi dòng log của app thành đúng 1 JSON object hợp lệ."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "lvl": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra_fields = getattr(record, "extra_fields", None)
        if isinstance(extra_fields, dict):
            payload.update(extra_fields)
        return json.dumps(payload, ensure_ascii=False)


def _configure_logger() -> logging.Logger:
    app_logger = logging.getLogger("agent")
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    app_logger.setLevel(level)
    app_logger.propagate = False
    if not app_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        app_logger.addHandler(handler)
    return app_logger


logger = _configure_logger()
_shutting_down = False

_previous_sigterm = signal.getsignal(signal.SIGTERM)


def _on_sigterm(signum, frame):
    global _shutting_down
    _shutting_down = True
    logger.info("SIGTERM received", extra={"extra_fields": {"event": "sigterm", "signal": signum}})
    if callable(_previous_sigterm):
        _previous_sigterm(signum, frame)


signal.signal(signal.SIGTERM, _on_sigterm)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _shutting_down
    _shutting_down = False
    logger.info(
        "Agent started",
        extra={
            "extra_fields": {
                "event": "startup",
                "environment": settings.environment,
                "port": settings.port,
            }
        },
    )
    yield
    _shutting_down = True
    logger.info("Agent shutting down gracefully", extra={"extra_fields": {"event": "shutdown"}})


app = FastAPI(title="Production AI Agent", lifespan=lifespan)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.error(
            "Unhandled request error",
            extra={
                "extra_fields": {
                    "event": "request_error",
                    "method": request.method,
                    "path": request.url.path,
                    "status": 500,
                    "ms": elapsed_ms,
                    "error": type(exc).__name__,
                }
            },
        )
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    logger.info(
        "HTTP request",
        extra={
            "extra_fields": {
                "event": "request",
                "method": request.method,
                "path": request.url.path,
                "status": status_code,
                "ms": elapsed_ms,
            }
        },
    )
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": "Invalid request payload", "errors": jsonable_encoder(exc.errors())},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception):
    logger.error(
        "Unhandled exception",
        extra={"extra_fields": {"event": "unhandled_exception", "error": type(exc).__name__}},
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = None


@app.get("/health")
def health():
    """Liveness probe: luôn trả 200 khi process còn chạy, kể cả khi Redis mất kết nối."""
    return {"status": "ok", "environment": settings.environment}


@app.get("/ready")
def ready():
    """Readiness probe: 200 khi sẵn sàng, 503 khi đang shutdown hoặc Redis lỗi."""
    if _shutting_down or not redis_healthy():
        raise HTTPException(status_code=503, detail="Service not ready")
    return {"status": "ready"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    """Xử lý câu hỏi qua auth → rate limit → cost guard → LLM → ghi nhận cost."""
    check_rate_limit(api_key)
    check_budget()

    answer = llm_ask(body.question)

    input_tokens = max(1, len(body.question.split()) * 2)
    output_tokens = max(1, len(answer.split()) * 2)
    cost_usd = record_cost(input_tokens, output_tokens)

    return {
        "question": body.question,
        "answer": answer,
        "user_id": body.user_id,
        "cost_usd": round(cost_usd, 8),
    }
