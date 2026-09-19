"""Esquema inicial (C-3): todas las entidades de design.md §1 y el estado de la FSM.

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table('advisors',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('zone', sa.String(), nullable=False),
    sa.Column('language', sa.String(), server_default='es', nullable=False),
    sa.Column('access_token', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_advisors')),
    sa.UniqueConstraint('access_token', name=op.f('uq_advisors_access_token'))
    )
    op.create_index(op.f('ix_advisors_zone'), 'advisors', ['zone'], unique=False)
    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('actor', sa.String(), nullable=False),
    sa.Column('action', sa.String(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_log'))
    )
    op.create_index(op.f('ix_audit_log_action'), 'audit_log', ['action'], unique=False)
    op.create_table('faq_entries',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('answer', sa.Text(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_faq_entries'))
    )
    op.create_table('leads',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('telegram_user_id', sa.String(), nullable=False),
    sa.Column('phone', sa.String(), nullable=True),
    sa.Column('email', sa.String(), nullable=True),
    sa.Column('consent_status', sa.String(), server_default='pending', nullable=False),
    sa.Column('status', sa.String(), server_default='nuevo', nullable=False),
    sa.Column('zone', sa.String(), nullable=True),
    sa.Column('budget', sa.Text(), nullable=True),
    sa.Column('timing', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("consent_status IN ('pending', 'granted', 'withdrawn')", name='ck_leads_consent_status'),
    sa.CheckConstraint("status IN ('nuevo', 'mql', 'sql', 'visit_scheduled', 'closed_won', 'closed_lost')", name='ck_leads_status'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leads')),
    sa.UniqueConstraint('telegram_user_id', name=op.f('uq_leads_telegram_user_id'))
    )
    op.create_table('properties',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('price', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('type', sa.String(), nullable=False),
    sa.Column('zone', sa.String(), nullable=False),
    sa.Column('m2', sa.Numeric(precision=8, scale=2), nullable=False),
    sa.Column('bedrooms', sa.Integer(), nullable=False),
    sa.Column('bathrooms', sa.Numeric(precision=3, scale=1), nullable=False),
    sa.Column('amenities', sa.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
    sa.Column('status', sa.String(), server_default='disponible', nullable=False),
    sa.Column('address_approx', sa.String(), nullable=False),
    sa.Column('address_exact', sa.String(), nullable=False),
    sa.CheckConstraint("status IN ('disponible', 'apartado', 'cerrado')", name='ck_properties_status'),
    sa.CheckConstraint("type IN ('venta', 'renta', 'preventa')", name='ck_properties_type'),
    sa.CheckConstraint('bedrooms >= 0', name='ck_properties_bedrooms'),
    sa.CheckConstraint('price >= 0', name='ck_properties_price'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_properties'))
    )
    op.create_index(op.f('ix_properties_zone'), 'properties', ['zone'], unique=False)
    op.create_table('conversations',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('lead_id', sa.UUID(), nullable=False),
    sa.Column('fsm_state', sa.String(), server_default='GREETING', nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_message_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("fsm_state IN ('GREETING', 'DISCOVERY', 'QUALIFY', 'RECOMMEND', 'SCHEDULE', 'HANDOFF', 'HUMAN_HANDOFF', 'NURTURE', 'LOST')", name='ck_conversations_fsm_state'),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_conversations_lead_id_leads'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversations'))
    )
    op.create_index(op.f('ix_conversations_lead_id'), 'conversations', ['lead_id'], unique=False)
    op.create_table('funnel_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('lead_id', sa.UUID(), nullable=False),
    sa.Column('event_type', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_funnel_events_lead_id_leads')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_funnel_events'))
    )
    op.create_index(op.f('ix_funnel_events_event_type'), 'funnel_events', ['event_type'], unique=False)
    op.create_index(op.f('ix_funnel_events_lead_id'), 'funnel_events', ['lead_id'], unique=False)
    op.create_table('lead_scores',
    sa.Column('lead_id', sa.UUID(), nullable=False),
    sa.Column('need_fit', sa.Integer(), nullable=False),
    sa.Column('budget', sa.Integer(), nullable=False),
    sa.Column('authority', sa.Integer(), nullable=False),
    sa.Column('timing', sa.Integer(), nullable=False),
    sa.Column('eligibility', sa.Integer(), nullable=False),
    sa.Column('total', sa.Integer(), nullable=False),
    sa.Column('band', sa.String(), nullable=False),
    sa.Column('reasons', sa.ARRAY(sa.Text()), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(band = 'COLD' AND total <= 40) OR (band = 'WARM' AND total BETWEEN 41 AND 70) OR (band = 'HOT' AND total >= 71)", name='ck_lead_scores_band_matches_total'),
    sa.CheckConstraint("band IN ('COLD', 'WARM', 'HOT')", name='ck_lead_scores_band'),
    sa.CheckConstraint('authority BETWEEN 0 AND 20', name='ck_lead_scores_authority'),
    sa.CheckConstraint('budget BETWEEN 0 AND 20', name='ck_lead_scores_budget'),
    sa.CheckConstraint('eligibility BETWEEN 0 AND 20', name='ck_lead_scores_eligibility'),
    sa.CheckConstraint('need_fit BETWEEN 0 AND 20', name='ck_lead_scores_need_fit'),
    sa.CheckConstraint('timing BETWEEN 0 AND 20', name='ck_lead_scores_timing'),
    sa.CheckConstraint('total BETWEEN 0 AND 100', name='ck_lead_scores_total'),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_lead_scores_lead_id_leads'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('lead_id', name=op.f('pk_lead_scores'))
    )
    op.create_table('promotions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('property_id', sa.String(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('valid_until', sa.Date(), nullable=False),
    sa.ForeignKeyConstraint(['property_id'], ['properties.id'], name=op.f('fk_promotions_property_id_properties'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_promotions'))
    )
    op.create_index(op.f('ix_promotions_property_id'), 'promotions', ['property_id'], unique=False)
    op.create_table('visits',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('lead_id', sa.UUID(), nullable=False),
    sa.Column('property_id', sa.String(), nullable=False),
    sa.Column('advisor_id', sa.String(), nullable=False),
    sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('idempotency_key', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['advisor_id'], ['advisors.id'], name=op.f('fk_visits_advisor_id_advisors')),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_visits_lead_id_leads'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['property_id'], ['properties.id'], name=op.f('fk_visits_property_id_properties')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_visits')),
    sa.UniqueConstraint('idempotency_key', name='uq_visits_idempotency_key')
    )
    op.create_index(op.f('ix_visits_lead_id'), 'visits', ['lead_id'], unique=False)
    op.create_table('fsm_context',
    sa.Column('conversation_id', sa.UUID(), nullable=False),
    sa.Column('lead_qualified', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('last_tool_results', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('score_signal', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text('\'{"budget_hint": null, "decision_maker": "no_declarado", "urgency": "no_declarada", "matching_properties": 0, "meets_requirements": null}\'::jsonb'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_fsm_context_conversation_id_conversations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('conversation_id', name=op.f('pk_fsm_context'))
    )
    op.create_table('messages',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('conversation_id', sa.UUID(), nullable=False),
    sa.Column('role', sa.String(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('tool_calls', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('user', 'assistant', 'system', 'tool')", name='ck_messages_role'),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_messages_conversation_id_conversations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_messages'))
    )
    op.create_index('ix_messages_conversation_created', 'messages', ['conversation_id', 'created_at'], unique=False)

    # funnel_events es append-only (design.md §1): se rechaza cualquier UPDATE o DELETE.
    op.execute("""
        CREATE FUNCTION reject_funnel_event_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'funnel_events es append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER funnel_events_append_only
        BEFORE UPDATE OR DELETE ON funnel_events
        FOR EACH ROW EXECUTE FUNCTION reject_funnel_event_change()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS funnel_events_append_only ON funnel_events")
    op.execute("DROP FUNCTION IF EXISTS reject_funnel_event_change()")
    op.drop_index('ix_messages_conversation_created', table_name='messages')
    op.drop_table('messages')
    op.drop_table('fsm_context')
    op.drop_index(op.f('ix_visits_lead_id'), table_name='visits')
    op.drop_table('visits')
    op.drop_index(op.f('ix_promotions_property_id'), table_name='promotions')
    op.drop_table('promotions')
    op.drop_table('lead_scores')
    op.drop_index(op.f('ix_funnel_events_lead_id'), table_name='funnel_events')
    op.drop_index(op.f('ix_funnel_events_event_type'), table_name='funnel_events')
    op.drop_table('funnel_events')
    op.drop_index(op.f('ix_conversations_lead_id'), table_name='conversations')
    op.drop_table('conversations')
    op.drop_index(op.f('ix_properties_zone'), table_name='properties')
    op.drop_table('properties')
    op.drop_table('leads')
    op.drop_table('faq_entries')
    op.drop_index(op.f('ix_audit_log_action'), table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_index(op.f('ix_advisors_zone'), table_name='advisors')
    op.drop_table('advisors')
