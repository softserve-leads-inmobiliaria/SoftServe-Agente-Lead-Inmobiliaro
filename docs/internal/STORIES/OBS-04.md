# OBS-4 — Gold sets como *gate* de merge

**Épica:** E4 · Observabilidad y calidad  
**Prioridad:** P0 · **Puntos:** 3 · **Track:** Cross  
**Depende de:** C-6, C-7, C-8

## Historia de usuario

Como equipo, quiero que CI bloquee el merge si cualquier gold set de guardrails baja del umbral, para que un guardrail roto nunca llegue a `main`.

## Criterios de aceptación

- [ ] CI falla si cualquier gold set no está al umbral definido en su propia historia.

## Definition of Done

- [ ] Código en `main` vía PR revisado por al menos otra persona del equipo (idealmente quien consume el contrato/módulo desde el otro lado).
- [ ] Suite de tests del servicio/paquete afectado en verde en CI.
- [ ] Ningún *contract test* roto (`tests/contracts/`).
- [ ] Estado de la historia actualizado en GitHub Issues/Project — nunca en este archivo (ver `docs/ESTRUCTURA-DEL-PROYECTO.md` §5).
- [ ] Si la historia introduce o cambia una decisión de arquitectura, tiene su entrada correspondiente en `docs/DECISIONS/decisions.md`.
