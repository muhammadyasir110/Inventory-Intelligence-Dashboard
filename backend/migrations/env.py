"""Alembic environment. Uses the application's engine configuration
(DATABASE_URL) and the ORM metadata, so migrations and models cannot drift
apart unnoticed (tests/test_migrations.py runs `compare_metadata`)."""
import os
import sys
from logging.config import fileConfig

from alembic import context

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.db import Base, engine  # noqa: E402
from models import models  # noqa: E402,F401  (registers tables)

config = context.config
if config.config_file_name is not None and not config.attributes.get("skip_logging"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=str(engine.url), target_metadata=target_metadata,
                      literal_binds=True, render_as_batch=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is not None:
        _run(connectable)
        return
    with engine.connect() as connection:
        _run(connection)


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=connection.dialect.name == "sqlite",
                      compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
