"""Shared pytest fixtures."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_redis(monkeypatch):
    """Replace the real Redis client with an in-memory fake for endpoint tests."""
    fake_store: dict[str, dict[str, str]] = {}

    mock = MagicMock()

    def hset(key, mapping):
        fake_store.setdefault(key, {}).update(mapping)

    def hgetall(key):
        return fake_store.get(key, {})

    mock.hset.side_effect = hset
    mock.hgetall.side_effect = hgetall

    monkeypatch.setattr("app.main.redis_client", mock)
    return mock


@pytest.fixture
def mock_task_delay(monkeypatch):
    """Prevent /synthesize from actually publishing to RabbitMQ during tests."""
    mock = MagicMock()
    monkeypatch.setattr("app.main.synthesize_task.delay", mock)
    return mock
