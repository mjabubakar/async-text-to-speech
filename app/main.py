import logging
import uuid

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.redis_client import redis_client
from app.tasks import synthesize_task

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class SynthesizeText(BaseModel):
    text: str


class ExceptionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception as e:
            logger.exception("Unhandled exception in request")
            response = JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "detail": [{"msg": str(e), "loc": [], "type": "server_error"}]
                },
            )
        return response


app = FastAPI(
    title="Async Document to Speech Pipeline",
    description="Queues text, chunks it, synthesizes via ElevenLabs, returns streamable audio",
    version="0.1.0",
)

app.add_middleware(ExceptionMiddleware)
app.mount("/audio", StaticFiles(directory=settings.audio_output_dir), name="audio")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": exc.errors()},
    )


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.post("/synthesize")
async def synthesize(data: SynthesizeText):
    job_id = str(uuid.uuid4())
    redis_client.hset(f"job:{job_id}", mapping={"status": "queued"})
    synthesize_task.delay(job_id, data.text)
    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = redis_client.hgetall(f"job:{job_id}")
    if not job:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "detail": [
                    {"msg": "Job not found", "loc": ["job_id"], "type": "not_found"}
                ]
            },
        )
    return job
