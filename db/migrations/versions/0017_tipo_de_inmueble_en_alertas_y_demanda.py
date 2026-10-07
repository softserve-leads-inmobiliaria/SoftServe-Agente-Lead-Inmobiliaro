"""Tipo de inmueble en las alertas (A-18) y en la demanda no atendida (N-5) (#209).

Desde #188 (DT-54) `search_properties` filtra por `kind`, pero lo que se guarda de esas búsquedas no lo
registraba: una alerta de "apartment en El Poblado" avisaba también de casas y terrenos, y la demanda no
atendida no distinguía qué inmueble faltaba. Se agrega `kind` a las dos tablas, opcional como el resto de
los filtros y con el mismo CHECK que `properties.kind`. Las filas existentes quedan en NULL: no se sabe
qué inmueble se pidió, y NULL es "cualquiera", que es como se comportaban hasta ahora.

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None

_KINDS = "('departamento', 'casa', 'terreno', 'local', 'estudio')"


def upgrade() -> None:
    for table in ('unmet_searches', 'property_alerts'):
        op.add_column(table, sa.Column('kind', sa.String(), nullable=True))
        op.create_check_constraint(f'ck_{table}_kind', table, f"kind IS NULL OR kind IN {_KINDS}")


def downgrade() -> None:
    for table in ('unmet_searches', 'property_alerts'):
        op.drop_constraint(f'ck_{table}_kind', table, type_='check')
        op.drop_column(table, 'kind')
