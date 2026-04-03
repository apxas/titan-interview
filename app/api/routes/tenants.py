import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import Tenant
from app.db.session import get_db

router = APIRouter()


class TenantCreate(BaseModel):
    name: str


@router.post("")
def create_tenant(body: TenantCreate, db: Session = Depends(get_db)):
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="name must not be empty")

    tenant = Tenant(
        id=uuid.uuid4(),
        name=body.name.strip(),
        api_key=secrets.token_hex(32),
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    return {
        "id": str(tenant.id),
        "name": tenant.name,
        "api_key": tenant.api_key,
        "created_at": tenant.created_at.isoformat(),
    }


@router.get("")
def list_tenants(db: Session = Depends(get_db)):
    tenants = db.query(Tenant).order_by(Tenant.created_at.desc()).all()
    return {
        "items": [
            {
                "id": str(t.id),
                "name": t.name,
                "created_at": t.created_at.isoformat(),
            }
            for t in tenants
        ],
        "total": len(tenants),
    }
