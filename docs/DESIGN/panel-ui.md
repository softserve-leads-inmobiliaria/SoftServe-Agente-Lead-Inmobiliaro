# UI/UX del panel de asesores — referencia compartida

Este documento es el **boceto acordado** antes de implementar el rediseño del panel
([#178](https://github.com/softserve-leads-inmobiliaria/SoftServe-Agente-Lead-Inmobiliaro/issues/178)).
Fija el sistema visual, los componentes, la navegación y el comportamiento por vista para que el
rediseño no se improvise archivo por archivo. El *por qué* de cada decisión de arquitectura vive en
`docs/DECISIONS/decisions.md`; acá vive el *cómo se ve y se usa*.

**Alcance.** Solo `services/panel/` (FastAPI + Jinja2 + la hoja de estilos de `base.html`). No cambia
rutas, datos, autenticación ni la regla de privacidad de las vistas agregadas (DT-41). Si una decisión
de este documento cambia, se cambia acá primero y se enlaza al PR que la implementa.

## 1. Estado actual y problemas

El panel funciona y cumple sus historias, pero visualmente está por debajo de una demo. Hoy:

- **Navegación.** No hay menú. Cada vista enlaza a las demás con texto suelto en el subtítulo
  (`leads_list.html`: "Avisos · Funnel del negocio →"; `funnel.html`: "Vista global · Inventario y
  negocio · ← Mis leads"). Avisos, funnel y negocio no se encuentran a simple vista.
- **Jerarquía.** `negocio.html` apila ~10 tarjetas de tablas sin indicadores arriba, sin gráficas y con
  la misma tipografía para todo: no responde "¿cómo vamos?" de un vistazo.
- **Cola.** Identifica al lead por `telegram_user_id`; la banda y el estado se distinguen por color de
  texto (`base.html`: `.band-HOT`, `.band-WARM`, `.band-COLD`, `.pill-sql`, `.pill-mql`); el SLA
  vencido de B-12 es un fondo rosado tenue (`tr.overdue`).
- **Formularios.** Botones, selector de periodo y fechas sin estados (hover, focus, deshabilitado). No
  hay confirmación visual después de "Marcar en gestión".
- **Accesibilidad.** La banda se comunica solo por color, el gris `--muted: #6a7280` tiene poco
  contraste sobre `--bg: #f6f7f9`, y no hay estilos de foco para teclado.
- **Responsivo.** Las tablas anchas (negocio, funnel) se desbordan en pantallas chicas.

## 2. Principios

1. **Un vistazo primero.** Cada vista responde su pregunta principal arriba (KPIs) antes del detalle.
2. **Nada solo por color.** Banda, estado y SLA llevan texto (o icono + texto), no solo color.
3. **Reutilizar, no repetir.** Un componente por patrón (tarjeta, tabla, badge, botón, formulario,
   estado vacío) en `base.html`, usado por todas las plantillas.
4. **Sin paso de build.** Jinja2 + CSS plano; si hace falta una librería, por CDN. Las gráficas son SVG
   generado en la plantilla.
5. **La privacidad no se toca.** Las vistas agregadas (funnel, negocio) siguen sin mostrar leads ni
   conversaciones (DT-41). El rediseño no agrega datos, solo los presenta mejor.
6. **No romper los tests.** Los tests del panel que buscan texto en el HTML se mantienen o se ajustan
   en el mismo PR (ver §11).

## 3. Sistema visual (tokens)

Se definen como variables CSS en `:root` de `base.html` (hoy ya hay cinco: `--bg`, `--card`, `--ink`,
`--muted`, `--line`, `--hot`, `--warm`, `--cold`).

### Color

| Token | Claro | Oscuro | Uso |
| :--- | :--- | :--- | :--- |
| `--bg` | `#f6f7f9` | `#12151a` | Fondo de página |
| `--surface` | `#ffffff` | `#1b1f24` | Tarjetas, tablas |
| `--surface-2` | `#f0f2f5` | `#232830` | Filas alternas, encabezados de tabla |
| `--ink` | `#1b1f24` | `#e8eaed` | Texto principal |
| `--muted` | `#525a66` | `#9aa4b2` | Texto secundario (**corregido**: el actual `#6a7280` no pasa AA sobre `--bg`) |
| `--line` | `#e3e6ea` | `#2c323b` | Bordes |
| `--brand` | `#1f4e79` | `#8ab4f8` | Acciones, enlaces, nav activo |
| `--hot` / `--warm` / `--cold` | `#b3261e` / `#8a5200` / `#33566e` | `#f2b8b5` / `#f0c36d` / `#9ec1d6` | Banda de score (siempre con texto) |
| `--danger` | `#b3261e` | `#f2b8b5` | Errores, SLA vencido |
| `--ok` | `#1e6b3a` | `#7fd0a0` | Confirmaciones |

Modo oscuro con `@media (prefers-color-scheme: dark)` y, si se acuerda, un toggle que fija
`data-theme` en `<html>`. El toggle es opcional; el modo oscuro automático no.

### Tipografía

- Stack: `system-ui, -apple-system, "Segoe UI", Roboto, sans-serif` (el actual).
- Escala: `--text-xs: 12px`, `--text-sm: 13px`, `--text-base: 15px`, `--text-lg: 18px`,
  `--text-xl: 24px`, `--text-2xl: 30px`. Peso `600` para títulos, `400` para cuerpo.
- Los títulos de sección dejan de ser `<h2 style="font-size:15px">` inline y pasan a una clase
  `.section-title` (`--text-lg`).

### Espaciado y forma

- Escala de 4px: `--space-1: 4px` … `--space-6: 32px`.
- Radio: `--radius: 10px` (tarjetas), `--radius-sm: 6px` (controles).
- Elevación: borde `1px solid var(--line)` (el panel evita sombras); sombra suave solo en el menú
  desplegable en móvil.

## 4. Componentes reutilizables

Se implementan como clases en `base.html` y/o macros de Jinja (`{% macro %}`), para usarlos desde
todas las plantillas:

- **`.card`** — contenedor con borde y radio. Encabezado opcional `.card-head` con título y acción.
- **`.stat`** — indicador clave: valor grande (`--text-2xl`), etiqueta (`--text-sm`, `--muted`),
  variación opcional. Se usan en fila (`.stat-row`) en funnel y negocio.
- **`.table`** — tabla con encabezado en `--surface-2`, filas alternas y `overflow-x: auto` en un
  contenedor `.table-wrap`. En móvil puede pasar a `.table-cards` (ver §9).
- **`.badge`** — etiqueta con texto. Variantes `.badge-hot/.badge-warm/.badge-cold` (banda) y
  `.badge-estado` (nuevo, mql, sql, cerrada). **Siempre con texto**, no solo color.
- **`.btn`** — botón primario (`.btn-primary`, fondo `--brand`), secundario (`.btn-ghost`, borde) y
  peligroso (`.btn-danger`). Estados `:hover`, `:focus-visible`, `[disabled]` y `.is-loading`.
- **`.field`** — `label` + control con `--radius-sm`, foco visible y mensaje de error `.field-error`.
- **`.empty`** — estado vacío: texto + acción sugerida cuando aplica ("Todavía no tienes avisos…").
- **`.notice`** — item de aviso; el no leído lleva un punto/etiqueta "nuevo" **y** negrita, no solo
  negrita.
- **`.kpi`** — reutiliza `.stat` con un delta (p. ej. "% del bot").

## 5. Layout y navegación

Un *layout* común en `base.html` con `{% block %}` para contenido:

- **Escritorio (≥ 1024px):** barra lateral fija a la izquierda (`.sidebar`) con:
  - Nombre del asesor y su zona arriba.
  - Items: **Mis leads**, **Avisos** (con contador de no leídos), **Funnel**, **Negocio**.
  - El item activo se marca con `aria-current="page"` y fondo `--brand` tenue.
- **Móvil (< 1024px):** barra superior (`.topbar`) con el nombre y un botón que despliega el mismo
  menú (sin JavaScript obligatorio: `<details>`/`<summary>` basta).
- **Enlace "saltar al contenido"** al principio del `body` (`<a class="skip" href="#main">`), visible
  al enfocar.
- Los enlaces de "volver" se conservan en la ficha, pero la navegación principal deja de depender del
  subtítulo.

El contador de Avisos sale del `unread` que ya calcula `main.py`; no se agrega ninguna consulta nueva.

## 6. Vistas

### 6.1 Cola (`leads_list.html`) — `/panel/{token}`

- **KPIs arriba:** leads en la cola, HOT sin tomar (con el umbral de `sla_minutes`), avisos sin leer.
- **Tabla:** columnas Lead, Score (badge + total), Etapa, Estado (badge), Último mensaje, acción.
- **Identificador legible:** en vez del `telegram_user_id` crudo, mostrar `Lead <short>` (primeros 6
  caracteres del UUID) con la zona; el `telegram_user_id` queda en la ficha (§6.2). Es solo
  presentación: no cambia la privacidad ni la identificación real.
- **SLA vencido (B-12):** fila con borde izquierdo `--danger`, badge "HOT sin tomar hace X min" con
  texto e icono; no solo el fondo rosado.

### 6.2 Ficha (`lead_detail.html`) — `/panel/{token}/leads/{id}`

- **Encabezado:** `Lead <short>`, badge de banda y estado, y las acciones ("Marcar en gestión",
  "Cerrar operación") a la derecha.
- **Secciones** en tarjetas: Datos del lead, Score BANT-E (con las cinco dimensiones en barras),
  Visitas, Conversación (orden cronológico).
- **Confirmación:** después de "Marcar en gestión", un aviso `.flash` "Lead en gestión" (el POST
  redirige con 303; el mensaje puede viajar como parámetro de query y renderizarse una vez).
- **Cerrar operación:** el formulario usa `.field`; los errores de validación se muestran junto al
  campo (`close_error`), manteniendo los valores escritos (hoy ya se conservan).

### 6.3 Avisos (`avisos.html`) — `/panel/{token}/avisos`

- Lista con encabezado y contador; el aviso no leído lleva etiqueta "nuevo" + punto de color + negrita.
- Se mantiene el auto-refresh (`<meta http-equiv="refresh" content="{{ refresh_seconds }}">`), que es
  lo que hace que los avisos "lleguen solos".
- Estado vacío claro con la acción esperada.

### 6.4 Funnel (`funnel.html`) — `/panel/{token}/funnel`

- **KPIs:** total de leads y la etapa más avanzada con más caída.
- **Tabla de etapas** con una barra proporcional (SVG inline) al lado del porcentaje, para comparar de
  un vistazo. Los estados laterales (NURTURE, LOST…) en una sección aparte.
- Se conservan las notas de DT-17 (la entrada a GREETING no es una transición).

### 6.5 Negocio (`negocio.html`) — `/panel/{token}/negocio`

- **Fila de KPIs:** colocadas, monto colocado, % atribuido al bot y tiempo de atención (B-12).
- **Secciones por pregunta de negocio**, no 10 tarjetas iguales: "¿Qué se colocó?" (por zona/asesor),
  "¿Qué rota rápido y qué está estancado?" (rotación y estancadas), "¿De dónde vienen los cierres?"
  (atribución), "¿Qué buscan y no encuentran?" (demanda insatisfecha). Cada sección con su tabla o
  barras.
- **Selector de periodo** con `.field` y botón; "Incluir historia de demo" como checkbox etiquetado.
- **Estado vacío por sección** ("Sin datos en este periodo."), que ya existe y se conserva.

## 7. Gráficas

- **SVG inline generado en Jinja** (barras horizontales para comparar categorías; barras de etapas en
  el funnel). Sin dependencias ni CDN. Escala relativa al máximo de la serie.
- Si se acuerda una librería (p. ej. Chart.js por CDN), se justifica en este documento y se fija la
  versión por URL; por defecto, no.

## 8. Accesibilidad

- **Contraste AA:** texto normal ≥ 4.5:1, texto grande ≥ 3:1. `--muted` corregido (§3).
- **Foco visible:** `:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }` en
  enlaces, botones, controles y filas de tabla navegables.
- **Nada solo por color:** banda, estado, SLA y "nuevo" llevan texto y/o icono.
- **Semántica:** `<nav aria-label="Principal">`, `aria-current="page"` en el item activo, `scope="col"`
  en los `th`, `label` asociado a cada control, `role="status"` en el `.flash`.
- **Movimiento:** respetar `prefers-reduced-motion`; el auto-refresh de avisos es contenido, no
  decoración, y se mantiene.
- **Zoom:** el layout funciona a 200% sin scroll horizontal (salvo tablas, que scrollean en su caja).

## 9. Responsivo

- **≥ 1024px:** barra lateral + contenido (máx. ~1100px).
- **640–1023px:** barra superior; tablas con `overflow-x: auto`.
- **< 640px:** tablas anchas en formato tarjeta (`.table-cards`: cada fila es una tarjeta con
  `data-label` en cada celda). Aplica sobre todo a negocio y funnel.

## 10. Restricciones y compatibilidad

- **Sin build:** Jinja2 + CSS; ninguna herramienta de compilación en la imagen del panel.
- **Sin datos nuevos:** todo sale de lo que `main.py` y `repo.py` ya calculan (`unread`, `overdue`,
  `report`, `view`). El rediseño no agrega consultas ni columnas.
- **Rutas y auth intactas:** mismas URLs, mismo token estático (DT-5), mismo 404 uniforme para token o
  lead ajenos. No se introducen roles ni login.
- **Privacidad intacta (DT-41):** funnel y negocio siguen sin mostrar leads, conversaciones ni
  contacto; la lista de estancadas muestra propiedades.

## 11. Tests que miran el HTML

El rediseño conserva o ajusta (en el mismo PR) los textos que los tests buscan hoy, entre otros:

- `test_avisos.py`: `"Avisos (1 sin leer)"`, `"1 sin leer"`, `"Todavía no tienes avisos"`,
  `http-equiv="refresh"`.
- `test_funnel.py`: `"0 leads empezaron"`, `"Todavía no hay leads"`, `"75%"`.
- `test_negocio.py`: `"Del … al …"`, `"1 operaciones cerradas por $…"`, `"Sin datos en este periodo."`
  (10 secciones), `"feria-sep"`, `"+60"`, `"100%"`.
- `test_close_deal.py`: mensajes de error de cierre y `data-kind`/`selected` del selector de propiedad.
- `test_panel.py` / `test_avisos.py`: el `telegram_user_id` visible en la cola (si se cambia a
  `Lead <short>`, el test se ajusta a propósito en el PR).

## 12. Preguntas abiertas (a acordar con el equipo)

1. **Navegación:** ¿barra lateral en escritorio o barra superior para todos los tamaños?
2. **Modo oscuro:** ¿solo automático por `prefers-color-scheme`, o además un toggle manual?
3. **Gráficas:** ¿alcanza con SVG inline, o se quiere una librería por CDN?
4. **Identificador del lead:** ¿`Lead <short>` es suficiente, o se prefiere otro esquema?
5. **Auto-refresh:** ¿se mantiene a 30 s, o se sube/baja con el rediseño?

## 13. Mapeo con #178

Cada punto de la solución propuesta en el issue queda cubierto así:

| Punto de #178 | Sección de este documento |
| :--- | :--- |
| Sistema visual (tokens, componentes, modo oscuro) | §3, §4 |
| Navegación con menú y vista activa | §5 |
| Vista de negocio (KPIs, gráficas, secciones) | §6.5, §7 |
| Cola y ficha (badges, SLA, identificador) | §6.1, §6.2 |
| Accesibilidad | §8 |
| Responsivo | §9 |
| Sin build / CDN | §7, §10 |
| Mantener/ajustar tests | §11 |

La implementación de #178 se hace en un PR que sigue este boceto; si algo cambia en el camino, se
actualiza este documento en el mismo PR.
