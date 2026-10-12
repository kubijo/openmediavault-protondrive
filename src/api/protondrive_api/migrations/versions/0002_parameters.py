"""Persist typed operation parameters for idempotency and recovery."""

import sqlalchemy as sa
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('parameters', sa.LargeBinary, nullable=True))


def downgrade() -> None:
    op.drop_column('jobs', 'parameters')
