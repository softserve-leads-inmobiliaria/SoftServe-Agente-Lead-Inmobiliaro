# OBS-2 — Métricas del funnel en Postgres

**Épica:** E4 · Observabilidad y calidad  
**Prioridad:** P0 · **Puntos:** 3 · **Track:** Cross  
**Depende de:** A-1, C-3

## Historia de usuario

Ver B-9 — esta historia construye la tabla `FunnelEvent`; B-9 es quien la consume.

## Criterios de aceptación

- [ ] Tabla append-only.
- [ ] Una fila por transición real de `fsm_state`, no por turno.

## Definition of Done

- [ ] Código en `main` vía PR revisado por al menos otra persona del equipo (idealmente quien consume el contrato/módulo desde el otro lado).
- [ ] Suite de tests del servicio/paquete afectado en verde en CI.
- [ ] Ningún *contract test* roto (`tests/contracts/`).
- [ ] Estado de la historia actualizado en GitHub Issues/Project — nunca en este archivo (ver `docs/ESTRUCTURA-DEL-PROYECTO.md` §5).
- [ ] Si la historia introduce o cambia una decisión de arquitectura, tiene su entrada correspondiente en `docs/DECISIONS/decisions.md`.

## Notas de diseño

Si B-9 (el dashboard que la consume) no se planea en el mismo ciclo, dejarlo anotado explícitamente para no dejar una tabla sin consumidor.
