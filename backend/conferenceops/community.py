"""The community feed on core-api: posts and one level of replies, written by the demo personas.

- `GET /api/v1/community/posts`: the newest posts first, each with its replies oldest first; public fields only.
- `POST /api/v1/community/posts`: a post, or a reply when `parentId` names a post, by the persona in
  `X-Demo-Profile-ID` (AUTHZ-1). The author is always that persona; the client can't name another. An unknown
  persona and an unknown or reply-level parent both answer 404 with one body.

The text is public community content, labeled as such in the UI and never presented as an official fact (ACCESS-1).
It never reaches logs, spans or metrics (PRIV-1): failures log the error's type only. The seeded posts belong to the
fictional personas and say nothing in a real speaker's voice (SPEAKER-1)."""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session as DbSession

from conferenceops.db import transaction
from conferenceops.models import Post, Profile
from conferenceops.personas import DEMO_PROFILE_OPENAPI, DEMO_PROFILE_RESPONSES, ErrorDetail, demo_profile_id
from conferenceops.search import ApiModel

log = logging.getLogger("conferenceops.community")

MAX_BODY = 500  # the same bound as the column's check constraint
FEED_DEFAULT = 20
FEED_MAX = 50


# --- What the data layer returns ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class PostRow:
    id: uuid.UUID
    author: str  # the persona's display name, a public field
    author_role: str
    body: str
    created_at: datetime
    parent_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Thread:
    post: PostRow
    replies: tuple[PostRow, ...]


# A write's outcome. AUTHZ-1: an unknown persona and an unknown parent both answer 404 with the same body.
WriteResult = PostRow | Literal["no_profile", "no_parent"]


class CommunityData(Protocol):
    def feed(self, limit: int) -> list[Thread]: ...

    def create(self, author_id: uuid.UUID, body: str, parent_id: uuid.UUID | None) -> WriteResult: ...


class PostgresCommunityData:
    def feed(self, limit: int) -> list[Thread]:
        with transaction() as db:
            top = db.execute(
                select(Post, Profile.display_name, Profile.role)
                .join(Profile, Profile.id == Post.author_id)
                .where(Post.parent_id.is_(None))
                .order_by(Post.created_at.desc(), Post.id)
                .limit(limit)
            ).all()
            ids = [post.id for post, _, _ in top]
            replies: dict[uuid.UUID, list[PostRow]] = {i: [] for i in ids}
            if ids:
                for post, name, role in db.execute(
                    select(Post, Profile.display_name, Profile.role)
                    .join(Profile, Profile.id == Post.author_id)
                    .where(Post.parent_id.in_(ids))
                    .order_by(Post.created_at, Post.id)
                ).all():
                    replies[post.parent_id].append(_row(post, name, role))
            return [Thread(_row(post, name, role), tuple(replies[post.id])) for post, name, role in top]

    def create(self, author_id: uuid.UUID, body: str, parent_id: uuid.UUID | None) -> WriteResult:
        with transaction() as db:
            author = db.execute(
                select(Profile.display_name, Profile.role).where(
                    Profile.id == author_id, Profile.demo_selectable.is_(True)
                )
            ).one_or_none()
            if author is None:
                return "no_profile"
            if parent_id is not None:
                parent = db.scalars(select(Post).where(Post.id == parent_id)).one_or_none()
                if parent is None or parent.parent_id is not None:  # replies are one level deep
                    return "no_parent"
            post = Post(author_id=author_id, parent_id=parent_id, body=body)
            db.add(post)
            db.flush()
            db.refresh(post)
            return _row(post, author.display_name, author.role)


def _row(post: Post, name: str, role: object) -> PostRow:
    return PostRow(
        id=post.id,
        author=name,
        author_role=str(getattr(role, "value", role)),
        body=post.body,
        created_at=post.created_at,
        parent_id=post.parent_id,
    )


def get_community_data() -> CommunityData:
    return PostgresCommunityData()


# --- The seeded conversation (fictional personas only) -----------------------------------------------------------

SEED_NAMESPACE = uuid.UUID("6b0c8f0e-5c55-4f43-9a51-2f6f1c1f0a01")
# (key, persona key, parent key, minutes before the anchor, text). Fictional personas talking about the fictional
# CloudBridge Summit and their own plans; no real speaker is quoted or paraphrased (SPEAKER-1).
SEED_POSTS: tuple[tuple[str, str, str | None, int, str], ...] = (
    (
        "post-1",
        "P1",
        None,
        180,
        "The CloudBridge replay of “AI SRE in Practice” was the most useful hour of my month. Who else is "
        "following the agent-reliability talks this week?",
    ),
    ("reply-1a", "P3", "post-1", 170, "Me. I'm mostly after the security side: how far an agent should be trusted."),
    ("reply-1b", "P5", "post-1", 160, "Thanks! The transcript is published if you want the exact numbers."),
    (
        "post-2",
        "P4",
        None,
        120,
        "First KubeCon. Any tips for an OpenTelemetry beginner? I've bookmarked three intro talks and I'm nervous "
        "about picking the wrong rooms.",
    ),
    (
        "reply-2a",
        "P8",
        "post-2",
        110,
        "Welcome! “Where should I go next?” only suggests talks you can still reach, and the staff at "
        "registration can help with anything access-related.",
    ),
    (
        "post-3",
        "P2",
        None,
        60,
        "Looking for people to compare GitOps rollout stories with between sessions. Reply here and let's meet at a "
        "break.",
    ),
)
SEED_ANCHOR = datetime(2026, 11, 9, 18, 0, tzinfo=UTC)  # the build day, 11:00 in Salt Lake City


def seed_post_id(key: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, key)


def seed_posts(db: DbSession, author_of: Callable[[str], uuid.UUID]) -> int:
    """Adds the seeded conversation when it's missing; never changes or re-adds a post that exists (idempotent).
    Returns how many rows were added. `author_of` maps a persona key to her profile ID."""
    added = 0
    for key, persona, parent, minutes, text in SEED_POSTS:
        created = db.execute(
            pg_insert(Post)
            .values(
                id=seed_post_id(key),
                author_id=author_of(persona),
                parent_id=seed_post_id(parent) if parent else None,
                body=text,
                created_at=SEED_ANCHOR - timedelta(minutes=minutes),
            )
            .on_conflict_do_nothing(index_elements=["id"])
            .returning(Post.id)
        )
        if created.scalar_one_or_none() is not None:
            added += 1
    return added


# --- The API models (camelCase on the wire) ----------------------------------------------------------------------


class CommunityPost(ApiModel):
    id: str
    author: str = Field(description="The persona's display name; every persona is fictional")
    author_role: str = Field(description="attendee, speaker, partner, organizer or host")
    body: str
    created_at: datetime = Field(description="UTC instant (TIME-1)")
    parent_id: str | None = None
    replies: list["CommunityPost"] = Field(default_factory=list)


class CommunityFeed(ApiModel):
    posts: list[CommunityPost]
    is_community_content: bool = Field(
        default=True, description="Always true: posts are what personas wrote, never official event information"
    )


class NewPost(ApiModel):
    body: str = Field(min_length=1, max_length=MAX_BODY)
    parent_id: uuid.UUID | None = Field(default=None, description="The post this replies to; replies are one level")

    @field_validator("body")
    @classmethod
    def not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("A post needs some text")
        return stripped


def _card(row: PostRow, replies: tuple[PostRow, ...] = ()) -> CommunityPost:
    return CommunityPost(
        id=str(row.id),
        author=row.author,
        author_role=row.author_role,
        body=row.body,
        created_at=row.created_at,
        parent_id=str(row.parent_id) if row.parent_id else None,
        replies=[_card(r) for r in replies],
    )


# --- Routes ------------------------------------------------------------------------------------------------------

router = APIRouter(tags=["community"])

Data = Annotated[CommunityData, Depends(get_community_data)]


def _unavailable(exc: Exception) -> HTTPException:
    # PRIV-1: the error's type only; a message could quote a post.
    log.error("community feed failed: %s", type(exc).__name__)
    return HTTPException(status_code=503, detail="The community feed isn't available right now")


@router.get(
    "/api/v1/community/posts",
    response_model=CommunityFeed,
    response_model_by_alias=True,
    responses={503: {"model": ErrorDetail, "description": "The community feed isn't available right now"}},
)
def community_feed(
    data: Data,
    limit: Annotated[int, Query(ge=1, le=FEED_MAX, description="How many posts, newest first")] = FEED_DEFAULT,
) -> CommunityFeed:
    """The newest posts first, each with its replies oldest first. Public fields only."""
    try:
        threads = data.feed(limit)
    except Exception as exc:
        raise _unavailable(exc) from exc
    return CommunityFeed(posts=[_card(t.post, t.replies) for t in threads])


@router.post(
    "/api/v1/community/posts",
    status_code=201,
    response_model=CommunityPost,
    response_model_by_alias=True,
    openapi_extra=DEMO_PROFILE_OPENAPI,
    responses={
        **DEMO_PROFILE_RESPONSES,
        404: {"model": ErrorDetail, "description": "Profile or parent post not found"},
        503: {"model": ErrorDetail, "description": "The community feed isn't available right now"},
    },
)
def create_post(
    new: NewPost,
    author_id: Annotated[uuid.UUID, Depends(demo_profile_id)],
    data: Data,
) -> CommunityPost:
    """A post, or a reply when `parentId` is set, by the persona in X-Demo-Profile-ID (AUTHZ-1)."""
    try:
        result = data.create(author_id, new.body, new.parent_id)
    except Exception as exc:
        raise _unavailable(exc) from exc
    if isinstance(result, str):
        # AUTHZ-1: one body for both cases.
        raise HTTPException(status_code=404, detail="Not found")
    return _card(result)
