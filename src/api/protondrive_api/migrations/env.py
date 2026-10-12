"""Run packaged migrations on the broker's already-open transaction."""

from typing import cast

from alembic import context
from sqlalchemy.engine import Connection

from protondrive_api.store import Base

connection = cast(object, context.config.attributes['connection'])
if not isinstance(connection, Connection):
    raise TypeError('Migrations require the broker transaction')
context.configure(connection=connection, target_metadata=Base.metadata)
with context.begin_transaction():
    context.run_migrations()
