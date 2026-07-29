def test_synthesize_returns_job_id(client, mock_redis, mock_task_delay):
    response = client.post("/synthesize", json={"text": "Hello world."})

    assert response.status_code == 200
    body = response.json()
    assert "job_id" in body
    assert isinstance(body["job_id"], str)


def test_synthesize_enqueues_task(client, mock_redis, mock_task_delay):
    response = client.post("/synthesize", json={"text": "Hello world."})
    job_id = response.json()["job_id"]

    mock_task_delay.assert_called_once()
    args = mock_task_delay.call_args[0]
    assert args[0] == job_id
    assert args[1] == "Hello world."


def test_synthesize_sets_initial_status_queued(client, mock_redis, mock_task_delay):
    response = client.post("/synthesize", json={"text": "Hello world."})
    job_id = response.json()["job_id"]

    job = mock_redis.hgetall(f"job:{job_id}")
    assert job.get("status") == "queued"


def test_synthesize_missing_text_returns_422(client, mock_redis, mock_task_delay):
    response = client.post("/synthesize", json={})

    assert response.status_code == 422
    assert "detail" in response.json()


def test_synthesize_wrong_type_returns_422(client, mock_redis, mock_task_delay):
    response = client.post("/synthesize", json={"text": 12345})

    assert response.status_code == 422


def test_get_job_returns_status_when_exists(client, mock_redis):
    mock_redis.hset(
        "job:abc123", mapping={"status": "done", "audio_url": "/audio/abc123.mp3"}
    )

    response = client.get("/jobs/abc123")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert body["audio_url"] == "/audio/abc123.mp3"


def test_get_job_returns_404_when_not_found(client, mock_redis):
    response = client.get("/jobs/does-not-exist")

    assert response.status_code == 404


def test_root_serves_frontend(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
