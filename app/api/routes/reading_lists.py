import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import Book, ReadingListSubmission, Tenant
from app.db.session import get_db
from app.services.pii import hash_email, hash_patron

router = APIRouter()

OL_BASE = "https://openlibrary.org"


class ReadingListRequest(BaseModel):
    name: str
    email: str
    books: list[str]


def _get_tenant_or_404(tenant_id: str, db: Session) -> Tenant:
    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tenant = db.query(Tenant).filter(Tenant.id == tid).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


async def _resolve_book_ids(
    tenant_id: uuid.UUID,
    ol_work_ids: list[str],
    db: Session,
) -> tuple[list[str], list[str]]:
    """
    For each OL work ID, check local DB first.
    Fall back to a live OL /works/{id}.json call if not found locally.
    Returns (resolved_ids, failed_ids) where resolved_ids are local book UUIDs.
    """
    resolved: list[str] = []
    failed: list[str] = []

    async with httpx.AsyncClient(timeout=60) as client:
        for ol_id in ol_work_ids:
            # Normalise: ensure leading slash for consistent storage
            normalised = ol_id if ol_id.startswith("/") else f"/works/{ol_id}"

            local = (
                db.query(Book)
                .filter(
                    Book.tenant_id == tenant_id,
                    Book.ol_work_id == normalised,
                )
                .first()
            )

            if local:
                resolved.append(str(local.id))
                continue

            # Not in local DB — try live OL lookup
            work_key = normalised.lstrip("/").split("/")[-1]  # "OL123W"
            try:
                resp = await client.get(f"{OL_BASE}/works/{work_key}.json")
                if resp.status_code == 200:
                    resolved.append(normalised)   # store OL key as ref; not in local DB
                else:
                    failed.append(ol_id)
            except Exception:
                failed.append(ol_id)

    return resolved, failed


@router.post("")
async def submit_reading_list(
    tenant_id: str,
    body: ReadingListRequest,
    db: Session = Depends(get_db),
):
    tenant = _get_tenant_or_404(tenant_id, db)

    # Hash PII before any other operation — raw values are never used again
    email_hash = hash_email(body.email)
    patron_hash = hash_patron(body.name, body.email)

    resolved, failed = await _resolve_book_ids(tenant.id, body.books, db)

    existing = (
        db.query(ReadingListSubmission)
        .filter(
            ReadingListSubmission.tenant_id == tenant.id,
            ReadingListSubmission.email_hash == email_hash,
        )
        .first()
    )

    if existing:
        existing.patron_hash = patron_hash
        existing.books_requested = body.books
        existing.books_resolved = resolved
        existing.books_failed = failed
        db.commit()
        db.refresh(existing)
        submission_id = str(existing.id)
    else:
        submission = ReadingListSubmission(
            tenant_id=tenant.id,
            patron_hash=patron_hash,
            email_hash=email_hash,
            books_requested=body.books,
            books_resolved=resolved,
            books_failed=failed,
        )
        db.add(submission)
        db.commit()
        db.refresh(submission)
        submission_id = str(submission.id)

    return {
        "submission_id": submission_id,
        "books_resolved": resolved,
        "books_failed": failed,
    }
