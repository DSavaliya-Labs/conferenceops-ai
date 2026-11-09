"""The community feed against a real PostgreSQL: the seeded conversation (idempotent), the newest-first feed with its
replies, and the writes' outcomes for an unknown persona, an unknown parent and a reply to a reply."""

import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from conferenceops.cloudbridge import load_cloudbridge, profile_id
from conferenceops.community import SEED_POSTS, PostgresCommunityData, PostRow, seed_post_id, seed_posts
from conferenceops.db import transaction

pytestmark = pytest.mark.integration

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(scope="module")
def engine():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set")
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND, "migrations"))
    command.upgrade(cfg, "head")
    return create_engine(url)


@pytest.fixture
def seeded(engine):
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE outbox_jobs, conferences, profiles CASCADE"))
    with transaction(engine) as db:
        load_cloudbridge(db)
        assert seed_posts(db, profile_id) == len(SEED_POSTS)
        assert seed_posts(db, profile_id) == 0  # a second seed adds nothing and changes nothing


def test_the_feed_is_newest_first_with_replies_oldest_first(seeded):
    feed = PostgresCommunityData().feed(20)
    assert [t.post.id for t in feed] == [seed_post_id("post-3"), seed_post_id("post-2"), seed_post_id("post-1")]
    assert [r.id for r in feed[2].replies] == [seed_post_id("reply-1a"), seed_post_id("reply-1b")]
    assert feed[2].post.author == "Aisha Rahman"
    assert PostgresCommunityData().feed(1)[0].post.id == seed_post_id("post-3")


def test_a_persona_posts_and_replies_and_misses_are_reported(seeded):
    data = PostgresCommunityData()
    aisha = profile_id("P1")
    post = data.create(aisha, "Anyone for the eBPF talks?", None)
    assert isinstance(post, PostRow) and post.author == "Aisha Rahman" and post.author_role == "attendee"
    assert data.feed(1)[0].post.id == post.id
    reply = data.create(profile_id("P3"), "Me", post.id)
    assert isinstance(reply, PostRow) and reply.parent_id == post.id
    assert data.create(aisha, "deeper", reply.id) == "no_parent"  # replies are one level deep
    assert data.create(aisha, "x", uuid.uuid4()) == "no_parent"
    assert data.create(uuid.uuid4(), "x", None) == "no_profile"
