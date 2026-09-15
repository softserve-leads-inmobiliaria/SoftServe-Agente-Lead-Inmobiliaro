# OBS-8 — Plan B de observabilidad documentado

**Épica:** E4 · Observabilidad y calidad  
**Prioridad:** P2 · **Puntos:** 1 · **Track:** Cross  
**Depende de:** OBS-1

## Historia de usuario

Como equipo, quiero un plan B de observabilidad documentado, para no bloquear el proyecto si la herramienta principal falla.

## Criterios de aceptación

- [ ] Alternativa concreta documentada en `docs/DECISIONS/decisions.md`, sin implementarse a menos que haga falta.

## Definition of Done

- [ ] Código en `main` vía PR revisado por al menos otra persona del equipo (idealmente quien consume el contrato/módulo desde el otro lado).
- [ ] Suite de tests del servicio/paquete afectado en verde en CI.
- [ ] Ningún *contract test* roto (`tests/contracts/`).
- [ ] Estado de la historia actualizado en GitHub Issues/Project — nunca en este archivo (ver `docs/ESTRUCTURA-DEL-PROYECTO.md` §5).
- [ ] Si la historia introduce o cambia una decisión de arquitectura, tiene su entrada correspondiente en `docs/DECISIONS/decisions.md`.
