"""Tareas diferidas (A-17, DT-44).

`scheduled_jobs` guarda lo que el *scheduler* de `core` tiene que hacer sin que el lead escriba: por
ahora, los seguimientos y las aristas `no_response` de la FSM (`DISCOVERY -> NURTURE -> LOST`).

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'scheduled_jobs',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('dedupe_key', sa.String(), nullable=False),
        sa.Column('lead_id', sa.UUID(), nullable=True),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"),
                  nullable=False),
        sa.Column('run_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(), server_default='pending', nullable=False),
        sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('no_response')", name='ck_scheduled_jobs_kind'),
        sa.CheckConstraint("status IN ('pending', 'done', 'failed', 'cancelled')", name='ck_scheduled_jobs_status'),
        sa.CheckConstraint('attempts >= 0', name='ck_scheduled_jobs_attempts'),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE',
                                name=op.f('fk_scheduled_jobs_lead_id_leads')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_scheduled_jobs')),
    )
    op.create_index(op.f('ix_scheduled_jobs_lead_id'), 'scheduled_jobs', ['lead_id'])
    op.create_index('ix_scheduled_jobs_due', 'scheduled_jobs', ['run_at'],
                    postgresql_where=sa.text("status = 'pending'"))
    op.create_index('uq_scheduled_jobs_pending_key', 'scheduled_jobs', ['kind', 'dedupe_key'], unique=True,
                    postgresql_where=sa.text("status = 'pending'"))


def downgrade() -> None:
    op.drop_index('uq_scheduled_jobs_pending_key', table_name='scheduled_jobs')
    op.drop_index('ix_scheduled_jobs_due', table_name='scheduled_jobs')
    op.drop_index(op.f('ix_scheduled_jobs_lead_id'), table_name='scheduled_jobs')
    op.drop_table('scheduled_jobs')
