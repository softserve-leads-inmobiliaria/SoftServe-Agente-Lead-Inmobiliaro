"""Racha de turnos sin resolver por conversación (A-13): proxy de frustración.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('fsm_context', sa.Column('unresolved_streak', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.create_check_constraint(op.f('ck_fsm_context_unresolved_streak_non_negative'), 'fsm_context',
                               'unresolved_streak >= 0')


def downgrade() -> None:
    op.drop_constraint(op.f('ck_fsm_context_unresolved_streak_non_negative'), 'fsm_context', type_='check')
    op.drop_column('fsm_context', 'unresolved_streak')
