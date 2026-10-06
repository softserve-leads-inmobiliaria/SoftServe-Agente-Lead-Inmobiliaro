"""Tipo de inmueble de la propiedad (#188).

`properties.type` es la operación (venta/renta/preventa); el inmueble (departamento, casa, terreno,
local, estudio) no estaba en ningún lado y solo se podía adivinar por el `title`, así que
`search_properties` no podía filtrarlo. Se agrega `kind`, se rellena lo ya sembrado a partir del
título y queda NOT NULL con su CHECK.

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None

# El orden importa: "Local comercial" contiene "comercial", "Estudio" no contiene "casa", etc. El
# `ELSE 'casa'` es solo red de seguridad para una fila con un título que no reconocemos.
_BACKFILL = """
UPDATE properties SET kind = CASE
    WHEN title ILIKE '%estudio%' THEN 'estudio'
    WHEN title ILIKE '%depto%' OR title ILIKE '%departamento%' OR title ILIKE '%penthouse%' THEN 'departamento'
    WHEN title ILIKE '%terreno%' THEN 'terreno'
    WHEN title ILIKE '%local%' THEN 'local'
    WHEN title ILIKE '%casa%' THEN 'casa'
    ELSE 'casa'
END
WHERE kind IS NULL
"""


def upgrade() -> None:
    # En tres pasos: nullable para poder agregarla sobre filas existentes, relleno por título, y
    # recién ahí NOT NULL. El modelo no lleva server_default, así que la columna tampoco.
    op.add_column('properties', sa.Column('kind', sa.String(), nullable=True))
    op.execute(_BACKFILL)
    op.alter_column('properties', 'kind', existing_type=sa.String(), nullable=False)
    op.create_check_constraint(
        'ck_properties_kind', 'properties',
        "kind IN ('departamento', 'casa', 'terreno', 'local', 'estudio')")


def downgrade() -> None:
    op.drop_constraint('ck_properties_kind', 'properties', type_='check')
    op.drop_column('properties', 'kind')
