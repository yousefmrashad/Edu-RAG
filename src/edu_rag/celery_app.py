"""Celery application for the ingest worker."""

from celery import Celery

from edu_rag.config import settings

# Shared with the API, which dispatches by name and cannot import the worker:
# the API image does not install the ingest extra.
INGEST_TASK = "edu_rag.workers.ingest.ingest_document"

app = Celery(
    "edu_rag",
    broker=settings.celery_broker_url,
    include=["edu_rag.workers.ingest"],
)

# The worker listens on `-Q ingest`; a task published to the default `celery`
# queue would have no consumer.
app.conf.task_default_queue = "ingest"

# Children are recycled every `--max-tasks-per-child`; late acks mean a task
# that dies with its child is redelivered instead of lost.
app.conf.task_acks_late = True
