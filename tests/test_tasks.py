from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.tasks import _synthesize_all_chunks, _synthesize_chunk


@pytest.mark.asyncio
async def test_synthesize_chunk_returns_audio_bytes():
    mock_response = MagicMock()
    mock_response.content = b"fake-audio-bytes"
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    result = await _synthesize_chunk(mock_client, "Hello world.")

    assert result == b"fake-audio-bytes"
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_synthesize_chunk_raises_on_http_error():
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "error", request=MagicMock(), response=MagicMock(status_code=500)
    )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with pytest.raises(httpx.HTTPStatusError):
        await _synthesize_chunk(mock_client, "Hello world.")


@pytest.mark.asyncio
async def test_synthesize_all_chunks_preserves_order():
    async def fake_synthesize_chunk(client, chunk):
        # simulate each chunk "returning" identifiable bytes
        return chunk.encode()

    with patch("app.tasks._synthesize_chunk", side_effect=fake_synthesize_chunk):
        chunks = ["first", "second", "third"]
        results = await _synthesize_all_chunks(chunks)

    assert results == [b"first", b"second", b"third"]


def test_synthesize_task_writes_file_and_updates_redis(tmp_path):
    from app import tasks

    with (
        patch.object(tasks.settings, "audio_output_dir", tmp_path),
        patch("app.tasks.chunk_text", return_value=["Hello there."]),
        patch("app.tasks.asyncio.run", return_value=[b"audio-bytes"]),
        patch("app.tasks.redis_client") as mock_redis,
    ):
        job_id = "test-job-123"
        result = tasks.synthesize_task.run(job_id, "Hello there.")

        assert result == job_id
        output_file = tmp_path / f"{job_id}.mp3"
        assert output_file.read_bytes() == b"audio-bytes"

        # confirm status was set to "done" at some point
        calls = [call.kwargs for call in mock_redis.hset.call_args_list]
        assert any(c.get("mapping", {}).get("status") == "done" for c in calls)
