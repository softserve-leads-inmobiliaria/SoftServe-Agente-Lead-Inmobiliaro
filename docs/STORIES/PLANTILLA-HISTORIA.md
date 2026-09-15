# <ID> — <Título corto en imperativo o sustantivo, no una frase completa>

**Épica:** <E0-E5> · <nombre de la épica>
**Prioridad:** <P0|P1|P2> · **Puntos:** <1|2|3|5|8> · **Track:** <A|B|C|Cross>
**Depende de:** <IDs de las que depende, o —>

## Historia de usuario

Como <rol>, quiero <acción/capacidad>, para <beneficio/razón>.

## Criterios de aceptación

- [ ] <Criterio verificable, no ambiguo — evitar "funciona bien", preferir "responde con el `address_exact` real cuando `lead_qualified=true`">
- [ ] <...>

## Definition of Done

- [ ] Código en `main` vía PR revisado por al menos otra persona del equipo (idealmente quien consume el contrato/módulo desde el otro lado).
- [ ] Suite de tests del servicio/paquete afectado en verde en CI.
- [ ] Ningún *contract test* roto (`tests/contracts/`).
- [ ] Estado de la historia actualizado en GitHub Issues/Project — nunca en este archivo (ver `docs/ESTRUCTURA-DEL-PROYECTO.md` §5).
- [ ] Si la historia introduce o cambia una decisión de arquitectura, tiene su entrada correspondiente en `docs/DECISIONS/decisions.md`.
- [ ] <Criterio de DoD específico de esta historia, si aplica — p. ej. "gold set en verde en CI", "verificado en vivo contra el sistema real, no solo mockeado">

## Notas para v2 (aprendido en v1)

<Opcional. Solo si esta historia repite o corrige explícitamente algo documentado en la retrospectiva o en LESSONS-LEARNED.md de v1 — si no aplica, borrar esta sección entera en vez de dejarla vacía.>

---

**Al crear una historia nueva:**
1. Copiar este archivo con el nombre `<ID>.md` (dos dígitos si el número es de un solo dígito, p. ej. `D-01.md`).
2. Agregar su fila en la tabla de la épica correspondiente en [`README.md`](README.md).
3. Crear el Issue en GitHub con el mismo `<ID>` en el título y dejar que el *workflow* de alta automática lo agregue al Project (ver `docs/ESTRUCTURA-DEL-PROYECTO.md` §5) — no agregarlo a mano.
