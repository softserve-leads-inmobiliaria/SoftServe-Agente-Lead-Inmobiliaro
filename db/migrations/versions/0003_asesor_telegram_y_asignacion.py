"""Chat de Telegram del asesor y asignación lead->asesor (B-7).

`advisors.telegram_chat_id` es a dónde llega la tarjeta del lead calificado; se completa cuando el
asesor abre el bot con su deep-link. `leads.advisor_id` deja escrito quién recibió cada lead: sin
esto la cola por asesor (B-6) y el corte por asesor del dashboard (B-9) serían ambiguos cuando dos
asesores comparten zona.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('advisors', sa.Column('telegram_chat_id', sa.String(), nullable=True))
    op.create_unique_constraint(op.f('uq_advisors_telegram_chat_id'), 'advisors', ['telegram_chat_id'])

    op.add_column('leads', sa.Column('advisor_id', sa.String(), nullable=True))
    op.create_foreign_key(op.f('fk_leads_advisor_id_advisors'), 'leads', 'advisors',
                          ['advisor_id'], ['id'])
    op.create_index(op.f('ix_leads_advisor_id'), 'leads', ['advisor_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_leads_advisor_id'), table_name='leads')
    op.drop_constraint(op.f('fk_leads_advisor_id_advisors'), 'leads', type_='foreignkey')
    op.drop_column('leads', 'advisor_id')

    op.drop_constraint(op.f('uq_advisors_telegram_chat_id'), 'advisors', type_='unique')
    op.drop_column('advisors', 'telegram_chat_id')
