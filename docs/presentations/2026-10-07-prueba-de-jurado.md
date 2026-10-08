# Confiabilidad del agente frente a un jurado — 7 de octubre de 2026

Qué se encontró en la prueba de jurado (#247), qué estrategia se eligió, qué quedó hecho y qué sigue. Sirve para
retomar el trabajo en otra sesión, incluso con la base de datos recién creada. Las decisiones están en
[`decisions.md`](../DECISIONS/decisions.md): DT-57 (actualización), DT-62, DT-63, DT-64 y DT-65. Cómo correr la
prueba está en [`TESTING.md`](../TESTING.md), "Prueba de jurado". El estado de cada issue vive en GitHub.

## 1. El problema

La demo la evalúa un jurado con preguntas no pautadas. La evaluación en vivo (#216) mide escenarios conocidos y
quedaba dentro de los umbrales, pero no dice qué pasa con preguntas nuevas. La prueba de jurado
(`harness/scripts/jury.py`) corre sets de conversaciones libres contra Bedrock real (Sonnet 4.5 para la
conversación, Haiku 4.5 para el clasificador), y cada respuesta se lee a mano.

Criterio para la demo: **ningún dato inventado, y a lo sumo 1 o 2 errores visibles por set.**

## 2. Lo que mostraron las corridas

| Corrida | Turnos | Degradadas | Errores graves | Qué cambió antes |
| :--- | ---: | ---: | ---: | :--- |
| J r1 | 37 | 6 (+1 traspaso por racha) | — | Línea base: los falsos positivos del guardrail |
| J r2, r3 | 37 | 1, 2 | — | Arreglos de #247: amenidades, "available", números del lead, agenda |
| N r1 | 35 | 2 | ~3 | Set nuevo |
| N r2 | 35 | 0 | — | Arreglos sobre N (mide regresión, no generalización) |
| M r1 | 35 | 2 | 3 | Set nuevo |
| **M r2** | 35 | **0** (1 regenerada) | **0** | DT-62 a DT-65 y el guard de dirección (ver §5: mide arreglos, no generalización) |
| **O r1** | 59 | **3** (5,1%) | **6** | Set nuevo de 10 perfiles, con el código de M r2 (ver §5 bis) |

Las transcripciones están en `tmp/jury/` (no se versiona; se conserva al borrar la base).

**Lectura de M r1** (35 turnos, preguntas no vistas):

- **Graves (3):**
  - M3 t3: "What are your office hours?" disparó el traspaso por frustración a un lead tranquilo.
  - M4 t3: "I don't have any houses for rent in Usaquén", y había una en venta: se arrastró el filtro de renta.
  - M5 t2: "Actually, I'd rather buy" recibió la respuesta degradada. El log del core muestra la causa: un
    falso positivo del guard de dirección en la ficha ("near Avenida El Poblado" arriba de "• 1 bedroom").
- **Moderados (4):**
  - M1 t5: afirmó que las HOA fees van aparte, sin dato.
  - M2 t3: mezcló el presupuesto con la cuota inicial.
  - M1 t7 y M5 t7: "next Tuesday afternoon" y "another day" no se respetaron. Era la tercera vez, contando
    "this weekend" en N1.
- **Menores:** una opinión ("one of the most popular neighborhoods"), una frase que se contradice y títulos con
  los campos en otro orden. M4 t6 se degradó por una referencia inventada: es seguro, pero no ayuda.
- **Bien:** no inventó Cali, zonificación, mascotas ni el piso; se negó a opinar de seguridad; mandó a un asesor
  la "promesa del colega"; entendió "the cheaper one"; admitió que es un bot.

**Conclusión de M r1.** Las degradaciones bajaron (7 → 0 → 2), así que los guardrails funcionan: ante la duda,
el agente no inventa. Pero los errores graves por set nuevo no bajaban (~3 → ~3), porque los arreglos con regex
o reglas de prompt resolvían la frase vista y no la siguiente. Con ~3 graves cada 35 turnos, una demo libre de
15 turnos tiene ~70% de probabilidad de mostrar uno.

## 3. La estrategia (DT-62)

La causa: una sola llamada de texto libre hace cinco trabajos (entender, decidir la propiedad, llamar tools,
cumplir políticas y redactar), y los chequeos leen el inglés con regex. Lo que pasó a estructura (marcadores
DT-60, agenda en el código DT-32) dejó de fallar. La estrategia es seguir haciendo eso: **cada decisión que hoy
se adivina sobre el texto pasa a un campo del JSON del clasificador**, que ya se llama en cada turno. Así no
suma llamadas, y los 10 RPM (DT-19) no lo permitirían.

Descartado: reescribir el servicio (ya es FastAPI), subagentes y un juez en vivo (más llamadas), fine-tuning.

## 4. Qué quedó hecho

| Error de M r1 | Arreglo | Dónde |
| :--- | :--- | :--- |
| M3 t3, traspaso por "frustración" | La racha traspasa solo si el clasificador lee molestia (`frustrated`) | DT-65, `handoff.py` |
| M4 t3, filtro de renta arrastrado | Las alternativas también sueltan la operación, si el lead nombró el tipo | DT-57, `alternatives.py` |
| M5 t2, degradada | La dirección exacta no cruza de línea | `guardrails.md`, `address_guard.py` |
| M1 t7, M5 t7, día ignorado | `visit_when`: la agenda filtra por día y franja, y si no hay lo dice con texto fijo | DT-64, `visits.py` |
| M4 t6 y la "propiedad equivocada" | `refers_to`: el clasificador elige entre las propiedades mostradas, y el código valida | DT-63, `interest.py`, `turn.py` |
| Respuestas distintas en cada corrida | `temperature` 0 en el modelo de conversación | DT-62 |

Además:

- C (separar tono de factualidad) no hizo falta. Ningún chequeo de tono degrada hoy, y la urgencia ya se
  recorta sin regenerar.
- Los sets J, N y M quedaron versionados en `harness/scripts/jury.py`, con lo que hay que mirar en cada turno
  que falló.
- `flujo_completo_hasta_handoff` busca un "studio": con varios resultados, la agenda ya no adivina (DT-63).
- Tests: 2216 en verde entre `services/core`, `tests/` y el arnés.

## 5. Resultado de M r2

Corrida del 7-oct (22:53–23:07 UTC), con el código de §4 en el contenedor y Bedrock real: 35 turnos en 14 min,
**0 degradadas**, 1 regenerada que se recuperó (M2 t2, un número), sin errores del LLM. La transcripción está en
`tmp/jury/2026-10-07_M_r2.md`.

**Ojo:** M ya se había corrido antes de los arreglos. Esta corrida dice que los arreglos funcionan, no que
generalicen. Para eso hace falta un set nuevo (§6).

**Los 7 errores de M r1:**

| Turno | M r1 | M r2 |
| :--- | :--- | :--- |
| M3 t3 "office hours" | Traspaso por "frustración" | ✅ Contesta y sigue; el traspaso llega en t4, cuando lo pide |
| M4 t3 "house in Usaquen" | "No houses for rent in Usaquén" | ✅ Muestra la casa en venta y pide calificar para la dirección exacta |
| M5 t2 "I'd rather buy" | Degradada | ✅ Muestra los 2 departamentos en venta |
| M1 t7 "next Tuesday afternoon" | Horarios del jueves | ✅ Martes 13-oct, 12:00, 13:00 y 14:00 |
| M5 t7 "another day" | "I didn't quite get which time" | ✅ Ofrece horarios de otro día |
| M4 t6 "the reserved apartment" | Degradada (referencia inventada) | ✅ "We've only talked about the 3-bedroom house in Usaquén… which is available" |
| M1 t5 "HOA fees included?" | Afirmaba que van aparte | ❌ Igual, y ahora se contradice: "No, HOA fees are separate…" y en la frase siguiente "I don't have the HOA fee amount in the system". El "No" sigue siendo inventado; ninguna tool trae ese dato (DT-62, consecuencias) |
| M2 t3 presupuesto y cuota inicial | Los mezclaba | ⚠️ Mejor, pero insinúa que con $140,000 cubre la cuota, y afirma sin respaldo que "could potentially finance the rest with a bank". En t6 pregunta si ese es el monto para la cuota |

**Nuevo en M r2:**

- **Moderado.** M6 t3–t5 ("lot", "how big", "is it flat land?"): no busca. Hay un terreno en Usaquen, pero el
  agente pide operación y ciudad tres veces. No inventa nada, pero se ve torpe.
- **Moderado.** M5 t6 "Let's schedule a visit": venía hablando de la prop_00231 desde t3 y aun así pregunta
  "Just to confirm, would you like to visit the 2-bedroom apartment…?". La búsqueda de t2 trajo dos, y el
  clasificador no la eligió. Es seguro (no adivina), pero agrega un turno.
- **Menor.** M5 t7: "Here are the available times on another day…" suena raro. Sale de `visits.describe` cuando
  la preferencia es solo `other_day`.
- **Menor.** M2 t6: "that's handled by the system once you're ready".
- **A revisar.** El log del core trae `no se pudo generar el resumen de la conversación (LLMFormatError)` a las
  23:01:41 UTC, en uno de los traspasos (M3 t4 o M4 t5). La ficha salió sin resumen (DT-46), y en la demo eso
  se nota. Hay dos causas posibles y el log no decía cuál:
  - el JSON se cortó por `maxTokens` (400): el recorte a la última frase completa solo se aplica al chat;
  - vino una sección vacía o null.

  Desde esta corrida, el aviso trae `error_detail` con el motivo (JSON ilegible o qué sección faltó),
  `stopReason` y los tokens de salida, sin el contenido. Si se repite en O, se arregla con eso.

**Lectura.** Hay 0 graves y 2 moderados que se ven en una demo libre (el terreno y la confirmación de la
visita). Los dos que dependen de datos que no hay (HOA fees, cuota inicial) siguen igual, como se esperaba.

## 5 bis. Resultado de O r1: no cumple el criterio

Corrida del 8-oct (00:21–00:46 UTC), 10 conversaciones escritas después de los arreglos, 59 turnos. Dio 3
degradadas (5,1%), 8 regeneradas, ningún error del LLM y ningún fallo del resumen. La transcripción está en
`tmp/jury/2026-10-07_O_r1.md`.

**Lo que funcionó en preguntas nuevas:**

- `refers_to`: "I like the second one" (O5).
- `visit_when`: "Saturday morning", "this weekend" y "Monday morning" (O5, O9).
- La reserva de punta a punta (O5, O9).
- El traspaso cuando lo piden, incluido "Could someone call us?" (O2).
- La impaciencia sin traspaso falso (O4).
- Los guardrails de entrada: fuera de alcance, *jailbreak* y español (O7, O10).
- No dio opiniones de inversión ni pronósticos (O1, O10).
- Tampoco inventó la zonificación (O8).

**Graves (6):**

| # | Turno | Qué pasó | Causa |
| :--- | :--- | :--- | :--- |
| 1 | O6 t4–t6 | Desde el anuncio de la casa **apartada**, "Show me something similar that I can visit" ofreció horarios **de esa misma casa**, y "the cheaper one" también | El estado solo se consultaba para la propiedad de `refers_to`. La recordada, que se guarda sin estado, pasaba como visitable. Es un hueco de DT-63 |
| 2 | O3 t3–t4 | "Yes, absolutely! You can share with a roommate", "Yes, 6 months should be fine!" | **Dato inventado**: políticas que el catálogo no tiene. Misma clase que las HOA fees de M |
| 3 | O3 t5 | "What's the cheapest you have" → degradada, tras 4 turnos sin buscar | No busca hasta tener la ciudad (como el "lot" de M6) |
| 4 | O5 t2 | "Best price per square meter?" → degradada | Un número calculado (precio/m²) no está en ninguna tool, y la factualidad lo bloquea |
| 5 | O6 t3 | "When will you know if it's free again?" → degradada | Una violación de `status`. Ya había pasado en J3 t3 ("When will it be available again?") |
| 6 | O9 t7 | "Can I bring my wife to the visit?" → "That doesn't affect how we help you…" | El guardrail de categoría protegida lee "my wife", y la respuesta fija de #200 no contesta lo que se preguntó |

**Moderados:**

- O2 t2, t3 y t5: pregunta tres veces "which property?" en vez de contestar para las dos que mostró.
- O2 t4: "With a monthly income of $3,450…" le atribuye al lead el requisito de la propiedad.
- O6 t6 y O9 t3: "anything in the afternoon?" saltó al lunes de la semana siguiente, y "only after 5pm"
  ofreció el jueves 15. El clasificador completó `days` u `other_day` con la oferta anterior.
- O8 t3: llama "exact address" a la aproximada, y "you're all set!". En t4 da la dirección exacta: el lead
  ya estaba calificado (pagaba en efectivo, decidía solo y cerraba este mes), así que es lo esperado (A-6).

**Menores:** "reserved (reserved)" (O6 t2) e "If you came from a listing…" (O10 t4).

**Lectura.** Los arreglos estructurales generalizaron: ninguna de sus clases falló con frases nuevas, salvo el
hueco de DT-63. Lo que falló son clases que no tenían estructura. Dos de ellas ya se repiten en más de un set,
que es el criterio de DT-62 para arreglarlas:

- **sí o no a políticas sin dato** (M1 t5, O3);
- **degradar una pregunta legítima** sobre disponibilidad futura (J3, O6) o sobre un número calculado (O5).

## 6. Qué sigue

En este orden:

1. **PR en draft** de `core/247-jurado-sobre-main`: `Closes #229`, `Refs #247`, `Refs #51`. #247 se cierra
   recién cuando O cumpla el criterio: el "No" de las HOA fees es un dato inventado.
2. **Un set nuevo (O)** antes de tocar nada más. Es lo único que mide si los arreglos generalizan: correr
   otra vez J, N o M solo mide regresiones. **2 o 3 de sus 6 conversaciones las escribe una persona del
   equipo, como lo haría el jurado.** Quien escribió los arreglos tiende a preguntar lo que ya sabe que funciona.
3. **Decidir según O:**
   - si cumple el criterio, no se hacen más cambios de comportamiento. Lo de mensajes cortos entra solo si
     vuelve a aparecer;
   - si no cumple, se arregla con la regla de DT-62 y solo las clases que se repitan. Las candidatas son
     buscar con un solo tipo de inmueble ("lot") y que `refers_to` tome la propiedad de la última respuesta.
4. **Guion de la demo (#52),** con lo que O muestre que es seguro, más una respuesta preparada para las
   preguntas fuera del catálogo (HOA fees, servicios del edificio).
5. **D, regresión con expectativas.** Pasar J, N y M a escenarios de `scenarios.py` con expectativas por turno
   (qué propiedad, qué no puede decir), para que `evaluate.py` los mida. Un juez LLM *offline* sobre las
   transcripciones es opcional y va a ritmo de 10 RPM.
6. **Lo que dejó M r2** (§5):
   - el agente no busca con mensajes cortos de un solo tipo ("lot");
   - pide confirmar la propiedad cuando la conversación ya venía hablando de una: `refers_to` podría tomar la
     propiedad de la última respuesta del asistente;
   - el texto de "another day" en `visits.describe`;
   - el resumen del traspaso que falló con `LLMFormatError`.
7. **Lo que sigue sin estructura:** datos que ninguna tool trae (HOA fees, servicios del edificio) y mezclar
   presupuesto con cuota inicial. Hoy dependen del prompt y de A-5.
8. **Antes del cierre (Z-1), una sola vez:** `evaluate.py --reps 5` con el código final, y comparar con la línea base del
   6-oct en `TESTING.md`.

## 7. Cómo retomar desde cero

```
bash scripts/demo/down.sh          # exporta conversaciones y borra la base
bash scripts/demo/up.sh            # migraciones, asesores, inventario de demo e historia sintética
python harness/scripts/jury.py M   # ~15 min con Bedrock; deja tmp/jury/<fecha>_M.md
docker compose logs core | grep "guardrail de salida"   # por qué se regeneró o degradó cada turno
```

- Antes, el `.env` con `LLM_PROVIDER=bedrock` y las credenciales de AWS. Sin Bedrock, el agente falso no sirve
  para esta prueba.
- Con 10 RPM no se corre nada en paralelo: ni dos sets a la vez, ni el jurado junto con la evaluación.
- Después de cambiar el código del core: `docker compose up -d --build core`. Si no, el contenedor sigue con
  la versión anterior.
