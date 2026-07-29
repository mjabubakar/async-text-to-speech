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


@celery_app.task
def add(x: int, y: int) -> int:
    return x + y
