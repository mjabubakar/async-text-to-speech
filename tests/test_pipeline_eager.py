import pytest

from app.celery_app import celery_app
from app.config import settings


@pytest.fixture
def eager(monkeypatch):
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(settings, "max_chunk_chars", 15)  # force 2 chunks


def test_full_pipeline_happy_path(client, fake_redis, stub_tts, eager):
    r = client.post("/synthesize", json={"text": "Hello there. General Kenobi."})
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "done", job

    audio = client.get(job["audio_url"])
    assert audio.status_code == 200
    assert audio.content == b"<Hello there.><General Kenobi.>"  # order preserved

    # context hints travelled in the real request bodies
    first, second = stub_tts.calls
    assert first["next_text"] == "General Kenobi."
    assert second["previous_text"] == "Hello there."


def test_full_pipeline_bad_key_fails_fast_without_retries(
    client, fake_redis, stub_tts, eager
):
    stub_tts.status = 401
    job_id = client.post("/synthesize", json={"text": "Hello there."}).json()["job_id"]
    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert job["error"] == "TTS API error: 401"
    assert len(stub_tts.calls) == 1  # not retried
