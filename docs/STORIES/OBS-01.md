# OBS-1 — Traza por turno

**Épica:** E4 · Observabilidad y calidad  
**Prioridad:** P0 · **Puntos:** 5 · **Track:** Cross  
**Depende de:** A-1, CT-6

## Historia de usuario

Como equipo, quiero una traza completa de cada turno (estado FSM, tools, guardrails, latencia, costo), para depurar y medir el sistema sin adivinar.

## Criterios de aceptación

- [ ] Auto-instrumentación del SDK del proveedor de LLM elegido.
- [ ] *Spans* manuales por turno y por tool del catalog.

## Definition of Done

- [ ] Código en `main` vía PR revisado por al menos otra persona del equipo (idealmente quien consume el contrato/módulo desde el otro lado).
- [ ] Suite de tests del servicio/paquete afectado en verde en CI.
- [ ] Ningún *contract test* roto (`tests/contracts/`).
- [ ] Estado de la historia actualizado en GitHub Issues/Project — nunca en este archivo (ver `docs/ESTRUCTURA-DEL-PROYECTO.md` §5).
- [ ] Si la historia introduce o cambia una decisión de arquitectura, tiene su entrada correspondiente en `docs/DECISIONS/decisions.md`.

## Notas para v2 (aprendido en v1)

Una vez instrumentado, revisar la primera traza real completa antes de asumir que solo se ve lo que se instrumentó a mano — el SDK del protocolo (MCP u otro) puede traer instrumentación nativa dormida hasta que algo active un *provider* real (lecciones v1 §2).
