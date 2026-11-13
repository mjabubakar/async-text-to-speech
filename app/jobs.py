"""Job-state helpers. Every write refreshes the key's TTL."""

from app.config import settings


def job_key(job_id: str) -> str:
    return f"job:{job_id}"


def set_status_sync(client, job_id: str, **fields: str) -> None:
    client.hset(job_key(job_id), mapping=fields)
    client.expire(job_key(job_id), settings.job_ttl_seconds)


async def set_status_async(client, job_id: str, **fields: str) -> None:
    await client.hset(job_key(job_id), mapping=fields)
    await client.expire(job_key(job_id), settings.job_ttl_seconds)
