"""Alertas de propiedades nuevas (A-18, DT-45).

`property_alerts` guarda los filtros de la búsqueda por la que el lead pidió "avísame si sale algo así",
sin texto del lead. Dos clases de *job* nuevas: `alert_scan` (busca coincidencias) y `property_alert`
(le escribe al lead, una vez por propiedad).

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None

_KINDS_BEFORE = "kind IN ('no_response', 'visit_reminder')"
_KINDS_AFTER = "kind IN ('no_response', 'visit_reminder', 'alert_scan', 'property_alert')"


def upgrade() -> None:
    op.create_table(
        'property_alerts',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('lead_id', sa.UUID(), nullable=False),
        sa.Column('conversation_id', sa.UUID(), nullable=False),
        sa.Column('zone', sa.String(), nullable=True),
        sa.Column('type', sa.String(), nullable=True),
        sa.Column('bedrooms', sa.Integer(), nullable=True),
        sa.Column('budget_min', sa.Numeric(14, 2), nullable=True),
        sa.Column('budget_max', sa.Numeric(14, 2), nullable=True),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("type IS NULL OR type IN ('venta', 'renta', 'preventa')", name='ck_property_alerts_type'),
        sa.CheckConstraint('bedrooms IS NULL OR bedrooms >= 0', name='ck_property_alerts_bedrooms'),
        sa.CheckConstraint('budget_min IS NULL OR budget_min >= 0', name='ck_property_alerts_budget_min'),
        sa.CheckConstraint('budget_max IS NULL OR budget_max >= 0', name='ck_property_alerts_budget_max'),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE',
                                name=op.f('fk_property_alerts_lead_id_leads')),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE',
                                name=op.f('fk_property_alerts_conversation_id_conversations')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_property_alerts')),
    )
    op.create_index('uq_property_alerts_active_lead', 'property_alerts', ['lead_id'], unique=True,
                    postgresql_where=sa.text('active'))
    op.drop_constraint('ck_scheduled_jobs_kind', 'scheduled_jobs', type_='check')
    op.create_check_constraint('ck_scheduled_jobs_kind', 'scheduled_jobs', _KINDS_AFTER)


def downgrade() -> None:
    op.execute("DELETE FROM scheduled_jobs WHERE kind IN ('alert_scan', 'property_alert')")
    op.drop_constraint('ck_scheduled_jobs_kind', 'scheduled_jobs', type_='check')
    op.create_check_constraint('ck_scheduled_jobs_kind', 'scheduled_jobs', _KINDS_BEFORE)
    op.drop_index('uq_property_alerts_active_lead', table_name='property_alerts')
    op.drop_table('property_alerts')
