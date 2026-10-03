"""Reporte semanal al gerente (N-10, DT-47): `weekly_report_plan` y `weekly_report` como clases de *job*.

Revision ID: 0013
Revises: 0012
"""
from alembic import op

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None

_KINDS_BEFORE = "kind IN ('no_response', 'visit_reminder', 'alert_scan', 'property_alert')"
_KINDS_AFTER = ("kind IN ('no_response', 'visit_reminder', 'alert_scan', 'property_alert', "
                "'weekly_report_plan', 'weekly_report')")


def upgrade() -> None:
    op.drop_constraint('ck_scheduled_jobs_kind', 'scheduled_jobs', type_='check')
    op.create_check_constraint('ck_scheduled_jobs_kind', 'scheduled_jobs', _KINDS_AFTER)


def downgrade() -> None:
    op.execute("DELETE FROM scheduled_jobs WHERE kind IN ('weekly_report_plan', 'weekly_report')")
    op.drop_constraint('ck_scheduled_jobs_kind', 'scheduled_jobs', type_='check')
    op.create_check_constraint('ck_scheduled_jobs_kind', 'scheduled_jobs', _KINDS_BEFORE)
