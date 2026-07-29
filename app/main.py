import logging

from celery import Celery
from config import settings
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class SynthesizeText(BaseModel):
    text: str


class ExceptionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception as e:
            logger.exception("Unhandled error in request")
            response = JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "detail": [{"msg": str(e), "loc": [], "type": "server_error"}]
                },
            )
        return response


celery_app = Celery(
    "worker",
    broker=settings.rabbitmq_url,
    backend=settings.redis_url,  # separate service for results/status
)

app = FastAPI(
    title="Async Document to Speech Pipeline",
    description="Queues text, chunks it, synthesizes via ElevenLabs, returns streamable audio",
    version="0.1.0",
)

app.add_middleware(ExceptionMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )


@app.post("/synthesize")
async def synthesize(data: SynthesizeText):
    return data


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    return {"job_id": job_id}
