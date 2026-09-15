# OBS-3 — Suite de transiciones de la FSM

**Épica:** E4 · Observabilidad y calidad  
**Prioridad:** P0 · **Puntos:** 3 · **Track:** Cross  
**Depende de:** A-1

## Historia de usuario

Como equipo, quiero una suite que cubra todas las aristas de la FSM (incluido `HUMAN_HANDOFF`), para no depender solo de la demo en vivo para encontrar estados inalcanzables.

## Criterios de aceptación

- [ ] Una arista de la FSM, un test como mínimo.

## Definition of Done

- [ ] Código en `main` vía PR revisado por al menos otra persona del equipo (idealmente quien consume el contrato/módulo desde el otro lado).
- [ ] Suite de tests del servicio/paquete afectado en verde en CI.
- [ ] Ningún *contract test* roto (`tests/contracts/`).
- [ ] Estado de la historia actualizado en GitHub Issues/Project — nunca en este archivo (ver `docs/ESTRUCTURA-DEL-PROYECTO.md` §5).
- [ ] Si la historia introduce o cambia una decisión de arquitectura, tiene su entrada correspondiente en `docs/DECISIONS/decisions.md`.
