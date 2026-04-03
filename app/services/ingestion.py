import asyncio
import uuid
from datetime import datetime, timezone

import httpx

from app.db.models import Book, BookVersion, IngestionLog
from app.db.session import SessionLocal
from app.services.open_library import resolve_work, search_works


VERSIONED_FIELDS = ("title", "authors", "first_publish_year", "subjects", "cover_url")


def _merge_with_regression_guard(existing: Book, resolved: dict) -> dict:
    """
    Returns a copy of `resolved` where any incoming missing/empty value is
    replaced by the existing book's value, preventing field regression.
    raw_data is intentionally NOT guarded — it always reflects the actual OL response.
    """
    merged = dict(resolved)
    for field in VERSIONED_FIELDS:
        incoming = resolved.get(field)
        current = getattr(existing, field)
        incoming_missing = incoming is None or incoming == [] or incoming == ""
        current_present = current is not None and current != [] and current != ""
        if incoming_missing and current_present:
            merged[field] = current
    return merged


def _compute_diff(existing: Book, merged: dict) -> dict:
    """
    Returns a dict of changed fields: {field: {"from": old_val, "to": new_val}}.
    Empty dict means no change.
    """
    diff = {}
    for field in VERSIONED_FIELDS:
        old_val = getattr(existing, field)
        new_val = merged.get(field)
        if old_val != new_val:
            diff[field] = {"from": old_val, "to": new_val}
    return diff


async def run_ingestion(
    tenant_id: str,
    query_type: str,
    query_value: str,
    log_id: str,
    limit: int = 50,
) -> None:
    """
    Full ingestion flow:
    1. Mark log as running
    2. Search Open Library
    3. Resolve each work (follow-up calls as needed)
    4. Upsert books into the DB
    5. Update log with final counts and status
    """
    db = SessionLocal()
    try:
        log = db.query(IngestionLog).filter(IngestionLog.id == log_id).first()
        if not log:
            return

        log.status = "running"
        db.commit()

        errors: list[str] = []
        success_count = 0
        fail_count = 0

        try:
            works = await search_works(query_type, query_value, limit=limit)
        except Exception as exc:
            log.status = "failed"
            log.errors = [f"search_works failed: {exc}"]
            log.finished_at = datetime.now(timezone.utc)
            db.commit()
            return

        log.fetched_count = len(works)
        db.commit()

        # Reuse a single httpx client across all work resolutions
        async with httpx.AsyncClient(timeout=15) as client:
            for i, work in enumerate(works):
                work_key = work.get("key")
                if not work_key:
                    fail_count += 1
                    errors.append(f"Work at index {i} missing key field")
                    continue

                try:
                    resolved = await resolve_work(work_key, work, client=client)

                    existing = (
                        db.query(Book)
                        .filter(
                            Book.tenant_id == uuid.UUID(tenant_id),
                            Book.ol_work_id == resolved["ol_work_id"],
                        )
                        .first()
                    )

                    if existing:
                        merged = _merge_with_regression_guard(existing, resolved)
                        diff = _compute_diff(existing, merged)
                        if diff:
                            new_version = existing.current_version + 1
                            existing.title = merged["title"]
                            existing.authors = merged["authors"]
                            existing.first_publish_year = merged["first_publish_year"]
                            existing.subjects = merged["subjects"]
                            existing.cover_url = merged["cover_url"]
                            existing.raw_data = merged["raw_data"]
                            existing.current_version = new_version
                            db.add(
                                BookVersion(
                                    book_id=existing.id,
                                    tenant_id=existing.tenant_id,
                                    version_number=new_version,
                                    title=merged["title"],
                                    authors=merged["authors"],
                                    first_publish_year=merged["first_publish_year"],
                                    subjects=merged["subjects"],
                                    cover_url=merged["cover_url"],
                                    raw_data=merged["raw_data"],
                                    change_summary=diff,
                                )
                            )
                        # else: no change — skip write (idempotent re-ingestion)
                    else:
                        book = Book(
                            tenant_id=uuid.UUID(tenant_id),
                            ol_work_id=resolved["ol_work_id"],
                            title=resolved["title"],
                            authors=resolved["authors"],
                            first_publish_year=resolved["first_publish_year"],
                            subjects=resolved["subjects"],
                            cover_url=resolved["cover_url"],
                            raw_data=resolved["raw_data"],
                            current_version=1,
                        )
                        db.add(book)
                        db.flush()
                        db.add(
                            BookVersion(
                                book_id=book.id,
                                tenant_id=book.tenant_id,
                                version_number=1,
                                title=resolved["title"],
                                authors=resolved["authors"],
                                first_publish_year=resolved["first_publish_year"],
                                subjects=resolved["subjects"],
                                cover_url=resolved["cover_url"],
                                raw_data=resolved["raw_data"],
                                change_summary={},
                            )
                        )

                    db.commit()
                    success_count += 1

                except Exception as exc:
                    fail_count += 1
                    errors.append(f"Failed to process {work_key}: {exc}")
                    db.rollback()

                # Rate-limit between work resolutions
                if i < len(works) - 1:
                    await asyncio.sleep(0.5)

        log.status = "complete"
        log.success_count = success_count
        log.fail_count = fail_count
        log.errors = errors
        log.finished_at = datetime.now(timezone.utc)
        db.commit()

    except Exception as exc:
        try:
            log.status = "failed"
            log.errors = [str(exc)]
            log.finished_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            pass
    finally:
        db.close()
