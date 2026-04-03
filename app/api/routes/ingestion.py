import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import IngestionLog, Tenant
from app.db.session import get_db

router = APIRouter()


class IngestRequest(BaseModel):
    query_type: str
    query_value: str


def _get_tenant_or_404(tenant_id: str, db: Session) -> Tenant:
    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tenant = db.query(Tenant).filter(Tenant.id == tid).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


def _serialize_log(log: IngestionLog) -> dict:
    return {
        "log_id": str(log.id),
        "tenant_id": str(log.tenant_id),
        "query_type": log.query_type,
        "query_value": log.query_value,
        "status": log.status,
        "celery_task_id": log.celery_task_id,
        "fetched_count": log.fetched_count,
        "success_count": log.success_count,
        "fail_count": log.fail_count,
        "errors": log.errors,
        "started_at": log.started_at.isoformat() if log.started_at else None,
        "finished_at": log.finished_at.isoformat() if log.finished_at else None,
    }


@router.post("")
def trigger_ingestion(tenant_id: str, body: IngestRequest, db: Session = Depends(get_db)):
    if body.query_type not in ("author", "subject"):
        raise HTTPException(status_code=422, detail="query_type must be 'author' or 'subject'")

    tenant = _get_tenant_or_404(tenant_id, db)

    log = IngestionLog(
        tenant_id=tenant.id,
        query_type=body.query_type,
        query_value=body.query_value,
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    from app.workers.tasks import ingest_books
    task = ingest_books.delay(str(tenant.id), body.query_type, body.query_value, str(log.id))

    log.celery_task_id = task.id
    db.commit()

    return {
        "log_id": str(log.id),
        "task_id": task.id,
        "status": log.status,
    }


@router.get("/{task_id}")
def get_ingestion_status(tenant_id: str, task_id: str, db: Session = Depends(get_db)):
    _get_tenant_or_404(tenant_id, db)

    log = (
        db.query(IngestionLog)
        .filter(
            IngestionLog.tenant_id == uuid.UUID(tenant_id),
            IngestionLog.celery_task_id == task_id,
        )
        .first()
    )
    if not log:
        raise HTTPException(status_code=404, detail="Ingestion job not found")

    return _serialize_log(log)
