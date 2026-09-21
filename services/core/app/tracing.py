"""Trazas por turno (OBS-1, DT-9, DT-18): OpenTelemetry hacia Phoenix, sin datos de los leads.

- Sin `OTEL_EXPORTER_OTLP_ENDPOINT` las trazas están apagadas: los *spans* de la API son no-op y
  el core funciona igual. Con él, los *spans* se exportan por OTLP/HTTP (Phoenix en el compose).
- Exportar nunca puede romper un turno: el exportador corre en segundo plano y sus fallos solo se
  registran en el log.
- Las trazas no llevan el texto de los mensajes ni datos de contacto. La auto-instrumentación del
  proveedor de LLM sí los grabaría por defecto, así que se configura para ocultar el contenido y
  conservar solo modelo, tokens y tiempos. `TRACE_CONTENT=1` lo activa para depurar en local.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

logger = logging.getLogger("core.tracing")

TRACER_NAME = "core"
DEFAULT_PROJECT = "lead-agent"

# Atributo que Phoenix (OpenInference) usa para pintar cada span.
SPAN_KIND = "openinference.span.kind"

_provider: TracerProvider | None = None


def get_tracer() -> trace.Tracer:
    """El tracer de `core`: el configurado aquí o, si no hay ninguno, el no-op de la API."""
    return (_provider or trace.get_tracer_provider()).get_tracer(TRACER_NAME)


def content_hidden() -> bool:
    return os.environ.get("TRACE_CONTENT", "").lower() not in ("1", "true", "yes")


def _trace_config() -> Any:
    from openinference.instrumentation import TraceConfig

    hide = content_hidden()
    return TraceConfig(hide_inputs=hide, hide_outputs=hide, hide_input_messages=hide,
                       hide_output_messages=hide, hide_llm_invocation_parameters=hide)


def instrument_libraries(provider: TracerProvider) -> list[str]:
    """Activa la auto-instrumentación del proveedor de LLM (Bedrock, DT-6) y del SDK de MCP.

    Si un instrumentador no está instalado se omite y se avisa en el log: el core no depende de él.
    """
    active = []
    for name, module, cls in (("bedrock", "openinference.instrumentation.bedrock", "BedrockInstrumentor"),
                              ("mcp", "openinference.instrumentation.mcp", "MCPInstrumentor")):
        try:
            instrumentor = getattr(__import__(module, fromlist=[cls]), cls)()
            if not instrumentor.is_instrumented_by_opentelemetry:
                kwargs = {"config": _trace_config()} if name == "bedrock" else {}
                instrumentor.instrument(tracer_provider=provider, **kwargs)
            active.append(name)
        except Exception:  # noqa: BLE001 - un instrumentador roto no debe tumbar el servicio
            logger.warning("instrumentación %s no disponible", name, exc_info=True)
    return active


def configure(exporter: SpanExporter | None = None, *, processor: Any = None,
              service_name: str = "core", instrument: bool = True) -> TracerProvider | None:
    """Enciende las trazas. Sin `exporter` ni `OTEL_EXPORTER_OTLP_ENDPOINT` las deja apagadas.

    `processor` permite a los tests exportar de forma síncrona en memoria.
    """
    global _provider
    if exporter is None:
        if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
            return None
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter()
    provider = TracerProvider(resource=Resource.create({
        "service.name": service_name,
        "openinference.project.name": os.environ.get("OTEL_PROJECT_NAME", DEFAULT_PROJECT),
    }))
    provider.add_span_processor((processor or BatchSpanProcessor)(exporter))
    _provider = provider
    if instrument:
        instrument_libraries(provider)
    return provider


def reset() -> None:
    """Vuelve al estado sin trazas (para los tests)."""
    global _provider
    if _provider is not None:
        _provider.shutdown()
    _provider = None
    for module, cls in (("openinference.instrumentation.bedrock", "BedrockInstrumentor"),
                        ("openinference.instrumentation.mcp", "MCPInstrumentor")):
        try:
            getattr(__import__(module, fromlist=[cls]), cls)().uninstrument()
        except Exception:  # noqa: BLE001
            pass
