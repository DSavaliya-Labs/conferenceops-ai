"""transcripts: the publication manifest of a concluded session's captioned replay

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRANSCRIPT_STATUS = postgresql.ENUM("processing", "published", name="transcriptstatus")


def upgrade() -> None:
    # Additive only: the previous app version keeps running while this applies (PLAN §14.5). The enum type is used by
    # exactly one column, so create_table creates it with the table.
    op.create_table(
        "transcripts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("session_id", sa.UUID(), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("speaker_name", sa.String(200), nullable=False),
        sa.Column("is_simulated", sa.Boolean(), nullable=False),
        sa.Column("media_url", sa.Text(), nullable=False),
        sa.Column("captions_url", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("cues", postgresql.JSONB(), nullable=False),
        sa.Column("status", TRANSCRIPT_STATUS, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("session_id"),
    )


def downgrade() -> None:
    op.drop_table("transcripts")
    TRANSCRIPT_STATUS.drop(op.get_bind(), checkfirst=True)
