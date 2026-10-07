"""Journal extraction directories and their atomic publication boundary."""

import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'publications',
        sa.Column('job_id', sa.String(36), sa.ForeignKey('jobs.id'), primary_key=True),
        sa.Column('parent', sa.String, nullable=False),
        sa.Column('parent_device', sa.Integer, nullable=False),
        sa.Column('parent_inode', sa.Integer, nullable=False),
        sa.Column('staging', sa.String, nullable=False),
        sa.Column('destination', sa.String, nullable=False),
        sa.Column('device', sa.Integer, nullable=False),
        sa.Column('inode', sa.Integer, nullable=False),
        sa.Column('phase', sa.String, nullable=False),
    )


def downgrade() -> None:
    op.drop_table('publications')
