"""Siembra la FAQ con sus embeddings en pgvector (C-5, DT-7).

Sin esto la tabla `faq_entries` queda vacía y `catalog.search_faq` siempre responde `matches: []`.
Codifica los pares de `services/catalog/app/faq_data.py` con el modelo autoalojado
(`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`) y los guarda en `faq_entries`.

Idempotente por pregunta: si el par ya existe se actualiza la respuesta y su vector; si no, se
inserta. Los pares que ya no están en `faq_data.py` no se tocan (no se borran a ciegas).

Uso:
  python scripts/seed_faq.py            # siembra o refresca la FAQ
  python scripts/seed_faq.py --borrar   # borra solo las preguntas de faq_data.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import delete, func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.models import FaqEntry  # noqa: E402
from db.session import get_engine, session_scope  # noqa: E402
from services.catalog.app.embeddings import Encoder, FastEmbedEncoder  # noqa: E402
from services.catalog.app.faq_data import FAQ  # noqa: E402


def seed(engine=None, encoder: Encoder | None = None) -> int:
    """Inserta o actualiza los pares de la FAQ y devuelve cuántos quedaron en la tabla."""
    encoder = encoder or FastEmbedEncoder()
    questions = [item["question"] for item in FAQ]
    vectors = encoder.encode(questions)
    with session_scope(engine or get_engine()) as session:
        for item, vector in zip(FAQ, vectors, strict=True):
            row = session.scalar(select(FaqEntry).where(FaqEntry.question == item["question"]))
            if row is None:
                session.add(FaqEntry(question=item["question"], answer=item["answer"],
                                     embedding=vector))
            else:
                row.answer = item["answer"]
                row.embedding = vector
        session.flush()
        return session.scalar(select(func.count()).select_from(FaqEntry)) or 0


def clear(engine=None) -> int:
    """Borra solo las preguntas que hoy están en `faq_data.py`."""
    with session_scope(engine or get_engine()) as session:
        deleted = session.execute(
            delete(FaqEntry).where(FaqEntry.question.in_([item["question"] for item in FAQ]))
        ).rowcount
        return deleted or 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Siembra la FAQ con embeddings (C-5).")
    parser.add_argument("--borrar", action="store_true", help="Borra los pares de la FAQ y termina.")
    args = parser.parse_args()

    if args.borrar:
        print(f"FAQ borrada: {clear()} pregunta(s).")
        return

    total = seed()
    print(f"FAQ sembrada: {len(FAQ)} pares codificados con embeddings; {total} filas en faq_entries.")


if __name__ == "__main__":
    main()
