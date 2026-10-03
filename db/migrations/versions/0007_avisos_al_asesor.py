"""Avisos al asesor en el panel (B-15).

Hasta ahora la tarjeta del lead calificado (B-7) y la ficha de traspaso (A-12) solo existían como
mensajes de Telegram: un asesor sin chat registrado no se enteraba de nada. `advisor_notifications`
guarda cada aviso para que el panel lo muestre; Telegram queda como canal adicional.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'advisor_notifications',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('advisor_id', sa.String(), nullable=False),
        sa.Column('lead_id', sa.UUID(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('calificado', 'traspaso')", name='ck_advisor_notifications_kind'),
        sa.ForeignKeyConstraint(['advisor_id'], ['advisors.id'], ondelete='CASCADE',
                                name=op.f('fk_advisor_notifications_advisor_id_advisors')),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE',
                                name=op.f('fk_advisor_notifications_lead_id_leads')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_advisor_notifications')),
    )
    op.create_index('ix_advisor_notifications_advisor_created', 'advisor_notifications',
                    ['advisor_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_advisor_notifications_advisor_created', table_name='advisor_notifications')
    op.drop_table('advisor_notifications')
