# OBS-5 — Contract tests en CI

**Épica:** E4 · Observabilidad y calidad  
**Prioridad:** P0 · **Puntos:** 3 · **Track:** Cross  
**Depende de:** CT-1, CT-2, CT-3

## Historia de usuario

Como equipo, quiero un test por contrato corriendo en CI desde el principio, para que ningún cambio rompa la forma de datos acordada sin que CI lo note.

## Criterios de aceptación

- [ ] Un test por contrato, incluido el schema de cada tool `catalog.*`.

## Definition of Done

- [ ] Código en `main` vía PR revisado por al menos otra persona del equipo (idealmente quien consume el contrato/módulo desde el otro lado).
- [ ] Suite de tests del servicio/paquete afectado en verde en CI.
- [ ] Ningún *contract test* roto (`tests/contracts/`).
- [ ] Estado de la historia actualizado en GitHub Issues/Project — nunca en este archivo (ver `docs/ESTRUCTURA-DEL-PROYECTO.md` §5).
- [ ] Si la historia introduce o cambia una decisión de arquitectura, tiene su entrada correspondiente en `docs/DECISIONS/decisions.md`.
