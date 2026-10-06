"""Los asesores atienden en inglés por omisión (#202, DT-55).

Desde #202 la conversación con el lead es solo en inglés, y el ruteo (`assign_advisor`) pide asesores
en inglés. `advisors.language` tenía `server_default='es'` y nadie lo completaba: el seed no lo pasa,
así que todos los asesores quedaban en `es` y el ruteo dejaba de encontrar "zona e idioma". Se cambia
el default y se pasan a `en` los asesores que tenían el valor por omisión.

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('advisors', 'language', existing_type=sa.String(), server_default='en')
    op.execute("UPDATE advisors SET language = 'en' WHERE language = 'es'")


def downgrade() -> None:
    op.execute("UPDATE advisors SET language = 'es' WHERE language = 'en'")
    op.alter_column('advisors', 'language', existing_type=sa.String(), server_default='es')
