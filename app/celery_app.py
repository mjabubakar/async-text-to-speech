from celery import Celery

from app.config import settings

celery_app = Celery(
    "worker",
    broker=settings.rabbitmq_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
)

import app.tasks  # noqa: F401  — registers synthesize_task with celery_app
