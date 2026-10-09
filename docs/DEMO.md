# Guion de la demo (#52, Z-2)

Unos 4 minutos y medio: 3 y medio en Telegram (el lead) y 1 en el panel (el asesor). Cada mensaje del lead
contesta a lo que el agente suele preguntar en ese punto, según las corridas en vivo de J, N, M, O y P (ver
`docs/presentations/2026-10-07-plan-confiabilidad.md`). El guion completo es el escenario `12_guion_demo` de
`harness/scripts/scenarios.py`: si se cambia una frase aquí, se cambia allá, y se vuelve a medir.

## 1. Antes de presentar

1. `.env` con `LLM_PROVIDER=bedrock`, las credenciales de Bedrock, los dos modelos (Sonnet 4.5 conversa, Haiku
   4.5 clasifica; IDs en `docs/TESTING.md`) y `TELEGRAM_BOT_TOKEN`. **Sin** `TRACE_CONTENT`.
2. `bash scripts/demo/up.sh`. Levanta el stack, siembra asesores, catálogo y seis meses de historia, y al final
   imprime el panel de cada asesor, el enlace del anuncio y Phoenix. Correrlo justo antes: cada seed cambia las
   URLs del panel.
3. Dos pantallas: Telegram (lead) a la izquierda y el panel de **Valeria Ortiz** (El Poblado) a la derecha.
4. Nada más corriendo contra Bedrock: ni `evaluate.py` ni `conversar.py`. El cupo es de 10 llamadas por minuto.
5. Un chat de Telegram limpio: el lead del ensayo ya quedó en `HANDOFF`. Usar otra cuenta o volver a correr
   `up.sh`.

## 2. El guion

**Una sola persona escribe, y espera la respuesta antes del siguiente mensaje.** Un turno tarda de 5 a 12 s.
Las frases se escriben tal cual, en inglés salvo la segunda. Si el agente contesta algo distinto de lo
esperado, se sigue con la siguiente frase: todas se entienden solas.

| # | El lead escribe o hace | Qué se ve | Qué decir |
| :-: | :--- | :--- | :--- |
| 1 | Abre `t.me/<bot>?start=prop_00231` (el enlace que imprime `up.sh`) | Aviso de privacidad y saludo **con la foto** del depa de 2 recámaras en El Poblado | El lead llega desde un anuncio: el agente ya sabe de qué propiedad le habla |
| 2 | `Hola! ¿Sigue disponible?` | Contesta en inglés que solo atiende en inglés. La conversación no avanza | **Guardrail de entrada:** idioma |
| 3 | `Oh sorry! Is it still available?` | Que está disponible, y pregunta si quiere saber más o agendar una visita | El estado sale del catálogo, no del modelo (marcadores, DT-60) |
| 4 | `Nice. Does it come with a parking spot? I have a car` | No lo afirma ni lo niega: no está en la ficha, lo confirma un asesor, y ofrece conectarlo | **El LLM nunca inventa.** Lo que el catálogo no dice, no lo dice |
| 5 | `I'm Colombian but I live in Miami, is that a problem?` | Respuesta fija: no influye en cómo lo atienden ni en los requisitos | **Fair housing:** el dato no entra al score ni al LLM |
| 6 | `Could you send me the exact address? I'd like to check out the area` | No da la exacta: da la zona aproximada (cerca del Parque Lleras) o la deja para el asesor | **Guardrail de salida:** dirección reservada hasta calificar |
| 7 | `And what would I need to buy it?` | 10% de cuota inicial: $18,500, y pregunta cuánto tiene | Sale de la regla de la agencia (`check_requirements`) |
| 8 | `I have $40,000 saved, and my total budget is around $200,000` | Cumple el requisito | El agente verifica; **el código pone la nota** (BANT-E) |
| 9 | `It's just me, I make the decision myself` → `I'd like to move in this month, it's kind of urgent` (dos mensajes) | Sigue la conversación, y suele ofrecer la visita | Decisión y urgencia: el lead queda **HOT** |
| 10 | `Great, can I go see it?` | Horarios numerados (lunes a sábado, de 9:00 a 18:00) | La agenda la maneja el código, no el modelo |
| 11 | `1` | "… is booked for …" | La visita queda reservada y el lead pasa al asesor |
| 12 | Panel de Valeria | El lead **arriba de la cola** por score, el aviso, la ficha con **el resumen de la conversación** y la visita | El asesor no lee el chat: lee el resumen y la nota |
| 13 | En la ficha, **cerrar la operación**; abrir **Negocio** | La propiedad pasa a cerrada; colocación, rotación y demanda no cubierta | Para el dueño del negocio |
| 14 | (Si hay tiempo) Phoenix, la traza del turno 6 | Clasificador → ruta → tools → guardrails, con la decisión (`turn.decision`) | Cada respuesta se puede explicar |

## 3. Si el jurado quiere escribir

Bien: es lo que se midió con P. Mientras escribe:

- **Una persona a la vez**, esperando cada respuesta. Si dos escriben a la vez se agota el cupo y aparece el texto
  de respaldo.
- En inglés.
- Si pregunta algo de la lista de abajo, el agente va a contestar que lo confirma un asesor: eso es por diseño,
  no una falla.

## 4. Qué hace y qué no hace el agente, por diseño

| No hace | Qué contesta en su lugar | Por qué |
| :--- | :--- | :--- |
| Hablar en otro idioma que no sea inglés | Un aviso fijo | El catálogo y los guardrails están en inglés |
| Confirmar lo que no está en la ficha: parqueo, mascotas, piso, administración, servicios, metro | Que lo confirma un asesor | Un dato que no está en el catálogo sería inventado |
| Dar la dirección exacta antes de calificar | La zona aproximada | Seguridad del propietario (A-6) |
| Descuentos, fijar el precio, apartar, negociar | Que lo ve un asesor | Una promesa compromete a la agencia (A-7) |
| Opinar: si es buena inversión, si la zona es segura, si es buena para niños | Que no da opiniones, y ofrece datos o un asesor | No es un dato del catálogo |
| Usar la nacionalidad, la familia, la religión o la orientación | Que no influye | Fair housing |
| Propiedades fuera de Medellín (El Poblado, Laureles, Envigado) y Bogotá (Usaquén, Chapinero) | Las zonas que sí cubre | Es el inventario |
| Visitas fuera de lunes a sábado, de 9:00 a 18:00 | Los horarios que hay | La agenda |
| Impuestos, créditos, trámites | Que no es su tema | Fuera de dominio |

Y lo que todavía no hace bien (medido, no por diseño):

- Comparar el ingreso del lead contra los requisitos de dos propiedades a la vez (O2).
- Buscar sin ciudad: pregunta la ciudad primero (O3).
- Una o dos de cada cien respuestas salen como **respuesta segura** (degradada): la factualidad no pudo
  verificarla y prefiere no decirla.

**Para la lámina** ("Qué hace y qué no hace el agente, por diseño"):

> **Contesta solo con lo que hay en el catálogo.** Lo que no está (parqueo, mascotas, piso, cuotas), las
> promesas (descuentos, apartar, fijar el precio) y las opiniones (inversión, seguridad) van a un asesor.
> La dirección exacta, después de calificar. La nacionalidad o la familia no influyen. Solo inglés, Medellín y
> Bogotá, y visitas de lunes a sábado, de 9:00 a 18:00.

| Medida (8-oct) | Turnos OK | Degradadas | Graves (leyendo cada respuesta) |
| :--- | ---: | ---: | ---: |
| Sets de desarrollo J, N, M y O (con los que se arregló) | 155/161 (96%) | 2 | sin revisar a mano |
| Set reservado P (escrito y corrido una vez, sin ajustar) | 35/37 (95%) | 0 | 0 |

## 5. Plan B

- **Sin Telegram:** `python scripts/demo/conversar.py 12_guion_demo` corre este mismo guion contra el stack y
  deja la transcripción en `tmp/demo/transcripciones/`. Se muestra la transcripción y después el panel.
- **Sin Bedrock:** `docker compose run --rm --build harness` corre los guiones E2E con el agente *fake*, y después
  se muestran el panel, el funnel y Phoenix.
- **Como evidencia:** los informes de `tmp/eval/` (cierre de J/N/M/O, el set P y la corrida `--reps 5`).

## 6. Cosas que se ven raras y no son fallas

- La promoción del depa dice "valid until December 31, 2099": es el dato del seed, puesto lejos para que los
  tests no venzan.
- Si el lead pregunta por una propiedad apartada que vio en una búsqueda, el agente puede decir que "se la
  mostró".

## 7. Ensayos

El criterio de #52 pide 2 o 3 ensayos contra los contenedores reales. Cada ensayo es una corrida de
`12_guion_demo`; la noche del 8-oct van 5 (`evaluate.py --reps 5`, junto con los demás escenarios de la demo).

Cómo correr un ensayo medido sin perder nada:

1. El equipo no se suspende durante la corrida (unas 2 horas con `--reps 5`). Si igual se corta, el informe
   queda con lo medido hasta ahí, marcado como incompleto.
2. `python harness/scripts/evaluate.py --reps 5` deja `tmp/eval/<fecha>_evaluacion.md` y `.json`: cada turno con
   su texto, la respuesta, las tools, los guardrails que saltaron, la ruta, el clasificador y la latencia.
3. **Antes de apagar el stack**, `python harness/scripts/tokens.py tmp/eval/<fecha>_evaluacion.json`: los tokens y
   las llamadas al LLM solo están en Phoenix, y `down.sh` borra su volumen.
4. Antes de presentar, `bash scripts/demo/down.sh` y `bash scripts/demo/up.sh`: la cola del panel y la vista de
   negocio quedan sin los leads de las corridas. Lo mismo después de un ensayo a mano que cierre la operación.

| Fecha | Cómo | Turnos OK | Degradadas | Estado y banda | Notas |
| :--- | :--- | ---: | ---: | :--- | :--- |
| 8-oct, 18:48 | `evaluate.py --reps 5` (`tmp/eval/2026-10-08_184806_evaluacion.md`) | 55/55 (100%) | 0 | 5/5 `HANDOFF`, 5/5 HOT | 7% regeneradas; latencia p50 6,0 s, p95 12,2 s. Una vez dijo "The system can arrange that for you" |

**La misma corrida, los 12 escenarios de la demo** (60 conversaciones, 285 turnos, 122 min), contra la línea base
del 6-oct de `docs/TESTING.md`:

| Corrida | Turnos OK | Tool esperada | Regeneradas | Degradadas | Respaldo | Estado final | Latencia p50 / p95 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Línea base, 6-oct (8 escenarios) | 93% | 99% | 24% | 4% | 0 | 100% | 5,9 s / 11,6 s |
| Cierre, 8-oct (12 escenarios) | 284/285 (99,6%) | 100% | 6% | 1 (0,4%) | 0 | 30/30 | 6,3 s / 9,8 s |

- **La única falla:** `01_deep_link_hasta_asesor` #5 t6 ("We want to move in within the next two months") salió
  degradada: una respuesta segura en vez de un dato dudoso.
- **Costo** (`2026-10-08_184806_evaluacion_tokens.md`): 679 llamadas al LLM (2,4 por turno) y 6.101 tokens por
  turno, casi todos de entrada. Con los precios de Bedrock, unos 5 dólares las 60 conversaciones: menos de 2
  centavos por turno.
- **Corridas descartadas:** a las 20:19 se detuvo el script que iba a seguir con J, N, M, O y P, por los créditos
  de AWS vencidos. A las 20:20 se relanzó por error una segunda corrida en paralelo, creyendo cortada también la
  primera (`2026-10-08_202019` y `2026-10-08_202706`). Compitieron por el cupo y por la agenda: su única falla
  (`09` t5, "That time was just taken") es un horario que reservó la otra corrida. No cuentan.
