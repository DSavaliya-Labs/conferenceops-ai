"""The demo personas the profile switcher offers, and how a request says which one it acts as (AUTHZ-1).

The browser sends the selected persona's ID in `X-Demo-Profile-ID`; the server looks the persona up in PostgreSQL
and never trusts a role or anything else the client claims. This is a demonstration mechanism, not authentication:
anyone can pick any persona (PLAN §1.3).

The listing carries public profile fields only. Private preferences live in `profile_preferences` and never leave
the server through this module (PRIV-1)."""

import uuid
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import select

from conferenceops.db import transaction
from conferenceops.models import Profile

DEMO_PROFILE_HEADER = "X-Demo-Profile-ID"


class PersonaCard(BaseModel):
    """A selectable persona: public fields only (PRIV-1)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: str
    display_name: str
    role: str
    company: str | None = None
    interests: list[str] = Field(default_factory=list, description="Public professional interests")
    is_fictional: bool


class PersonaList(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    personas: list[PersonaCard]


class PersonaDirectory(Protocol):
    def selectable(self) -> list[PersonaCard]: ...


class PostgresPersonaDirectory:
    def selectable(self) -> list[PersonaCard]:
        statement = select(Profile).where(Profile.demo_selectable.is_(True)).order_by(Profile.display_name, Profile.id)
        with transaction() as db:
            rows = db.scalars(statement).all()
        return [
            PersonaCard(
                id=str(row.id),
                display_name=row.display_name,
                role=row.role.value,
                company=row.company,
                interests=list(row.interests or []),
                is_fictional=row.is_fictional,
            )
            for row in rows
        ]


def get_persona_directory() -> PersonaDirectory:
    return PostgresPersonaDirectory()


def demo_profile_id(
    x_demo_profile_id: Annotated[str | None, Header(alias=DEMO_PROFILE_HEADER)] = None,
) -> uuid.UUID:
    """The persona a request acts as. Missing: 400. Not a UUID: 404, like any profile that doesn't exist; the caller
    still checks that the ID belongs to a selectable persona (AUTHZ-1)."""
    if not x_demo_profile_id or not x_demo_profile_id.strip():
        raise HTTPException(status_code=400, detail=f"Choose a persona first: send {DEMO_PROFILE_HEADER}")
    try:
        return uuid.UUID(x_demo_profile_id.strip())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Profile not found") from exc


router = APIRouter(tags=["personas"])


@router.get("/api/v1/demo/profiles", response_model=PersonaList, response_model_by_alias=True)
def list_demo_profiles(directory: Annotated[PersonaDirectory, Depends(get_persona_directory)]) -> PersonaList:
    """The personas the profile switcher offers. Public fields only: never preferences or access needs (PRIV-1)."""
    try:
        return PersonaList(personas=directory.selectable())
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Personas aren't available right now") from exc
