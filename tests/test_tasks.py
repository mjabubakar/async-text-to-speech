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
async def test_synthesize_chunk_retries_on_500_then_succeeds():
    fail_response = MagicMock()
    fail_response.status_code = 503
    fail_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "server error", request=MagicMock(), response=fail_response
    )

    success_response = MagicMock()
    success_response.content = b"audio-bytes"
    success_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[fail_response, success_response])

    result = await _synthesize_chunk(mock_client, "test chunk")

    assert result == b"audio-bytes"
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_synthesize_chunk_does_not_retry_on_401():
    fail_response = MagicMock()
    fail_response.status_code = 401
    fail_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "unauthorized", request=MagicMock(), response=fail_response
    )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fail_response)

    with pytest.raises(httpx.HTTPStatusError):
        await _synthesize_chunk(mock_client, "test chunk")

    assert mock_client.post.call_count == 1


@pytest.mark.asyncio
async def test_synthesize_chunk_gives_up_after_max_attempts():
    fail_response = MagicMock()
    fail_response.status_code = 503
    fail_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "server error", request=MagicMock(), response=fail_response
    )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fail_response)

    with pytest.raises(httpx.HTTPStatusError):
        await _synthesize_chunk(mock_client, "test chunk")

    assert mock_client.post.call_count == 4


@pytest.mark.asyncio
async def test_synthesize_all_chunks_preserves_order():
    async def fake_synthesize_chunk(client, chunk):
        return chunk.encode()

    with patch("app.tasks._synthesize_chunk", side_effect=fake_synthesize_chunk):
        chunks = ["first", "second", "third"]
        results = await _synthesize_all_chunks(chunks)

    assert results == [b"first", b"second", b"third"]


def test_synthesize_task_writes_file_and_updates_redis(tmp_path):
    from app import tasks

    async def fake_synthesize_all_chunks(chunks):
        return [b"audio-bytes"]

    with (
        patch.object(tasks.settings, "audio_output_dir", tmp_path),
        patch("app.tasks.chunk_text", return_value=["Hello there."]),
        patch(
            "app.tasks._synthesize_all_chunks", side_effect=fake_synthesize_all_chunks
        ),
        patch("app.tasks.redis_client") as mock_redis,
    ):
        job_id = "test-job-123"
        result = tasks.synthesize_task.run(job_id, "Hello there.")

        assert result == job_id
        output_file = tmp_path / f"{job_id}.mp3"
        assert output_file.read_bytes() == b"audio-bytes"

        calls = [call.kwargs for call in mock_redis.hset.call_args_list]
        assert any(c.get("mapping", {}).get("status") == "done" for c in calls)
