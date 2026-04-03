# Open Library Catalog Service

A multi-tenant library catalog API that ingests book metadata from the Open Library API, maintains a versioned local catalog per tenant, and supports patron reading list submissions with privacy-preserving PII handling. It is designed for library systems that need to manage and refresh their own curated catalogs independently — each tenant's data is fully isolated, ingestion runs asynchronously in the background, and the catalog stays fresh automatically via a scheduled 24-hour refresh cycle.

---

## Architecture Overview

**Stack:** FastAPI (API layer), PostgreSQL with SQLAlchemy ORM (persistence), Redis (Celery broker and result backend), Celery with Beat (async task queue and scheduler), httpx (async HTTP client for Open Library calls), Alembic (schema migrations). FastAPI was chosen for its native async support, automatic OpenAPI generation, and Pydantic-based request validation. PostgreSQL was chosen over a document store because the data model is relational — books belong to tenants, versions belong to books, logs belong to tenants — and JSONB columns handle the variable-shape Open Library payloads without sacrificing query capability. Redis is the natural pairing with Celery and adds no operational overhead beyond what the task queue already requires.

**Multi-tenancy** is enforced at the database level: every table (`books`, `book_versions`, `ingestion_logs`, `reading_list_submissions`) carries a `tenant_id UUID` column with a foreign key to `tenants`. All queries in every route handler filter on `tenant_id` derived from the URL path segment (`/api/{tenant_id}/...`). There is no shared global state — a book, log, or submission that belongs to tenant A is structurally unreachable from tenant B's API scope.

**Ingestion flow:** A `POST /api/{tenant_id}/ingest` request validates the query type (`author` or `subject`), creates an `IngestionLog` row with `status=pending`, and immediately returns a `task_id` and `log_id` to the caller. The actual work is delegated to a Celery task (`ingest_books`) running on the worker container. The task calls `search_works` against the Open Library `/search.json` endpoint, then iterates each result calling `resolve_work` to assemble a normalized book record. Each book is upserted into `books` and a corresponding `BookVersion` record is written. The log is updated to `running` at task start and `complete` or `failed` at finish, with per-book error details captured in the `errors` JSONB column.

**Open Library multi-endpoint resolution** is necessary because the `/search.json` endpoint returns partial data: subjects are frequently absent, author names sometimes missing, and cover IDs inconsistently populated. `resolve_work` starts with the search result and makes follow-up calls only when fields are missing — a `GET /works/{id}.json` call when subjects are absent, and `GET /authors/{id}.json` calls (one per author key) when author names are absent. A 0.5-second sleep is observed between each follow-up call to respect Open Library's rate limits. This means a work with complete search data costs one HTTP call, while a work with missing subjects and unknown authors may cost several.

**PII handling:** When a patron submits a reading list, the name and email fields are hashed before any database write or further use. `hash_email` computes SHA-256 of the stripped, lowercased email. `hash_patron` computes SHA-256 of `SECRET_KEY:name:email`, salting the hash with the application secret to prevent rainbow table attacks. Neither the name nor the email appears in any database column, log entry, or API response after the hash step. Deduplication is enforced by `email_hash`: if a submission arrives for an email already in the tenant's `reading_list_submissions` table, the existing row is updated in place rather than a new row being inserted, keeping one submission record per patron per tenant.

**Catalog freshness:** The Celery Beat scheduler runs `refresh_all_catalogs` every 24 hours. The task queries `ingestion_logs` for every distinct `(tenant_id, query_type, query_value)` combination where `status=complete` — representing every ingestion query that has ever successfully run — creates a new `IngestionLog` row for each, and enqueues a fresh `ingest_books` task. This means every tenant's catalog is re-fetched from Open Library on a 24-hour cycle without any manual intervention or per-tenant configuration.

**Version management:** Every book upsert path writes a `BookVersion` record. On first insert the initial version (`version_number=1`, `change_summary={}`) is created alongside the book. On re-ingestion, `_merge_with_regression_guard` is applied first: any incoming field that is `None`, `[]`, or `""` is replaced with the existing book's current value, preventing Open Library data gaps from overwriting known-good catalog data. Then `_compute_diff` compares the merged resolved data against the current book state field by field across `title`, `authors`, `first_publish_year`, `subjects`, and `cover_url`. If the diff is non-empty, `current_version` is incremented, the book row is updated, and a new `BookVersion` is written with `change_summary` containing the per-field `{"from": old, "to": new}` record. If the diff is empty, no write occurs — re-ingesting identical data is fully idempotent.

---

## Setup and Running

**Prerequisites:** Docker Desktop (with file sharing enabled for this repository's path).

```bash
# 1. Clone the repository
git clone <repo-url>
cd titan-interview

# 2. Configure environment
cp .env.example .env
# .env ships with working defaults for local development:
#   DATABASE_URL=postgresql://postgres:postgres@db:5432/titan
#   REDIS_URL=redis://redis:6379/0
#   SECRET_KEY=changeme   ← change before any real deployment

# 3. Start all services (builds images, runs migrations, starts API + worker + beat)
docker compose up

# 4. Seed the database
make seed
# Creates a "Demo Library" tenant (idempotent) and triggers a Tolkien author ingestion.
# Prints the tenant ID and API key to use in subsequent requests.

# API is available at:  http://localhost:8000
# OpenAPI docs at:      http://localhost:8000/docs
```

---

## API Reference

Replace `{tenant_id}` with the UUID printed by `make seed` or returned by `POST /api/tenants`.

### Tenants

**Create tenant**
```bash
curl -s -X POST http://localhost:8000/api/tenants \
  -H "Content-Type: application/json" \
  -d '{"name": "Westside Public Library"}'
```
```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "name": "Westside Public Library",
  "api_key": "6c59b588b15eef5daed6228d810af32c6205ec6bc90f391057f2ae8be3808528",
  "created_at": "2026-04-02T18:00:00.000000"
}
```

**List tenants** (api_key omitted)
```bash
curl -s http://localhost:8000/api/tenants
```
```json
{
  "items": [
    {"id": "a1b2c3d4-...", "name": "Westside Public Library", "created_at": "2026-04-02T18:00:00.000000"}
  ],
  "total": 1
}
```

---

### Ingestion

**Trigger ingestion** (`query_type` must be `author` or `subject`)
```bash
curl -s -X POST http://localhost:8000/api/{tenant_id}/ingest \
  -H "Content-Type: application/json" \
  -d '{"query_type": "author", "query_value": "ursula le guin"}'
```
```json
{
  "log_id": "d1e2f3a4-b5c6-7890-abcd-ef1234567890",
  "task_id": "da3d0abd-bdd9-4912-8eae-d8adb8d7b7f4",
  "status": "pending"
}
```

**Get ingestion status**
```bash
curl -s http://localhost:8000/api/{tenant_id}/ingest/{task_id}
```
```json
{
  "log_id": "d1e2f3a4-...",
  "tenant_id": "a1b2c3d4-...",
  "query_type": "author",
  "query_value": "ursula le guin",
  "status": "complete",
  "celery_task_id": "da3d0abd-...",
  "fetched_count": 50,
  "success_count": 50,
  "fail_count": 0,
  "errors": [],
  "started_at": "2026-04-02T18:01:00.000000",
  "finished_at": "2026-04-02T18:02:23.000000"
}
```

---

### Books

**List books** (paginated, newest first)
```bash
curl -s "http://localhost:8000/api/{tenant_id}/books?page=1&page_size=3"
```
```json
{
  "items": [
    {
      "book_id": "d4bd157d-a99c-41e0-9759-6427c2728b6b",
      "tenant_id": "a1b2c3d4-...",
      "ol_work_id": "/works/OL27482W",
      "title": "The Hobbit",
      "authors": ["J.R.R. Tolkien"],
      "first_publish_year": 1937,
      "subjects": ["Fantasy", "Hobbits", "Wizards"],
      "cover_url": "https://covers.openlibrary.org/b/id/14627509-M.jpg",
      "created_at": "2026-04-02T18:00:35.000000",
      "updated_at": "2026-04-02T18:00:35.000000"
    }
  ],
  "total": 5,
  "page": 1,
  "page_size": 3
}
```

**Search books** (title and authors full-text, case-insensitive)
```bash
curl -s "http://localhost:8000/api/{tenant_id}/books/search?q=hobbit&page=1&page_size=5"
```
```json
{
  "items": [...],
  "total": 1,
  "page": 1,
  "page_size": 5,
  "q": "hobbit"
}
```

**Filter books** (all params optional, combinable)
```bash
curl -s "http://localhost:8000/api/{tenant_id}/books/filter?author=tolkien&year_min=1937&year_max=1955"
```
```json
{
  "items": [...],
  "total": 3,
  "page": 1,
  "page_size": 20,
  "filters": {
    "author": "tolkien",
    "subject": "",
    "year_min": 1937,
    "year_max": 1955
  }
}
```

**Book detail** (includes `raw_data` — full OL search doc)
```bash
curl -s http://localhost:8000/api/{tenant_id}/books/{book_id}
```
```json
{
  "book_id": "d4bd157d-...",
  "tenant_id": "a1b2c3d4-...",
  "ol_work_id": "/works/OL27482W",
  "title": "The Hobbit",
  "authors": ["J.R.R. Tolkien"],
  "first_publish_year": 1937,
  "subjects": ["Fantasy", "Hobbits", "Wizards"],
  "cover_url": "https://covers.openlibrary.org/b/id/14627509-M.jpg",
  "created_at": "2026-04-02T18:00:35.000000",
  "updated_at": "2026-04-02T18:00:35.000000",
  "raw_data": {"key": "/works/OL27482W", "title": "The Hobbit", "...": "..."}
}
```

**Book version history** (newest version first)
```bash
curl -s http://localhost:8000/api/{tenant_id}/books/{book_id}/versions
```
```json
{
  "book_id": "d4bd157d-...",
  "ol_work_id": "/works/OL27482W",
  "total_versions": 2,
  "versions": [
    {
      "version_number": 2,
      "title": "The Hobbit",
      "authors": ["J.R.R. Tolkien"],
      "first_publish_year": 1937,
      "subjects": ["Fantasy", "Hobbits", "Wizards"],
      "cover_url": "https://covers.openlibrary.org/b/id/14627509-M.jpg",
      "change_summary": {
        "subjects": {
          "from": ["Fantasy"],
          "to": ["Fantasy", "Hobbits", "Wizards"]
        }
      },
      "created_at": "2026-04-03T00:30:35.000000"
    },
    {
      "version_number": 1,
      "title": "The Hobbit",
      "authors": ["J.R.R. Tolkien"],
      "first_publish_year": 1937,
      "subjects": ["Fantasy"],
      "cover_url": "https://covers.openlibrary.org/b/id/14627509-M.jpg",
      "change_summary": {},
      "created_at": "2026-04-02T18:00:35.000000"
    }
  ]
}
```

---

### Ingestion Logs

**List logs** (most recent first)
```bash
curl -s http://localhost:8000/api/{tenant_id}/logs
```
```json
{
  "items": [
    {
      "log_id": "d1e2f3a4-...",
      "tenant_id": "a1b2c3d4-...",
      "query_type": "author",
      "query_value": "tolkien",
      "status": "complete",
      "celery_task_id": "da3d0abd-...",
      "fetched_count": 50,
      "success_count": 50,
      "fail_count": 0,
      "errors": [],
      "started_at": "2026-04-02T18:01:00.000000",
      "finished_at": "2026-04-02T18:02:23.000000"
    }
  ],
  "total": 1
}
```

---

### Reading Lists

Books are resolved against the local catalog first; any work ID not found locally is looked up live against `openlibrary.org/works/{id}.json`. Work IDs that return a non-200 response land in `books_failed`. Submitting the same email a second time updates the existing record rather than creating a new one. Name and email are never stored.

```bash
curl -s -X POST http://localhost:8000/api/{tenant_id}/reading-list \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Jane Smith",
    "email": "jane@example.com",
    "books": ["OL27482W", "OL99999999W"]
  }'
```
```json
{
  "submission_id": "f7a8b9c0-d1e2-f3a4-b5c6-d7e8f9a0b1c2",
  "books_resolved": ["/works/OL27482W"],
  "books_failed": ["OL99999999W"]
}
```

---

## Running Tests

```bash
make test
```

40 tests, 1 skipped. The suite covers health, tenant CRUD and API key exposure, tenant isolation for books/logs/book detail, book list/search/filter/detail with pagination, ingestion triggering and status lookup, log ordering, reading list resolution (mixed resolved/failed, deduplication via DB row count, no plaintext PII in response or DB), the `refresh_all_catalogs` scheduler task, and the full Tier 3 version management surface: route shape, tenant isolation, idempotent re-ingestion (no new version on unchanged data), version creation with correct `change_summary` on field change, regression guard for missing incoming fields, and live Open Library fallback for reading list resolution via a fresh tenant with no local books. The one skip (`test_book_detail_tenant_isolation`) requires a book to already exist in tenant A's catalog at test time — it passes when run after `make seed`.

---

## What I Would Do Differently With More Time

**Authentication.** The `api_key` column exists on the `tenants` table and is returned at creation time, but it is never checked on any route. In production every tenant-scoped route should validate the `X-API-Key` header against the stored key using a constant-time comparison, and the key should be hashed at rest rather than stored as a plaintext hex string. The current model gives the illusion of multi-tenant security while enforcing none of it at the API boundary.

**Celery async antipattern.** The `ingest_books` task is a synchronous Celery task that calls `asyncio.run(run_ingestion(...))` to execute the async ingestion pipeline. This works — `asyncio.run` creates a fresh event loop per task execution — but it means each worker process can only run one ingestion at a time and the event loop lifetime is tied to the task lifetime. A better approach would be [ARQ](https://arq-docs.helpmanual.io/) (async Redis Queue), which runs workers as native async coroutines, or structuring Celery workers with `gevent` or `eventlet` to interleave concurrent OL HTTP calls across multiple tasks on the same worker.

**Open Library rate limiting.** The current implementation sleeps 0.5 seconds between follow-up calls within a single `resolve_work` invocation. This is a fixed global delay — it does not account for concurrent ingestions from multiple tenants, does not back off on 429 responses, and does not share a rate budget across worker processes. The correct model is a per-tenant token bucket in Redis, shared across all worker processes, with exponential backoff on HTTP 429 and respect for `Retry-After` headers.

**Retry granularity.** The `autoretry_for=(Exception,)` decorator retries the entire `ingest_books` task on any failure. If 49 of 50 books succeed and the 50th raises an exception, all 50 are re-fetched on retry, duplicating 49 OL API calls and risking duplicate version writes if the upsert logic has any edge case. Retry logic should be applied at the individual work-resolution level, with failed work IDs checkpointed to Redis so a retry picks up only where it left off.

**Noisy neighbor throttling.** All tenants share a single Celery queue. A tenant that triggers ingestion for a high-volume subject (e.g., `subject=fiction`, limit=50) with slow OL responses will block the worker for several minutes, delaying all other tenants' tasks. The fix is per-tenant priority queues or a dedicated queue per tenant with weighted round-robin scheduling across workers. At minimum, a per-tenant concurrency cap would prevent one tenant from monopolizing the pool.

**Test coverage gaps.** `test_book_detail_tenant_isolation` is skipped unless books exist in the DB — it should instead create its own books directly via the ORM (as the version isolation test does) so it is always deterministic. There are no isolated unit tests for the service layer: `_merge_with_regression_guard`, `_compute_diff`, `hash_email`, and `hash_patron` are only exercised through the full integration stack. There are no concurrency tests verifying that simultaneous ingestions for the same tenant and work ID do not produce duplicate book rows or version conflicts.

**Observability.** Failed ingestions write error strings to the `errors` JSONB column, which is queryable but not observable in real time. There is no structured logging (log lines are Uvicorn's default formatter), no request tracing, no Celery task metrics, and no alerting on repeated failures. In production this would need a structured logger (e.g., `structlog`) emitting JSON with `tenant_id`, `task_id`, and `ol_work_id` on every log line, and a metrics sink (Prometheus or StatsD) tracking ingestion throughput, OL latency, and failure rates per tenant.

**Connection pooling.** SQLAlchemy's default pool size of 5 with overflow of 10 is not tuned for the actual concurrency model. Each Celery worker process opens its own pool, so with 4 worker processes the total connection ceiling is effectively `4 × (5 + 10) = 60` connections — which may exhaust PostgreSQL's default `max_connections=100` under load. The pool should be sized deliberately based on the number of worker processes and the expected query concurrency per task, and `pool_pre_ping=True` should be set to recover gracefully from idle connection drops.
