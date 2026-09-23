"""Elige el agente según el entorno (A-2). Un valor inválido falla al arrancar, no en el primer turno."""
from __future__ import annotations

import os
from typing import Any, Mapping

from app.agent import Agent, BedrockAgent, FakeAgent
from app.bedrock_llm import BedrockLLM
from app.llm_retry import Retrier, RetryPolicy
from app.rate_limit import RateLimits

DEFAULT_REGION = "us-east-1"
DEFAULT_RATE_LIMIT = 10  # solicitudes por minuto y por modelo (DT-6)
DEFAULT_MAX_WAIT = 15.0  # segundos que un turno espera cupo antes de responder con el texto seguro
# Reintentos ante errores transitorios de Bedrock (A-14, DT-30). Los defaults son los de `RetryPolicy`.
DEFAULT_RETRY = RetryPolicy()


class ConfigError(ValueError):
    """Configuración del LLM incompleta o inválida."""


def _get(env: Mapping[str, str], key: str, default: str = "") -> str:
    return (env.get(key) or "").strip() or default


def _bedrock_client(env: Mapping[str, str]) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client("bedrock-runtime", region_name=_get(env, "BEDROCK_REGION", DEFAULT_REGION),
                        config=Config(connect_timeout=5, read_timeout=20, retries={"max_attempts": 1}))


def _retry_policy(env: Mapping[str, str]) -> RetryPolicy:
    """Los límites de los reintentos, validados al arrancar: un valor inválido falla acá, no en el primer 429."""
    try:
        return RetryPolicy(
            max_attempts=int(_get(env, "LLM_RETRY_MAX_ATTEMPTS", str(DEFAULT_RETRY.max_attempts))),
            base_delay=float(_get(env, "LLM_RETRY_BASE_DELAY_SECONDS", str(DEFAULT_RETRY.base_delay))),
            max_delay=float(_get(env, "LLM_RETRY_MAX_DELAY_SECONDS", str(DEFAULT_RETRY.max_delay))),
            max_total_seconds=float(_get(env, "LLM_RETRY_MAX_TOTAL_SECONDS", str(DEFAULT_RETRY.max_total_seconds))))
    except ValueError as exc:
        raise ConfigError(f"reintentos del LLM inválidos: {exc}") from exc


def build_agent(env: Mapping[str, str] | None = None, client: Any = None) -> Agent:
    """`LLM_PROVIDER=fake` (por defecto) o `bedrock`. `client` permite inyectar uno simulado en las pruebas."""
    env = os.environ if env is None else env
    provider = _get(env, "LLM_PROVIDER", "fake").lower()
    if provider == "fake":
        return FakeAgent()
    if provider != "bedrock":
        raise ConfigError(f"LLM_PROVIDER inválido: {provider!r} (válidos: fake, bedrock)")
    chat, classifier = _get(env, "BEDROCK_MODEL_ID_CHAT"), _get(env, "BEDROCK_MODEL_ID_CLASSIFIER")
    missing = [name for name, value in (("BEDROCK_MODEL_ID_CHAT", chat),
                                        ("BEDROCK_MODEL_ID_CLASSIFIER", classifier)) if not value]
    if missing:
        raise ConfigError(f"con LLM_PROVIDER=bedrock faltan: {', '.join(missing)}")
    try:
        per_minute = int(_get(env, "LLM_RATE_LIMIT_PER_MINUTE", str(DEFAULT_RATE_LIMIT)))
        max_wait = float(_get(env, "LLM_MAX_QUEUE_WAIT_SECONDS", str(DEFAULT_MAX_WAIT)))
        limits = RateLimits(per_minute, 60.0)
    except ValueError as exc:
        raise ConfigError(f"límite de tasa inválido: {exc}") from exc
    retrier = Retrier(_retry_policy(env))
    return BedrockAgent(BedrockLLM(client or _bedrock_client(env), chat, classifier, limits,
                                   max_wait=max_wait, retrier=retrier))
