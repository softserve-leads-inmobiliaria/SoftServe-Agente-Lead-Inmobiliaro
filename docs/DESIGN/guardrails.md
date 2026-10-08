# Guardrails — detalle de implementación

Extiende la sección de guardrails de cada historia con el detalle que 2+ historias necesitan
compartir: qué detecta cada validador, la lista exacta de *claims* prohibidos, y el estado real de
cobertura. El comportamiento específico de cada guardrail (qué hace al fallar, sus casos límite)
vive en la historia que lo implementa (`A-5`, `A-6`, `A-7`, `A-8`, `A-11`).

## 1. Validador de ENTRADA (`A-8`)

Corre **antes** de que el mensaje llegue a la FSM/LLM. Bloquea o marca; nunca deja pasar en
silencio salvo lo que cada fila indica.

| Detector | Qué busca | Acción al detectar |
| :--- | :--- | :--- |
| Anti-*jailbreak* | Instrucciones para cambiar de rol, ignorar el *system prompt*, o extraerlo | Se procesa como pregunta de dominio normal — no se refleja ni confirma la instrucción; se registra en `AuditLog` |
| PII no solicitada | Datos sensibles ofrecidos sin que el flujo los haya pedido (ej. identificación oficial, tarjeta) | No se persiste ese campo; se responde recordando qué datos sí hacen falta |
| Opt-out (`/stop`) | Comando explícito o frase equivalente | Determinista (regla, no LLM). Cancela todo *job* pendiente del lead; marca `consent_status=withdrawn`; confirma la baja |
| Fuera de dominio | Preguntas fuera de bienes raíces, o que piden dictamen legal/fiscal/crediticio formal | Respuesta acotada + oferta de `HUMAN_HANDOFF` si insiste |
| Categoría protegida en la entrada | El lead menciona origen, religión, situación familiar, género, nacionalidad | Se responde con normalidad al resto del mensaje; el dato no entra a `ScoreSignal`. Si pregunta si influye, se le dice que no (#200) |
| Idioma (#202) | Mensaje escrito en español | Aviso fijo en inglés para que lo repita en inglés; no llega al LLM, no suma al score ni a la frustración, y se registra en `AuditLog` (`input.not_english`). Corre después de la baja, la PII y fair housing |

El origen se detecta también como lo dice un lead (#116): gentilicios ("soy colombiano", "somos
argentinos") y frases de procedencia ("soy de Colombia", "vengo de Venezuela", "nací en Perú"). La
lista de países es cerrada, para no marcar "soy de El Poblado". Los gentilicios que también nombran otra
cosa ("pesos mexicanos", "colonia Americana", "cocina americana", "hablo español") solo cuentan detrás
de "soy" o "somos". El costo de esa regla: "mi esposa es española" no se marca. Un gentilicio con errata
detrás de "soy/somos" ("soy colombian", "soy venezolan") también cuenta (#200).

Quitar la mención no puede dejar sin respuesta la pregunta del lead: ante "I'm Colombian, does that
matter?", al agente le llega solo "does that matter?". Por eso el turno marca que se quitó una mención
(`TurnInput.protected_removed`) y el LLM recibe una nota, sin el dato quitado, que le pide contestar que
no influye en la atención ni en los requisitos (`agent._with_notes`, como la nota del anuncio de #122). Si
lo que queda es una pregunta sin otra intención, los dos agentes contestan un texto fijo (`PROTECTED_ANSWER`)
sin pasar por el chat: en vivo (#202) el modelo ignoraba la nota y volvía al enganche. "I'm Colombian, how
much is it?" sigue siendo una consulta de precio, y ahí el LLM contesta con la nota. El clasificador no ve la
nota: solo lee lo que escribió el lead.

El resumen del traspaso para el asesor (A-16) habla del lead en tercera persona, y en inglés "the lead is
Colombian" o "they're Catholic" no pasan por "I'm/we're", que es lo único que hace contar a los gentilicios
ambiguos y a la religión en singular en la entrada. Por eso el resumen se revisa con `describes_protected`,
que suma esas formas, y con las de familia en tercera persona ("his wife", "her kids"); "their partner" y
"their family" siguen permitidas, como en la entrada (DT-40).

**Idioma (#202, DT-55).** La experiencia es en inglés: los cinco detectores reconocen inglés y conservan
sus patrones en español, y un sexto, `app/language_guard.py`, contesta un mensaje en español con el aviso
de "solo en inglés". Cuenta las palabras que solo son de un idioma: es español si tiene más de español
que de inglés, y ante el empate pasa. No cuentan las ciudades y zonas del inventario, las vías con número
("Calle 10", "Carrera 37 # 8A-24") ni un artículo seguido de mayúscula ("La Candelaria"), así que "Do you
have anything in El Poblado?" o "Hola! I'm looking for..." pasan. La baja, la PII y fair housing se
aplican antes, en los dos idiomas: retirar el consentimiento o proteger un dato no depende del idioma. En inglés valen las mismas reglas: lista cerrada de países ("I'm from
Venezuela" sí, "I'm from El Poblado" no), y los gentilicios ambiguos ("Colombian pesos", "Mexican food",
"I speak Spanish") y las religiones en singular ("Christian" también es un nombre) solo cuentan detrás
de "I'm/we're". "my wife" o "my husband" se generalizan a "my partner", y "my kids" a "my family". La
baja reconoce "stop messaging me", "unsubscribe", "opt out" y similares, pero no "don't call me", que
pide otro canal. La PII es la de Colombia: cédula de ciudadanía o de extranjería (CC, CE), NIT,
pasaporte y licencia, con su número con puntos o guion; sin el nombre del documento delante, un número
de 10 dígitos no se distingue de un teléfono y no se toca.

**Regla general:** el guardrail de entrada nunca detiene la conversación en seco salvo `/stop` —
degrada a una respuesta segura o a `HUMAN_HANDOFF`.

## 2. Validador de SALIDA

### 2.1 Factualidad (`A-5`)

**Desde DT-60 (#243), los datos de la propiedad los escribe el código.** El modelo escribe un marcador con la
referencia y el campo (`{prop_00231.price}`), y `app/markers.py` lo completa con lo que devolvieron las tools
del turno o la memoria de hechos (el estado, solo del turno). Se bloquea, con el mismo regenerar/degradar:

- un marcador que no se puede completar (una propiedad o un campo que ninguna tool dio, o `address_exact` sin
  `lead_qualified`);
- un número escrito fuera de un marcador, salvo los del lead en la conversación, la numeración de una lista y
  un dato de la propiedad que nombra la línea (con un marcador o su referencia) o, si no nombra ninguna, de una
  sola propiedad (si dos lo tienen, podría ser el de la otra);
- una referencia `prop_…` que ninguna tool dio, salvo la del anuncio o una que escribió el lead.

Si un número suelto es un dato de varias propiedades, la corrección le dice al modelo entre qué marcadores
elegir. Así deja de pasar el valor de *otra* propiedad cuando las dos lo tienen, el límite que DT-23 dejaba
abierto; si solo una lo tiene, pasa aunque la frase hable de otra (límite conocido de DT-60). El estado de una
propiedad conocida que no vino en el turno lo consulta el código con `get_availability`, sin gastar una llamada
al LLM. Lo que el modelo
escribe fuera de los marcadores sigue pasando por la revisión de estados y por la regla de #230 que se describen
abajo; la dirección reservada (A-6) y las promesas (A-7) se revisan sobre el texto ya completado. El gold set
de los marcadores (`test_markers_gold_set.py`) usa la salida real del catálogo de demo.

Todo número/estado de la respuesta candidata se compara literalmente contra `last_tool_results`
del turno (`app/factuality.py`, DT-23) — también se admite lo que el propio lead dijo, para repetir lo suyo
(su presupuesto, lo que pidió). No para confirmarlo como dato de la propiedad (#230): si la frase lo
afirma como precio, dormitorios, baños o m² y las tools dan ese campo con otro valor, se bloquea ("Is it
$150,000?" → "Yes, it's $150,000" cuando cuesta $185,000). Un número también
vale si una tool lo dio en un turno anterior de la conversación: la memoria de hechos verificados
(`app/facts.py`, DT-59, #217) guarda los campos citables de cada propiedad durante 24 horas, nunca su
estado. 1er fallo:
se regenera con una instrucción explícita de qué no se pudo verificar, con las tools todavía
disponibles. La instrucción va en el *system* y dice que el lead no la ve, para que el modelo no se
disculpe ni prometa buscar después (#213). 2do fallo: se degrada a un texto fijo que ofrece un asesor (`intent="otro"`, sin forzar
`HUMAN_HANDOFF` — si se repite, la racha de turnos sin resolver ya existente lo dispara sola).

En inglés (#202), "available" detrás de "have", "has", "you" o "we're" no cuenta como estado: es el dinero
o el tiempo del lead ("how much do you have available for the down payment?", "are you available on
Friday?"). En vivo, esa pregunta degradaba la respuesta en los turnos sin tools. Tampoco detrás de "what's"
o "what is" (#213): "show you what's available" es el inventario en general, y degradaba una pregunta de
aclaración ("which property do you mean?"). Ni el que califica a otras propiedades en plural (#220): "other
available houses" o "no other houses available" ofrecen alternativas de una propiedad apartada, y degradaban
5 de cada 25 turnos del escenario de guardrails en la evaluación en vivo (#216). En singular, o dicho de la
propiedad ("an available house", "this one is available"), sigue contando como estado. Tampoco cuenta un
estado futuro o posible (#247, prueba de jurado): "when it will become available again" o "if it becomes
available" no afirman el estado de ahora, y degradaban "When will it be free again?" (J3, O6). "This one is
available again" sí cuenta.

La regla se prueba contra el catálogo de demo real en el gold set de `C-7` (103 casos, y además la
validación de cada turno contra `contracts/mcp/`, DT-36), que corre el `AvailabilityGuard` real de A-4
como capa intermedia. El límite conocido —el guardrail compara
literales y no sabe de qué propiedad habla el texto, así que un número real de *otra* propiedad del
mismo turno se deja pasar— es DT-23 y queda fijado con casos en ese gold set.

### 2.2 Lista exacta de *claims* prohibidos (`A-7`)

| # | *Claim* prohibido | Ejemplo | Cobertura |
| :--- | :--- | :--- | :--- |
| 1 | Aprobación de crédito o financiamiento | "tu crédito está aprobado" | Implementado — frase siempre prohibida, `app/promise_guard.py` |
| 2 | Aceptación de mascotas sin confirmación | "sí se aceptan mascotas" (si no viene del catálogo) | Implementado — frase siempre prohibida (el catálogo no tiene campo de mascotas) |
| 3 | Negociación de precio no autorizada | "podemos bajarle el precio" | Implementado — frase siempre prohibida (no hay tool de negociar precio) |
| 4 | Apartar/reservar sin acción real | "ya te la aparto" | Implementado — frase siempre prohibida (no hay tool de apartar) |
| 5 | Amenidades no listadas en el catálogo | cualquier amenidad no presente en `amenities` | Implementado — comparación literal contra `amenities` del turno, vocabulario cerrado |
| 6 | Promoción no vigente | cualquier promoción fuera del resultado de `get_promotions` | Parcial: con número (descuento/porcentaje) ya lo bloquea A-5 (no respaldado en `last_tool_results`). Una promoción genérica sin número queda pendiente — DT-26 |
| 7 | Garantía de cierre de operación | "esta compra es un hecho" | Implementado — frase siempre prohibida |
| 8 | Urgencia falsa sin dato del catálogo | "solo queda 1" sin respaldo | Ya cubierto por A-5 sin cambios: el número no tiene respaldo en `last_tool_results` — ver `test_factuality_gold_set.py::urgencia_falsa_con_numero` |

### 2.3 Dirección reservada (`A-6`)

`address_exact` solo puede aparecer si `lead_qualified=true` (derivado de la banda del score, no
del LLM). El detalle de detección en texto libre vive en `A-6`.

### 2.4 Fair housing (`A-11`)

El esquema de `ScoreSignal` no tiene campo para categoría protegida — ver `design.md` §3. Prueba:
perfiles pareados, diferencia de score total = 0 (`C-6`).

## 3. Estado de cobertura

Tabla obligatoria desde la primera versión de este documento (no solo la lista de qué debería
existir) — se actualiza en el mismo PR que cierra la historia correspondiente.

| Guardrail | Historia | Estado |
| :--- | :--- | :--- |
| Disponibilidad (revalidación tras cada búsqueda/ficha) | `A-4` | Implementado: `services/core/app/availability.py`, aplicado por `process_turn` a todo agente |
| Factualidad | `A-5` | Implementado: los marcadores de `services/core/app/markers.py` (DT-60) y los estados y la regla de #230 de `services/core/app/factuality.py`, aplicados por `BedrockAgent` (DT-23). Solo cubre a `BedrockAgent`: `FakeAgent` construye su respuesta directo de los resultados reales, no necesita el guardrail |
| Dirección reservada | `A-6` | Implementado: `services/core/app/address_guard.py`, aplicado por `BedrockAgent` en el mismo *pipeline* de regenerar/degradar que A-5 (DT-23). No es literal como A-5: detecta la forma de una dirección exacta (tipo de vía + nombre + número, o vía numerada + placa: "Carrera 37 # 8A-24") en prosa, en español y en inglés. Una vía numerada sin placa ("near Calle 10") es una referencia aproximada y no dispara (DT-55). La dirección no cruza de línea: en una ficha, "near Avenida El Poblado" arriba de "• 1 bedroom" se leía como dirección exacta y degradaba la respuesta (prueba de jurado, #247), porque el catálogo nunca devuelve `address_exact` sin `lead_qualified=true` y no hay valor real contra el que comparar. Solo cubre a `BedrockAgent`: `FakeAgent` nunca ve `address_exact` (el catálogo la redacta antes de que llegue) |
| Promesas no autorizadas (8 *claims*) | `A-7` | Implementado 6/8 con código: `services/core/app/promise_guard.py`, aplicado por `BedrockAgent` en el mismo *pipeline* que A-5/A-6 (DT-26). El 8 ya lo cubría A-5 sin cambios; el 6 queda parcial (ver §2.2). Detalle de cuál cubre cada uno en §2.2 |
| Resumen para el asesor | `A-16` | Implementado: `services/core/app/summary.py` pasa el resumen del traspaso por A-5, A-6 y A-7, con la transcripción guardada y los datos del lead como fuente, y rechaza toda mención de categoría protegida (las reglas de A-8, más "su esposa" o "sus hijos" en tercera persona). Si algo no pasa, se descarta sin regenerar y la ficha sale sin resumen (DT-46) |
| Entrada (PII/*jailbreak*/OOD/`/stop`/categoría protegida/idioma) | `A-8`, #202 | Implementado: los 5 detectores (`services/core/app/input_guard.py`) y el filtro de idioma (`services/core/app/language_guard.py`, DT-55). `/stop` cancela los *jobs* pendientes del lead y desactiva su alerta de propiedades nuevas por cualquiera de los dos caminos, el canal y `core` (`A-17`, `A-18`, DT-44, DT-45); el reingreso tras la baja es de `B-2` |
| Fair housing | `A-11`, #119 | Implementado (DT-38, DT-40): `decision_maker` es `declarado`/`no_declarado`, así que el contrato no guarda con quién decide el lead. El guardrail de entrada generaliza "mis hijos" a "mi familia" y "mi esposa", "mi marido" o "mi novia" a "mi pareja" en vez de borrarlos, así que el lead no pierde los puntos de Authority. El gold set de `C-6` (DT-35) queda con todos sus pares en diferencia 0, en español y en inglés (#202), sin `xfail` |
| PII en logs (no en el mensaje al LLM — eso ya es `A-8`) | `OBS-7` | **Sin caso real que enmascarar**: ningún logger de `core`/`panel`/`catalog` imprime hoy texto del lead (mensaje, historial, `clean_text`/`signal_text`) — los `logger.*` existentes solo llevan mensajes fijos o metadata (ids, nombres de excepción). `db/logging_config.py::JsonFormatter` no enmascara nada por diseño (DT-27); si algún día un log necesita llevar texto del lead, la regla es la misma que `InputVerdict.audit_kinds()`: pasar por `input_guard._redact_pii`/tipos-no-valores antes de loguear, nunca el texto crudo |

## 4. Mapa guardrail → prueba

| Guardrail | Prueba | ¿*Gate* de CI? |
| :--- | :--- | :--- |
| Disponibilidad | `services/core/tests/test_availability.py` | Sí — corre con la suite de `core` |
| Factualidad | Gold set de 32 casos, fusionado en `A-5` (`services/core/tests/test_factuality_gold_set.py`) | Sí — 100%. Sus `tool_results` están escritos a mano y ya no coinciden con el contrato desde C-1: **no se modifican** (prueban DT-23, y no se borran casos antiguos) |
| Factualidad sobre el catálogo de demo | Gold set de 103 casos sobre la salida real de `CatalogService`, ya pasada por el `AvailabilityGuard` real (`C-7`, `services/core/tests/test_factuality_catalogo_gold_set.py`) + validación de cada turno contra `contracts/mcp/` | Sí — 100%, sin `xfail`. 28/28 turnos y 12/12 propiedades del inventario. Cierra el hueco que dejó el de A-5: sus fixtures ya no son la salida de ninguna tool y nada lo señalaba (DT-36) |
| Marcadores (datos por construcción) | Gold set de 37 casos sobre la salida real del catálogo de demo (#243, `services/core/tests/test_markers_gold_set.py`): qué lee el lead y qué se bloquea | Sí — 100% |
| Dirección reservada | Gold set de dirección reservada, fusionado en `A-6` (`services/core/tests/test_address_guard_gold_set.py`) — antes iba a ser `C-8`, ver notas de `A-06.md` | Sí — 0 fugas |
| *Claims* prohibidos | Suite adversarial de A-7 (`services/core/tests/test_promise_guard.py`) — los 6 cubiertos con código, en positivo y negativo | Sí — corre con la suite de `core` |
| Fair housing | Gold set de perfiles pareados, 27 pares (`C-6`, `services/core/tests/test_fair_housing_gold_set.py`) | Sí — 27/27 con diferencia 0, sin `xfail`. Los 6 pares de "con quién compras" daban −8/−12/−20 (DT-35) hasta que A-11 los arregló (DT-38) |
| Idioma | Gold set de 71 mensajes: español típico de un lead, inglés y nombres propios (#202, `services/core/tests/test_language_guard_gold_set.py`) | Sí — todo el español detectado y 0 falsos positivos en inglés o en nombres |
| PII/consentimiento | Test de privacidad + `/stop` | Manual (E2E) |
| Contrato MCP del Catalog Service | *Contract test* dedicado | Sí |

"Sí" significa que corre en CI y deja el PR en rojo si falla. Nada impide mergear un PR en rojo (ver `design.md` §5.4): el *gate* depende de que el equipo no lo haga.

El umbral de cada gold set lo define su propia historia y lo comprueba un único registro
(`services/core/tests/test_gold_sets_gate.py`, OBS-4 / DT-37), que además se cae si aparece un gold set
nuevo que nadie registró ahí. La columna "¿*Gate* de CI?" de esta tabla es el resultado esperado de ese
registro, no una promesa: si un gold set baja de su mínimo, esa fila pasa a ser falsa y el CI avisa.
