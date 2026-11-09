"""Alembic environment. The URL comes from DATABASE_URL, never from a file in the repository (SEC-1)."""

from alembic import context
from sqlalchemy import create_engine, pool

from conferenceops.models import Base
from conferenceops.settings import settings

target_metadata = Base.metadata


def database_url() -> str:
    url = settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def run_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connectable = create_engine(database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
