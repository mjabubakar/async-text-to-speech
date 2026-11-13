import asyncio
import logging
import uuid
from uuid import UUID

from fastapi import Depends, FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from starlette.middleware.base import BaseHTTPMiddleware

from app import jobs
from app import redis_client as rc
from app.config import settings
from app.security import rate_limit, require_api_key
from app.tasks import synthesize_task

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class SynthesizeText(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        if len(value) > settings.max_text_chars:
            raise ValueError(
                f"text must be at most {settings.max_text_chars} characters"
            )
        return value


def _error(status_code: int, msg: str, type_: str, loc: list[str] | None = None):
    return JSONResponse(
        status_code=status_code,
        content={"detail": [{"msg": msg, "loc": loc or [], "type": type_}]},
    )


class ExceptionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception:
            logger.exception("Unhandled exception in request")
            return _error(500, "Internal server error", "server_error")


app = FastAPI(
    title="Async Text to Speech Pipeline",
    description="Queues text, chunks it, synthesizes via ElevenLabs, serves the audio",
    version="0.2.0",
)

app.add_middleware(ExceptionMiddleware)
app.mount("/audio", StaticFiles(directory=settings.audio_output_dir), name="audio")
app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # jsonable_encoder: errors from custom validators carry the raw exception in
    # `ctx`, which is not JSON-serializable on its own.
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": jsonable_encoder(exc.errors())},
    )


@app.get("/")
async def root():
    return FileResponse(settings.static_dir / "index.html")


# rate_limit runs before auth so wrong-key guessing is throttled too
@app.post("/synthesize", dependencies=[Depends(rate_limit), Depends(require_api_key)])
async def synthesize(data: SynthesizeText):
    job_id = str(uuid.uuid4())
    await jobs.set_status_async(rc.async_redis, job_id, status="queued")
    try:
        # .delay() is a blocking network call; run it off the event loop
        await asyncio.to_thread(synthesize_task.delay, job_id, data.text)
    except Exception:
        logger.exception("Failed to enqueue job %s", job_id)
        await jobs.set_status_async(
            rc.async_redis, job_id, status="failed", error="Queue unavailable"
        )
        return _error(503, "Queue unavailable, try again later", "service_unavailable")
    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
async def get_job(job_id: UUID):
    job = await rc.async_redis.hgetall(jobs.job_key(str(job_id)))
    if not job:
        return _error(404, "Job not found", "not_found", ["job_id"])
    return job
