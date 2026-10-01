"""Exporta a txt las conversaciones guardadas en Postgres (sirve para las que se hacen por Telegram).

Uso:  python scripts/demo/exportar.py [minutos]   (por defecto, las conversaciones activas en los últimos 60)
Deja tmp/demo/transcripciones/<fecha>_conversacion_<conversation_id>.txt, una por conversación, con el estado
final. El texto del lead es el que guarda core: ya filtrado, sin PII ni categorías protegidas.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SALIDA = REPO / "tmp" / "demo" / "transcripciones"  # tmp/ no se versiona
SEP = "\x1f"


def psql(sql: str, tabla: bool = False) -> str:
    args = ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "leads", "-d", "leads"]
    args += ["-c", sql] if tabla else ["-At", "-F", SEP, "-c", sql]
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", cwd=REPO).stdout


def main(minutos: int) -> None:
    SALIDA.mkdir(parents=True, exist_ok=True)
    filas = psql("select c.id, c.lead_id, l.telegram_user_id from conversations c join leads l on l.id = c.lead_id "
                 f"where c.last_message_at > now() - interval '{minutos} minutes' order by c.started_at")
    for fila in filter(None, filas.splitlines()):
        conv, lead, tg = fila.split(SEP)
        mensajes = psql("select extract(epoch from created_at), role, "
                        f"replace(content, chr(10), ' / ') from messages where conversation_id='{conv}' "
                        "order by created_at")
        ruta = SALIDA / f"{datetime.now():%Y-%m-%d_%H%M}_conversacion_{conv[:8]}.txt"
        with ruta.open("w", encoding="utf-8") as f:
            f.write(f"Conversación {conv}  lead={lead}  telegram_user_id={tg}\n" + "=" * 80 + "\n")
            for m in filter(None, mensajes.splitlines()):
                epoch, rol, texto = m.split(SEP, 2)
                hora = datetime.fromtimestamp(float(epoch)).strftime("%H:%M:%S")  # hora local del equipo
                f.write(f"\n[{hora}] {'LEAD' if rol == 'user' else 'AGENTE'}: {texto}\n")
            f.write("\n" + "=" * 80 + "\nEstado final en Postgres:\n")
            f.write(psql(f"select fsm_state from conversations where id='{conv}';"
                         f"select status, zone, advisor_id, consent_status from leads where id='{lead}';"
                         "select need_fit, budget, authority, timing, eligibility, total, band "
                         f"from lead_scores where lead_id='{lead}';", tabla=True))
        print(ruta)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 60)
