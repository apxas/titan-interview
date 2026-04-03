"""add book versions

Revision ID: a1b2c3d4e5f6
Revises: 33b0d89aba87
Create Date: 2026-04-02 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a1b2c3d4e5f6"
down_revision = "33b0d89aba87"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add current_version column to books
    op.add_column(
        "books",
        sa.Column(
            "current_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )

    # Create book_versions table
    op.create_table(
        "book_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("authors", postgresql.JSONB(), nullable=False),
        sa.Column("first_publish_year", sa.Integer(), nullable=True),
        sa.Column("subjects", postgresql.JSONB(), nullable=False),
        sa.Column("cover_url", sa.String(), nullable=True),
        sa.Column("raw_data", postgresql.JSONB(), nullable=False),
        sa.Column("change_summary", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("book_id", "version_number", name="uq_book_versions_book_version"),
    )

    op.create_index(
        "ix_book_versions_book_id_version",
        "book_versions",
        ["book_id", "version_number"],
        unique=True,
    )
    op.create_index(
        "ix_book_versions_tenant_book",
        "book_versions",
        ["tenant_id", "book_id"],
    )

    # Backfill: create version_number=1 for every existing book
    op.execute(
        """
        INSERT INTO book_versions (
            id, book_id, tenant_id, version_number,
            title, authors, first_publish_year, subjects,
            cover_url, raw_data, change_summary, created_at
        )
        SELECT
            gen_random_uuid(), id, tenant_id, 1,
            title, authors, first_publish_year, subjects,
            cover_url, raw_data, '{}', created_at
        FROM books
        """
    )


def downgrade() -> None:
    op.drop_index("ix_book_versions_tenant_book", table_name="book_versions")
    op.drop_index("ix_book_versions_book_id_version", table_name="book_versions")
    op.drop_table("book_versions")
    op.drop_column("books", "current_version")
