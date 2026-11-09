"""Where should I go next? (PLAN §1.4, §8.3, §10.1): the hard rules first, then the ranking, with reasons a person can
check. The answer is deterministic: no language model writes it (a generated explanation would be a separate step,
behind AI-1's validation and fallback).

1. **Who is asking.** The persona comes from `X-Demo-Profile-ID` and is looked up in PostgreSQL (AUTHZ-1): a missing
   header is 400, an unknown or unselectable persona 404.
2. **Retrieval** in Elasticsearch, with the index and filters search uses (one event, never a service session), plus
   time: only sessions that haven't started by the event's wall clock, inside the chosen window of the event's day
   (TIME-1). Channels, fused with reciprocal rank fusion:
   - her stated interests, and the optional query: BM25, and kNN on their embedding. If the model is slow or down,
     BM25 alone, and the answer says `lexical` (AI-1, fail open);
   - each session she attended or bookmarked (her CloudBridge history): kNN with that session's stored vector, so no
     model call.
3. **Hard rules**, on the canonical PostgreSQL rows: not started, inside the window, not already on her agenda, no
   conflict with her agenda, not at a level she excluded, and captions published when she asked for captions
   (§8.3 rule 5). A requirement is never softened to fill the list (ACCESS-1).
4. **Reasons**, each checkable: an interest whose every word appears in the talk; a talk she attended or bookmarked
   whose stored embedding is at least `SIMILARITY_MIN` cosine-similar; captions published when she asked for them. A
   talk without an interest or history reason isn't offered.
5. **Ranking:** `RELEVANCE_WEIGHT` x the fused relevance (normalized to the best candidate) + `INTEREST_WEIGHT` x the
   share of her interests it matches. Ties go to the earlier start, then the session ID.

Access on every card: only the event's published facts, each with its source link. Nothing is ever labeled step-free.
For a persona who needs step-free routes, every answer says routes aren't published and points to the event's staff
(ACCESS-1).

PRIV-1: interests, the query, preferences and history never reach logs, spans or metric labels. Logs carry counts and
the request ID, and no per-rule breakdown: the captions rule would reveal an access need."""

import logging
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from functools import lru_cache
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from elasticsearch import Elasticsearch
from fastapi import APIRouter, Depends, HTTPException
from openai import OpenAI
from pydantic import Field

from conferenceops.indexing import ALIAS, Embedder, OpenAIEmbedder
from conferenceops.personas import demo_profile_id
from conferenceops.search import CANDIDATES, TEXT_FIELDS, ApiModel, rrf_scores, session_filters, venue_time
from conferenceops.settings import settings
from conferenceops.where_next_data import (
    Event,
    PastSession,
    Persona,
    PlannedSession,
    PostgresWhereNextData,
    PublishedFact,
    SessionRow,
    WhereNextData,
)

log = logging.getLogger("conferenceops.where_next")

# The ranking configuration (PLAN §8.3). These numbers live here and nowhere else.
EVENT_SLUG = "kubecon-na-2026"  # the current event, on the real wall clock
RELEVANCE_WEIGHT = 0.765
INTEREST_WEIGHT = 0.235
# Cosine similarity of two stored talk embeddings (text-embedding-3-small). Measured on Nov 9 against the KubeCon index
# with the CloudBridge talks: talks on the same subject scored 0.55-0.70, loosely related ones 0.42 or less.
SIMILARITY_MIN = 0.5
MAX_HISTORY = 12  # the most recent history sessions, so a long history can't fan one request out
SESSION_FACT_KINDS = ("captions", "reserved_seating")  # published facts that apply to every session
CAPTIONS = "captions"
STAFF_HELP = "staff_help"
STEP_FREE = "step_free_route"

LevelName = Literal["beginner", "intermediate", "advanced", "all"]
Window = Literal["today", "tomorrow_morning", "any"]
ReasonCode = Literal["INTEREST_MATCH", "HISTORY_MATCH", "BOOKMARK_MATCH", "CAPTIONS_PUBLISHED"]
Exclusion = Literal[
    "STARTED",
    "OUTSIDE_WINDOW",
    "ON_AGENDA",
    "AGENDA_CONFLICT",
    "LEVEL_EXCLUDED",
    "CAPTIONS_NOT_PUBLISHED",
    "NO_MATCH",
]


class RecommendationFilters(ApiModel):
    excluded_levels: list[LevelName] = Field(
        default_factory=list,
        description="Levels to leave out, on top of the persona's saved exclusions. A request adds exclusions; it "
        "never removes a saved one.",
    )


class RecommendationRequest(ApiModel):
    query: str | None = Field(
        default=None,
        max_length=300,
        description="Optional words that refine the ranking among the talks that match the persona's interests or "
        "history. Never logged (PRIV-1).",
    )
    filters: RecommendationFilters = Field(default_factory=RecommendationFilters)
    window: Window = Field(
        default="any",
        description="today: the rest of the event's current day. tomorrow_morning: the event's next day until noon. "
        "any: every session that hasn't started. Days are in the event's time zone (TIME-1).",
    )
    limit: int = Field(default=3, ge=1, le=5)


class AccessStatement(ApiModel):
    kind: str = Field(description="captions, reserved_seating, or step_free_route")
    status: Literal["published", "not_published"]
    text: str = Field(description="The event's published wording, or why it isn't published")
    location: str | None = None
    source_url: str | None = Field(default=None, description="Where the event publishes it (ACCESS-1)")


class Reason(ApiModel):
    code: ReasonCode
    text: str
    interests: list[str] = Field(default_factory=list, description="INTEREST_MATCH: the interests the talk mentions")
    related_session_id: str | None = Field(default=None, description="HISTORY_MATCH, BOOKMARK_MATCH: the past talk")
    similarity: float | None = Field(default=None, description="Cosine similarity of the two talks' embeddings")


class Candidate(ApiModel):
    session_id: str
    title: str
    event: str
    conference: str
    is_fictional: bool
    starts_at: datetime
    ends_at: datetime
    starts_at_venue: str = Field(description="Start in the event's time zone, e.g. 'Tue Nov 10, 10:30 AM MST'")
    room: str | None = None
    level: str | None = None
    eligibility: Literal["confirmed", "unconfirmed"] = Field(
        description="confirmed: every access need the persona set is backed by a published fact. unconfirmed: one "
        "can't be confirmed from published information (step-free routes never can); `access` says which."
    )
    score: float
    source_revision: int
    access: list[AccessStatement]
    reasons: list[Reason]
    source_ids: list[str]


class Notice(ApiModel):
    code: Literal["STEP_FREE_NOT_PUBLISHED", "NO_SIGNALS"]
    text: str
    source_url: str | None = None


class RecommendationResponse(ApiModel):
    request_id: str
    status: Literal["matches", "no_fit"]
    retrieval_mode: Literal["hybrid", "lexical"] = Field(
        description="lexical: the interests and query were matched by keywords only, because the embedding model "
        "didn't answer (AI-1). History matching uses stored vectors and needs no model call."
    )
    conference_time: datetime = Field(description="Now on the event's clock (UTC)")
    conference_time_venue: str
    observed_at: datetime = Field(description="The actual wall-clock time (UTC)")
    window: Window
    candidates: list[Candidate]
    excluded: dict[str, int] = Field(
        description="How many retrieved talks each hard rule removed: STARTED, OUTSIDE_WINDOW, ON_AGENDA, "
        "AGENDA_CONFLICT, LEVEL_EXCLUDED, CAPTIONS_NOT_PUBLISHED; NO_MATCH counts talks with no interest or history "
        "reason. Counts only, never another attendee's data."
    )
    warnings: list[Notice]


def window_bounds(window: Window, now: datetime, zone: ZoneInfo) -> tuple[datetime, datetime | None]:
    """The window's [start, end) as UTC instants. Days come from the event's IANA zone, so a daylight-saving change
    moves the UTC bounds with it (TIME-1)."""
    if window == "any":
        return now, None
    today = now.astimezone(zone).date()
    if window == "today":
        return now, datetime.combine(today + timedelta(days=1), time(0), tzinfo=zone).astimezone(UTC)
    tomorrow = today + timedelta(days=1)
    start = datetime.combine(tomorrow, time(0), tzinfo=zone).astimezone(UTC)
    end = datetime.combine(tomorrow, time(12), tzinfo=zone).astimezone(UTC)
    return max(now, start), end


def time_filters(now: datetime, start: datetime, end: datetime | None) -> list[dict]:
    """Not started by now (strictly after), and inside the window."""
    window: dict[str, str] = {"gte": start.isoformat()}
    if end is not None:
        window["lt"] = end.isoformat()
    return [{"range": {"starts_at": {"gt": now.isoformat()}}}, {"range": {"starts_at": window}}]


_WORD = re.compile(r"\w+")


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.casefold()))


def matched_interests(interests: Sequence[str], row: SessionRow) -> list[str]:
    """The interests whose every word appears in the talk's title, abstract or topics: a reader can check each one."""
    words = _words(" ".join([row.title, row.abstract or "", *row.topics]))
    return [interest for interest in interests if (needed := _words(interest)) and needed <= words]


def history_of(persona: Persona, event: Event) -> list[PastSession]:
    """What she attended, then what she bookmarked at other events, each by start time; at most the last
    MAX_HISTORY. A talk both attended and bookmarked counts once, as attended."""
    seen: dict[str, PastSession] = {past.session_id: past for past in persona.attended}
    for item in persona.planned:
        if item.conference_id != event.id and item.session_id not in seen:
            seen[item.session_id] = PastSession(
                session_id=item.session_id, title=item.title, event=item.event, kind="bookmarked"
            )
    return list(seen.values())[-MAX_HISTORY:]


def agenda_on(persona: Persona, event: Event) -> list[PlannedSession]:
    return [item for item in persona.planned if item.conference_id == event.id]


def _fact(event: Event, kind: str) -> PublishedFact | None:
    return next((fact for fact in event.facts if fact.kind == kind), None)


def step_free_statement(event: Event) -> AccessStatement:
    """Routes are never published, so step-free is never claimed: point to the event's staff instead (ACCESS-1)."""
    staff = _fact(event, STAFF_HELP)
    pointer = staff.text if staff else "Ask the event's staff."
    return AccessStatement(
        kind=STEP_FREE,
        status="not_published",
        text=f"Step-free routes aren't published for this event. {pointer}",
        source_url=staff.source_url if staff else None,
    )


def access_for(event: Event, persona: Persona) -> tuple[list[AccessStatement], list[str]]:
    """The published facts that apply to every session, then, for a persona who needs them, the statement that
    step-free routes aren't published. Returns the statements and the IDs of the facts they quote."""
    facts = [fact for fact in event.facts if fact.kind in SESSION_FACT_KINDS]
    statements = [
        AccessStatement(
            kind=fact.kind, status="published", text=fact.text, location=fact.location, source_url=fact.source_url
        )
        for fact in facts
    ]
    ids = [fact.id for fact in facts]
    if persona.needs_step_free:
        statements.append(step_free_statement(event))
        staff = _fact(event, STAFF_HELP)
        if staff:
            ids.append(staff.id)
    return statements, ids


def exclusion(
    row: SessionRow,
    now: datetime,
    start: datetime,
    end: datetime | None,
    agenda: Sequence[PlannedSession],
    excluded_levels: frozenset[str],
    captions_missing: bool,
) -> Exclusion | None:
    """The first hard rule the talk breaks, or None (PLAN §8.3)."""
    if row.starts_at <= now:
        return "STARTED"
    if row.starts_at < start or (end is not None and row.starts_at >= end):
        return "OUTSIDE_WINDOW"
    if any(item.session_id == row.session_id for item in agenda):
        return "ON_AGENDA"
    if any(row.starts_at < item.ends_at and item.starts_at < row.ends_at for item in agenda):
        return "AGENDA_CONFLICT"
    if row.level is not None and row.level in excluded_levels:
        return "LEVEL_EXCLUDED"
    if captions_missing:
        return "CAPTIONS_NOT_PUBLISHED"
    return None


def _most_similar(
    row: SessionRow, history: Sequence[PastSession], similarity: dict[str, dict[str, float]]
) -> tuple[PastSession, float] | None:
    scored = [
        (past, cos)
        for past in history
        if (cos := similarity.get(past.session_id, {}).get(row.session_id)) is not None and cos >= SIMILARITY_MIN
    ]
    if not scored:
        return None
    return min(scored, key=lambda pair: (-pair[1], pair[0].title, pair[0].session_id))


def reasons_for(
    row: SessionRow,
    persona: Persona,
    history: Sequence[PastSession],
    similarity: dict[str, dict[str, float]],
    captions: PublishedFact | None,
) -> list[Reason]:
    reasons: list[Reason] = []
    matched = matched_interests(persona.interests, row)
    if matched:
        noun = "interest" if len(matched) == 1 else "interests"
        reasons.append(
            Reason(code="INTEREST_MATCH", text=f"Mentions your {noun}: {', '.join(matched)}", interests=matched)
        )
    for kind, code in (("attended", "HISTORY_MATCH"), ("bookmarked", "BOOKMARK_MATCH")):
        best = _most_similar(row, [past for past in history if past.kind == kind], similarity)
        if best:
            past, cos = best
            reasons.append(
                Reason(
                    code=code,
                    text=f"Similar to “{past.title}”, which you {kind} at {past.event}",
                    related_session_id=past.session_id,
                    similarity=round(cos, 3),
                )
            )
    if reasons and persona.wants_captions and captions is not None:
        reasons.append(
            Reason(code="CAPTIONS_PUBLISHED", text="You asked for captions, and the event publishes captioning")
        )
    return reasons


def decide(
    *,
    request: RecommendationRequest,
    persona: Persona,
    event: Event,
    now: datetime,
    rows: dict[str, SessionRow],
    relevance: dict[str, float],
    similarity: dict[str, dict[str, float]],
    history: Sequence[PastSession],
    mode: Literal["hybrid", "lexical"],
    request_id: str,
) -> RecommendationResponse:
    """The hard rules, the reasons and the ranking, on canonical rows only. Deterministic: the same inputs always give
    the same answer."""
    zone = ZoneInfo(event.timezone)
    start, end = window_bounds(request.window, now, zone)
    agenda = agenda_on(persona, event)
    excluded_levels = persona.excluded_levels | frozenset(request.filters.excluded_levels)
    captions = _fact(event, CAPTIONS)
    captions_missing = persona.wants_captions and captions is None
    access, fact_ids = access_for(event, persona)
    eligibility: Literal["confirmed", "unconfirmed"] = "unconfirmed" if persona.needs_step_free else "confirmed"

    excluded: dict[str, int] = {}
    qualified: list[tuple[SessionRow, list[Reason]]] = []
    for session_id in sorted(rows):
        row = rows[session_id]
        if session_id not in relevance:
            continue
        rule = exclusion(row, now, start, end, agenda, excluded_levels, captions_missing)
        reasons = reasons_for(row, persona, history, similarity, captions) if rule is None else []
        if rule is None and not reasons:
            rule = "NO_MATCH"
        if rule is not None:
            excluded[rule] = excluded.get(rule, 0) + 1
            continue
        qualified.append((row, reasons))

    candidates: list[Candidate] = []
    if qualified:
        best = max(relevance[row.session_id] for row, _ in qualified)
        for row, reasons in qualified:
            matched = next((reason.interests for reason in reasons if reason.code == "INTEREST_MATCH"), [])
            overlap = len(matched) / len(persona.interests) if persona.interests else 0.0
            score = round(RELEVANCE_WEIGHT * relevance[row.session_id] / best + INTEREST_WEIGHT * overlap, 4)
            candidates.append(
                Candidate(
                    session_id=row.session_id,
                    title=row.title,
                    event=event.name,
                    conference=event.slug,
                    is_fictional=event.is_fictional,
                    starts_at=row.starts_at,
                    ends_at=row.ends_at,
                    starts_at_venue=venue_time(row.starts_at, zone),
                    room=row.room,
                    level=row.level,
                    eligibility=eligibility,
                    score=score,
                    source_revision=row.revision,
                    access=access,
                    reasons=reasons,
                    source_ids=[f"session:{row.session_id}:{row.revision}", *(f"access_fact:{i}" for i in fact_ids)],
                )
            )
        candidates.sort(key=lambda c: (-c.score, c.starts_at, c.session_id))
        candidates = candidates[: request.limit]

    warnings: list[Notice] = []
    if persona.needs_step_free:
        statement = step_free_statement(event)
        warnings.append(Notice(code="STEP_FREE_NOT_PUBLISHED", text=statement.text, source_url=statement.source_url))
    return RecommendationResponse(
        request_id=request_id,
        status="matches" if candidates else "no_fit",
        retrieval_mode=mode,
        conference_time=now,
        conference_time_venue=venue_time(now, zone),
        observed_at=now,
        window=request.window,
        candidates=candidates,
        excluded=excluded,
        warnings=warnings,
    )


@dataclass
class Retrieved:
    relevance: dict[str, float]  # session ID -> fused RRF score
    similarity: dict[str, dict[str, float]]  # history session ID -> retrieved session ID -> cosine similarity
    mode: Literal["hybrid", "lexical"]


class Retriever:
    """Elasticsearch channels with identical filters, fused with RRF (PLAN §8.2)."""

    def __init__(self, es: Any, embedder: Embedder):
        self.es = es
        self.embedder = embedder

    def _hits(self, **body: Any) -> list[dict]:
        return self.es.search(index=ALIAS, **body)["hits"]["hits"]

    def _bm25(self, text: str, filters: list[dict]) -> list[str]:
        query = {"bool": {"must": {"multi_match": {"query": text, "fields": TEXT_FIELDS}}, "filter": filters}}
        return [hit["_id"] for hit in self._hits(query=query, size=CANDIDATES, source=False)]

    def _knn(self, vector: list[float], filters: list[dict]) -> list[dict]:
        knn = {
            "field": "embedding",
            "query_vector": vector,
            "k": CANDIDATES,
            "num_candidates": CANDIDATES * 4,
            "filter": filters,
        }
        return self._hits(knn=knn, size=CANDIDATES, source=False)

    def _stored_vectors(self, ids: Sequence[str]) -> dict[str, list[float]]:
        """The stored embeddings of past sessions. Elasticsearch 9 leaves vectors out of `_source` unless asked."""
        if not ids:
            return {}
        hits = self._hits(
            query={"ids": {"values": list(ids)}},
            size=len(ids),
            source={"includes": ["embedding"], "exclude_vectors": False},
        )
        found = {hit["_id"]: (hit.get("_source") or {}).get("embedding") for hit in hits}
        return {i: found[i] for i in ids if found.get(i)}

    def retrieve(self, texts: Sequence[str], history_ids: Sequence[str], filters: list[dict]) -> Retrieved:
        rankings = [self._bm25(text, filters) for text in texts]
        mode: Literal["hybrid", "lexical"] = "hybrid"
        if texts:
            try:
                vectors = self.embedder.embed(list(texts))
            except Exception as exc:  # noqa: BLE001 - fail open to keywords (AI-1)
                # PRIV-1: the error's type only; never the text that was embedded.
                log.warning("embedding failed (%s); matching interests by keywords", type(exc).__name__)
                vectors = []
                mode = "lexical"
            rankings += [[hit["_id"] for hit in self._knn(vector, filters)] for vector in vectors]
        similarity: dict[str, dict[str, float]] = {}
        for past_id, vector in self._stored_vectors(history_ids).items():
            hits = self._knn(vector, filters)
            rankings.append([hit["_id"] for hit in hits])
            # Elasticsearch scores cosine similarity as (1 + cosine) / 2.
            similarity[past_id] = {hit["_id"]: 2 * hit["_score"] - 1 for hit in hits}
        return Retrieved(relevance=rrf_scores(rankings), similarity=similarity, mode=mode)


class WhereNext:
    def __init__(
        self,
        data: WhereNextData,
        es: Any,
        embedder: Embedder,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        event_slug: str = EVENT_SLUG,
    ):
        self.data = data
        self.retriever = Retriever(es, embedder)
        self.clock = clock
        self.event_slug = event_slug

    def recommend(self, profile_id: uuid.UUID, request: RecommendationRequest) -> RecommendationResponse:
        persona = self.data.persona(profile_id)
        if persona is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        event = self.data.event(self.event_slug)
        if event is None:
            raise HTTPException(status_code=503, detail="The current event's schedule isn't imported yet")
        request_id = str(uuid.uuid4())
        now = self.clock()  # the event's clock: KubeCon runs on the real wall clock (TIME-1)
        history = history_of(persona, event)
        texts = [text for text in ("; ".join(persona.interests), (request.query or "").strip()) if text]
        if not texts and not history:
            response = decide(
                request=request,
                persona=persona,
                event=event,
                now=now,
                rows={},
                relevance={},
                similarity={},
                history=[],
                mode="hybrid",
                request_id=request_id,
            )
            response.warnings.append(
                Notice(
                    code="NO_SIGNALS",
                    text="There's nothing to go on yet: no stated interests and no history. Try searching instead.",
                )
            )
            return response
        start, end = window_bounds(request.window, now, ZoneInfo(event.timezone))
        filters = [*session_filters(event.slug), *time_filters(now, start, end)]
        retrieved = self.retriever.retrieve(texts, [past.session_id for past in history], filters)
        rows = self.data.sessions(sorted(retrieved.relevance), event.id)
        response = decide(
            request=request,
            persona=persona,
            event=event,
            now=now,
            rows=rows,
            relevance=retrieved.relevance,
            similarity=retrieved.similarity,
            history=history,
            mode=retrieved.mode,
            request_id=request_id,
        )
        # PRIV-1: counts and the request ID only.
        log.info(
            "where-next %s: %d suggestions from %d retrieved, %s retrieval",
            request_id,
            len(response.candidates),
            len(rows),
            response.retrieval_mode,
        )
        return response


@lru_cache
def get_where_next() -> WhereNext:
    cfg = settings()
    if not cfg.elasticsearch_url:
        raise HTTPException(status_code=503, detail="Suggestions aren't configured")
    auth = (cfg.elasticsearch_username, cfg.elasticsearch_password) if cfg.elasticsearch_username else None
    es = Elasticsearch(cfg.elasticsearch_url, basic_auth=auth, request_timeout=5)
    # A short timeout: a slow model must not hold the answer hostage; interests fall back to keywords (AI-1).
    client = OpenAI(api_key=cfg.openai_api_key or "missing", timeout=3, max_retries=0)
    return WhereNext(PostgresWhereNextData(), es, OpenAIEmbedder(client, cfg.openai_embedding_model))


router = APIRouter(tags=["recommendations"])


@router.post("/api/v1/recommendations/next", response_model=RecommendationResponse, response_model_by_alias=True)
def where_next(
    request: RecommendationRequest,
    profile_id: Annotated[uuid.UUID, Depends(demo_profile_id)],
    service: Annotated[WhereNext, Depends(get_where_next)],
) -> RecommendationResponse:
    """Where should I go next? Talks that haven't started, that pass the persona's hard rules, ranked by her stated
    interests and history, each with checkable reasons and the published access facts. The persona comes from
    X-Demo-Profile-ID (AUTHZ-1); nothing she typed or set is logged (PRIV-1)."""
    try:
        return service.recommend(profile_id, request)
    except HTTPException:
        raise
    except Exception as exc:
        # PRIV-1: the error's type only. An error message can quote the request (an Elasticsearch query, say).
        log.error("where-next failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Suggestions aren't available right now") from exc
