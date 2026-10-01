# Propuestas de funcionalidades

Ideas para después del alcance actual, con lo que cada una necesita del sistema. **No son historias
ni compromisos**: cuando se elija alguna, se baja a una historia en `docs/STORIES/` y su issue en el
[Project](https://github.com/orgs/softserve-leads-inmobiliaria/projects/1). Lo que ya está construido
está en [`design.md` §5](design.md#5-estado-real-del-sistema).

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
| 2 | Demanda insatisfecha (§3) | Cuesta poco: las búsquedas ya pasan por `core` |
| 3 | Fotos y botones en Telegram (§5) | El mayor efecto visual en la demo por el esfuerzo |

## 7. Pendientes que conviene cerrar antes

Estas propuestas se apoyan en flujos que hoy tienen huecos conocidos:

- **#125**: dar datos de calificación cuenta como frustración y lleva al lead a `HUMAN_HANDOFF`. Ensucia
  la conversión por banda y cualquier métrica de frustración.
- **`B-8`**: sin agenda real no hay visitas, y la métrica "consultas sin visitas" de §2.2 sale vacía.
- **`need_fit`** solo cuenta resultados de `search_properties`: un lead que llega por el anuncio de una
  propiedad que entra en su presupuesto queda con `need_fit = 0`.
