import asyncio
import logging

from celery import Celery

from app.core.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "titan",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.beat_schedule = {
    "refresh-all-catalogs-24h": {
        "task": "app.workers.tasks.refresh_all_catalogs",
        "schedule": 86400.0,
    },
}


@celery_app.task(
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
)
def ingest_books(tenant_id: str, query_type: str, query_value: str, log_id: str) -> None:
    from app.services.ingestion import run_ingestion
    asyncio.run(run_ingestion(tenant_id, query_type, query_value, log_id))


@celery_app.task(
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
)
def refresh_all_catalogs() -> None:
    """
    Scheduled task (every 24 h via Celery Beat).

    Finds all distinct (tenant_id, query_type, query_value) combinations that
    have at least one successfully completed ingestion_log, then enqueues a
    fresh ingest_books task for each one.
    """
    from sqlalchemy import distinct
    from app.db.models import IngestionLog
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        rows = (
            db.query(
                distinct(IngestionLog.tenant_id),
                IngestionLog.query_type,
                IngestionLog.query_value,
            )
            .filter(IngestionLog.status == "complete")
            .all()
        )

        queued = 0
        for tenant_id, query_type, query_value in rows:
            log = IngestionLog(
                tenant_id=tenant_id,
                query_type=query_type,
                query_value=query_value,
            )
            db.add(log)
            db.commit()
            db.refresh(log)

            task = ingest_books.delay(
                str(tenant_id), query_type, query_value, str(log.id)
            )
            log.celery_task_id = task.id
            db.commit()
            queued += 1

        logger.info("refresh_all_catalogs: queued %d ingestion jobs", queued)
        return {"queued": queued}
    finally:
        db.close()
