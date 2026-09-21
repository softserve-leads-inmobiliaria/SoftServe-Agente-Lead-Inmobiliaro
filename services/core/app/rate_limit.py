"""Límite de tasa del proveedor de LLM (A-2, DT-6): 10 solicitudes por minuto y por modelo, sin Redis.

Ventana deslizante en memoria con cola: quien llega con el cupo agotado espera su turno (en orden de
llegada) hasta `max_wait` segundos; pasado ese tiempo falla con `RateLimited` y el agente responde
con un texto seguro en vez de dejar al lead esperando indefinidamente.

Es por proceso: con varios *workers* el límite real sería N veces mayor. El compose corre un solo
*worker* de `core`; si eso cambia hay que compartir el contador (p. ej. en Postgres).
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Awaitable, Callable


class RateLimited(Exception):
    """El cupo del modelo no se libera a tiempo."""


class RateLimiter:
    def __init__(self, max_calls: int = 10, per_seconds: float = 60.0, *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        if max_calls < 1 or per_seconds <= 0:
            raise ValueError("el límite debe permitir al menos una llamada en una ventana positiva")
        self._max_calls = max_calls
        self._per_seconds = per_seconds
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self, max_wait: float) -> float:
        """Reserva una llamada. Devuelve cuánto esperó; lanza `RateLimited` si tendría que esperar más de `max_wait`."""
        waited = 0.0
        async with self._lock:  # el lock se mantiene mientras se espera: los que llegan después hacen cola
            while True:
                now = self._clock()
                while self._calls and self._calls[0] <= now - self._per_seconds:
                    self._calls.popleft()
                if len(self._calls) < self._max_calls:
                    self._calls.append(now)
                    return waited
                wait = self._calls[0] + self._per_seconds - now
                if waited + wait > max_wait:
                    raise RateLimited(f"cupo agotado: {self._max_calls} llamadas por {self._per_seconds:g}s")
                await self._sleep(wait)
                waited += wait


class RateLimits:
    """Un limitador por identificador de modelo (el límite de Bedrock es por modelo)."""

    def __init__(self, max_calls: int = 10, per_seconds: float = 60.0, **kwargs) -> None:
        self._make = lambda: RateLimiter(max_calls, per_seconds, **kwargs)
        self._make()  # valida el límite ahora, no al usar el primer modelo
        self._by_model: dict[str, RateLimiter] = defaultdict(self._make)

    def for_model(self, model_id: str) -> RateLimiter:
        return self._by_model[model_id]
