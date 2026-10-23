"""Usuarios del panel con rol de asesor o de gerente (B-17, #265, DT-72).

Hasta ahora el panel se abría con el token de cada asesor en la URL (DT-5). `panel_users` guarda quién
puede entrar con usuario y contraseña: el asesor, atado a su fila de `advisors`, y el gerente, sin
asesor. La contraseña se guarda solo como hash scrypt con sal propia. No toca ninguna tabla existente:
`advisors.access_token` sigue sirviendo para el registro en Telegram (B-7).

Revision ID: 0018
Revises: 0017
"""
from alembic import op
import sqlalchemy as sa

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'panel_users',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('username', sa.String(), nullable=False),
        sa.Column('password_hash', sa.String(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('advisor_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("role IN ('asesor', 'gerente')", name='ck_panel_users_role'),
        sa.CheckConstraint("(role = 'asesor' AND advisor_id IS NOT NULL) OR (role = 'gerente' AND advisor_id IS NULL)",
                           name='ck_panel_users_advisor_por_rol'),
        sa.ForeignKeyConstraint(['advisor_id'], ['advisors.id'], ondelete='CASCADE',
                                name=op.f('fk_panel_users_advisor_id_advisors')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_panel_users')),
        sa.UniqueConstraint('username', name=op.f('uq_panel_users_username')),
        sa.UniqueConstraint('advisor_id', name=op.f('uq_panel_users_advisor_id')),
    )


def downgrade() -> None:
    op.drop_table('panel_users')
