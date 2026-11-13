import uuid

import pytest

from app.config import settings

PAYLOAD = {"text": "Hello world."}


def test_synthesize_returns_uuid_job_id(client, fake_redis, mock_task_delay):
    r = client.post("/synthesize", json=PAYLOAD)
    assert r.status_code == 200
    uuid.UUID(r.json()["job_id"])


def test_synthesize_enqueues_task_and_sets_queued_with_ttl(
    client, fake_redis, mock_task_delay
):
    job_id = client.post("/synthesize", json=PAYLOAD).json()["job_id"]
    mock_task_delay.assert_called_once_with(job_id, "Hello world.")
    assert fake_redis.hget(f"job:{job_id}", "status") == "queued"
    assert fake_redis.ttl(f"job:{job_id}") == settings.job_ttl_seconds


@pytest.mark.parametrize("body", [{}, {"text": 12345}, {"text": ""}, {"text": "  \n "}])
def test_invalid_text_returns_422_json_and_enqueues_nothing(
    client, fake_redis, mock_task_delay, body
):
    r = client.post("/synthesize", json=body)
    assert r.status_code == 422
    assert "detail" in r.json()  # also proves the error body is JSON-serializable
    mock_task_delay.assert_not_called()


def test_oversized_text_returns_422(client, fake_redis, mock_task_delay):
    r = client.post("/synthesize", json={"text": "a" * (settings.max_text_chars + 1)})
    assert r.status_code == 422
    mock_task_delay.assert_not_called()


def test_broker_down_returns_503_and_marks_job_failed(
    client, fake_redis, mock_task_delay
):
    mock_task_delay.side_effect = ConnectionError("broker down")
    r = client.post("/synthesize", json=PAYLOAD)
    assert r.status_code == 503
    (key,) = fake_redis.keys("job:")
    assert fake_redis.hget(key, "status") == "failed"


def test_unexpected_error_returns_generic_500(
    client, fake_redis, mock_task_delay, monkeypatch
):
    async def boom(*a, **k):
        raise RuntimeError("secret-internal-detail")

    monkeypatch.setattr("app.main.jobs.set_status_async", boom)
    r = client.post("/synthesize", json=PAYLOAD)
    assert r.status_code == 500
    assert "secret-internal-detail" not in r.text


# ---------- auth and rate limiting ----------


def test_auth_is_disabled_by_default(client, fake_redis, mock_task_delay):
    assert client.post("/synthesize", json=PAYLOAD).status_code == 200


def test_api_key_is_enforced_when_configured(
    client, fake_redis, mock_task_delay, monkeypatch
):
    monkeypatch.setattr(settings, "api_key", "s3cret")
    assert client.post("/synthesize", json=PAYLOAD).status_code == 401
    assert (
        client.post(
            "/synthesize", json=PAYLOAD, headers={"X-API-Key": "wrong"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/synthesize", json=PAYLOAD, headers={"X-API-Key": "s3cret"}
        ).status_code
        == 200
    )
    assert mock_task_delay.call_count == 1  # rejected requests never enqueue


def test_rate_limit_returns_429_with_retry_after(
    client, fake_redis, mock_task_delay, monkeypatch
):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    assert client.post("/synthesize", json=PAYLOAD).status_code == 200
    assert client.post("/synthesize", json=PAYLOAD).status_code == 200
    r = client.post("/synthesize", json=PAYLOAD)
    assert r.status_code == 429
    assert 1 <= int(r.headers["retry-after"]) <= 60
    assert mock_task_delay.call_count == 2


# ---------- /jobs and misc ----------


def test_get_job_returns_state(client, fake_redis):
    jid = str(uuid.uuid4())
    fake_redis.hset(
        f"job:{jid}", mapping={"status": "done", "audio_url": f"/audio/{jid}.mp3"}
    )
    r = client.get(f"/jobs/{jid}")
    assert r.status_code == 200
    assert r.json() == {"status": "done", "audio_url": f"/audio/{jid}.mp3"}


def test_get_job_404_when_unknown(client, fake_redis):
    assert client.get(f"/jobs/{uuid.uuid4()}").status_code == 404


def test_get_job_rejects_non_uuid(client, fake_redis):
    assert client.get("/jobs/not-a-uuid").status_code == 422


def test_root_serves_frontend(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
