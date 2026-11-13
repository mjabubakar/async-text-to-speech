# Async Text-to-Speech Pipeline

[![CI](https://github.com/mjabubakar/async-text-to-speech/actions/workflows/ci.yml/badge.svg)](https://github.com/mjabubakar/async-text-to-speech/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Submit long-form text and get back an MP3. The service splits the text at sentence
boundaries, synthesizes the chunks concurrently through the ElevenLabs API, stitches
the audio together in order, and serves the file. The slow work runs in a Celery
worker behind a RabbitMQ queue, so the HTTP request returns immediately.

Built to practise queue-backed async architecture: a fast request path regardless of
synthesis time, retries that tell transient failures from permanent ones, bounded
use of a third-party API, and tests that don't depend on real services.

## How it works

```
   1. POST /synthesize ─► FastAPI ──2. enqueue──► RabbitMQ ──3. deliver──► Celery worker
                            │  ▲                                              │
                            │  └──── 4. GET /jobs/{id} (poll) ◄── Redis ◄─────┤ status
                            ▼                                                 │
                        Browser ◄── 6. GET /audio/{id}.mp3 ◄── shared volume ◄┘ 5. write mp3
```

`POST /synthesize` never calls ElevenLabs. It validates the input, records a
`queued` job in Redis, publishes a task and returns a `job_id`. The worker then:

1. Splits the text into chunks of at most `MAX_CHUNK_CHARS` characters.
2. Sends the chunks to ElevenLabs concurrently, with at most `TTS_CONCURRENCY`
   requests in flight, passing neighbouring text (`previous_text` / `next_text`) so
   the voice stays continuous across chunk boundaries.
3. Retries transient failures per chunk.
4. Concatenates the audio in order, writes it atomically (temp file, then rename)
   and marks the job `done`.

## Key design decisions

- **Sentence-aware chunking.** Text is tokenized with NLTK Punkt and packed greedily up
  to the chunk limit. A paragraph break inside a chunk is preserved. A sentence longer
  than the limit is split on whitespace, so every chunk fits the limit. The tokenizer
  is English.
- **Per-chunk retries, classified.** Each ElevenLabs call is wrapped in Tenacity: up to
  4 attempts with exponential backoff. Only timeouts, connection errors, `5xx` and
  `429` are retried. `4xx` errors such as a bad API key fail immediately.
- **Bounded concurrency.** A semaphore caps in-flight provider requests per job, and it
  wraps the retry loop, so the cap also holds while chunks are retrying.
- **Input validation and abuse controls.** Blank or oversized text is rejected with a
  `422` before anything is queued. `/synthesize` has optional shared-secret auth
  (`API_KEY`, compared in constant time) and a per-IP rate limit in Redis.
- **No internals in errors.** Clients get short generic messages (`Internal server
error`, `TTS API error: 401`); full detail goes to the server logs.
- **State in Redis, audio on disk.** Redis stores small job state (`status`,
  `chunk_count`, `audio_url`, `error`) with a TTL. Audio is written to a shared volume
  and deleted after `AUDIO_RETENTION_SECONDS`. Celery's result backend is disabled
  because the job hash is the source of truth.
- **Worker-crash behaviour.** The worker uses late acknowledgement and
  `reject_on_worker_lost`, so a task whose worker process was killed is redelivered,
  and Compose restarts the worker. See limitations for what this does and doesn't cover.
- **Non-blocking API.** The API uses async Redis and runs the blocking `.delay()` call
  in a thread. If the broker is unreachable it returns `503` and marks the job failed.

## Tech stack

| Layer                     | Choice                             |
| ------------------------- | ---------------------------------- |
| API                       | FastAPI                            |
| Task queue                | Celery                             |
| Broker                    | RabbitMQ                           |
| Job state / rate limiting | Redis                              |
| Config                    | pydantic-settings                  |
| HTTP client               | httpx (async)                      |
| Retries                   | Tenacity                           |
| Sentence tokenization     | NLTK                               |
| Tests                     | pytest, pytest-asyncio, pytest-cov |
| Lint / format             | ruff                               |
| Containers                | Docker, Docker Compose             |
| CI                        | GitHub Actions                     |
| Dependencies              | uv                                 |

## Project structure

```
.
├── app/
│   ├── main.py          # Routes, validation, middleware, error handlers
│   ├── celery_app.py    # Celery config (late acks, no result backend)
│   ├── tasks.py         # chunk → synthesize → stitch → store
│   ├── chunking.py      # Sentence-safe chunking
│   ├── security.py      # API-key auth, rate limiting
│   ├── jobs.py          # Job-state helpers (with TTL)
│   ├── cleanup.py       # Old-audio deletion
│   ├── config.py        # Env-driven settings
│   └── redis_client.py  # Sync (worker) and async (API) Redis clients
├── static/index.html    # Submit, poll, play
├── tests/               # chunking, tasks, endpoints, cleanup, in-process end-to-end
├── Dockerfile           # API image
├── Dockerfile.worker    # Worker image
├── docker-compose.yaml  # rabbitmq, redis, api, worker
└── .github/workflows/ci.yml
```

## Getting started

```bash
git clone https://github.com/mjabubakar/async-text-to-speech && cd async-text-to-speech
cp .env.example .env        # then set ELEVENLABS_API_KEY
```

**Option A: everything in containers**

```bash
docker compose up -d --build
```

Compose overrides `RABBITMQ_URL` and `REDIS_URL` with the in-network hostnames, so
`.env` can stay as it is. Open `http://localhost:8000`.

**Option B: infrastructure in Docker, app on the host**

```bash
docker compose up -d rabbitmq redis
uv sync
uv run celery -A app.celery_app worker --loglevel=info   # terminal 1
uv run uvicorn app.main:app --reload                     # terminal 2
```

RabbitMQ and Redis are published on `127.0.0.1` only.

### Configuration

| Variable                                     | Default                 | Purpose                                                                                                   |
| -------------------------------------------- | ----------------------- | --------------------------------------------------------------------------------------------------------- |
| `ELEVENLABS_API_KEY`                         | required                | Provider credential                                                                                       |
| `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL_ID` | demo voice, `eleven_v4` | Voice and model                                                                                           |
| `MAX_CHUNK_CHARS`                            | 4000                    | Chunk size. Keep it below the model's per-request limit (10,000 for Multilingual v2, 3,000 for Eleven v3) |
| `TTS_CONCURRENCY`                            | 4                       | Max in-flight provider requests per job                                                                   |
| `MAX_TEXT_CHARS`                             | 100000                  | Largest accepted input                                                                                    |
| `API_KEY`                                    | unset                   | If set, `/synthesize` requires an `X-API-Key` header                                                      |
| `RATE_LIMIT_PER_MINUTE`                      | 10                      | Per-IP limit on `/synthesize`; 0 disables                                                                 |
| `JOB_TTL_SECONDS`, `AUDIO_RETENTION_SECONDS` | 6 h, 24 h               | Expiry of job state and audio files                                                                       |

### Try it

```bash
curl -X POST localhost:8000/synthesize \
  -H "Content-Type: application/json" -H "X-API-Key: <only if API_KEY is set>" \
  -d '{"text": "Hello, this is a test of the synthesis pipeline."}'
# => {"job_id": "..."}

curl localhost:8000/jobs/<job_id>
# => {"status": "processing"} ... eventually
# => {"status": "done", "audio_url": "/audio/<job_id>.mp3", "chunk_count": "1"}
```

## API reference

| Method | Path                  | Description                                                                                                                                   |
| ------ | --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| POST   | `/synthesize`         | Body `{"text": "..."}`. Returns `{"job_id"}`. Errors: `401` bad key, `422` blank/oversized text, `429` rate limited, `503` queue unavailable. |
| GET    | `/jobs/{job_id}`      | `job_id` must be a UUID. Status: `queued` → `processing` → `done` / `failed`. `404` if unknown or expired.                                    |
| GET    | `/audio/{job_id}.mp3` | The finished audio.                                                                                                                           |
| GET    | `/`                   | Minimal browser UI.                                                                                                                           |

## Testing

```bash
uv run pytest
```

Coverage is collected automatically and enforced in CI (`--cov-fail-under`); see the
badge or latest CI run for the current figure.

- **`test_chunking.py`**: pure logic. Empty input, limit guarantees, paragraph
  handling, oversized sentences, abbreviations.
- **`test_tasks.py`**: ElevenLabs is mocked. Retry classification is parametrized over
  status codes and transport errors (no real sleeping), plus ordering, the concurrency
  cap, and task outcomes (success, each failure class, no leaked internals, cleanup).
- **`test_endpoints.py`**: FastAPI `TestClient` with an in-memory Redis fake. Covers
  validation, auth, rate limiting, `503` on broker failure, and generic `500`s.
- **`test_pipeline_eager.py`**: API → task → Redis → audio file in one process, with
  ElevenLabs stubbed at the HTTP layer and checks on the actual request bodies.

Nothing here runs against a real RabbitMQ or Redis; the broker/Redis path is checked
manually with `docker compose`.

## CI

On every push and PR to `main` (`.github/workflows/ci.yml`): `ruff check`, `ruff format
--check`, the test suite with a coverage gate, then Docker builds of both images.

## Known limitations

- **No real-infrastructure tests in CI.** Redis and the broker are faked or eager in
  tests; they are exercised by hand via Compose.
- **Job failure is all-or-nothing.** If one chunk exhausts its retries the job fails and
  successful chunks are discarded. In-flight sibling requests are not cancelled
  immediately; they are torn down when the task exits.
- **Retries are simple.** Exponential backoff without jitter, and `Retry-After` is not
  read.
- **Crash recovery is partial.** A killed worker's task is redelivered and restarts from
  scratch (paying the provider again). There is no stale-job detection, so a job whose
  worker never returns stays `processing` until its Redis entry expires; a message that
  crashes the worker every time would be redelivered repeatedly.
- **Audio is held in memory** while stitching, and chunks are joined by raw MP3
  concatenation. Neighbouring-text context reduces, but doesn't remove, audible shifts
  at chunk seams.
- **Single-host storage.** API and worker share a Docker volume, so they must share a
  host. Multi-host would need object storage.
- **Auth is one shared secret** from the environment. There are no per-user keys,
  hashing, scopes or rotation, and the UI asks the user to type the key.
- **Rate limiting is a fixed window keyed on client IP**; behind a reverse proxy it needs
  forwarded-header configuration.
- **English sentence tokenizer**, although the TTS model is multilingual. Input is plain
  text; there is no PDF/DOCX parsing.
- **RabbitMQ stopgap.** The Compose file enables a deprecated RabbitMQ queue feature
  (`transient_nonexcl_queues`) that Celery/kombu's default queue declarations may still
  rely on. It's intentional for this setup, not a hardened configuration.
