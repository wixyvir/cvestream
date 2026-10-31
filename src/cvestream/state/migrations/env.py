"""Alembic environment, configured in code (no alembic.ini).

`State.migrate()` passes its connection through `config.attributes`; offline
migrations are not supported.
"""

from alembic import context

from cvestream.state import schema
from cvestream.state.schema import StateError


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:
        raise StateError("migrations run through State.migrate()")
    context.configure(
        connection=connection,
        target_metadata=schema.metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise StateError("offline migrations are not supported")
run_migrations_online()
