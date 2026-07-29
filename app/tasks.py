"""Celery task: chunk text, synthesize via ElevenLabs, stitch, store result."""

import asyncio
import logging

import httpx

from app.celery_app import celery_app
from app.chunking import chunk_text
from app.config import settings
from app.redis_client import redis_client

logger = logging.getLogger(__name__)

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


async def _synthesize_chunk(client: httpx.AsyncClient, chunk: str) -> bytes:
    """Call ElevenLabs TTS API for a single chunk, return raw audio bytes."""
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


async def _synthesize_all_chunks(chunks: list[str]) -> list[bytes]:
    """Call ElevenLabs for every chunk concurrently, preserving chunk order."""
    async with httpx.AsyncClient() as client:
        # asyncio.gather preserves order of results matching order of awaitables,
        # even though the requests themselves complete concurrently/out of order
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

        # MP3 files can be concatenated as raw bytes and remain valid playable
        # audio — this works because MP3 frames are self-contained; it's not
        # true for all audio formats, but is the standard approach for MP3 stitching.
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
        logger.exception("ElevenLabs API error for job %s", job_id)
        _set_status(
            job_id, status="failed", error=f"TTS API error: {e.response.status_code}"
        )
        raise
    except Exception as e:
        logger.exception("Unhandled error synthesizing job %s", job_id)
        _set_status(job_id, status="failed", error=str(e))
        raise
