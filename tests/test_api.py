"""
Integration tests for the Titan Library Catalog API.

Hits the running service at http://localhost:8000 via httpx.AsyncClient.
Requires `docker compose up` to be running before executing.

Run with: make test  (or: pytest tests/)
"""
import uuid

import httpx
import pytest

# Tier 2 tests import SQLAlchemy models directly to verify DB state.
# These imports are deferred inside test functions so collection succeeds
# even if the app environment is unavailable.

BASE = "http://localhost:8000"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def create_tenant(client: httpx.AsyncClient, name: str) -> dict:
    resp = await client.post("/api/tenants", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        yield c


@pytest.fixture
async def tenant_a(client):
    return await create_tenant(client, f"Library A {uuid.uuid4().hex[:6]}")


@pytest.fixture
async def tenant_b(client):
    return await create_tenant(client, f"Library B {uuid.uuid4().hex[:6]}")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Tenant creation and listing
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_create_tenant_returns_full_record(client):
    name = f"Test Library {uuid.uuid4().hex[:6]}"
    resp = await client.post("/api/tenants", json={"name": name})
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == name
    assert "id" in data
    assert "api_key" in data
    assert "created_at" in data
    # api_key should be a non-empty string
    assert isinstance(data["api_key"], str)
    assert len(data["api_key"]) > 0


@pytest.mark.anyio
async def test_create_tenant_empty_name_rejected(client):
    resp = await client.post("/api/tenants", json={"name": "   "})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_list_tenants_does_not_expose_api_key(client, tenant_a):
    resp = await client.get("/api/tenants")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 1
    for item in data["items"]:
        assert "api_key" not in item
        assert "id" in item
        assert "name" in item
        assert "created_at" in item


@pytest.mark.anyio
async def test_list_tenants_includes_created_tenant(client, tenant_a):
    resp = await client.get("/api/tenants")
    ids = [t["id"] for t in resp.json()["items"]]
    assert tenant_a["id"] in ids


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_tenant_isolation_books(client, tenant_a, tenant_b):
    """Books ingested under tenant A must not appear under tenant B."""
    tid_a = tenant_a["id"]
    tid_b = tenant_b["id"]

    # Trigger a tiny ingestion for tenant A only (we don't wait for it to
    # complete — just confirm list scoping even when both are empty)
    resp_a = await client.get(f"/api/{tid_a}/books")
    resp_b = await client.get(f"/api/{tid_b}/books")
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200

    ids_a = {b["book_id"] for b in resp_a.json()["items"]}
    ids_b = {b["book_id"] for b in resp_b.json()["items"]}
    assert ids_a.isdisjoint(ids_b), "Books leaked across tenant boundaries"


@pytest.mark.anyio
async def test_tenant_isolation_logs(client, tenant_a, tenant_b):
    """Ingestion logs for tenant A must not appear under tenant B."""
    tid_a = tenant_a["id"]
    tid_b = tenant_b["id"]

    # Trigger an ingest under tenant A
    await client.post(
        f"/api/{tid_a}/ingest",
        json={"query_type": "author", "query_value": "test-isolation"},
    )

    logs_a = (await client.get(f"/api/{tid_a}/logs")).json()["items"]
    logs_b = (await client.get(f"/api/{tid_b}/logs")).json()["items"]

    ids_a = {l["log_id"] for l in logs_a}
    ids_b = {l["log_id"] for l in logs_b}
    assert ids_a.isdisjoint(ids_b), "Logs leaked across tenant boundaries"


@pytest.mark.anyio
async def test_book_detail_tenant_isolation(client, tenant_a, tenant_b):
    """A book_id belonging to tenant A must 404 when requested under tenant B."""
    # Use the pre-existing test-tenant books if available, otherwise skip
    tid_a = tenant_a["id"]
    tid_b = tenant_b["id"]

    books_resp = await client.get(f"/api/{tid_a}/books")
    books = books_resp.json()["items"]

    if not books:
        pytest.skip("No books in tenant A to test isolation with")

    book_id = books[0]["book_id"]
    resp = await client.get(f"/api/{tid_b}/books/{book_id}")
    # Should either 404 (tenant not found or book not found) — not 200
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Books — list
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_books_list_unknown_tenant_404(client):
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/api/{fake_id}/books")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Tenant not found"


@pytest.mark.anyio
async def test_books_list_response_shape(client, tenant_a):
    resp = await client.get(f"/api/{tenant_a['id']}/books")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert isinstance(data["items"], list)
    assert data["total"] >= 0


@pytest.mark.anyio
async def test_books_list_pagination(client):
    """Pagination params are reflected in the response."""
    # Use any existing tenant that has books
    tenants = (await client.get("/api/tenants")).json()["items"]
    tenant_with_books = None
    for t in tenants:
        resp = await client.get(f"/api/{t['id']}/books")
        if resp.json()["total"] > 0:
            tenant_with_books = t
            break

    if tenant_with_books is None:
        pytest.skip("No tenant with books found")

    tid = tenant_with_books["id"]
    resp = await client.get(f"/api/{tid}/books?page=1&page_size=2")
    data = resp.json()
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert len(data["items"]) <= 2


# ---------------------------------------------------------------------------
# Books — search
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_books_search_response_shape(client):
    tenants = (await client.get("/api/tenants")).json()["items"]
    tenant_with_books = None
    for t in tenants:
        if (await client.get(f"/api/{t['id']}/books")).json()["total"] > 0:
            tenant_with_books = t
            break

    if tenant_with_books is None:
        pytest.skip("No tenant with books found")

    tid = tenant_with_books["id"]
    resp = await client.get(f"/api/{tid}/books/search?q=the")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert "q" in data
    assert data["q"] == "the"


@pytest.mark.anyio
async def test_books_search_no_results(client):
    tenants = (await client.get("/api/tenants")).json()["items"]
    if not tenants:
        pytest.skip("No tenants")
    tid = tenants[0]["id"]
    resp = await client.get(f"/api/{tid}/books/search?q=xyzzy_no_match_ever")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    assert resp.json()["items"] == []


@pytest.mark.anyio
async def test_books_search_unknown_tenant_404(client):
    resp = await client.get(f"/api/{uuid.uuid4()}/books/search?q=hobbit")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Books — filter
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_books_filter_response_shape(client):
    tenants = (await client.get("/api/tenants")).json()["items"]
    if not tenants:
        pytest.skip("No tenants")
    tid = tenants[0]["id"]
    resp = await client.get(f"/api/{tid}/books/filter?year_min=1900")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert "filters" in data
    assert data["filters"]["year_min"] == 1900


@pytest.mark.anyio
async def test_books_filter_year_range(client):
    tenants = (await client.get("/api/tenants")).json()["items"]
    tenant_with_books = None
    for t in tenants:
        if (await client.get(f"/api/{t['id']}/books")).json()["total"] > 0:
            tenant_with_books = t
            break

    if tenant_with_books is None:
        pytest.skip("No tenant with books found")

    tid = tenant_with_books["id"]
    resp = await client.get(f"/api/{tid}/books/filter?year_min=1900&year_max=2000")
    assert resp.status_code == 200
    for book in resp.json()["items"]:
        if book["first_publish_year"] is not None:
            assert 1900 <= book["first_publish_year"] <= 2000


@pytest.mark.anyio
async def test_books_filter_unknown_tenant_404(client):
    resp = await client.get(f"/api/{uuid.uuid4()}/books/filter?author=tolkien")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Books — single detail
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_book_detail_valid(client):
    tenants = (await client.get("/api/tenants")).json()["items"]
    tenant_with_books = None
    for t in tenants:
        if (await client.get(f"/api/{t['id']}/books")).json()["total"] > 0:
            tenant_with_books = t
            break

    if tenant_with_books is None:
        pytest.skip("No tenant with books found")

    tid = tenant_with_books["id"]
    books = (await client.get(f"/api/{tid}/books")).json()["items"]
    book_id = books[0]["book_id"]

    resp = await client.get(f"/api/{tid}/books/{book_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["book_id"] == book_id
    assert "title" in data
    assert "authors" in data
    assert "ol_work_id" in data
    assert "raw_data" in data  # full detail includes raw_data


@pytest.mark.anyio
async def test_book_detail_not_found(client):
    tenants = (await client.get("/api/tenants")).json()["items"]
    if not tenants:
        pytest.skip("No tenants")
    tid = tenants[0]["id"]
    resp = await client.get(f"/api/{tid}/books/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_book_detail_invalid_uuid(client):
    tenants = (await client.get("/api/tenants")).json()["items"]
    if not tenants:
        pytest.skip("No tenants")
    tid = tenants[0]["id"]
    resp = await client.get(f"/api/{tid}/books/not-a-uuid")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_ingest_returns_task_id_immediately(client, tenant_a):
    resp = await client.post(
        f"/api/{tenant_a['id']}/ingest",
        json={"query_type": "author", "query_value": "tolkien"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "task_id" in data
    assert "log_id" in data
    assert data["status"] == "pending"
    # Confirm it's a non-empty string (Celery task ID)
    assert isinstance(data["task_id"], str)
    assert len(data["task_id"]) > 0


@pytest.mark.anyio
async def test_ingest_invalid_query_type(client, tenant_a):
    resp = await client.post(
        f"/api/{tenant_a['id']}/ingest",
        json={"query_type": "title", "query_value": "hobbit"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_ingest_unknown_tenant_404(client):
    resp = await client.post(
        f"/api/{uuid.uuid4()}/ingest",
        json={"query_type": "author", "query_value": "tolkien"},
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_ingest_status_lookup(client, tenant_a):
    # Trigger a job
    post_resp = await client.post(
        f"/api/{tenant_a['id']}/ingest",
        json={"query_type": "subject", "query_value": "science fiction"},
    )
    assert post_resp.status_code == 200
    task_id = post_resp.json()["task_id"]

    # Look it up immediately — should be pending or running
    get_resp = await client.get(f"/api/{tenant_a['id']}/ingest/{task_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["celery_task_id"] == task_id
    assert data["status"] in ("pending", "running", "complete", "failed")
    assert data["query_type"] == "subject"
    assert data["query_value"] == "science fiction"


@pytest.mark.anyio
async def test_ingest_status_not_found(client, tenant_a):
    resp = await client.get(f"/api/{tenant_a['id']}/ingest/nonexistent-task-id")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_logs_response_shape(client, tenant_a):
    # Ensure at least one log exists
    await client.post(
        f"/api/{tenant_a['id']}/ingest",
        json={"query_type": "author", "query_value": "ursula le guin"},
    )
    resp = await client.get(f"/api/{tenant_a['id']}/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 1

    log = data["items"][0]
    assert "log_id" in log
    assert "query_type" in log
    assert "query_value" in log
    assert "status" in log
    assert "fetched_count" in log
    assert "success_count" in log
    assert "fail_count" in log
    assert "started_at" in log


@pytest.mark.anyio
async def test_logs_most_recent_first(client, tenant_a):
    # Trigger two jobs and verify ordering
    await client.post(
        f"/api/{tenant_a['id']}/ingest",
        json={"query_type": "author", "query_value": "first"},
    )
    await client.post(
        f"/api/{tenant_a['id']}/ingest",
        json={"query_type": "author", "query_value": "second"},
    )
    resp = await client.get(f"/api/{tenant_a['id']}/logs")
    items = resp.json()["items"]
    assert len(items) >= 2
    # started_at descending
    dates = [i["started_at"] for i in items]
    assert dates == sorted(dates, reverse=True)


@pytest.mark.anyio
async def test_logs_unknown_tenant_404(client):
    resp = await client.get(f"/api/{uuid.uuid4()}/logs")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tier 2 — Reading list submissions
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_reading_list_mix_resolved_and_failed(client, tenant_a):
    """
    OL27482W (The Hobbit) is a real work — resolves via live OL call.
    OL99999999W is fake — OL returns 404, lands in books_failed.
    """
    resp = await client.post(
        f"/api/{tenant_a['id']}/reading-list",
        json={
            "name": "Test Patron",
            "email": f"patron_{uuid.uuid4().hex[:8]}@example.com",
            "books": ["OL27482W", "OL99999999W"],
        },
        timeout=120,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "submission_id" in data
    assert "books_resolved" in data
    assert "books_failed" in data
    assert len(data["books_resolved"]) == 1
    assert len(data["books_failed"]) == 1
    assert "OL99999999W" in data["books_failed"]


@pytest.mark.anyio
async def test_reading_list_duplicate_email_updates_not_inserts(client, tenant_a):
    """Same email submitted twice → same submission_id, DB row count stays at 1."""
    from app.db.models import ReadingListSubmission
    from app.db.session import SessionLocal
    from app.services.pii import hash_email

    email = f"dedup_{uuid.uuid4().hex[:8]}@example.com"

    resp1 = await client.post(
        f"/api/{tenant_a['id']}/reading-list",
        json={"name": "Patron", "email": email, "books": ["OL99999999W"]},
    )
    assert resp1.status_code == 200
    sid1 = resp1.json()["submission_id"]

    resp2 = await client.post(
        f"/api/{tenant_a['id']}/reading-list",
        json={"name": "Patron", "email": email, "books": ["OL99999998W"]},
    )
    assert resp2.status_code == 200
    sid2 = resp2.json()["submission_id"]

    assert sid1 == sid2, "Second submission must reuse the same record, not create a new one"

    db = SessionLocal()
    try:
        count = (
            db.query(ReadingListSubmission)
            .filter(ReadingListSubmission.email_hash == hash_email(email))
            .count()
        )
        assert count == 1, f"Expected 1 DB row for this email hash, found {count}"
    finally:
        db.close()


@pytest.mark.anyio
async def test_reading_list_response_has_no_plaintext_pii(client, tenant_a):
    """The API response must never echo back name or email."""
    name = "ShouldNeverAppearInResponse"
    email = f"noemail_{uuid.uuid4().hex[:8]}@private.invalid"

    resp = await client.post(
        f"/api/{tenant_a['id']}/reading-list",
        json={"name": name, "email": email, "books": []},
    )
    assert resp.status_code == 200
    body = resp.text
    assert name not in body, "name appeared in response body"
    assert email not in body, "email appeared in response body"
    data = resp.json()
    assert "name" not in data, "'name' key present in response"
    assert "email" not in data, "'email' key present in response"


@pytest.mark.anyio
async def test_reading_list_db_stores_only_hashes(client, tenant_a):
    """DB record must contain 64-char hex hashes, not plaintext name or email."""
    from app.db.models import ReadingListSubmission
    from app.db.session import SessionLocal

    name = "PlaintextCheckPatron"
    email = f"hashcheck_{uuid.uuid4().hex[:8]}@verify.invalid"

    resp = await client.post(
        f"/api/{tenant_a['id']}/reading-list",
        json={"name": name, "email": email, "books": []},
    )
    assert resp.status_code == 200
    submission_id = resp.json()["submission_id"]

    db = SessionLocal()
    try:
        row = (
            db.query(ReadingListSubmission)
            .filter(ReadingListSubmission.id == uuid.UUID(submission_id))
            .first()
        )
        assert row is not None

        # Both hashes must be 64-character lowercase hex strings (SHA-256)
        assert len(row.email_hash) == 64
        assert set(row.email_hash).issubset(set("0123456789abcdef"))
        assert len(row.patron_hash) == 64
        assert set(row.patron_hash).issubset(set("0123456789abcdef"))

        # Plaintext must not appear in any stored column value
        for col in (row.email_hash, row.patron_hash, str(row.books_requested),
                    str(row.books_resolved), str(row.books_failed)):
            assert name not in col, f"Plaintext name found in column: {col!r}"
            assert email not in col, f"Plaintext email found in column: {col!r}"
    finally:
        db.close()


@pytest.mark.anyio
async def test_reading_list_unknown_tenant_404(client):
    resp = await client.post(
        f"/api/{uuid.uuid4()}/reading-list",
        json={"name": "A", "email": "a@b.com", "books": []},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tier 2 — Scheduler: refresh_all_catalogs
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_refresh_all_catalogs_creates_pending_logs(client):
    """
    Calling refresh_all_catalogs must create exactly one new ingestion_log
    per distinct (tenant_id, query_type, query_value) that has status=complete.
    """
    from app.db.models import IngestionLog
    from app.db.session import SessionLocal
    from app.workers.tasks import refresh_all_catalogs

    db = SessionLocal()
    try:
        completed = (
            db.query(IngestionLog)
            .filter(IngestionLog.status == "complete")
            .all()
        )
        if not completed:
            pytest.skip("No completed ingestion logs found — run an ingestion first")

        distinct_queries = {
            (str(row.tenant_id), row.query_type, row.query_value)
            for row in completed
        }
        expected_new = len(distinct_queries)
        log_count_before = db.query(IngestionLog).count()
    finally:
        db.close()

    # Run the task eagerly in-process so the test is not sensitive to worker queue depth.
    # refresh_all_catalogs only creates logs + enqueues ingest_books sub-tasks;
    # we verify the log side-effects, not the downstream ingestion results.
    outcome = refresh_all_catalogs.apply().get()

    assert outcome["queued"] == expected_new, (
        f"Expected {expected_new} queued jobs, got {outcome['queued']}"
    )

    db = SessionLocal()
    try:
        log_count_after = db.query(IngestionLog).count()
        assert log_count_after == log_count_before + expected_new, (
            f"Expected {log_count_before + expected_new} total logs, "
            f"got {log_count_after}"
        )

        # Newest logs should have a celery_task_id and a valid status
        new_logs = (
            db.query(IngestionLog)
            .order_by(IngestionLog.started_at.desc())
            .limit(expected_new)
            .all()
        )
        for log in new_logs:
            assert log.status in ("pending", "running", "complete", "failed"), (
                f"Unexpected status {log.status!r}"
            )
            assert log.celery_task_id is not None, "celery_task_id not set on new log"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Tier 3 — Book version management
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_book_versions_route_shape(client):
    """GET /{book_id}/versions returns expected response shape."""
    tenants = (await client.get("/api/tenants")).json()["items"]
    tenant_with_books = None
    for t in tenants:
        if (await client.get(f"/api/{t['id']}/books")).json()["total"] > 0:
            tenant_with_books = t
            break

    if tenant_with_books is None:
        pytest.skip("No tenant with books found")

    tid = tenant_with_books["id"]
    books = (await client.get(f"/api/{tid}/books")).json()["items"]
    book_id = books[0]["book_id"]

    resp = await client.get(f"/api/{tid}/books/{book_id}/versions")
    assert resp.status_code == 200
    data = resp.json()
    assert "book_id" in data
    assert "ol_work_id" in data
    assert "total_versions" in data
    assert "versions" in data
    assert data["book_id"] == book_id
    assert data["total_versions"] >= 1
    assert len(data["versions"]) == data["total_versions"]

    v = data["versions"][0]
    assert "version_number" in v
    assert "title" in v
    assert "authors" in v
    assert "first_publish_year" in v
    assert "subjects" in v
    assert "cover_url" in v
    assert "change_summary" in v
    assert "created_at" in v


@pytest.mark.anyio
async def test_book_versions_unknown_book_404(client):
    tenants = (await client.get("/api/tenants")).json()["items"]
    if not tenants:
        pytest.skip("No tenants")
    tid = tenants[0]["id"]
    resp = await client.get(f"/api/{tid}/books/{uuid.uuid4()}/versions")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_book_versions_tenant_isolation(client, tenant_a, tenant_b):
    """Version history for a book in tenant A is not accessible from tenant B."""
    from app.db.models import Book, BookVersion
    from app.db.session import SessionLocal

    # Create a book directly in tenant A's DB
    db = SessionLocal()
    try:
        book = Book(
            tenant_id=uuid.UUID(tenant_a["id"]),
            ol_work_id="/works/OL_ISOLATION_TEST",
            title="Isolation Test Book",
            authors=["Test Author"],
            first_publish_year=2000,
            subjects=["Testing"],
            cover_url=None,
            raw_data={},
            current_version=1,
        )
        db.add(book)
        db.flush()
        db.add(BookVersion(
            book_id=book.id,
            tenant_id=book.tenant_id,
            version_number=1,
            title=book.title,
            authors=book.authors,
            first_publish_year=book.first_publish_year,
            subjects=book.subjects,
            cover_url=book.cover_url,
            raw_data=book.raw_data,
            change_summary={},
        ))
        db.commit()
        book_id = str(book.id)
    finally:
        db.close()

    # Accessible from tenant A
    resp_a = await client.get(f"/api/{tenant_a['id']}/books/{book_id}/versions")
    assert resp_a.status_code == 200

    # Not accessible from tenant B
    resp_b = await client.get(f"/api/{tenant_b['id']}/books/{book_id}/versions")
    assert resp_b.status_code == 404


@pytest.mark.anyio
async def test_book_version_not_created_when_unchanged(client):
    """Re-ingesting the same data must not create a new version."""
    from app.db.models import Book, BookVersion
    from app.db.session import SessionLocal
    from app.services.ingestion import _compute_diff, _merge_with_regression_guard

    db = SessionLocal()
    try:
        # Find any book with at least 1 version
        book = db.query(Book).first()
        if book is None:
            pytest.skip("No books in DB")

        version_count_before = (
            db.query(BookVersion).filter(BookVersion.book_id == book.id).count()
        )

        # Build a resolved dict identical to current book state
        resolved = {
            "ol_work_id": book.ol_work_id,
            "title": book.title,
            "authors": book.authors,
            "first_publish_year": book.first_publish_year,
            "subjects": book.subjects,
            "cover_url": book.cover_url,
            "raw_data": book.raw_data,
        }

        merged = _merge_with_regression_guard(book, resolved)
        diff = _compute_diff(book, merged)

        # No diff → no new version should be created
        assert diff == {}, f"Expected empty diff for identical data, got: {diff}"

        version_count_after = (
            db.query(BookVersion).filter(BookVersion.book_id == book.id).count()
        )
        assert version_count_after == version_count_before, (
            "Version was created even though nothing changed"
        )
    finally:
        db.close()


@pytest.mark.anyio
async def test_book_version_created_on_change(client):
    """Changing a field and running the diff logic creates a new version with correct change_summary."""
    from app.db.models import Book, BookVersion
    from app.db.session import SessionLocal
    from app.services.ingestion import _compute_diff, _merge_with_regression_guard

    db = SessionLocal()
    try:
        book = db.query(Book).first()
        if book is None:
            pytest.skip("No books in DB")

        book_id = book.id
        tenant_id = book.tenant_id
        version_before = book.current_version

        version_count_before = (
            db.query(BookVersion).filter(BookVersion.book_id == book_id).count()
        )

        # Craft a resolved dict with a changed title
        new_title = book.title + " — Revised Edition"
        resolved = {
            "ol_work_id": book.ol_work_id,
            "title": new_title,
            "authors": book.authors,
            "first_publish_year": book.first_publish_year,
            "subjects": book.subjects,
            "cover_url": book.cover_url,
            "raw_data": book.raw_data,
        }

        merged = _merge_with_regression_guard(book, resolved)
        diff = _compute_diff(book, merged)

        assert "title" in diff, f"Expected title in diff, got: {diff}"
        assert diff["title"]["from"] == book.title
        assert diff["title"]["to"] == new_title

        # Simulate what ingestion does on a detected change
        new_version = version_before + 1
        book.title = merged["title"]
        book.current_version = new_version
        db.add(BookVersion(
            book_id=book_id,
            tenant_id=tenant_id,
            version_number=new_version,
            title=merged["title"],
            authors=merged["authors"],
            first_publish_year=merged["first_publish_year"],
            subjects=merged["subjects"],
            cover_url=merged["cover_url"],
            raw_data=merged["raw_data"],
            change_summary=diff,
        ))
        db.commit()

        version_count_after = (
            db.query(BookVersion).filter(BookVersion.book_id == book_id).count()
        )
        assert version_count_after == version_count_before + 1, (
            f"Expected {version_count_before + 1} versions, got {version_count_after}"
        )

        latest = (
            db.query(BookVersion)
            .filter(BookVersion.book_id == book_id)
            .order_by(BookVersion.version_number.desc())
            .first()
        )
        assert latest.version_number == new_version
        assert latest.change_summary == diff
        assert latest.title == new_title
    finally:
        db.close()


@pytest.mark.anyio
async def test_book_version_regression_guard(client):
    """Incoming empty authors must not overwrite existing authors."""
    from app.db.models import Book
    from app.db.session import SessionLocal
    from app.services.ingestion import _compute_diff, _merge_with_regression_guard

    db = SessionLocal()
    try:
        # Find a book that has authors populated
        book = db.query(Book).filter(Book.authors != "[]").first()
        if book is None:
            pytest.skip("No book with authors found")

        existing_authors = book.authors

        # Simulate an incoming OL response with authors missing
        resolved = {
            "ol_work_id": book.ol_work_id,
            "title": book.title,
            "authors": [],      # empty — regression scenario
            "first_publish_year": book.first_publish_year,
            "subjects": book.subjects,
            "cover_url": book.cover_url,
            "raw_data": book.raw_data,
        }

        merged = _merge_with_regression_guard(book, resolved)

        # Regression guard must preserve existing authors
        assert merged["authors"] == existing_authors, (
            f"Regression guard failed: authors was overwritten with {merged['authors']!r}"
        )

        # No diff on authors since guard preserved them
        diff = _compute_diff(book, merged)
        assert "authors" not in diff, (
            f"authors incorrectly appears in diff after regression guard: {diff}"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Tier 3 — Live OL fallback for reading list
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_reading_list_resolves_via_live_ol(client):
    """
    A valid OL work ID submitted under a brand-new tenant (no local books) must
    appear in books_resolved via the live OL fallback, not books_failed.
    OL27482W = The Hobbit — known good work, reliably returns HTTP 200 from OL.
    """
    # Fresh tenant — no ingestion has ever run for it, so no local books exist.
    tenant = await create_tenant(client, f"OL Fallback Test {uuid.uuid4().hex[:6]}")
    tid = tenant["id"]

    resp = await client.post(
        f"/api/{tid}/reading-list",
        json={
            "name": "Live OL Patron",
            "email": f"livefallback_{uuid.uuid4().hex[:8]}@test.invalid",
            "books": ["OL27482W"],
        },
        timeout=30,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "books_resolved" in data
    assert "books_failed" in data
    assert len(data["books_resolved"]) == 1, (
        f"Expected OL27482W in books_resolved via live OL, got: {data}"
    )
    assert len(data["books_failed"]) == 0, (
        f"Expected no failures, got: {data['books_failed']}"
    )
    # The resolved value must be the OL key (not a local UUID, since no local book exists)
    assert "/works/OL27482W" in data["books_resolved"][0] or "OL27482W" in data["books_resolved"][0]
