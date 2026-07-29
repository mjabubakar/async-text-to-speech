"""Celery task: chunk text, synthesize via ElevenLabs, stitch, store result."""

import asyncio
import logging

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.celery_app import celery_app
from app.chunking import chunk_text
from app.config import settings
from app.redis_client import redis_client

logger = logging.getLogger(__name__)

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

# Retry on transient failures (server errors, timeouts, connection issues) —
# NOT on 4xx client errors like bad auth or malformed input, which won't
# succeed no matter how many times we retry.
RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)


def _is_retryable_http_status_error(exc: BaseException) -> bool:
    """Retry on 5xx (server-side, likely transient) and 429 (rate limit)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, RETRYABLE_EXCEPTIONS)


def _should_retry(exc: BaseException) -> bool:
    """True if this exception represents a transient failure worth retrying."""
    if isinstance(exc, RETRYABLE_EXCEPTIONS):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return False


@retry(
    retry=retry_if_exception(_should_retry),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def _synthesize_chunk(client: httpx.AsyncClient, chunk: str) -> bytes:
    """Call ElevenLabs TTS API for a single chunk, return raw audio bytes.

    Retries up to 4 attempts total, with exponential backoff (2s, 4s, 8s,
    capped at 20s) between attempts. Only retries on transient failures
    (timeouts, connection errors, 5xx, 429) — a 401/400 fails immediately
    since retrying won't fix a bad API key or malformed request.
    """
    try:
        response = await client.post(
            ELEVENLABS_TTS_URL.format(voice_id=settings.elevenlabs_voice_id),
            headers={
                "xi-api-key": settings.elevenlabs_api_key,
                "Content-Type": "application/json",
            },
            json={"text": chunk, "model_id": "eleven_multilingual_v2"},
            timeout=60.0,
        )
        response.raise_for_status()
        return response.content
    except httpx.HTTPStatusError as e:
        if not _is_retryable_http_status_error(e):
            logger.error("Non-retryable ElevenLabs error: %s", e.response.status_code)
            raise
        raise


async def _synthesize_all_chunks(chunks: list[str]) -> list[bytes]:
    """Call ElevenLabs for every chunk concurrently, preserving chunk order."""
    async with httpx.AsyncClient() as client:
        tasks = [_synthesize_chunk(client, chunk) for chunk in chunks]
        return await asyncio.gather(*tasks)


def _set_status(job_id: str, **fields: str) -> None:
    redis_client.hset(f"job:{job_id}", mapping=fields)


@celery_app.task(bind=True)
def synthesize_task(self, job_id: str, text: str) -> str:
    """Chunk text, synthesize audio per chunk, stitch, write to disk, update Redis."""
    try:
        _set_status(job_id, status="processing")

        chunks = chunk_text(text, max_chars=settings.max_chunk_chars)
        if not chunks:
            _set_status(job_id, status="failed", error="No text to synthesize")
            return job_id

        audio_parts = asyncio.run(_synthesize_all_chunks(chunks))
        stitched_audio = b"".join(audio_parts)

        output_path = settings.audio_output_dir / f"{job_id}.mp3"
        output_path.write_bytes(stitched_audio)

        _set_status(
            job_id,
            status="done",
            audio_url=f"/audio/{job_id}.mp3",
            chunk_count=str(len(chunks)),
        )
        return job_id

    except httpx.HTTPStatusError as e:
        logger.exception("ElevenLabs API error for job %s (exhausted retries)", job_id)
        _set_status(
            job_id, status="failed", error=f"TTS API error: {e.response.status_code}"
        )
        raise
    except RETRYABLE_EXCEPTIONS as e:
        logger.exception("Network error for job %s (exhausted retries)", job_id)
        _set_status(job_id, status="failed", error=f"Network error: {e}")
        raise
    except Exception as e:
        logger.exception("Unhandled error synthesizing job %s", job_id)
        _set_status(job_id, status="failed", error=str(e))
        raise
