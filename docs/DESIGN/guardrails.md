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
| Categoría protegida en la entrada | El lead menciona origen, religión, situación familiar, género, nacionalidad | Se responde con normalidad al resto del mensaje; el dato no entra a `ScoreSignal` |

**Regla general:** el guardrail de entrada nunca detiene la conversación en seco salvo `/stop` —
degrada a una respuesta segura o a `HUMAN_HANDOFF`.

## 2. Validador de SALIDA

### 2.1 Factualidad (`A-5`)

Todo número/estado de la respuesta candidata se compara literalmente contra `last_tool_results`
del turno (`app/factuality.py`, DT-23) — también se admite lo que el propio lead dijo. 1er fallo:
se regenera con una instrucción explícita de qué no se pudo verificar, con las tools todavía
disponibles. 2do fallo: se degrada a un texto fijo que ofrece un asesor (`intent="otro"`, sin forzar
`HUMAN_HANDOFF` — si se repite, la racha de turnos sin resolver ya existente lo dispara sola).

La regla se prueba contra el catálogo de demo real en el gold set de `C-7` (75 casos, y además la
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
| Factualidad | `A-5` | Implementado: `services/core/app/factuality.py`, aplicado por `BedrockAgent` (DT-23). Solo cubre a `BedrockAgent`: `FakeAgent` construye su respuesta directo de los resultados reales, no necesita el guardrail |
| Dirección reservada | `A-6` | Implementado: `services/core/app/address_guard.py`, aplicado por `BedrockAgent` en el mismo *pipeline* de regenerar/degradar que A-5 (DT-23). No es literal como A-5: detecta la forma de una dirección exacta (tipo de vía + nombre + número) en prosa, porque el catálogo nunca devuelve `address_exact` sin `lead_qualified=true` y no hay valor real contra el que comparar. Solo cubre a `BedrockAgent`: `FakeAgent` nunca ve `address_exact` (el catálogo la redacta antes de que llegue) |
| Promesas no autorizadas (8 *claims*) | `A-7` | Implementado 6/8 con código: `services/core/app/promise_guard.py`, aplicado por `BedrockAgent` en el mismo *pipeline* que A-5/A-6 (DT-26). El 8 ya lo cubría A-5 sin cambios; el 6 queda parcial (ver §2.2). Detalle de cuál cubre cada uno en §2.2 |
| Entrada (PII/*jailbreak*/OOD/`/stop`/categoría protegida) | `A-8` | Implementado: los 5 detectores (`services/core/app/input_guard.py`). Pendiente dentro de `/stop`: cancelar *jobs* del lead, que no existen hasta que haya *scheduler*; el reingreso tras la baja es de `B-2` |
| Fair housing | `A-11` | El *scoring* sigue con sesgo: `C-6` (DT-35) encontró que `DECISION_MAKER_POINTS` da 8 a `familia` y 12 a `pareja` contra 20 de `solo` (−8/−12), y que "con mis hijos" baja 20 puntos porque el guardrail de entrada borra la mención y el extractor ya no ve quién decide. Los 6 pares que lo exponen van con `xfail(strict=True)` en el gold set: CI en verde, defecto visible con su número, y el test se pone rojo solo cuando A-11 lo arregle. **No lo arregla C-6** — es de A-11 |
| PII en logs (no en el mensaje al LLM — eso ya es `A-8`) | `OBS-7` | **Sin caso real que enmascarar**: ningún logger de `core`/`panel`/`catalog` imprime hoy texto del lead (mensaje, historial, `clean_text`/`signal_text`) — los `logger.*` existentes solo llevan mensajes fijos o metadata (ids, nombres de excepción). `db/logging_config.py::JsonFormatter` no enmascara nada por diseño (DT-27); si algún día un log necesita llevar texto del lead, la regla es la misma que `InputVerdict.audit_kinds()`: pasar por `input_guard._redact_pii`/tipos-no-valores antes de loguear, nunca el texto crudo |

## 4. Mapa guardrail → prueba

| Guardrail | Prueba | ¿*Gate* de CI? |
| :--- | :--- | :--- |
| Disponibilidad | `services/core/tests/test_availability.py` | Sí — corre con la suite de `core` |
| Factualidad | Gold set de 32 casos, fusionado en `A-5` (`services/core/tests/test_factuality_gold_set.py`) | Sí — 100%. Sus `tool_results` están escritos a mano y ya no coinciden con el contrato desde C-1: **no se modifican** (prueban DT-23, y no se borran casos antiguos) |
| Factualidad sobre el catálogo de demo | Gold set de 75 casos sobre la salida real de `CatalogService`, ya pasada por el `AvailabilityGuard` real (`C-7`, `services/core/tests/test_factuality_catalogo_gold_set.py`) + validación de cada turno contra `contracts/mcp/` | Sí — 100%, sin `xfail`. 27/27 turnos y 12/12 propiedades del inventario. Cierra el hueco que dejó el de A-5: sus fixtures ya no son la salida de ninguna tool y nada lo señalaba (DT-36) |
| Dirección reservada | Gold set de dirección reservada, fusionado en `A-6` (`services/core/tests/test_address_guard_gold_set.py`) — antes iba a ser `C-8`, ver notas de `A-06.md` | Sí — 0 fugas |
| *Claims* prohibidos | Suite adversarial de A-7 (`services/core/tests/test_promise_guard.py`) — los 6 cubiertos con código, en positivo y negativo | Sí — corre con la suite de `core` |
| Fair housing | Gold set de perfiles pareados, 22 pares (`C-6`, `services/core/tests/test_fair_housing_gold_set.py`) | Parcial — 16/22 en verde. Los 6 pares de "con quién compras" van con `xfail(strict=True)` porque el *scoring* todavía tiene sesgo (DT-35): diferencia −8/−12/−20, y `strict=True` los vuelve a poner en rojo en cuanto A-11 los arregle |
| PII/consentimiento | Test de privacidad + `/stop` | Manual (E2E) |
| Contrato MCP del Catalog Service | *Contract test* dedicado | Sí |

"Sí" significa que corre en CI y deja el PR en rojo si falla. Nada impide mergear un PR en rojo (ver `design.md` §5.4): el *gate* depende de que el equipo no lo haga.
