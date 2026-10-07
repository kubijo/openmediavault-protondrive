"""Create durable jobs and their ordered event history."""

import sqlalchemy as sa
from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'jobs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('actor', sa.String(128), nullable=False),
        sa.Column('operation', sa.Integer, nullable=False),
        sa.Column('state', sa.Integer, nullable=False),
        sa.Column('message', sa.String, nullable=False),
        sa.Column('sequence', sa.Integer, nullable=False),
        sa.Column('created_at', sa.String, nullable=False),
    )
    op.create_table(
        'job_events',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('job_id', sa.String(36), sa.ForeignKey('jobs.id'), nullable=False),
        sa.Column('operation', sa.Integer, nullable=False),
        sa.Column('state', sa.Integer, nullable=False),
        sa.Column('message', sa.String, nullable=False),
        sa.Column('sequence', sa.Integer, nullable=False),
        sa.UniqueConstraint('job_id', 'sequence'),
    )


def downgrade() -> None:
    op.drop_table('job_events')
    op.drop_table('jobs')
