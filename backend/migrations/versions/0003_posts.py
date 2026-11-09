"""posts: the community feed, posts and one level of replies

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive only: the previous app version keeps running while this applies (PLAN §14.5).
    op.create_table(
        "posts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("author_id", sa.UUID(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_id", sa.UUID(), sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("char_length(body) BETWEEN 1 AND 500", name="ck_posts_body_length"),
    )
    op.create_index("ix_posts_parent_id", "posts", ["parent_id"])
    op.create_index("ix_posts_created_at", "posts", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_posts_created_at", table_name="posts")
    op.drop_index("ix_posts_parent_id", table_name="posts")
    op.drop_table("posts")
