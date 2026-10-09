# Propuestas de funcionalidades

Ideas para después del alcance actual, con lo que cada una necesita del sistema. Cada propuesta ya
tiene su historia en `docs/internal/STORIES/` (ver §9); el orden y el reparto están en
[`ROADMAP.md` §8](../internal/STORIES/ROADMAP.md#8-propuestas-de-octubre-e6-y-lo-que-sale-de-propuestasmd), y el
estado, en el [Project](https://github.com/orgs/softserve-leads-inmobiliaria/projects/1). Lo que ya está
construido está en [`design.md` §5](../DESIGN/design.md#5-estado-real-del-sistema).

Origen: el profesor pidió que la inmobiliaria pueda saber cuántas propiedades se arrendaron en la última
semana o mes, cuáles están sin rotación y cuáles se arrendaron después de conversar con el bot. El resto
salió de la demo del 1 de octubre y de revisar qué datos guarda hoy el sistema.

## 1. Qué datos faltan hoy

Lo que pidió el profesor no se puede calcular con el esquema actual (`db/models.py`):

| Falta | Hoy | Consecuencia |
| :--- | :--- | :--- |
| Historial de estado de la propiedad | `Property.status` guarda solo el estado actual, sin fecha | No se sabe cuándo se arrendó ni cuánto tiempo estuvo disponible |
| Operación cerrada | `closed_won` existe en `LEAD_STATUSES`, pero nada lo asigna y no dice qué propiedad se cerró | No hay cierres que contar ni atribuir |
| Interés del lead por propiedad | `fsm_context.last_tool_results` se sobrescribe en cada turno; `messages.tool_calls` guarda solo el nombre de la tool | No se sabe qué propiedades consultó un lead antes de cerrar |
| Campaña de origen | `utm_campaign` llega en el evento canónico (`?start=camp_…`) pero `core` no lo guarda | No hay forma de medir qué anuncio trae leads |
| Búsquedas sin resultados | Pasan por `search_properties` y se pierden | No se sabe qué demanda no se está cubriendo |

## 2. Panel de inventario y rotación (lo que pidió el profesor)

### 2.1 Datos nuevos

- **`property_status_events`**: una fila por cambio de estado (`disponible` → `apartado` → `arrendado`/`vendido`,
  o de vuelta a `disponible`), con fecha y quién lo hizo.
- **`deals`** (operaciones): propiedad, lead, asesor, tipo (renta o venta), precio de cierre y fecha. Lo
  registra el asesor desde la ficha del lead en el panel ("Cerrar operación"). Deja al lead en `closed_won`
  y a la propiedad en `arrendado`/`vendido`, con su evento en `property_status_events`.
- **`property_interest`**: cada vez que un lead toca una propiedad, con fecha y tipo: entró por el anuncio,
  la consultó (`get_property`), le apareció en una búsqueda o agendó una visita. Lo escribe `core` en la
  misma transacción del turno (`advance` en `turn.py`).

### 2.2 Métricas

| Métrica | Pregunta que responde |
| :--- | :--- |
| Colocadas por periodo (semana, mes o rango), por zona, tipo y asesor | ¿Cuánto rentamos o vendimos? |
| Días en el mercado (primera vez `disponible` → cierre) | ¿Qué tan rápido rota cada zona o tipo? |
| Sin rotación: disponibles hace más de 30, 60 o 90 días | ¿Qué está estancado? |
| Sin rotación cruzada con demanda: muchas consultas y ninguna visita, o ninguna consulta | ¿El problema es el precio o la visibilidad? |
| Atribución al bot: cierres de leads que conversaron con el bot sobre esa propiedad antes de cerrar | ¿Cuánto negocio trae el agente? |
| Tiempo del primer mensaje al cierre | ¿El bot acelera el cierre? |
| Conversión por banda del score (HOT, WARM, COLD) | ¿El score predice bien? |

### 2.3 Cosas a decidir antes de construirlo

- **Regla de atribución.** Qué cuenta como "cerrada gracias al bot": que el lead haya consultado esa
  propiedad con el bot, que haya entrado por su anuncio, o solo que haya conversado. También la ventana
  (por ejemplo, 60 días entre la última interacción y el cierre). Va en una entrada de `decisions.md`.
- **Rol de gerente en el panel.** Hoy cada token ve solo la cola de su asesor (DT-5). Las métricas de
  inventario son de toda la inmobiliaria, así que hace falta un rol o un token de gerencia.
- **Historia sembrada para la demo.** Sin datos históricos, el panel sale en cero. El `seed` tendría que
  generar unos 6 meses de cambios de estado, consultas y cierres sintéticos, marcados como tales.
- **Quién cambia el estado de la propiedad.** Si además del cierre desde el panel la inmobiliaria cambia
  estados en otro sistema, el historial queda incompleto. Para el alcance del curso basta con el panel.
- **Privacidad.** Las métricas de gerencia son agregadas: no muestran la conversación ni los datos de
  contacto del lead. El detalle sigue en la ficha del asesor, como hoy.

## 3. Inteligencia comercial

- **Demanda insatisfecha.** Guardar cada búsqueda sin resultados (zona, tipo, recámaras y presupuesto,
  sin texto del lead) le dice a la inmobiliaria qué propiedades captar. Las búsquedas ya pasan por `core`;
  solo falta registrarlas.
- **Retorno por campaña.** Guardar `utm_campaign` en el lead permite medir anuncio → conversación → lead
  calificado → cierre.
- **Ranking de propiedades** por consultas, visitas y cierres.
- **Reporte semanal por Telegram al gerente**: colocadas, estancadas y la demanda insatisfecha más
  repetida. Usa el bot que ya existe; necesita un *scheduler*, el mismo que pide el *nurturing* (§5.3 de
  `design.md`).

## 4. Para el asesor

- **Resumen de la conversación** en la ficha, hecho por el LLM: qué busca, objeciones y siguiente paso
  sugerido. Evita leer la transcripción completa. Tiene que pasar por los mismos guardrails de salida.
- **SLA de atención**: cuánto tarda el asesor en tomar un lead HOT, con una alerta si pasa de un umbral
  y un ranking por asesor.

## 5. Para el lead

- **Agenda real** (`B-8`) con recordatorio el día anterior. En la demo del 1 de octubre el agente ofreció
  agendar sin poder reservar.
- **Alertas de propiedades nuevas** ("avísame si sale algo así"): reactiva leads fríos y le da sentido a
  `NURTURE`, que hoy nada dispara.
- **Fotos y botones en Telegram**: tarjeta con foto de la propiedad y botones "Agendar visita" o "Hablar
  con un asesor".
- **Notas de voz** transcritas a texto antes del guardrail de entrada.

## 6. Prioridad sugerida

| Orden | Propuesta | Por qué |
| :---: | :--- | :--- |
| 1 | Historial, operaciones y atribución (§2) | Es lo que pidió el profesor y le da un número de negocio al proyecto |
| 2 | Demanda insatisfecha y campaña de origen (§3) | Cuesta poco: las búsquedas y `utm_campaign` ya pasan por `core` |
| 3 | Fotos y botones en Telegram (§5) | El mayor efecto visual en la demo por el esfuerzo |

## 7. Esfuerzo estimado

Con la fórmula de `docs/internal/STORIES/README.md` (`horas = puntos × 2`). Es una estimación para conversar con
el equipo, no un plan.

| Propuesta | Qué hay que construir | Puntos | Horas |
| :--- | :--- | :---: | :---: |
| §2 | Migración de `property_status_events`, `deals` y `property_interest` | 2 | 4 |
| §2 | "Cerrar operación" en el panel: lead a `closed_won`, propiedad a `arrendado`/`vendido`, con su evento | 3 | 6 |
| §2 | `property_interest` escrito en `advance` (`turn.py`), en la transacción del turno | 2 | 4 |
| §2 | Rol o token de gerencia en el panel | 1 | 2 |
| §2 | Consultas y vistas de las métricas de §2.2 | 5 | 10 |
| §2 | Historia sintética en el `seed` y la regla de atribución en `decisions.md` | 3 | 6 |
| **§2** | **Subtotal** | **16** | **~32** |
| §3 | Demanda insatisfecha | 1–2 | 2–4 |
| §3 | `utm_campaign` en el lead: llega en el evento canónico (`channels/canonical.py`) y `core` no lo guarda. Cabe en la misma migración de §2 | 1 | 2 |
| §5 | Fotos y botones en Telegram: `Property` no tiene foto y el canal no maneja `callback_query` ni `reply_markup` | 3 | 6 |
| §4 | Resumen de la conversación con el LLM, con guardrails de salida | 3 | 6 |
| §4 | SLA de atención del asesor | 2 | 4 |
| §3, §5 | Reporte semanal y alertas de propiedades nuevas: necesitan el *scheduler*, que no existe (§5.3 de `design.md`) | 5+ | 10+ |
| §5 | Notas de voz: necesita un servicio de transcripción | 3 | 6 |

Las tres prioridades de §6 suman unos **21 puntos (~42 h)**. Lo que depende del *scheduler* y las notas
de voz quedan fuera de un alcance razonable para el curso.

## 8. Pendientes y en qué orden encajan

Estado al 1 de octubre de 2026. Algunas historias abiertas son base de estas propuestas y otras no las
tocan:

| Pendiente | Horas | Relación con las propuestas |
| :--- | :---: | :--- |
| #125: dar datos de calificación contaba como frustración | — | Cerrado en #127 |
| **`B-8`** (#23): agenda real | 6 | **Es base.** Sin visitas, "consultas sin visitas" de §2.2 sale vacía y el botón "Agendar visita" de §5 no tiene qué hacer. `get_slots` y `book` ya existen en el catálogo; falta que `core` los llame |
| **`need_fit`** solo cuenta resultados de `search_properties` | ~2 | **Es base.** Un lead que llega por el anuncio de una propiedad que entra en su presupuesto queda con `need_fit = 0`, y eso sesga la conversión por banda del score |
| #119: `decision_maker` y "mi esposa" | ~4 | No la bloquea, pero cambia un contrato: conviene antes de sumar más cambios de esquema |
| #104: endurecer el stack de Docker | ~4 | Independiente y barato; tiene que estar antes de la demo |
| `C-5` (#31): FAQ con embeddings | 6 | Independiente. Para la demo aporta menos que §2. Tarea futura: fuera del capstone (7-oct) |
| `OBS-6` (#48): E2E contra el bot real | 6 | Independiente, pero `Z-2` depende de ella |
| `Z-1` (#51), `Z-2` (#52): hardening y demo | 16 | Cierre por definición: `Z-1` no admite alcance nuevo, así que las propuestas tienen que entrar antes |

Orden sugerido, a decidir con el equipo:

1. `B-8` y la corrección de `need_fit`. En paralelo, #119.
2. §2 con la demanda insatisfecha y `utm_campaign` en la misma migración.
3. Fotos y botones en Telegram, si hay margen.
4. #104 y una corrida real de `OBS-6`, para no descubrir en la semana 8 un fallo contra Telegram o Bedrock.
5. `Z-1` y `Z-2` al final, con las propuestas ya dentro: la demo tiene que mostrar el sistema final.

`C-5` queda por debajo de §2: si falta tiempo, es la primera en salir.

Capacidad: con 8 semanas desde el 15 de septiembre y la semana 8 reservada para cierre, quedan unas 4
semanas de construcción, unas 86 h netas al ritmo de `ROADMAP.md`. Lo pendiente más los pasos 1 a 3 suma
unas 75 h: cabe con poco margen. Si hay que recortar, se recorta §4 y §5 antes que §2.

## 9. Historias

| Propuesta | Historias |
| :--- | :--- |
| §2.1 Datos nuevos | [`N-1`](../internal/STORIES/N-01.md) esquema, [`N-3`](../internal/STORIES/N-03.md) cerrar operación, [`N-4`](../internal/STORIES/N-04.md) interés por propiedad |
| §2.2 Métricas | [`N-6`](../internal/STORIES/N-06.md) inventario y rotación, [`N-7`](../internal/STORIES/N-07.md) atribución y conversión, [`N-8`](../internal/STORIES/N-08.md) vista en el panel |
| §2.3 Cosas a decidir | [`N-2`](../internal/STORIES/N-02.md) regla de atribución y visibilidad, [`N-9`](../internal/STORIES/N-09.md) historia sintética |
| §3 Demanda insatisfecha, campaña y ranking | [`N-5`](../internal/STORIES/N-05.md); el ranking va en [`N-6`](../internal/STORIES/N-06.md) |
| §3 Reporte semanal | [`N-10`](../internal/STORIES/N-10.md) (P2) |
| §4 Para el asesor | [`A-16`](../internal/STORIES/A-16.md) resumen, [`B-12`](../internal/STORIES/B-12.md) SLA (P2) |
| §5 Agenda real y recordatorio | `B-8` (ya existía), [`B-13`](../internal/STORIES/B-13.md) recordatorio (P2) |
| §5 Fotos y botones | [`B-10`](../internal/STORIES/B-10.md) foto, [`B-11`](../internal/STORIES/B-11.md) botones |
| §5 Alertas y notas de voz | [`A-18`](../internal/STORIES/A-18.md), [`B-14`](../internal/STORIES/B-14.md) (P2, tarea futura: fuera del capstone) |
| *Scheduler* (lo piden §3 y §5) | [`A-17`](../internal/STORIES/A-17.md) (P2) |
| §8 `need_fit` | [`A-15`](../internal/STORIES/A-15.md) |
