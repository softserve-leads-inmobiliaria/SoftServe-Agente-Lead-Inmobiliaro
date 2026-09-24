# OBS-6 — Conversaciones E2E contra el bot real

**Épica:** E4 · Observabilidad y calidad  
**Prioridad:** P1 · **Puntos:** 3 · **Track:** Cross  
**Depende de:** B-1, A-12

## Historia de usuario

Como equipo, quiero un conjunto de conversaciones E2E ejecutables de principio a fin contra los servicios reales, para verificar el sistema completo, no solo unidades mockeadas.

## Criterios de aceptación

- [ ] Mínimo 8 guiones, crecidos incrementalmente desde CT-9 en cada sprint relevante, no todos de golpe al final.
- [ ] **Al menos una corrida real (no mockeada) contra los 3 servicios antes de cerrar la historia.**

## Definition of Done

- [ ] Código en `main` vía PR revisado por al menos otra persona del equipo (idealmente quien consume el contrato/módulo desde el otro lado).
- [ ] Suite de tests del servicio/paquete afectado en verde en CI.
- [ ] Ningún *contract test* roto (`tests/contracts/`).
- [ ] Estado de la historia actualizado en GitHub Issues/Project — nunca en este archivo (ver `docs/ESTRUCTURA-DEL-PROYECTO.md` §5).
- [ ] Si la historia introduce o cambia una decisión de arquitectura, tiene su entrada correspondiente en `docs/DECISIONS/decisions.md`.

## Notas de diseño

Un test unitario mockeado de la herramienta de arnés puede estar en verde mientras la herramienta tiene bugs reales (timeouts insuficientes, excepciones no capturadas) que solo aparecen contra la red real. "Correr una vez de verdad" es parte de la Definition of Done aquí, no un paso opcional.
