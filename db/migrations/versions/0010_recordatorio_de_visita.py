"""Recordatorio de la visita el día anterior (B-13): `visit_reminder` como clase de *job*.

Revision ID: 0010
Revises: 0009
"""
from alembic import op

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint('ck_scheduled_jobs_kind', 'scheduled_jobs', type_='check')
    op.create_check_constraint('ck_scheduled_jobs_kind', 'scheduled_jobs', "kind IN ('no_response', 'visit_reminder')")


def downgrade() -> None:
    op.execute("DELETE FROM scheduled_jobs WHERE kind = 'visit_reminder'")
    op.drop_constraint('ck_scheduled_jobs_kind', 'scheduled_jobs', type_='check')
    op.create_check_constraint('ck_scheduled_jobs_kind', 'scheduled_jobs', "kind IN ('no_response')")
