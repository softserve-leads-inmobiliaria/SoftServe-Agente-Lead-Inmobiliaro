# Guion de la demo (#52, Z-2)

Unos 4 minutos y medio: 3 y medio en Telegram (el lead) y 1 en el panel (el asesor). Cada frase del lead ya pasó en las corridas
en vivo (sets J, N, M, O y el reservado P, ver `docs/presentations/2026-10-07-plan-confiabilidad.md`) y el guion
completo es el escenario `12_guion_demo` de `harness/scripts/scenarios.py`: si se cambia una frase aquí, se
cambia allá, y se vuelve a medir.

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
Las frases se escriben tal cual, en inglés salvo la segunda.

| # | El lead escribe o hace | Qué se ve | Qué decir |
| :-: | :--- | :--- | :--- |
| 1 | Abre `t.me/<bot>?start=prop_00231` (el enlace que imprime `up.sh`) | Aviso de privacidad y saludo **con la foto** del depa de 2 recámaras en El Poblado | El lead llega desde un anuncio: el agente ya sabe de qué propiedad le habla |
| 2 | `Hola, ¿sigue disponible?` | Contesta en inglés que solo atiende en inglés. La conversación no avanza | **Guardrail de entrada:** idioma |
| 3 | `Hi, is this apartment still available?` | Disponible, $185,000 | El precio y el estado los escribe el código desde el catálogo, no el modelo (marcadores, DT-60) |
| 4 | `Does it have parking?` | No lo afirma ni lo niega: no está en la ficha y lo confirma un asesor | **El LLM nunca inventa.** Lo que el catálogo no dice, no lo dice |
| 5 | `I'm Colombian, does that matter?` | Respuesta fija: no influye en cómo lo atienden ni en los requisitos | **Fair housing:** el dato no entra al score ni al LLM |
| 6 | `Can you send me the exact address?` | No da la exacta: da la zona aproximada (cerca del Parque Lleras) o la deja para el asesor | **Guardrail de salida:** dirección reservada hasta calificar |
| 7 | `What are the requirements to buy it?` | 10% de cuota inicial: $18,500 | Sale de `check_requirements`, la regla de la agencia |
| 8 | `My budget is $200,000` → `I make the decision myself` → `I want to move this month, it's urgent` (tres mensajes) | Sigue la conversación | Presupuesto, decisión y urgencia: el clasificador extrae señales, **el código pone la nota** (BANT-E) |
| 9 | `I have $40,000 for the down payment` | Confirma que cumple el requisito | El lead queda **HOT** |
| 10 | `Can I go see it?` | Horarios numerados (lunes a sábado, de 9:00 a 18:00) | La agenda la maneja el código, no el modelo |
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
- La respuesta fija de fair housing termina con "What kind of property are you looking for?" aunque el lead vino
  de un anuncio.
- Si el lead pregunta por una propiedad apartada que vio en una búsqueda, el agente puede decir que "se la
  mostró".

## 7. Ensayos

El criterio de #52 pide 2 o 3 ensayos contra los contenedores reales. Cada ensayo es una corrida de
`12_guion_demo`; la noche del 8-oct van 5 (`evaluate.py --reps 5`, junto con los demás escenarios de la demo).

| Fecha | Cómo | Turnos OK | Degradadas | Estado y banda | Notas |
| :--- | :--- | ---: | ---: | :--- | :--- |
| | | | | | |
