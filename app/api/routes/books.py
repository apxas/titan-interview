import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Text, cast
from sqlalchemy.orm import Session

from app.db.models import Book, BookVersion, Tenant
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


def _serialize_book(book: Book) -> dict:
    return {
        "book_id": str(book.id),
        "tenant_id": str(book.tenant_id),
        "ol_work_id": book.ol_work_id,
        "title": book.title,
        "authors": book.authors,
        "first_publish_year": book.first_publish_year,
        "subjects": book.subjects,
        "cover_url": book.cover_url,
        "created_at": book.created_at.isoformat(),
        "updated_at": book.updated_at.isoformat(),
    }


@router.get("")
def list_books(
    tenant_id: str,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    tenant = _get_tenant_or_404(tenant_id, db)
    tid = tenant.id

    offset = (page - 1) * page_size
    total = db.query(Book).filter(Book.tenant_id == tid).count()
    books = (
        db.query(Book)
        .filter(Book.tenant_id == tid)
        .order_by(Book.created_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    return {
        "items": [_serialize_book(b) for b in books],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/search")
def search_books(
    tenant_id: str,
    q: str = "",
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    tenant = _get_tenant_or_404(tenant_id, db)
    tid = tenant.id

    base = db.query(Book).filter(Book.tenant_id == tid)

    if q:
        pattern = f"%{q}%"
        base = base.filter(
            Book.title.ilike(pattern)
            | cast(Book.authors, Text).ilike(pattern)
        )

    total = base.count()
    offset = (page - 1) * page_size
    books = base.order_by(Book.title).offset(offset).limit(page_size).all()

    return {
        "items": [_serialize_book(b) for b in books],
        "total": total,
        "page": page,
        "page_size": page_size,
        "q": q,
    }


@router.get("/filter")
def filter_books(
    tenant_id: str,
    author: str = "",
    subject: str = "",
    year_min: int = None,
    year_max: int = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    tenant = _get_tenant_or_404(tenant_id, db)
    tid = tenant.id

    base = db.query(Book).filter(Book.tenant_id == tid)

    if author:
        base = base.filter(cast(Book.authors, Text).ilike(f"%{author}%"))
    if subject:
        base = base.filter(cast(Book.subjects, Text).ilike(f"%{subject}%"))
    if year_min is not None:
        base = base.filter(Book.first_publish_year >= year_min)
    if year_max is not None:
        base = base.filter(Book.first_publish_year <= year_max)

    total = base.count()
    offset = (page - 1) * page_size
    books = base.order_by(Book.first_publish_year).offset(offset).limit(page_size).all()

    return {
        "items": [_serialize_book(b) for b in books],
        "total": total,
        "page": page,
        "page_size": page_size,
        "filters": {
            "author": author,
            "subject": subject,
            "year_min": year_min,
            "year_max": year_max,
        },
    }


@router.get("/{book_id}/versions")
def get_book_versions(tenant_id: str, book_id: str, db: Session = Depends(get_db)):
    tenant = _get_tenant_or_404(tenant_id, db)

    try:
        bid = uuid.UUID(book_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Book not found")

    book = (
        db.query(Book)
        .filter(Book.tenant_id == tenant.id, Book.id == bid)
        .first()
    )
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    versions = (
        db.query(BookVersion)
        .filter(BookVersion.tenant_id == tenant.id, BookVersion.book_id == bid)
        .order_by(BookVersion.version_number.desc())
        .all()
    )

    return {
        "book_id": str(book.id),
        "ol_work_id": book.ol_work_id,
        "total_versions": len(versions),
        "versions": [
            {
                "version_number": v.version_number,
                "title": v.title,
                "authors": v.authors,
                "first_publish_year": v.first_publish_year,
                "subjects": v.subjects,
                "cover_url": v.cover_url,
                "change_summary": v.change_summary,
                "created_at": v.created_at.isoformat(),
            }
            for v in versions
        ],
    }


@router.get("/{book_id}")
def get_book(tenant_id: str, book_id: str, db: Session = Depends(get_db)):
    tenant = _get_tenant_or_404(tenant_id, db)

    try:
        bid = uuid.UUID(book_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Book not found")

    book = (
        db.query(Book)
        .filter(Book.tenant_id == tenant.id, Book.id == bid)
        .first()
    )
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    result = _serialize_book(book)
    result["raw_data"] = book.raw_data
    return result
