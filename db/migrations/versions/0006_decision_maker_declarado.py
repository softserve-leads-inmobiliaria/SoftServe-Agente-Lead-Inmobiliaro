"""`decision_maker` deja de distinguir solo, pareja y familia (#119, DT-40).

El contrato `ScoreSignal.decision_maker` pasa a `declarado`/`no_declarado`: estado civil y situación
familiar son categorías protegidas y no se guardan. Los valores viejos que ya están en la base se
reescriben, en la señal acumulada de cada conversación y en las razones del último score, para que
`compute_score` no los rechace y para que no quede ese dato guardado.

El `downgrade` no puede recuperar con quién decidía cada lead: deja `solo`, que valía los mismos 20
puntos (DT-38).

Revision ID: 0006
Revises: 0005
"""
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None

_OLD = ('solo', 'pareja', 'familia')


def _rewrite(old_values: tuple[str, ...], new: str) -> None:
    values = ", ".join(f"'{value}'" for value in old_values)
    op.execute(f"""
        UPDATE fsm_context
        SET score_signal = jsonb_set(score_signal, '{{decision_maker}}', '"{new}"')
        WHERE score_signal->>'decision_maker' IN ({values})
    """)
    for value in old_values:
        op.execute(f"""
            UPDATE lead_scores
            SET reasons = array_replace(reasons, 'Authority: decision_maker={value}',
                                        'Authority: decision_maker={new}')
            WHERE 'Authority: decision_maker={value}' = ANY(reasons)
        """)


def upgrade() -> None:
    _rewrite(_OLD, 'declarado')


def downgrade() -> None:
    _rewrite(('declarado',), 'solo')
