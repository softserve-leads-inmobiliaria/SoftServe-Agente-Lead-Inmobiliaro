"""Formato de logging JSON con enmascarado de PII, centralizado (se implementa en OBS-7)."""
from __future__ import annotations

import logging
import logging.config
import os
from json import dumps
from typing import Any

_RESERVED = frozenset(vars(logging.makeLogRecord({})))


class JsonFormatter(logging.Formatter):
    """Un registro de log por línea de JSON: timestamp UTC, nivel, logger, mensaje y excepción si hay.

    Sin enmascarado de PII propio: `docs/DECISIONS/decisions.md` DT-27 documenta por qué — ningún
    logger de los 3 servicios imprime hoy contenido del lead (el guardrail de entrada ya limpia
    `clean_text`/`signal_text` antes de que ese texto llegue a cualquier lugar que loguee). Si un
    `logger.info(..., extra={...})` agrega campos, quien llama es responsable de no meter ahí
    valores sensibles, mismo principio que `InputVerdict.audit_kinds()` (tipos, nunca contenido).
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        payload.update({k: v for k, v in vars(record).items() if k not in _RESERVED and k not in payload})
        return dumps(payload, default=str, ensure_ascii=False)


def _dict_config(level: str) -> dict[str, Any]:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": f"{__name__}.JsonFormatter"}},
        "handlers": {"json": {"class": "logging.StreamHandler", "formatter": "json", "stream": "ext://sys.stdout"}},
        "root": {"handlers": ["json"], "level": level},
        # uvicorn configura estos 3 loggers ANTES de que se importe app.main, 2 de ellos con
        # propagate=False (uvicorn.config.LOGGING_CONFIG) — sin nombrarlos acá seguirían en texto
        # plano pese al root de arriba. Ver docs/DECISIONS/decisions.md DT-27.
        "loggers": {
            "uvicorn": {"handlers": ["json"], "level": level, "propagate": False},
            "uvicorn.error": {"handlers": ["json"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": ["json"], "level": level, "propagate": False},
        },
    }


def configure_json_logging(level: str | None = None) -> None:
    """Reconfigura todo el árbol de logging (root + loggers propios de uvicorn) a JSON.

    Se llama una sola vez por proceso, en el `main.py` de cada servicio, justo después de los
    imports: para entonces uvicorn ya corrió su propio `Config.configure_logging()` (si el proceso
    arrancó vía `uvicorn app.main:app`), así que este `dictConfig` corre después y gana, sin
    condición de carrera. `LOG_LEVEL` (env) manda si no se pasa `level` explícito; default `INFO`.
    """
    logging.config.dictConfig(_dict_config((level or os.environ.get("LOG_LEVEL", "INFO")).upper()))
