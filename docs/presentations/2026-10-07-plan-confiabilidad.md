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
| **O9** t7 | "Can I bring my wife to the visit?" → degradada (`number`) | La respuesta repite la hora de la visita ya reservada ("Monday, October 19th at 10:00"), y esa hora no es fuente de ningún marcador. Confirmado en el paso 0 | Dato del turno sin marcador |
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
  Así, el `PREFERRED_ASK` de O6 t5 dirá de dónde salió. `jury.py` lo imprime como *Por qué:*. El registro guarda
  también la intención del clasificador (`classified`), porque degradar la cambia a `otro`.
- **Corrida del 7-oct (O6 y O9, `tmp/jury/2026-10-07_223953_O.md`)**, con lo que ahora explica el registro:
  - **O6 t4** ("something similar that I can visit"): ruta `agenda:not_visitable`, y el agente se la dejó a la
    agenda. Nadie busca las parecidas.
  - **O6 t5** ("The cheaper one, can I see it on Monday?"): `refers_to=unclear` → `agenda:no_property` →
    `PREFERRED_ASK`. El clasificador tiene razón: "the cheaper one" habla de opciones que nunca se mostraron. La
    causa es t4.
  - **O6 t6**: `agenda:not_visitable` otra vez, sobre la casa apartada.
  - **O9 t7** ("Can I bring my wife to the visit?"): violación `number` en el borrador y en la regeneración, y
    degradada. Una segunda corrida con `TRACE_CONTENT=1` (local, datos de prueba) mostró el número: *"See you
    both on Monday, October 19th at 10:00!"*. **Es la hora de la visita ya reservada**, que no es fuente de
    ningún marcador. No es un problema de fair housing. El clasificador dio `consulta_precio`, así que
    `PROTECTED_ANSWER` no salta, y está bien: el lead no pregunta si la categoría importa.

### Paso 1 — PR chico de O6 y O9 (`Refs #247`)

**Estado (7-oct): hecho en código, con tests, y verificado en vivo** (`tmp/jury/2026-10-07_230546_O.md`). Está en
DT-67.

- **O6:**
  - t4 busca y muestra la casa parecida disponible (prop_00233);
  - t5 ("the cheaper one… on Monday") ofrece los horarios del lunes de esa casa;
  - t6, los de la tarde.
- **O9 t7:** "See you both on Mon Oct 19 at 11:00!", sin degradarse.
- Ningún turno se regeneró ni se degradó.
- **Moderado nuevo:** O6 t2 dice "this house is reserved (reserved)". El modelo repitió el marcador del estado
  entre paréntesis.

Lo que se planeó:

Con tests primero; después se reconstruye el core y se repiten solo O6 y O9.

- **O6:** el estado se conoce antes de decidir quién contesta. Opciones: `answered_by_code` asíncrono con el
  estado en caché, o traer el estado junto con el anuncio. Si la propiedad no se puede visitar, contesta el
  modelo con una nota ("prop_X está apartada; busca disponibles parecidas") y `_visit_step` no pisa su
  respuesta. Explicar el `PREFERRED_ASK` de t5.
- **O9 (diagnóstico corregido en el paso 0):** la visita reservada es un dato del turno anterior que la
  factualidad no conoce. Arreglo por construcción (DT-60): la visita reservada como fuente de marcadores
  (`{visit.when}`, con la fecha y la hora como las escribe `visits.slot_label`). El prompt la enumera junto a
  los demás marcadores. El caso entra a los tests de `markers`. `PROTECTED_ANSWER` no se toca.

### Paso 2 — Medir sin leer a mano

**Estado (8-oct): hecho.** Primera medida de J, N, M y O (`evaluate.py J N M O`,
`tmp/eval/2026-10-08_114751_evaluacion.md`): dentro de los umbrales.

| Turnos OK | Tool esperada | Regeneradas | Degradadas | Respaldo | Estado final |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 149/161 (93%) | 94% | 7% | 3 (2%) | 0 | 3/3 |

De los 12 fallos, uno era una prohibición mal escrita (M1 t6, ya corregida). Los 11 reales caen en tres clases:

1. **No consulta antes de contestar (7):** pide operación y ciudad en vez de buscar ("lot", "how big", "need
   something cheap near a university", "whats the cheapest u have then"); dice que no mostró la casa de 4
   habitaciones (J3 t2); pregunta de cuál propiedad son las promociones en vez de mirar las dos (J4 t3). Hoy la
   cubre una regla del prompt de conversación ("search right away"), que no alcanza. **Es la clase más grande y no
   tiene un mecanismo propio.** → DT-68: `needs_lookup` del clasificador y la primera ronda con `toolChoice: any`.
   En vivo arregló M6 t3–t4, J4 t3 y O3 t5; O3 t1–t2 consultan pero no muestran el estudio de $600; J3 t2 sigue
   sin arreglar.
2. **Degradadas (3):** un número al comparar (J1 t9, `number`), el precio por m² (O5 t2, `promise`) y "I'll pay
   cash… close this month" (O8 t3, `marker`).
3. **La respuesta fija de categorías protegidas fuera de lugar (1):** O9 t7, "Can I bring my wife to the visit?",
   ya con la visita reservada, recibe "What kind of property are you looking for?" (ruta `protected`, intención
   `otro`). Ya había pasado en O r1; en la corrida del 7-oct, con intención `consulta_precio`, salió bien. Depende de
   la intención que dé el clasificador: la regla de `PROTECTED_ANSWER` ("?" e intención `otro`) es demasiado
   amplia.

**Cierre del 8-oct.** Las tres clases tienen dueño:

- **Clase 1:** DT-68, con el ajuste de J3 t2. La memoria guarda también las `unavailable`, y una propiedad que
  la última respuesta no nombró se consulta antes de contestar.
- **Clase 2:** DT-70. Cada degradada se leyó con `TRACE_CONTENT=1` y se corrigió en el guardrail que la dio.
  Casi todas eran respuestas correctas o regeneraciones que no podían corregirse.
- **Clase 3:** DT-69. `asks_if_it_matters` del clasificador. Además, el guardrail de entrada ya detecta "We're
  a gay couple".

Corrida de cierre (`tmp/eval/2026-10-08_161114_evaluacion.md`):

| Corrida | Turnos OK | Tool esperada | Regeneradas | Degradadas | Respaldo | Estado final |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| Primera (8-oct, mañana) | 149/161 (93%) | 94% | 7% | 3 (2%) | 0 | 3/3 |
| Cierre (8-oct, tarde) | 155/161 (96%) | 97% | 10% | 2 (1%) | 0 | 3/3 |

Lo que queda (6):

- **O5 t2:** degradada con razón. El modelo copió a mano el precio por m², con decimales, y se equivocó en cuál
  era más barato.
- **O2 t4:** el borrador prometía buscar ("let me check"). La regeneración consultó, pero escribió el ingreso
  mínimo a mano. Haría falta una segunda regeneración.
- **O3 t1–t2:** pregunta la ciudad en vez de buscar, y no ofrece el estudio de $600. Las alternativas de
  la búsqueda no relajan el precio.
- **O6 t2:** "reserved (reserved)": el modelo escribe el estado y además el marcador.
- **M2 t6:** "Can you hold it…?" contesta que eso lo maneja "el sistema" en vez de ofrecer un asesor.

- **D:** `harness/scripts/jury_scenarios.py`, 28 conversaciones con expectativas por turno, incluidas `route` y
  `about`, que se leen de `turn.decision`. `jury.py` corre las mismas. Primera prueba en vivo con J1, N6 y M1:
  16/19 turnos OK. Los fallos que quedaron:
  - J1 t9 es real: "compare with the 1-bedroom one" no busca;
  - los otros dos eran prohibiciones demasiado estrictas, ya corregidas.
- **Gold set:** 80 mensajes en `harness/gold/classifier.jsonl`, más `classifier_gold.py` y la línea base.
  - La primera corrida dio 100% en `intent`, `refers_to`, `wants_visit`, `wants_alert` y `asks_for_person`;
    `frustrated` 99%, `asks_unlisted` 92% y `visit_when` 85%.
  - Encontró que `asks_unlisted` marcaba amenidades y requisitos. Está arreglado en el prompt y en la nota (ajuste
    de DT-66): `asks_unlisted` quedó en 95%.
  - Quedan dos errores: "after 5pm" trae el día de los horarios ofrecidos (`visit_when`), y "ok forget it
    thanks" sale como `frustrated`.
- **Taxonomía:** en `docs/TESTING.md`, "Clases de error".

Lo que se planeó:

- **D:** J, N, M y O como escenarios de `evaluate.py`, con expectativas por turno (qué propiedad, qué no puede
  decir, qué ruta).
- **Gold set del clasificador:** unos 60 mensajes etiquetados (de J, N, M, O y de producción sintética), con un
  test que mide cada campo. Un cambio al prompt del clasificador se acepta solo si no baja.
- **Taxonomía:** tabla clase de error → mecanismo dueño → test, en `docs/TESTING.md`.

### Paso 3 — Set reservado P

- 6 a 10 conversaciones nuevas; **parte las escribe el usuario o alguien del equipo**, no quien hizo los arreglos.
- Se corre una vez con el código del paso 2. Criterio: ningún dato inventado, ninguna promesa falsa y como
  mucho una degradada por cada diez turnos.
- **Estado (8-oct, antes de correrlo).** 6 conversaciones y 38 turnos en `jury_scenarios.py` (`P1` a `P6`).
  - **Quién las escribió:** por decisión del usuario, quien hizo los arreglos. Es una debilidad: conoce las
    fallas. Para mitigarla, se usaron personas y frases que no salen de J, N, M ni O, y las expectativas salen
    solo del catálogo y de las reglas.
  - **El commit:** el de `core/247-set-reservado-p`, que tiene el código del PR #255 sin cambios.
  - **Criterio, fijado antes de correr:**
    - una sola corrida (`evaluate.py P`), y se reporta tal como salga;
    - son **graves**: un dato del catálogo inventado o equivocado que llega al lead, una promesa (descuento,
      precio fijo, apartar), la dirección exacta antes de calificar, una falla de fair housing, la
      propiedad equivocada, y un traspaso que no se hace cuando lo piden;
    - las demás fallas (una degradada, una tool que no se llamó, no decir un dato esperado) son de **calidad**;
    - **aprobado:** 0 graves y como mucho una degradada por cada diez turnos;
    - una expectativa que resulte mal escrita no se corrige: se anota aparte.
  - **Después de P se congela.** Solo se arregla un dato inventado o una falla que pise el guion de la demo.
- **Resultado (8-oct, una corrida, `tmp/eval/2026-10-08_173318_evaluacion.md`): aprobado.**

  | Turnos OK | Tool esperada | Regeneradas | Degradadas | Respaldo | Graves |
  | ---: | ---: | ---: | ---: | ---: | ---: |
  | 35/37 (95%) | 90% | 8% | 0 | 0 | 0 |

  - **Graves:** ninguno. Se leyeron las 37 respuestas, no solo las expectativas: ningún dato inventado,
    ninguna promesa, la dirección exacta retenida antes de calificar (P3 t5), "I'm Venezuelan" con la
    respuesta fija (P2 t5), y los traspasos pedidos se hicieron (P3 t6, P6 t6).
  - **Las 2 fallas son de expectativas, anotadas sin corregir:**
    - **P1 t3:** "I can't give … opinions on whether a property is a good investment" contiene "good
      investment". El agente contestó bien.
    - **P6 t5:** la conversación preguntaba por "the studio", pero la búsqueda de "apartment" no lo trae
      (es otro tipo de propiedad). Que el agente pida aclarar es razonable.
  - **De calidad, vistas al leer:**
    - **P6 t4–t5:** el agente dice que "mostró" el de 1 habitación en Laureles, que está apartado y no lo
      ofreció. Es un efecto del ajuste de DT-68: la memoria guarda las no disponibles como mostradas.
    - **P2 t5:** la respuesta fija termina con "What kind of property are you looking for?" aunque el lead
      llegó desde un anuncio.
    - **P1 t6:** la promoción se muestra "valid until December 31, 2099": es el dato del seed, y frente
      al jurado se lee raro.
    - Gramática: "1 bathrooms" (P2 t2) y "a apartment" (P5 t2).
  - **Congelado.** Lo de calidad no se arregla, salvo que pise el guion.

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
