import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import IngestionLog, Tenant
from app.db.session import get_db

router = APIRouter()


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


@router.get("")
def list_logs(
    tenant_id: str,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    tenant = _get_tenant_or_404(tenant_id, db)
    tid = tenant.id

    offset = (page - 1) * page_size
    total = db.query(IngestionLog).filter(IngestionLog.tenant_id == tid).count()
    logs = (
        db.query(IngestionLog)
        .filter(IngestionLog.tenant_id == tid)
        .order_by(IngestionLog.started_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    return {
        "items": [_serialize_log(l) for l in logs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
