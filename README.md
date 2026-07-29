# Async Document to Speech Pipeline

Submits long-form text, chunks it intelligently, synthesizes audio via the
ElevenLabs TTS API, and returns a streamable result. Built with FastAPI,
Celery, and Redis.

## Status

🚧 Work in progress — see commits for build order.

## Local development

1. Copy `.env.example` to `.env` and fill in values
2. `docker compose up -d` (starts RabbitMQ + Redis)
3. `celery -A app.celery_app worker --loglevel=info`
