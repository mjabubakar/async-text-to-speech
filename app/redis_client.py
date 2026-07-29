"""Redis client for storing job status/metadata (not audio bytes)."""

import redis

from app.config import settings

redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
