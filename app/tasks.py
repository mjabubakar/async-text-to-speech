"""Celery task: chunk text, synthesize via ElevenLabs, stitch, store result."""

import asyncio
import logging
import os

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app import jobs
from app import redis_client as rc
from app.celery_app import celery_app
from app.chunking import chunk_text
from app.cleanup import cleanup_old_audio
from app.config import settings

logger = logging.getLogger(__name__)

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)


def _should_retry(exc: BaseException) -> bool:
    """Retry transient failures (timeouts, connection errors, 5xx, 429).

    4xx errors such as a bad API key or malformed input are never retried:
    they will not succeed no matter how many times we try.
    """
    if isinstance(exc, RETRYABLE_EXCEPTIONS):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code >= 500 or code == 429
    return False


@retry(
    retry=retry_if_exception(_should_retry),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def _synthesize_chunk(
    client: httpx.AsyncClient,
    chunk: str,
    previous_text: str | None = None,
    next_text: str | None = None,
) -> bytes:
    """Call ElevenLabs TTS for one chunk and return raw audio bytes.

    Up to 4 attempts in total, with exponential backoff (2s minimum, 20s cap).
    previous_text/next_text give the model neighbouring context so the prosody
    stays continuous across chunk boundaries.
    """
    payload: dict[str, str] = {"text": chunk, "model_id": settings.elevenlabs_model_id}
    if previous_text:
        payload["previous_text"] = previous_text
    if next_text:
        payload["next_text"] = next_text

    response = await client.post(
        ELEVENLABS_TTS_URL.format(voice_id=settings.elevenlabs_voice_id),
        headers={
            "xi-api-key": settings.elevenlabs_api_key,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60.0,
    )
    response.raise_for_status()
    return response.content


def _context_for(chunks: list[str], index: int) -> dict[str, str | None]:
    n = settings.context_chars
    return {
        "previous_text": chunks[index - 1][-n:] if index > 0 else None,
        "next_text": chunks[index + 1][:n] if index < len(chunks) - 1 else None,
    }


async def _synthesize_all_chunks(chunks: list[str]) -> list[bytes]:
    """Synthesize all chunks concurrently, at most `tts_concurrency` in flight.

    The semaphore wraps the retry loop, so a chunk waiting to retry keeps its slot
    and the cap holds under failure too. Results come back in chunk order.
    """
    semaphore = asyncio.Semaphore(settings.tts_concurrency)

    async def run_one(client: httpx.AsyncClient, index: int) -> bytes:
        async with semaphore:
            return await _synthesize_chunk(
                client, chunks[index], **_context_for(chunks, index)
            )

    async with httpx.AsyncClient() as client:
        return list(
            await asyncio.gather(*(run_one(client, i) for i in range(len(chunks))))
        )


def _set_status(job_id: str, **fields: str) -> None:
    jobs.set_status_sync(rc.sync_redis, job_id, **fields)


def _cleanup_quietly() -> None:
    try:
        cleanup_old_audio(settings.audio_output_dir, settings.audio_retention_seconds)
    except Exception:
        logger.warning("Audio cleanup failed", exc_info=True)


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

        # write to a temp file, then rename: the API never serves a partial file
        output_path = settings.audio_output_dir / f"{job_id}.mp3"
        tmp_path = settings.audio_output_dir / f"{job_id}.mp3.tmp"
        tmp_path.write_bytes(b"".join(audio_parts))
        os.replace(tmp_path, output_path)

        _set_status(
            job_id,
            status="done",
            audio_url=f"/audio/{job_id}.mp3",
            chunk_count=str(len(chunks)),
        )
        return job_id

    # Clients see short, generic messages; full details go to the worker log only.
    except httpx.HTTPStatusError as e:
        logger.exception("ElevenLabs API error for job %s (exhausted retries)", job_id)
        _set_status(
            job_id, status="failed", error=f"TTS API error: {e.response.status_code}"
        )
        raise
    except RETRYABLE_EXCEPTIONS:
        logger.exception("Network error for job %s (exhausted retries)", job_id)
        _set_status(
            job_id, status="failed", error="Network error contacting TTS provider"
        )
        raise
    except Exception:
        logger.exception("Unhandled error synthesizing job %s", job_id)
        _set_status(
            job_id, status="failed", error="Internal error while synthesizing audio"
        )
        raise
    finally:
        _cleanup_quietly()
