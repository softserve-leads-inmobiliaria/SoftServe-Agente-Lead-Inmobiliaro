"""Codificación de texto a vectores para la FAQ (C-5, DT-7).

Modelo autoalojado `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384 dims, 50
idiomas, Apache-2.0): corre con `fastembed` (ONNX) dentro del contenedor del catálogo, sin llamada de
red a un proveedor externo y sin arrastrar `torch`/CUDA. Su dimensión coincide con
`db.models.EMBEDDING_DIM`, así que no cambia el esquema de `FaqEntry`.

El encoder se inyecta en `CatalogService` para que los tests y el arnés no dependan del modelo real:
`create_encoder()` devuelve `HashEncoder` cuando `EMBEDDINGS_FAKE=1`.
"""
from __future__ import annotations

import hashlib
import math
import os
from typing import Protocol, Sequence

from db.models import EMBEDDING_DIM

# Modelo y dimensión fijados por C-5 (DT-7). Cambiar uno obliga a cambiar el otro y a migrar la
# columna `faq_entries.embedding`.
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class Encoder(Protocol):
    """Contrato mínimo: convierte texto en vectores de `EMBEDDING_DIM` dimensiones."""

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        ...


def _normalize(vector: Sequence[float]) -> list[float]:
    """Deja el vector en norma 1. Con vectores normalizados, el coseno es estable sea cual sea el glm."""
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


class FastEmbedEncoder:
    """Encoder real (DT-7). `fastembed` trae el modelo ya exportado a ONNX (sin torch)."""

    def __init__(
        self,
        model_name: str = EMBEDDING_MODEL,
        cache_dir: str | None = None,
        local_only: bool | None = None,
    ) -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir or os.environ.get("FASTEMBED_CACHE_PATH")
        self._local_only = (
            os.environ.get("EMBEDDINGS_LOCAL_ONLY") == "1" if local_only is None else local_only
        )
        self._model = None

    def _load(self):
        # Import perezoso: `fastembed` pesa y solo hace falta cuando se codifica de verdad. Los tests
        # con `HashEncoder` no lo necesitan instalado.
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(
                model_name=self._model_name,
                cache_dir=self._cache_dir,
                local_files_only=self._local_only,
            )
        return self._model

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return [
            _normalize([float(value) for value in vector]) for vector in self._load().embed(list(texts))
        ]


class HashEncoder:
    """Encoder determinista sin dependencias, para tests y CI.

    Bolsa de palabras con *hashing* firmado: dos textos que comparten palabras quedan cerca, así que
    se puede probar la búsqueda sin bajar el modelo real ni tocar la red. No es un sustituto de
    calidad, solo de forma.
    """

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return [_normalize(self._vector(text)) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for token in text.lower().split():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dim
            vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
        return vector


def create_encoder() -> Encoder:
    """El encoder por defecto del servicio. `EMBEDDINGS_FAKE=1` usa el de tests (CI, arnés)."""
    if os.environ.get("EMBEDDINGS_FAKE") == "1":
        return HashEncoder()
    return FastEmbedEncoder()
