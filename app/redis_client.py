"""Redis clients for job status/metadata and rate limiting (never audio bytes).

The API uses the async client; the Celery worker is synchronous and uses the sync one.
Modules access these as attributes (`redis_client.sync_redis`) so tests can swap them.
"""

import redis
import redis.asyncio as aioredis

from app.config import settings

sync_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
async_redis = aioredis.from_url(settings.redis_url, decode_responses=True)
