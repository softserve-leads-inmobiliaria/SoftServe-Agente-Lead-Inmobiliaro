"""Esquema de inteligencia comercial (N-1).

Cuatro tablas nuevas: `property_status_events` (historial de estados de cada propiedad), `deals`
(operaciones cerradas), `property_interest` (qué propiedades tocó cada lead) y `unmet_searches`
(búsquedas sin resultados, sin lead). Columnas nuevas: `leads.utm_campaign` (N-5),
`properties.photo_url` (B-10) y `synthetic` en `leads` y en las cuatro tablas, para que la historia
sembrada de N-9 se distinga y se pueda borrar.

Todo en una sola migración: dos migraciones creadas en paralelo dejan dos *heads* en Alembic.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None

_STATUSES = "'disponible', 'apartado', 'cerrado'"


def _synthetic() -> sa.Column:
    return sa.Column('synthetic', sa.Boolean(), server_default=sa.text('false'), nullable=False)


def _created(name: str = 'created_at') -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False)


def upgrade() -> None:
    op.add_column('leads', sa.Column('utm_campaign', sa.String(), nullable=True))
    op.add_column('leads', _synthetic())
    op.add_column('properties', sa.Column('photo_url', sa.String(), nullable=True))

    op.create_table(
        'property_status_events',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('property_id', sa.String(), nullable=False),
        sa.Column('from_status', sa.String(), nullable=True),
        sa.Column('to_status', sa.String(), nullable=False),
        _created('changed_at'),
        sa.Column('actor', sa.String(), nullable=False),
        _synthetic(),
        sa.CheckConstraint(f"from_status IS NULL OR from_status IN ({_STATUSES})",
                           name='ck_property_status_events_from_status'),
        sa.CheckConstraint(f"to_status IN ({_STATUSES})", name='ck_property_status_events_to_status'),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ondelete='CASCADE',
                                name=op.f('fk_property_status_events_property_id_properties')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_property_status_events')),
    )
    op.create_index('ix_property_status_events_property_changed', 'property_status_events',
                    ['property_id', 'changed_at'])
    op.create_index(op.f('ix_property_status_events_changed_at'), 'property_status_events', ['changed_at'])

    op.create_table(
        'deals',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('property_id', sa.String(), nullable=False),
        sa.Column('lead_id', sa.UUID(), nullable=False),
        sa.Column('advisor_id', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('price', sa.Numeric(14, 2), nullable=False),
        _created('closed_at'),
        _synthetic(),
        sa.CheckConstraint("kind IN ('renta', 'venta')", name='ck_deals_kind'),
        sa.CheckConstraint('price >= 0', name='ck_deals_price'),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], name=op.f('fk_deals_property_id_properties')),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE', name=op.f('fk_deals_lead_id_leads')),
        sa.ForeignKeyConstraint(['advisor_id'], ['advisors.id'], name=op.f('fk_deals_advisor_id_advisors')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_deals')),
    )
    op.create_index('ix_deals_property_closed', 'deals', ['property_id', 'closed_at'])
    op.create_index(op.f('ix_deals_lead_id'), 'deals', ['lead_id'])
    op.create_index(op.f('ix_deals_advisor_id'), 'deals', ['advisor_id'])
    op.create_index(op.f('ix_deals_closed_at'), 'deals', ['closed_at'])

    op.create_table(
        'property_interest',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('lead_id', sa.UUID(), nullable=False),
        sa.Column('property_id', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        _created(),
        _synthetic(),
        sa.CheckConstraint("kind IN ('anuncio', 'consulta', 'busqueda', 'visita')",
                           name='ck_property_interest_kind'),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE',
                                name=op.f('fk_property_interest_lead_id_leads')),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ondelete='CASCADE',
                                name=op.f('fk_property_interest_property_id_properties')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_property_interest')),
    )
    op.create_index('ix_property_interest_lead_property_created', 'property_interest',
                    ['lead_id', 'property_id', 'created_at'])
    op.create_index('ix_property_interest_property_created', 'property_interest', ['property_id', 'created_at'])
    op.create_index(op.f('ix_property_interest_created_at'), 'property_interest', ['created_at'])

    op.create_table(
        'unmet_searches',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('zone', sa.String(), nullable=True),
        sa.Column('type', sa.String(), nullable=True),
        sa.Column('bedrooms', sa.Integer(), nullable=True),
        sa.Column('budget_min', sa.Numeric(14, 2), nullable=True),
        sa.Column('budget_max', sa.Numeric(14, 2), nullable=True),
        _created(),
        _synthetic(),
        sa.CheckConstraint("type IS NULL OR type IN ('venta', 'renta', 'preventa')", name='ck_unmet_searches_type'),
        sa.CheckConstraint('bedrooms IS NULL OR bedrooms >= 0', name='ck_unmet_searches_bedrooms'),
        sa.CheckConstraint('budget_min IS NULL OR budget_min >= 0', name='ck_unmet_searches_budget_min'),
        sa.CheckConstraint('budget_max IS NULL OR budget_max >= 0', name='ck_unmet_searches_budget_max'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_unmet_searches')),
    )
    op.create_index(op.f('ix_unmet_searches_created_at'), 'unmet_searches', ['created_at'])


def downgrade() -> None:
    op.drop_table('unmet_searches')
    op.drop_table('property_interest')
    op.drop_table('deals')
    op.drop_table('property_status_events')
    op.drop_column('properties', 'photo_url')
    op.drop_column('leads', 'synthetic')
    op.drop_column('leads', 'utm_campaign')
