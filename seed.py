"""
Seed script: creates a "Demo Library" tenant (if not already present) and
triggers an ingestion for author=tolkien.

Usage:
    docker compose exec app python seed.py
"""
import secrets
import uuid

from app.db.models import IngestionLog, Tenant
from app.db.session import SessionLocal

TENANT_NAME = "Demo Library"
QUERY_TYPE = "author"
QUERY_VALUE = "tolkien"


def main():
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.name == TENANT_NAME).first()
        if tenant:
            print(f"Tenant already exists — using existing record.")
        else:
            tenant = Tenant(
                id=uuid.uuid4(),
                name=TENANT_NAME,
                api_key=secrets.token_hex(32),
            )
            db.add(tenant)
            db.commit()
            db.refresh(tenant)
            print(f"Created tenant '{TENANT_NAME}'.")

        print(f"\nTenant ID : {tenant.id}")
        print(f"API Key   : {tenant.api_key}")

        log = IngestionLog(
            tenant_id=tenant.id,
            query_type=QUERY_TYPE,
            query_value=QUERY_VALUE,
        )
        db.add(log)
        db.commit()
        db.refresh(log)

        from app.workers.tasks import ingest_books
        task = ingest_books.delay(str(tenant.id), QUERY_TYPE, QUERY_VALUE, str(log.id))

        log.celery_task_id = task.id
        db.commit()

        print(f"\nIngestion triggered:")
        print(f"  query_type  : {QUERY_TYPE}")
        print(f"  query_value : {QUERY_VALUE}")
        print(f"  task_id     : {task.id}")
        print(f"  log_id      : {log.id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
