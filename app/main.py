from fastapi import FastAPI

from app.api.routes import books, ingestion, logs, reading_lists, tenants

app = FastAPI(title="Titan Library Catalog", redirect_slashes=False)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(tenants.router, prefix="/api/tenants", tags=["tenants"])
app.include_router(books.router, prefix="/api/{tenant_id}/books", tags=["books"])
app.include_router(ingestion.router, prefix="/api/{tenant_id}/ingest", tags=["ingestion"])
app.include_router(logs.router, prefix="/api/{tenant_id}/logs", tags=["logs"])
app.include_router(reading_lists.router, prefix="/api/{tenant_id}/reading-list", tags=["reading-lists"])
