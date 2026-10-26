"""create schema

Revision ID: 0001
Revises:
Create Date: 2026-10-26 21:42:17.873595

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import cvestream.state.schema

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "source_cursor",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("updated_at", cvestream.state.schema.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_cursor")),
        sa.UniqueConstraint("source", name=op.f("ux_source_cursor_source")),
    )
    op.create_table(
        "vulnerability",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("primary_id", sa.Text(), nullable=False),
        sa.Column("activity_at", cvestream.state.schema.UTCDateTime(), nullable=False),
        sa.Column("created_at", cvestream.state.schema.UTCDateTime(), nullable=False),
        sa.Column("changed_at", cvestream.state.schema.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vulnerability")),
    )
    with op.batch_alter_table("vulnerability", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_vulnerability_activity_at"), ["activity_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_vulnerability_primary_id"), ["primary_id"], unique=False
        )

    op.create_table(
        "alias",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("vulnerability_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["vulnerability_id"],
            ["vulnerability.id"],
            name=op.f("fk_alias_vulnerability_id_vulnerability"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alias")),
        sa.UniqueConstraint("alias", name=op.f("ux_alias_alias")),
    )
    with op.batch_alter_table("alias", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_alias_vulnerability_id"), ["vulnerability_id"], unique=False
        )

    op.create_table(
        "publication",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("endpoint_id", sa.Text(), nullable=False),
        sa.Column("vulnerability_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("guid", sa.Text(), nullable=False),
        sa.Column("primary_id", sa.Text(), nullable=False),
        sa.Column("band", sa.Text(), nullable=False),
        sa.Column("is_known_exploited", sa.Boolean(), nullable=False),
        sa.Column("products_json", sa.JSON(), nullable=False),
        sa.Column("labels_json", sa.JSON(), nullable=False),
        sa.Column("published_at", cvestream.state.schema.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["vulnerability_id"],
            ["vulnerability.id"],
            name=op.f("fk_publication_vulnerability_id_vulnerability"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publication")),
        sa.UniqueConstraint("guid", name=op.f("ux_publication_guid")),
    )
    with op.batch_alter_table("publication", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_publication_endpoint_id_published_at"),
            ["endpoint_id", "published_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_publication_endpoint_id_vulnerability_id"),
            ["endpoint_id", "vulnerability_id"],
            unique=False,
        )

    op.create_table(
        "source_record",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vulnerability_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("record_id", sa.Text(), nullable=False),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("activity_at", cvestream.state.schema.UTCDateTime(), nullable=False),
        sa.Column(
            "first_seen_at", cvestream.state.schema.UTCDateTime(), nullable=False
        ),
        sa.Column("changed_at", cvestream.state.schema.UTCDateTime(), nullable=False),
        sa.Column("last_seen_at", cvestream.state.schema.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["vulnerability_id"],
            ["vulnerability.id"],
            name=op.f("fk_source_record_vulnerability_id_vulnerability"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_record")),
        sa.UniqueConstraint(
            "source", "record_id", name=op.f("ux_source_record_source_record_id")
        ),
    )
    with op.batch_alter_table("source_record", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_source_record_vulnerability_id"),
            ["vulnerability_id"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("source_record", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_source_record_vulnerability_id"))

    op.drop_table("source_record")
    with op.batch_alter_table("publication", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_publication_endpoint_id_vulnerability_id"))
        batch_op.drop_index(batch_op.f("ix_publication_endpoint_id_published_at"))

    op.drop_table("publication")
    with op.batch_alter_table("alias", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_alias_vulnerability_id"))

    op.drop_table("alias")
    with op.batch_alter_table("vulnerability", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_vulnerability_primary_id"))
        batch_op.drop_index(batch_op.f("ix_vulnerability_activity_at"))

    op.drop_table("vulnerability")
    op.drop_table("source_cursor")
