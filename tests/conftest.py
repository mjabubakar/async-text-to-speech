"""Shared fixtures. Env is set BEFORE importing app, because Settings() runs at import."""

import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

os.environ.setdefault("RABBITMQ_URL", "memory://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("ELEVENLABS_API_KEY", "test-key-not-used-mocked")
os.environ.setdefault("AUDIO_OUTPUT_DIR", tempfile.mkdtemp(prefix="tts-tests-"))

from fastapi.testclient import TestClient

from app import redis_client as rc
from app.config import settings
from app.main import app


class FakeRedis:
    """Minimal in-memory stand-in for the Redis commands this app uses."""

    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.counters: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def expire(self, key, seconds):
        self.ttls[key] = seconds
        return True

    def ttl(self, key):
        return self.ttls.get(key, -1)

    def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def keys(self, prefix=""):
        return [k for k in self.hashes if k.startswith(prefix)]


class AsyncFacade:
    """Exposes a FakeRedis through async methods, like redis.asyncio."""

    def __init__(self, inner: FakeRedis):
        self._inner = inner

    def __getattr__(self, name):
        fn = getattr(self._inner, name)

        async def call(*args, **kwargs):
            return fn(*args, **kwargs)

        return call


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch):
    """Deterministic settings regardless of the developer's .env."""
    monkeypatch.setattr(settings, "api_key", None)
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def fake_redis(monkeypatch):
    """Sync and async views over one in-memory store. Returns the sync one."""
    fake = FakeRedis()
    monkeypatch.setattr(rc, "sync_redis", fake)
    monkeypatch.setattr(rc, "async_redis", AsyncFacade(fake))
    return fake


@pytest.fixture
def mock_task_delay(monkeypatch):
    """Prevent /synthesize from publishing to RabbitMQ."""
    mock = MagicMock()
    monkeypatch.setattr("app.main.synthesize_task.delay", mock)
    return mock


@pytest.fixture
def stub_tts(monkeypatch):
    """Fake ElevenLabs at the HTTP layer, so real request-building code runs."""
    state = SimpleNamespace(calls=[], status=200)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["xi-api-key"] == settings.elevenlabs_api_key
        body = json.loads(request.content)
        state.calls.append(body)
        if state.status != 200:
            return httpx.Response(state.status)
        return httpx.Response(200, content=f"<{body['text']}>".encode())

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    return state
