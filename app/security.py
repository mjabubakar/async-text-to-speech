"""Optional shared API-key auth and a Redis-backed fixed-window rate limiter."""

import secrets
import time

from fastapi import Header, HTTPException, Request, status

from app import redis_client as rc
from app.config import settings


def _detail(msg: str, loc: list[str], type_: str) -> list[dict]:
    return [{"msg": msg, "loc": loc, "type": type_}]


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = settings.api_key
    if not expected:
        return  # auth disabled
    if x_api_key is None or not secrets.compare_digest(
        x_api_key.encode(), expected.encode()
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail=_detail(
                "Invalid or missing API key", ["header", "x-api-key"], "unauthorized"
            ),
        )


async def rate_limit(request: Request) -> None:
    limit = settings.rate_limit_per_minute
    if limit <= 0:
        return
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    key = f"rl:{ip}:{int(now // 60)}"
    count = await rc.async_redis.incr(key)
    await rc.async_redis.expire(key, 70)
    if count > limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=_detail("Rate limit exceeded", [], "rate_limited"),
            headers={"Retry-After": str(max(1, 60 - int(now) % 60))},
        )
