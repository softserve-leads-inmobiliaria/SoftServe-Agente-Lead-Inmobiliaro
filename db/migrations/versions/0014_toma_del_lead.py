"""Cuándo el asesor tomó el lead (B-12).

`leads.claimed_at` se llena la primera vez que un asesor marca el lead "en gestión" desde el panel (B-6), y
no se pisa después. Con `qualified_at` da el tiempo de atención del asesor. Null si nadie lo tomó desde el
panel: los leads tomados antes de esta migración, o los que pasaron a `visit_scheduled` sin tomarse.

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('leads', 'claimed_at')
