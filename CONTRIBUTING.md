# Cómo trabajamos — reglas del equipo

Documento corto, operativo. El *por qué* de las decisiones de arquitectura vive en
`docs/DECISIONS/decisions.md`; el *qué* de cada historia vive en `docs/STORIES/`. Esto es el
*cómo* del día a día con 3 personas tocando el mismo repo a la vez.

**¿Primera vez usando Git/GitHub en equipo?** Empezá por [`docs/ONBOARDING.md`](docs/ONBOARDING.md)
— tiene el flujo completo paso a paso con los comandos exactos. Este documento son las reglas;
ese es el tutorial.

## Regla #1 — nunca push directo a `main`

Todo cambio entra por Pull Request, sin excepción — ni un `README.md` typo, ni un fix de 1 línea
de madrugada.

Por qué: con 3 personas trabajando en tracks que comparten archivos (`db/models.py`,
`docker-compose.yml`, contratos) un push directo sin review es la forma más rápida de romper el
trabajo de otro sin que nadie se entere hasta el siguiente `git pull`.

**Estado real de la protección técnica (2026-09-15):** intenté activar branch protection sobre
`main` (`scripts/github/protect_main.sh`) y **GitHub la rechaza** — ni la clásica ni la versión
nueva ("Rulesets") están disponibles en un repo **privado** bajo un plan **Free de organización**;
solo en repos públicos o con GitHub Pro/Team. Por ahora esta regla depende de disciplina del
equipo, no de que la plataforma la bloquee. Opciones reales si se quiere forzar técnicamente:

1. **Revisar si el curso/programa tiene GitHub Education / Campus Program** para la organización
   — muchos bootcamps lo gestionan con el instructor y da GitHub Team gratis a la org del equipo.
2. Hacer el repo público (gratis, habilita branch protection) — solo si no hay problema con que el
   código sea visible.
3. Pagar GitHub Team (~USD 4/persona/mes) — última opción, no recomendada para un capstone.

Una vez que cualquiera de las 3 esté resuelta, correr `scripts/github/protect_main.sh` — ya está
listo, solo esperando que la cuenta lo permita.

## Ramas y Pull Requests

- Nombre de rama: `<track>/<ID>-slug-corto` — ej. `core/a-4-recomendacion-datos-reales`,
  `borde/b-1-bot-telegram`. El `<ID>` coincide con el de `docs/STORIES/<ID>.md` y con el título
  `[ID] ...` del Issue — así se puede rastrear rama → Issue → historia sin ambigüedad.
- Un PR = una historia (o un `[gap]`/`[tech-debt]` chico). Si un PR crece hasta tocar 2 historias
  no relacionadas, se parte.
- **Quién revisa:** idealmente la persona que consume el contrato/módulo desde el otro lado (ej.
  quien construyó `catalog.get_property` revisa el PR que lo consume desde `core`) — no solo
  "cualquiera del equipo". Esto ya está en la Definition of Done común de `docs/STORIES/README.md`.
- No mergear con CI en rojo. Si un *contract test* se rompe, se arregla el contrato o el consumidor
  antes de mergear — nunca se "arregla después".
- El merge lo hace quien revisó (o el autor, una vez aprobado) — no auto-mergear sin aprobación
  aunque CI esté verde.

## Commits

- Mensaje describe el *por qué*, no el *qué* (el diff ya dice qué cambió).
- Si el commit cierra o avanza una historia, referenciar el Issue (`Refs #12`, o `Closes #12` en
  el PR) — así el estado en GitHub Issues/Project se actualiza solo al mergear, en vez de
  actualizarlo a mano (ver `docs/ESTRUCTURA-DEL-PROYECTO.md` §5, una sola fuente de verdad de
  estado).
- Nunca `git commit --amend` ni `git push --force` sobre una rama que otra persona ya tiene
  pulleada — si hace falta corregir algo ya pusheado y compartido, un commit nuevo.
- Nunca `--no-verify` para saltarse un hook, ni `-c commit.gpgsign=false` si la firma está
  configurada — si un hook falla, es una señal real, no un obstáculo.

## Docs — una sola fuente de verdad para cada cosa

Regla ya fijada en `docs/ESTRUCTURA-DEL-PROYECTO.md` §0/§3, repetida acá porque es la que más se
olvida bajo presión de entrega:

- **Estado** (To Do / In Progress / Done) vive **solo** en GitHub Issues/Project. Nunca lo
  marques en `docs/STORIES/<ID>.md` ni en un `todo.md` paralelo.
- **Contenido de la historia** (criterios de aceptación, DoD) vive **solo** en
  `docs/STORIES/<ID>.md`. El Issue es tracking, no la fuente — si el alcance cambia de verdad,
  se edita el `.md` vía PR, no solo el Issue.
- Si dudás dónde poner algo nuevo, es más probable que ya exista un lugar (`docs/DECISIONS/`,
  `docs/DESIGN/`) que necesite un archivo nuevo.
- **La documentación no referencia proyectos anteriores** ("v1", "FINAL-PROJECT", "en el proyecto
  anterior..."). Este repo se documenta y se justifica por sí mismo — cada regla, decisión o
  historia nueva lleva su propia razón, autocontenida, no una comparación con algo previo. Esto
  vale para toda la documentación de `docs/` salvo `docs/DECISIONS/decisions.md`, que solo se
  amplía y no se reescribe. `tests/infra/test_docs_links.py` falla si vuelve a aparecer una
  referencia.

## Secretos y configuración

- `.env` nunca se commitea — solo `.env.example` con placeholders. Si accidentalmente se pushea
  una clave real, se rota la clave, no alcanza con un commit que la borre (queda en el historial).
- Ningún token/API key en código, logs, ni en el cuerpo de un Issue/PR — ni siquiera "temporalmente
  para probar".

## Al cerrar un Issue

Antes de cerrar (o al mergear el PR que lo cierra), revisar — no todo aplica siempre, pero el
chequeo es obligatorio, no opcional:

- **¿La historia cambió de alcance real respecto a `docs/STORIES/<ID>.md`?** Si sí, editar ese
  archivo en el mismo PR — no dejar que el Issue diga una cosa y el `.md` otra.
- **¿Se tomó una decisión de arquitectura nueva (o se cambió una existente)?** Agregar su entrada
  en `docs/DECISIONS/decisions.md`, enlazando al Issue/PR — ya está en la Definition of Done común.
- **¿Algo salió distinto de lo esperado, se pisó un supuesto equivocado, o se encontró un
  *gotcha* que le va a ahorrar tiempo a la próxima persona (o a nosotros mismos en 2 semanas)?**
  Agregar una entrada corta en `docs/LESSONS-LEARNED.md`, para que el mismo error no se repita en
  otra historia. No hace falta para
  historias que salieron exactamente como estaban planeadas; sí para cualquier `[gap]`,
  `[tech-debt]`, o sorpresa real.

`docs/LESSONS-LEARNED.md` todavía no existe en este repo — crear la primera entrada ya lo crea.

## Cuando algo sale mal

- Antes de cualquier comando destructivo (`git reset --hard`, `git clean -f`, `git checkout --`
  sobre cambios sin commitear) ejecutar `git status` primero y confirmar qué se pierde.
- Si rompiste `main` sin querer (no debería poder pasar con la protección de rama activa, pero
  por si acaso): avisar al equipo antes de forzar nada — un revert vía PR es casi siempre más
  seguro que un force-push.
- Un guardrail o contract test roto en CI no se comenta/skipea para que pase — se arregla la causa
  o se abre un `[gap]` si el arreglo real no entra en el alcance del PR actual.
