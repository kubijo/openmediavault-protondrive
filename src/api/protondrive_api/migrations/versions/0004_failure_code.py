"""Persist failure categories alongside job state and replay events."""

import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('failure_code', sa.String(32), nullable=False, server_default=''))
    op.add_column('job_events', sa.Column('failure_code', sa.String(32), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('job_events', 'failure_code')
    op.drop_column('jobs', 'failure_code')
