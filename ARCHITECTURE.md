# Architecture Reference

## Project Overview
A multi-tenant library catalog service that ingests book data from Open Library and exposes a local API for searching, browsing, and managing reading list submissions.

## Stack
- **FastAPI** — web framework, all API endpoints
- **PostgreSQL** — primary database, all persistent storage
- **Redis** — message broker for Celery
- **Celery** — background worker for ingestion jobs, scheduled refresh via Celery Beat
- **httpx** — async HTTP client for Open Library API calls
- **SQLAlchemy** — ORM with Alembic for migrations
- **Docker Compose** — single-command startup bundling all services

## Project Structure
```
/
├── app/
│   ├── api/
│   │   └── routes/
│   │       ├── books.py
│   │       ├── ingestion.py
│   │       ├── reading_lists.py
│   │       └── logs.py
│   ├── core/
│   │   ├── config.py
│   │   └── security.py
│   ├── db/
│   │   ├── models.py
│   │   └── session.py
│   ├── services/
│   │   ├── open_library.py
│   │   ├── ingestion.py
│   │   └── pii.py
│   ├── workers/
│   │   └── tasks.py
│   └── main.py
├── alembic/
├── docker-compose.yml
├── Makefile
├── requirements.txt
├── .env.example
├── README.md
├── ARCHITECTURE.md
└── prompt-log.md
```

## Database Models

### tenants
- id (UUID, PK)
- name (string)
- api_key (string, unique)
- created_at (timestamp)

### books
- id (UUID, PK)
- tenant_id (UUID, FK → tenants)
- ol_work_id (string) — Open Library work ID
- title (string)
- authors (JSONB array of strings)
- first_publish_year (integer, nullable)
- subjects (JSONB array of strings)
- cover_url (string, nullable)
- raw_data (JSONB) — full response stored for debugging
- created_at (timestamp)
- updated_at (timestamp)

### ingestion_logs
- id (UUID, PK)
- tenant_id (UUID, FK → tenants)
- query_type (string) — "author" or "subject"
- query_value (string)
- status (string) — "pending", "running", "complete", "failed"
- fetched_count (integer)
- success_count (integer)
- fail_count (integer)
- errors (JSONB array of strings)
- celery_task_id (string, nullable)
- started_at (timestamp)
- finished_at (timestamp, nullable)

### reading_list_submissions
- id (UUID, PK)
- tenant_id (UUID, FK → tenants)
- patron_hash (string) — SHA-256 hash of name+email salt
- email_hash (string) — SHA-256 hash of email alone (for deduplication)
- books_requested (JSONB array of OL work IDs)
- books_resolved (JSONB array of book IDs found in local DB)
- books_failed (JSONB array of IDs that could not be resolved)
- created_at (timestamp)

## API Endpoints

### Books
- GET /api/{tenant_id}/books — list all books, paginated
- GET /api/{tenant_id}/books/{book_id} — single book detail
- GET /api/{tenant_id}/books/search?q= — keyword search by title or author
- GET /api/{tenant_id}/books/filter?author=&subject=&year_min=&year_max= — filter

### Ingestion
- POST /api/{tenant_id}/ingest — trigger ingestion job, body: {query_type, query_value}
- GET /api/{tenant_id}/ingest/{task_id} — check job status

### Logs
- GET /api/{tenant_id}/logs — list ingestion activity log

### Reading Lists
- POST /api/{tenant_id}/reading-list — submit a reading list

### System
- GET /health — health check

## Key Design Decisions

### Multi-Tenancy
Every database query must be scoped by tenant_id. No query should ever return results across tenant boundaries.

### Open Library Ingestion
Open Library requires multiple API calls to assemble a complete book record:
1. Search endpoint to get list of works by author or subject
2. Per-work follow-up calls to resolve full author names and subjects if missing
3. Cover image URL constructed from cover_id if present: https://covers.openlibrary.org/b/id/{cover_id}-M.jpg

### Non-Blocking Ingestion
POST /ingest immediately creates an ingestion_log record with status "pending", enqueues a Celery task, and returns the task ID. The Celery worker does all Open Library API calls in the background and updates the ingestion_log record as it progresses.

### PII Handling
Name and email are hashed with SHA-256 before any database write. Email is hashed separately to allow deduplication (same email = same patron). Raw PII is never logged or stored.

### Catalog Freshness
Celery Beat runs a scheduled task every 24 hours that re-triggers ingestion for all previously ingested queries per tenant.

### Single-Command Startup
docker compose up starts: FastAPI app, PostgreSQL, Redis, Celery worker, Celery Beat. Alembic migrations run automatically on app startup.