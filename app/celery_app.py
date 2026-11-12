from celery import Celery

from app.config import settings

# No result backend: job state lives in our own Redis hash (see app/jobs.py).
celery_app = Celery("worker", broker=settings.rabbitmq_url)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    # If the worker process is killed mid-task, the message is redelivered
    # instead of being lost. One task at a time per worker process.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
)

import app.tasks  # noqa: F401  — registers synthesize_task with celery_app
