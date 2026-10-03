"""Resumen de la conversación para el asesor (A-16).

`leads.handoff_summary` guarda el resumen del último traspaso del lead (`HANDOFF` o `HUMAN_HANDOFF`): qué
busca, sus objeciones y el siguiente paso sugerido. Null si no hubo traspaso o si no se pudo generar.
Va en `leads` y no en `conversations` porque `conversations` es la proyección congelada del contrato
`fsm_state.schema.json#/$defs/Conversation` (CT-3).

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('handoff_summary', sa.Text(), nullable=True))
    op.add_column('leads', sa.Column('handoff_summary_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('leads', 'handoff_summary_at')
    op.drop_column('leads', 'handoff_summary')
