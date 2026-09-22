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

### 2.2 Lista exacta de *claims* prohibidos (`A-7`)

| # | *Claim* prohibido | Ejemplo |
| :--- | :--- | :--- |
| 1 | Aprobación de crédito o financiamiento | "tu crédito está aprobado" |
| 2 | Aceptación de mascotas sin confirmación | "sí se aceptan mascotas" (si no viene del catálogo) |
| 3 | Negociación de precio no autorizada | "podemos bajarle el precio" |
| 4 | Apartar/reservar sin acción real | "ya te la aparto" |
| 5 | Amenidades no listadas en el catálogo | cualquier amenidad no presente en `amenities` |
| 6 | Promoción no vigente | cualquier promoción fuera del resultado de `get_promotions` |
| 7 | Garantía de cierre de operación | "esta compra es un hecho" |
| 8 | Urgencia falsa sin dato del catálogo | "solo queda 1" sin respaldo |

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
| Dirección reservada | `A-6` | Pendiente |
| Promesas no autorizadas (8 *claims*) | `A-7` | Pendiente |
| Entrada (PII/*jailbreak*/OOD/`/stop`/categoría protegida) | `A-8` | Implementado: los 5 detectores (`services/core/app/input_guard.py`). Pendiente dentro de `/stop`: cancelar *jobs* del lead, que no existen hasta que haya *scheduler*; el reingreso tras la baja es de `B-2` |
| Fair housing | `A-11` | Pendiente |

## 4. Mapa guardrail → prueba

| Guardrail | Prueba | ¿*Gate* de CI? |
| :--- | :--- | :--- |
| Disponibilidad | `services/core/tests/test_availability.py` | Sí — corre con la suite de `core` |
| Factualidad | Gold set de 32 casos, fusionado en `A-5` (`services/core/tests/test_factuality_gold_set.py`) | Sí — 100% |
| Dirección reservada | Gold set de dirección reservada (`C-8`) | Sí — 0 fugas |
| *Claims* prohibidos | Suite adversarial | Sí (básica en núcleo) |
| Fair housing | Script de perfiles pareados (`C-6`) | Sí — diferencia 0 |
| PII/consentimiento | Test de privacidad + `/stop` | Manual (E2E) |
| Contrato MCP del Catalog Service | *Contract test* dedicado | Sí |

Todo guardrail marcado como *gate* de CI bloquea el `merge` a `main`.
