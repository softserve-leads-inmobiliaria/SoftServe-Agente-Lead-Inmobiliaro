# OBS-7 — `/health`, logs estructurados, PII enmascarada

**Épica:** E4 · Observabilidad y calidad  
**Prioridad:** P1 · **Puntos:** 2 · **Track:** Cross  
**Depende de:** CT-5

## Historia de usuario

Como equipo, quiero logs en JSON con cualquier PII enmascarada desde el principio, para poder operar/depurar en producción sin exponer datos sensibles en texto plano.

## Criterios de aceptación

- [ ] Endpoint `/health` en cada servicio.
- [ ] Logs en JSON en los 3 servicios, incluyendo los *handlers* propios del servidor ASGI elegido (no solo el *root logger* — algunos frameworks configuran sus propios loggers antes de que la app arranque).
- [ ] Enmascarado de PII incluido en esta misma historia — si no hay un caso real de PII en logs todavía, esta historia documenta esa decisión explícitamente en la tabla de estado de `docs/DESIGN/guardrails.md`, en vez de dejarlo en el aire.

## Definition of Done

- [ ] Código en `main` vía PR revisado por al menos otra persona del equipo (idealmente quien consume el contrato/módulo desde el otro lado).
- [ ] Suite de tests del servicio/paquete afectado en verde en CI.
- [ ] Ningún *contract test* roto (`tests/contracts/`).
- [ ] Estado de la historia actualizado en GitHub Issues/Project — nunca en este archivo (ver `docs/ESTRUCTURA-DEL-PROYECTO.md` §5).
- [ ] Si la historia introduce o cambia una decisión de arquitectura, tiene su entrada correspondiente en `docs/DECISIONS/decisions.md`.

## Notas para v2 (aprendido en v1)

V1 partió esto en logs (sí construido) y PII (nunca, sin caso real que enmascarar). Para v2 es válido llegar a la misma conclusión, pero la decisión debe tomarse explícitamente en esta historia, no heredarse en silencio.
