# Async Document to Speech Pipeline

Submits long-form text, get back streamable audio. The service chunks text at sentence
boundaries, synthesizes each chunk concurrently via the ElevenLabs TTS API, stitches
the results, and serves the final audio file — all decoupled from the initial HTTP
request via a Celery/RabbitMQ job queue.

Built to demonstrate a production-shaped async architecture: request/response stays
fast regardless of how long synthesis takes, failures are retried intelligently
instead of blindly, and the whole thing is tested and containerized.

## How it works

```
                 ┌─────────────┐
   1. POST       │   FastAPI   │  2. enqueue job_id + text
   /synthesize ─►│   (api)     │─────────────────┐
                 └─────────────┘                 ▼
                       ▲                  ┌──────────────┐
                       │ 4. GET           │   RabbitMQ   │
                       │ /jobs/{id}       │   (broker)   │
                       │ (poll)           └──────┬───────┘
                       │                          │ 3. deliver
                 ┌─────┴───────┐                  ▼
                 │    Redis    │◄────────┌──────────────┐
                 │ (job status)│  status  │Celery worker │
                 └─────────────┘  updates │  chunk → TTS │
                                          │  → stitch    │
                 ┌─────────────┐         └──────┬───────┘
                 │  disk / mp3 │◄───────────────┘
                 │  (audio_out)│  5. write final .mp3
                 └──────┬──────┘
                        │ 6. served via
                        ▼ /audio/{job_id}.mp3
                 ┌─────────────┐
                 │   Browser   │  plays audio once
                 │  (polling)  │  status == "done"
                 └─────────────┘
```

**Why this shape:** `POST /synthesize` never touches the ElevenLabs API directly — it
only enqueues a job and returns a `job_id` in milliseconds. All the slow work
(chunking, external API calls, stitching, disk I/O) happens in a separate Celery
worker process. The API stays responsive no matter how long a synthesis job takes or
how many are queued.

## Key design decisions

- **Sentence-safe chunking, not fixed-size splitting.** Text is split via `nltk`
  sentence tokenization (handles abbreviations, decimals, and other edge cases a
  naive `.split(".")` gets wrong), then grouped into chunks under the API's character
  limit — respecting paragraph boundaries where possible so chunk seams don't
  interrupt natural pauses.
- **Per-chunk retry, not job-level retry.** Each ElevenLabs call is individually
  wrapped with Tenacity (exponential backoff, max 4 attempts). Retries only trigger
  on transient failures — timeouts, connection errors, `5xx`, `429` — never on `4xx`
  client errors like a bad API key, which won't succeed no matter how many times you
  retry.
- **Status/metadata in Redis, audio bytes on disk.** Redis stores small, fast-changing
  job state (`status`, `audio_url`, `error`) — not binary audio, which doesn't belong
  in an in-memory store. Audio is written to disk and served via a static file mount.
- **Broker and result backend are separate services.** RabbitMQ (message delivery)
  and Redis (job state/results) serve different purposes — RabbitMQ alone can't
  durably answer "what's the status of job X" the way `GET /jobs/{id}` needs.

## Tech stack

| Layer                      | Choice                             |
| -------------------------- | ---------------------------------- |
| API framework              | FastAPI                            |
| Async task queue           | Celery                             |
| Message broker             | RabbitMQ                           |
| Result / status store      | Redis                              |
| Config & validation        | Pydantic v2 / pydantic-settings    |
| HTTP client (external API) | httpx (async)                      |
| Retry logic                | Tenacity (exponential backoff)     |
| Sentence tokenization      | nltk                               |
| Testing                    | pytest, pytest-asyncio, pytest-cov |
| Linting / formatting       | ruff (+ pre-commit hooks)          |
| Containerization           | Docker, Docker Compose             |
| CI                         | GitHub Actions                     |
| Dependency management      | uv                                 |

## Project structure

```
.
├── app/
│   ├── main.py          # FastAPI app: routes, middleware, exception handlers
│   ├── celery_app.py    # Celery instance: broker/backend config
│   ├── tasks.py         # Synthesis task: chunk → TTS → stitch → store
│   ├── chunking.py      # Pure function: sentence-safe text chunking
│   ├── config.py         # Settings (env-driven), via pydantic-settings
│   └── redis_client.py    # Shared Redis connection for job status
├── static/
│   └── index.html          # Minimal frontend: submit, poll, play
├── tests/
│   ├── conftest.py           # Shared fixtures (TestClient, mocked Redis/Celery)
│   ├── test_chunking.py        # Pure logic, no I/O
│   ├── test_tasks.py             # Mocked ElevenLabs API + retry behavior
│   └── test_endpoints.py           # FastAPI TestClient, mocked dependencies
├── Dockerfile                          # API image
├── Dockerfile.worker                     # Celery worker image
├── docker-compose.yaml                     # RabbitMQ + Redis + api + worker
└── .github/workflows/ci.yml                    # Lint, test, coverage, build
```

## Getting started

### 1. Clone and configure

```bash
git clone <repo-url>
cd async-doc-to-speech
cp .env.example .env
```

Edit `.env` and add a real `ELEVENLABS_API_KEY`.

### 2. Choose a setup path

**Option A — fully containerized (simplest):**

```bash
docker compose up -d --build
```

Everything (RabbitMQ, Redis, API, worker) runs in containers. API available at
`http://localhost:8000`.

**Option B — hybrid local dev (infra in Docker, app on host):**

```bash
docker compose up -d rabbitmq redis
uv sync
uv run celery -A app.celery_app worker --loglevel=info   # terminal 1
uv run uvicorn app.main:app --reload                       # terminal 2
```

### ⚠️ A note on `.env` and Docker networking

The broker/backend hostnames differ depending on **where the process runs**, because
each Docker container has its own network namespace — `localhost` inside a container
refers to that container, not sibling containers or your host machine.

| Running from...                    | `RABBITMQ_URL` host | `REDIS_URL` host |
| ---------------------------------- | ------------------- | ---------------- |
| Your host machine (Option B)       | `localhost`         | `localhost`      |
| Inside `docker compose` (Option A) | `rabbitmq`          | `redis`          |

```bash
# .env — for Option B (host machine, containers only expose ports)
RABBITMQ_URL=amqp://guest:guest@localhost:5672//
REDIS_URL=redis://localhost:6379/0

# .env — for Option A (api/worker running inside docker-compose)
RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672//
REDIS_URL=redis://redis:6379/0
```

Docker Compose's internal DNS resolves service names (`rabbitmq`, `redis` — matching
the service names in `docker-compose.yaml`) to the correct container IPs. If you
switch between Option A and B, update `.env` accordingly.

### 3. Try it

```bash
curl -X POST http://localhost:8000/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello, this is a test of the synthesis pipeline."}'
# => {"job_id": "..."}

curl http://localhost:8000/jobs/<job_id>
# => {"status": "processing"} ... eventually {"status": "done", "audio_url": "/audio/<job_id>.mp3"}
```

Or open `http://localhost:8000` for the browser UI (submit → poll → play).

## API reference

| Method | Path                  | Description                                                 |
| ------ | --------------------- | ----------------------------------------------------------- |
| POST   | `/synthesize`         | Submit text. Returns `{"job_id": "..."}` immediately.       |
| GET    | `/jobs/{job_id}`      | Poll job status: `queued` → `processing` → `done`/`failed`. |
| GET    | `/audio/{job_id}.mp3` | Serves the stitched audio once status is `done`.            |
| GET    | `/`                   | Minimal browser frontend.                                   |

## Testing & coverage

```bash
uv run pytest -v
```

Coverage is collected automatically (configured in `pyproject.toml`):

```bash
uv run pytest --cov-report=term-missing
```

Current coverage: **93%** overall (see `pyproject.toml` for config; CI enforces a
minimum via `--cov-fail-under`).

Test suite is organized by what it verifies, not just what file it touches:

- **`test_chunking.py`** — pure function, no mocking needed. Includes edge cases:
  empty input, oversized single sentences, abbreviation handling.
- **`test_tasks.py`** — ElevenLabs API fully mocked (`httpx` never makes a real
  network call). Covers the retry/backoff logic explicitly, including a test that
  confirms non-retryable errors (`401`) fail fast rather than burning retry attempts.
- **`test_endpoints.py`** — FastAPI `TestClient`, with Redis and Celery's `.delay()`
  mocked via fixtures in `conftest.py` — no real broker/backend needed to run this
  suite.

## CI/CD

GitHub Actions (`.github/workflows/ci.yml`) runs on every push/PR to `main`:

1. `ruff check` + `ruff format --check`
2. Full pytest suite with coverage enforcement
3. Docker image builds (API + worker) as a build-correctness check

## Known limitations / possible next steps

Being upfront about trade-offs made for scope, not overlooked:

- **Job failure is all-or-nothing.** If one chunk exhausts its retries, the whole job
  fails — there's no partial-success/partial-audio path. Worth adding if partial
  results would be more useful than none.
- **MP3 stitching is raw byte concatenation.** Works reliably for MP3's frame format,
  but a library like `pydub` (via ffmpeg) would handle format edge cases and
  cross-fades more robustly if audio quality issues ever surface at chunk boundaries.
- **No auth on `/synthesize`.** Fine for a local/demo deployment; a real deployment
  would need at minimum an API key check to prevent unauthenticated ElevenLabs quota
  consumption.
- **`rabbitmq.conf` permits a deprecated RabbitMQ 4.x feature** (`transient_nonexcl_queues`)
  that Celery/kombu's default queue declarations still rely on. This is a documented,
  intentional stopgap — see the comment in `rabbitmq.conf` — not a production-hardened
  configuration.

## License

MIT
