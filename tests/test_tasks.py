import asyncio
import os
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from tenacity import wait_none

from app import tasks
from app.config import settings
from tests.helpers import make_response


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Retry tests must not actually wait for backoff."""
    monkeypatch.setattr(tasks._synthesize_chunk.retry, "wait", wait_none())


def client_with(*results):
    client = AsyncMock()
    client.post = AsyncMock(side_effect=list(results))
    return client


# ---------- single chunk: request + retry classification ----------


async def test_chunk_returns_audio_and_sends_expected_request():
    client = client_with(make_response(200, content=b"fake-audio"))

    assert await tasks._synthesize_chunk(client, "Hello.") == b"fake-audio"

    (url,), kwargs = client.post.call_args
    assert settings.elevenlabs_voice_id in url
    assert kwargs["headers"]["xi-api-key"] == settings.elevenlabs_api_key
    assert kwargs["json"] == {
        "text": "Hello.",
        "model_id": settings.elevenlabs_model_id,
    }


async def test_chunk_sends_neighbouring_context_when_given():
    client = client_with(make_response(200))
    await tasks._synthesize_chunk(client, "B.", previous_text="A.", next_text="C.")
    body = client.post.call_args.kwargs["json"]
    assert body["previous_text"] == "A." and body["next_text"] == "C."


@pytest.mark.parametrize("code", [429, 500, 503])
async def test_retries_retryable_status_then_succeeds(code):
    client = client_with(make_response(code), make_response(200, content=b"ok"))
    assert await tasks._synthesize_chunk(client, "x") == b"ok"
    assert client.post.call_count == 2


@pytest.mark.parametrize(
    "exc",
    [httpx.ReadTimeout("t"), httpx.ConnectError("c"), httpx.RemoteProtocolError("r")],
)
async def test_retries_transport_errors_then_succeeds(exc):
    client = client_with(exc, make_response(200, content=b"ok"))
    assert await tasks._synthesize_chunk(client, "x") == b"ok"
    assert client.post.call_count == 2


@pytest.mark.parametrize("code", [400, 401, 403, 422])
async def test_does_not_retry_client_errors(code):
    client = client_with(make_response(code))
    with pytest.raises(httpx.HTTPStatusError):
        await tasks._synthesize_chunk(client, "x")
    assert client.post.call_count == 1


async def test_gives_up_after_four_attempts():
    client = client_with(*[make_response(503)] * 4)
    with pytest.raises(httpx.HTTPStatusError):
        await tasks._synthesize_chunk(client, "x")
    assert client.post.call_count == 4


# ---------- fan-out ----------


async def test_all_chunks_preserve_order_even_when_completion_is_out_of_order(
    monkeypatch,
):
    async def fake(client, chunk, **kw):
        await asyncio.sleep(0.05 if chunk == "first" else 0)
        return chunk.encode()

    monkeypatch.setattr(tasks, "_synthesize_chunk", fake)
    result = await tasks._synthesize_all_chunks(["first", "second", "third"])
    assert result == [b"first", b"second", b"third"]


async def test_concurrency_is_bounded_by_setting(monkeypatch):
    monkeypatch.setattr(settings, "tts_concurrency", 2)
    in_flight = peak = 0

    async def fake(client, chunk, **kw):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return b"x"

    monkeypatch.setattr(tasks, "_synthesize_chunk", fake)
    await tasks._synthesize_all_chunks([str(i) for i in range(10)])
    assert peak == 2


def test_context_uses_neighbouring_chunks():
    chunks = ["A" * 500, "B" * 10, "C" * 500]
    mid = tasks._context_for(chunks, 1)
    assert mid["previous_text"] == "A" * settings.context_chars
    assert mid["next_text"] == "C" * settings.context_chars
    assert tasks._context_for(chunks, 0)["previous_text"] is None
    assert tasks._context_for(chunks, 2)["next_text"] is None
    assert tasks._context_for(["only"], 0) == {"previous_text": None, "next_text": None}


# ---------- the Celery task ----------


def _patch_pipeline(monkeypatch, tmp_path, chunks, synth=None):
    monkeypatch.setattr(settings, "audio_output_dir", tmp_path)
    monkeypatch.setattr(tasks, "chunk_text", lambda *a, **k: chunks)
    if synth:
        monkeypatch.setattr(tasks, "_synthesize_all_chunks", synth)


def test_task_writes_file_atomically_and_marks_done(tmp_path, fake_redis, monkeypatch):
    async def synth(chunks):
        return [b"a", b"b"]

    _patch_pipeline(monkeypatch, tmp_path, ["one", "two"], synth)

    assert tasks.synthesize_task.run("j1", "text") == "j1"

    assert (tmp_path / "j1.mp3").read_bytes() == b"ab"
    assert not list(tmp_path.glob("*.tmp"))
    job = fake_redis.hgetall("job:j1")
    assert job["status"] == "done"
    assert job["audio_url"] == "/audio/j1.mp3"
    assert job["chunk_count"] == "2"
    assert fake_redis.ttl("job:j1") == settings.job_ttl_seconds


def test_task_with_no_chunks_marks_failed(tmp_path, fake_redis, monkeypatch):
    _patch_pipeline(monkeypatch, tmp_path, [])
    tasks.synthesize_task.run("j2", "")
    job = fake_redis.hgetall("job:j2")
    assert job["status"] == "failed" and job["error"] == "No text to synthesize"


def test_task_http_error_marks_failed_with_status_and_reraises(
    tmp_path, fake_redis, monkeypatch
):
    async def synth(chunks):
        r = make_response(500)
        raise httpx.HTTPStatusError("x", request=r.request, response=r)

    _patch_pipeline(monkeypatch, tmp_path, ["a"], synth)
    with pytest.raises(httpx.HTTPStatusError):
        tasks.synthesize_task.run("j3", "t")
    job = fake_redis.hgetall("job:j3")
    assert job["status"] == "failed" and job["error"] == "TTS API error: 500"
    assert not (tmp_path / "j3.mp3").exists()


def test_task_network_error_marks_failed(tmp_path, fake_redis, monkeypatch):
    async def synth(chunks):
        raise httpx.ConnectError("boom")

    _patch_pipeline(monkeypatch, tmp_path, ["a"], synth)
    with pytest.raises(httpx.ConnectError):
        tasks.synthesize_task.run("j4", "t")
    assert fake_redis.hgetall("job:j4")["error"].startswith("Network error")


def test_task_unexpected_error_does_not_leak_details(tmp_path, fake_redis, monkeypatch):
    monkeypatch.setattr(settings, "audio_output_dir", tmp_path)

    def boom(*a, **k):
        raise RuntimeError("secret-internal-detail")

    monkeypatch.setattr(tasks, "chunk_text", boom)
    with pytest.raises(RuntimeError):
        tasks.synthesize_task.run("j5", "t")
    job = fake_redis.hgetall("job:j5")
    assert job["status"] == "failed"
    assert "secret-internal-detail" not in job["error"]


def test_task_removes_expired_audio_but_keeps_new(tmp_path, fake_redis, monkeypatch):
    old = tmp_path / "old.mp3"
    old.write_bytes(b"x")
    ancient = time.time() - settings.audio_retention_seconds - 100
    os.utime(old, (ancient, ancient))

    async def synth(chunks):
        return [b"a"]

    _patch_pipeline(monkeypatch, tmp_path, ["one"], synth)
    tasks.synthesize_task.run("j6", "t")
    assert not old.exists()
    assert (tmp_path / "j6.mp3").exists()


def test_celery_late_ack_settings_are_enabled():
    """Regression guard for the worker-crash behaviour documented in the README."""
    from app.celery_app import celery_app

    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
