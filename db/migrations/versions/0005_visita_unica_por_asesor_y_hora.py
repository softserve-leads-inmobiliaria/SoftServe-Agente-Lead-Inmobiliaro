"""Una sola visita por asesor y hora (C-2).

`book` revisa que el horario esté libre antes de insertar, pero dos reservas simultáneas pueden pasar
esa revisión a la vez. La restricción deja que la base decida: la segunda inserción falla y `book`
responde SLOT_TAKEN en lugar de agendar dos visitas al mismo asesor a la misma hora.

Revision ID: 0005
Revises: 0004
"""
from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint('uq_visits_advisor_id_scheduled_at', 'visits', ['advisor_id', 'scheduled_at'])


def downgrade() -> None:
    op.drop_constraint('uq_visits_advisor_id_scheduled_at', 'visits', type_='unique')
