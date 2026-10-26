"""add feed cache

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import cvestream.state.schema

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "feed_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("endpoint_id", sa.Text(), nullable=False),
        sa.Column("xml", sa.Text(), nullable=False),
        sa.Column("etag", sa.Text(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("rendered_at", cvestream.state.schema.UTCDateTime(), nullable=False),
        sa.Column("changed_at", cvestream.state.schema.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feed_cache")),
        sa.UniqueConstraint("endpoint_id", name=op.f("ux_feed_cache_endpoint_id")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("feed_cache")
