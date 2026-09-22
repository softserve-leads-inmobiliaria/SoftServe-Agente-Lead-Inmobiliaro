"""Cuándo un lead cruzó a calificado (#89).

Nueva guarda de idempotencia para `StateStore.qualify()`, independiente de `leads.status`: B-6
(#85) puede mover `status` a `sql` (lead reclamado a mano) antes de que el score llegue a cruzar a
WARM/HOT, y la guarda anterior (`WHERE status = 'nuevo'`) dejaba de disparar para siempre en ese
caso — ni notificación al asesor (B-7) ni `lead_qualified` para A-6.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('qualified_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('leads', 'qualified_at')
