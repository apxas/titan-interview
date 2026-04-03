import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    api_key = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    books = relationship("Book", back_populates="tenant")
    ingestion_logs = relationship("IngestionLog", back_populates="tenant")
    reading_list_submissions = relationship("ReadingListSubmission", back_populates="tenant")


class Book(Base):
    __tablename__ = "books"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    ol_work_id = Column(String, nullable=False)
    title = Column(String, nullable=False)
    authors = Column(JSONB, nullable=False, default=list)
    first_publish_year = Column(Integer, nullable=True)
    subjects = Column(JSONB, nullable=False, default=list)
    cover_url = Column(String, nullable=True)
    raw_data = Column(JSONB, nullable=False, default=dict)
    current_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    tenant = relationship("Tenant", back_populates="books")
    versions = relationship("BookVersion", back_populates="book", order_by="BookVersion.version_number.desc()")


class IngestionLog(Base):
    __tablename__ = "ingestion_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    query_type = Column(String, nullable=False)
    query_value = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    fetched_count = Column(Integer, nullable=False, default=0)
    success_count = Column(Integer, nullable=False, default=0)
    fail_count = Column(Integer, nullable=False, default=0)
    errors = Column(JSONB, nullable=False, default=list)
    celery_task_id = Column(String, nullable=True)
    started_at = Column(DateTime, server_default=func.now(), nullable=False)
    finished_at = Column(DateTime, nullable=True)

    tenant = relationship("Tenant", back_populates="ingestion_logs")


class ReadingListSubmission(Base):
    __tablename__ = "reading_list_submissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    patron_hash = Column(String, nullable=False)
    email_hash = Column(String, nullable=False)
    books_requested = Column(JSONB, nullable=False, default=list)
    books_resolved = Column(JSONB, nullable=False, default=list)
    books_failed = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    tenant = relationship("Tenant", back_populates="reading_list_submissions")


class BookVersion(Base):
    __tablename__ = "book_versions"
    __table_args__ = (
        UniqueConstraint("book_id", "version_number", name="uq_book_versions_book_version"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id = Column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    authors = Column(JSONB, nullable=False)
    first_publish_year = Column(Integer, nullable=True)
    subjects = Column(JSONB, nullable=False)
    cover_url = Column(String, nullable=True)
    raw_data = Column(JSONB, nullable=False)
    change_summary = Column(JSONB, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    book = relationship("Book", back_populates="versions")
