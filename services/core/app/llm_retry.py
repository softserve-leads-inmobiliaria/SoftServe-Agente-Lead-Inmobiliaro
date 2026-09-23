"""Reintentos con espera exponencial ante errores transitorios de Bedrock (A-14, DT-30).

Un límite momentáneo del proveedor no debe convertirse en el texto de respaldo si esperar un poco lo
resuelve. Este módulo decide *si* y *cuánto* esperar; `BedrockLLM._converse` hace la llamada.

- Transitorio (se reintenta): `ThrottlingException`, `ServiceUnavailableException`,
  `ModelTimeoutException`, `InternalServerException` y los errores de conexión de botocore.
  Cualquier otro (`AccessDeniedException`, `ValidationException`, modelo inexistente, credenciales)
  falla al primer intento: reintentarlo no lo arregla y solo retrasaría al lead.
- Espera: exponencial con *jitter* (`base * 2^(intento-1)`, tope `max_delay`, mitad fija y mitad al
  azar para que turnos concurrentes no reintenten en el mismo instante). Si el proveedor manda
  `Retry-After`, se respeta ese tiempo en lugar del calculado.
- Tope de tiempo por turno: la ventana se abre con el primer fallo transitorio del turno y la
  comparten todas las llamadas del turno (clasificador, chat, regeneración). Una espera que no cabe
  en lo que queda no se hace: el turno cae al texto seguro en vez de colgar la conversación.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Final

from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

TRANSIENT_CODES: Final = frozenset({"ThrottlingException", "ServiceUnavailableException",
                                    "ModelTimeoutException", "InternalServerException"})
# Lista explícita, no la jerarquía de botocore: `ReadTimeoutError` y `ConnectionClosedError` no heredan de
# `ConnectionError`, y un error de SSL o de proxy es de configuración: reintentarlo no lo arregla.
TRANSIENT_CONNECTION_ERRORS: Final = (EndpointConnectionError, ConnectTimeoutError, ConnectionClosedError,
                                      ReadTimeoutError)

RETRY = "retry"
NOT_TRANSIENT = "not_transient"
ATTEMPTS_EXHAUSTED = "attempts_exhausted"
TIME_EXHAUSTED = "time_exhausted"


@dataclass(frozen=True)
class RetryPolicy:
    """Límites de los reintentos. `max_attempts=1` es "no reintentar"."""

    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 8.0
    max_total_seconds: float = 20.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts debe ser al menos 1")
        if self.base_delay <= 0 or self.max_delay < self.base_delay:
            raise ValueError("la espera base debe ser positiva y no mayor que el tope de espera")
        if self.max_total_seconds < 0:
            raise ValueError("el tiempo total de reintentos no puede ser negativo")


NO_RETRIES: Final = RetryPolicy(max_attempts=1)


def error_code(exc: BaseException) -> str:
    """El código del error de Bedrock, o la clase de la excepción si no viene de la API."""
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code") or "ClientError")
    return type(exc).__name__


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, ClientError):
        return error_code(exc) in TRANSIENT_CODES
    return isinstance(exc, TRANSIENT_CONNECTION_ERRORS)


def retry_after(exc: BaseException) -> float | None:
    """Los segundos que el proveedor pide esperar (cabecera `Retry-After`), si los indica."""
    if not isinstance(exc, ClientError):
        return None
    headers = exc.response.get("ResponseMetadata", {}).get("HTTPHeaders") or {}
    value = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None  # una fecha HTTP u otro formato: se usa la espera calculada
    return seconds if seconds >= 0 else None


@dataclass(frozen=True)
class Decision:
    """Qué hacer tras un fallo: esperar `delay` segundos y reintentar, o rendirse (`delay=None`)."""

    reason: str
    delay: float | None = None
    provider_delay: bool = False  # la espera la pidió el proveedor, no salió del cálculo


class RetryBudget:
    """Tiempo de reintentos de un turno. Se abre con el primer fallo transitorio y no se vuelve a abrir."""

    def __init__(self) -> None:
        self.started: float | None = None


class Retrier:
    def __init__(self, policy: RetryPolicy = NO_RETRIES, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 uniform: Callable[[float, float], float] = random.uniform) -> None:
        self.policy = policy
        self.sleep = sleep
        self._clock = clock
        self._uniform = uniform

    def remaining(self, budget: RetryBudget) -> float:
        """Segundos que le quedan al turno para reintentar; todo el tope si aún no hubo fallos."""
        if budget.started is None:
            return self.policy.max_total_seconds
        return max(0.0, self.policy.max_total_seconds - (self._clock() - budget.started))

    def calculated_delay(self, attempt: int) -> float:
        """Espera tras el intento `attempt` (1, 2, ...): exponencial, con la mitad fija y la otra mitad al azar."""
        ceiling = min(self.policy.max_delay, self.policy.base_delay * 2 ** (attempt - 1))
        return ceiling / 2 + self._uniform(0.0, ceiling / 2)

    def decide(self, exc: BaseException, attempt: int, budget: RetryBudget) -> Decision:
        """Qué hacer tras el fallo `exc` del intento número `attempt` de una llamada."""
        if not is_transient(exc):
            return Decision(NOT_TRANSIENT)
        if attempt >= self.policy.max_attempts:
            return Decision(ATTEMPTS_EXHAUSTED)
        if budget.started is None:
            budget.started = self._clock()
        asked = retry_after(exc)
        delay = asked if asked is not None else self.calculated_delay(attempt)
        if delay > self.remaining(budget):
            return Decision(TIME_EXHAUSTED)
        return Decision(RETRY, delay, provider_delay=asked is not None)

