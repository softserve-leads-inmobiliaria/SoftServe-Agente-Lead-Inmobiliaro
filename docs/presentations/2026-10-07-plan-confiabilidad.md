# Plan de confiabilidad hasta la demo — 7 de octubre de 2026

Continúa a [`2026-10-07-prueba-de-jurado.md`](2026-10-07-prueba-de-jurado.md), en especial §3 bis (causa raíz) y
§5 ter (O r2). Este documento reemplaza su §6 como orden de trabajo. Issues: #247 (jurado), #51 (semana de
endurecimiento), #52 (demo, Z-2).

## 1. Estado de partida

- `main` en `63c430c`, con #249 fusionado (DT-62 a DT-66). #229 cerrado; #247 y #51 abiertos.
- Rama de trabajo: `core/247-o6-y-fair-housing`, creada desde `main`.
- Última corrida, **O r2**: 59 turnos, 2 degradadas y 3 errores graves, sin datos inventados.

| Corrida | Turnos | Degradadas | Graves |
| :--- | ---: | ---: | ---: |
| M r1 | 35 | 2 | 3 |
| M r2 | 35 | 0 | 0 |
| O r1 | 59 | 3 | 6 |
| O r2 | 59 | 2 | 3 |

Ojo: M y O ya se usaron para arreglar. Son sets de desarrollo; no miden si los arreglos generalizan.

## 2. Cómo se trabaja desde aquí

Cinco principios. Cada cambio de este plan debe poder justificarse con uno de ellos.

1. **Primero se mide.** Ningún cambio de comportamiento sin un caso que lo reproduzca y que quede como
   regresión. Sets de desarrollo (J, N, M, O) separados de uno reservado (P), que no se mira hasta medir.
2. **Cada clase de error tiene un solo mecanismo dueño** (tabla de §3 bis del documento del jurado). Un error
   nuevo se clasifica antes de arreglarlo; si su clase ya tiene dueño, se arregla el dueño, no se agrega otro.
3. **El código decide, el modelo redacta.** Entender (clasificador) → fundamentar (tools, estado) → decidir
   (código) → redactar (modelo con marcadores) → verificar (guards). Lo que hoy se adivina sobre el texto pasa
   a un campo del clasificador (DT-62, sin llamadas extra) o a construcción. Nada de regex nuevas por incidente.
4. **Cada falla se explica sola.** El log de cada turno dice qué entendió el clasificador, por qué ruta fue y qué
   regla de salida saltó. Hoy O9 no se puede explicar porque los logs se perdieron al reconstruir el core.
5. **El clasificador también se evalúa.** Tiene 11 campos y 3.650 caracteres de prompt: necesita su propio gold
   set, igual que los guards.

## 3. Diagnóstico de lo que queda

| Caso | Qué pasa | Causa | Clase |
| :--- | :--- | :--- | :--- |
| **O6** t4–t6 | Casa apartada que llega desde un anuncio. "Show me something similar that I can visit" no busca parecidas; después, "I can't book the visit from here right now". En t5 sale `PREFERRED_ASK` sin explicación | `_answered_by_code` es síncrono y decide antes de saber el estado: la propiedad del anuncio no trae `status` | Decidir antes de saber el estado |
| **O9** t7 | "Can I bring my wife to the visit?" → el guard de categoría protegida reescribe y luego la factualidad degrada | Un acompañante de la familia no es una pregunta sobre la categoría protegida. Falta saber qué regla de factualidad saltó (no hay logs) | Fair housing / verificación por detección |
| **O2** t4 | "Our pension is $2,500 a month. Is that enough?" → degradada (`number`) | No llama a `check_requirements` para comparar con el requisito de dos propiedades | Comparar contra requisitos |

O2 queda fuera de la demo (ver §5).

## 4. Pasos, en orden

### Paso 0 — Observabilidad

- Escribir en `audit_log` y en la traza de cada turno: el JSON del clasificador (`intent`, flags, `refers_to`,
  `visit_when`), la ruta (`agenda` o `agente`) y los tipos de violación de salida (sin valores, para no guardar
  contenido).
- `harness/scripts/jury.py` imprime eso debajo de cada turno de la transcripción.
- `TRACE_CONTENT=1` solo en local y con datos de prueba; después se reinicia el core sin él.
- Hecho cuando: una corrida de O6 y O9 deja en la transcripción por qué contestó cada turno.
- **Estado (7-oct): hecho en código y tests.** Cada turno deja `turn.decision` en `audit_log`, con `actor='turn'`.
  Lleva:
  - `route` y `agent_route`;
  - `intent`, `flags`, `refers_to` y `visit_when`.

  Las auditorías de la factualidad llevan además `kinds`. La agenda dice qué contestó (`VisitStep.kind`):
  `offer`, `closest`, `not_visitable`, `fallback_empty`, `fallback_error`, `scheduling_error`, `no_slots`…
  Así, el `PREFERRED_ASK` de O6 t5 dirá de dónde salió. `jury.py` lo imprime como *Por qué:*. Falta correr O6 y
  O9 con el core reconstruido.

### Paso 1 — PR chico de O6 y O9 (`Refs #247`)

Con tests primero; después se reconstruye el core y se repiten solo O6 y O9.

- **O6:** el estado se conoce antes de decidir quién contesta. Opciones: `answered_by_code` asíncrono con el
  estado en caché, o traer el estado junto con el anuncio. Si la propiedad no se puede visitar, contesta el
  modelo con una nota ("prop_X está apartada; busca disponibles parecidas") y `_visit_step` no pisa su
  respuesta. Explicar el `PREFERRED_ASK` de t5.
- **O9:** `PROTECTED_ANSWER` solo cuando el lead pregunta si la categoría importa ("do you rent to families?"),
  no cuando la menciona ("can I bring my wife?"). Encontrar la regla de factualidad que bloqueó y sumar el caso
  al gold set.

### Paso 2 — Medir sin leer a mano

- **D:** J, N, M y O como escenarios de `evaluate.py`, con expectativas por turno (qué propiedad, qué no puede
  decir, qué ruta).
- **Gold set del clasificador:** unos 60 mensajes etiquetados (de J, N, M, O y de producción sintética), con un
  test que mide cada campo. Un cambio al prompt del clasificador se acepta solo si no baja.
- **Taxonomía:** tabla clase de error → mecanismo dueño → test, en `docs/TESTING.md`.

### Paso 3 — Set reservado P

- 6 a 10 conversaciones nuevas; **parte las escribe el usuario o alguien del equipo**, no quien hizo los arreglos.
- Se corre una vez con el código del paso 2. Criterio: ningún dato inventado, ninguna promesa falsa y como
  mucho una degradada por cada diez turnos.

### Paso 4 — Según lo que muestre P

Solo si P muestra la clase "sí o no sobre datos que no existen":

- `details` en la ficha de la propiedad, con valores sembrados y `null` = "no consta". El código escribe "no
  consta, lo confirma un asesor".
- Un FAQ fijo de 10 a 15 respuestas, elegido por un campo del clasificador. No se rescata #195 entero.

### Paso 5 — Cierre

1. Congelar el comportamiento.
2. Guion de la demo (#52), con lo que P mostró que es seguro.
3. `evaluate.py --reps 5` una sola vez y comparar con la línea base del 6-oct en `TESTING.md`.
4. Cerrar #247 y #51.

## 5. Fuera de alcance para la demo

- Que el estado se escriba solo por marcador (sin excepciones en la factualidad).
- Buscar sin ciudad.
- El refactor completo del pipeline en etapas.
- O2 (`check_requirements` para comparar ingreso contra requisitos).

## 6. Reglas que siguen valiendo

- **DT-62:** lo que se adivina sobre el texto pasa a un campo del clasificador, con 0 llamadas extra. Un arreglo
  nuevo es estructural o es un test, nunca una regex o una regla de prompt por incidente.
- **Bedrock a 10 RPM:** nada en paralelo (ni dos sets, ni jurado con evaluación).
- Después de tocar el core: `docker compose up -d --build core`.
- Postgres levantado antes de `pytest`.
- `TRACE_CONTENT=1` solo en local con datos de prueba.
- PRs en draft; sacarlos de draft, aprobar y fusionar lo decide el dueño del repo.

## 7. Pendiente de definir

- La fecha de la demo, para saber hasta qué paso se llega.
