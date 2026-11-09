"""baseline: events, sessions, access facts, personas, outbox

Revision ID: 0001
Revises:
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENT_KIND = postgresql.ENUM("authored", "imported", name="eventkind")
CLOCK_MODE = postgresql.ENUM("fixed", "wall", name="clockmode")
LEVEL = postgresql.ENUM("beginner", "intermediate", "advanced", "all", name="level")
ROLE = postgresql.ENUM("attendee", "speaker", "partner", "organizer", "host", name="role")
ACCESS_FACT_KIND = postgresql.ENUM(
    "captions",
    "reserved_seating",
    "quiet_room",
    "prayer_room",
    "staff_help",
    "mobility_rental",
    name="accessfactkind",
)
OUTBOX_STATUS = postgresql.ENUM("pending", "done", "failed", name="outboxstatus")
ENUMS = (EVENT_KIND, CLOCK_MODE, LEVEL, ROLE, ACCESS_FACT_KIND, OUTBOX_STATUS)


def _id() -> sa.Column:
    return sa.Column("id", sa.UUID(), primary_key=True)


def _created() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    # Each enum type is used by exactly one column, so create_table creates it with that table.
    def use(enum: postgresql.ENUM) -> postgresql.ENUM:
        return enum

    op.create_table(
        "conferences",
        _id(),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("kind", use(EVENT_KIND), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("clock_mode", use(CLOCK_MODE), nullable=False),
        sa.Column("is_fictional", sa.Boolean(), nullable=False),
        sa.Column("has_transcripts", sa.Boolean(), nullable=False),
        _created(),
    )
    op.create_table(
        "rooms",
        _id(),
        sa.Column("conference_id", sa.UUID(), sa.ForeignKey("conferences.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(64)),
        sa.Column("name", sa.String(200), nullable=False),
        sa.UniqueConstraint("conference_id", "external_id"),
    )
    op.create_index("ix_rooms_conference_id", "rooms", ["conference_id"])
    op.create_table(
        "speakers",
        _id(),
        sa.Column("conference_id", sa.UUID(), sa.ForeignKey("conferences.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(64)),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("company", sa.String(200)),
        sa.Column("title", sa.String(200)),
        sa.UniqueConstraint("conference_id", "external_id"),
    )
    op.create_index("ix_speakers_conference_id", "speakers", ["conference_id"])
    op.create_table(
        "sessions",
        _id(),
        sa.Column("conference_id", sa.UUID(), sa.ForeignKey("conferences.id", ondelete="CASCADE"), nullable=False),
        sa.Column("room_id", sa.UUID(), sa.ForeignKey("rooms.id", ondelete="SET NULL")),
        sa.Column("external_id", sa.String(64)),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("abstract", sa.Text()),
        sa.Column("level", use(LEVEL)),
        sa.Column("topics", postgresql.ARRAY(sa.String(100)), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_service", sa.Boolean(), nullable=False),
        sa.Column("materials", postgresql.JSONB(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("conference_id", "external_id"),
    )
    op.create_index("ix_sessions_conference_id", "sessions", ["conference_id"])
    op.create_table(
        "session_speakers",
        sa.Column("session_id", sa.UUID(), sa.ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("speaker_id", sa.UUID(), sa.ForeignKey("speakers.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "access_facts",
        _id(),
        sa.Column("conference_id", sa.UUID(), sa.ForeignKey("conferences.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", use(ACCESS_FACT_KIND), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("location", sa.String(200)),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("read_on", sa.Date(), nullable=False),
    )
    op.create_index("ix_access_facts_conference_id", "access_facts", ["conference_id"])
    op.create_table(
        "profiles",
        _id(),
        sa.Column("role", use(ROLE), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("company", sa.String(200)),
        sa.Column("interests", postgresql.ARRAY(sa.String(100)), nullable=False),
        sa.Column("demo_selectable", sa.Boolean(), nullable=False),
        sa.Column("is_fictional", sa.Boolean(), nullable=False),
        _created(),
    )
    op.create_table(
        "profile_preferences",
        sa.Column("profile_id", sa.UUID(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("needs_step_free", sa.Boolean(), nullable=False),
        sa.Column("wants_captions", sa.Boolean(), nullable=False),
        sa.Column("reduced_motion", sa.Boolean(), nullable=False),
        sa.Column("excluded_levels", postgresql.ARRAY(sa.String(20)), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_table(
        "attendance",
        sa.Column("profile_id", sa.UUID(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("session_id", sa.UUID(), sa.ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "agenda_items",
        sa.Column("profile_id", sa.UUID(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("session_id", sa.UUID(), sa.ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True),
        _created(),
    )
    op.create_table(
        "outbox_jobs",
        _id(),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("entity_revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("status", use(OUTBOX_STATUS), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_error", sa.Text()),
        _created(),
    )
    op.create_index("ix_outbox_jobs_entity_id", "outbox_jobs", ["entity_id"])
    op.create_index("ix_outbox_jobs_status", "outbox_jobs", ["status"])


def downgrade() -> None:
    for table in (
        "outbox_jobs",
        "agenda_items",
        "attendance",
        "profile_preferences",
        "profiles",
        "access_facts",
        "session_speakers",
        "sessions",
        "speakers",
        "rooms",
        "conferences",
    ):
        op.drop_table(table)
    bind = op.get_bind()
    for enum in reversed(ENUMS):
        enum.drop(bind, checkfirst=True)
