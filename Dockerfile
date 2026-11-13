FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

# same fixed UID in both images so the shared audio volume is writable by both
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# system-wide, so the non-root runtime user can read it
RUN python -c "import nltk; nltk.download('punkt_tab', download_dir='/usr/local/share/nltk_data')"

COPY app/ ./app/
COPY static/ ./static/

RUN mkdir -p /app/audio_output && chown app:app /app/audio_output
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]