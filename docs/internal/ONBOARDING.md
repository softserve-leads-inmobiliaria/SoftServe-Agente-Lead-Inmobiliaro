# Guía de Git/GitHub para el equipo

Esta guía asume que nunca trabajaste con Git/GitHub en equipo. Cubre el flujo completo de este
repo, paso a paso, desde clonar hasta que tu código quede en `main`. Las *reglas* (qué no hacer)
están en [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — esto es el *cómo*, con comandos concretos.

## 0. Conceptos en una frase

- **Repo (repositorio):** la carpeta del proyecto completo, con todo su historial de cambios.
- **Rama (branch):** una copia de trabajo separada de `main` donde se hacen cambios sin afectar
  el código de los demás hasta que estén listos.
- **Commit:** un "punto de guardado" con un mensaje que explica qué cambió y por qué.
- **Push:** subir los commits de tu computadora a GitHub.
- **Pull Request (PR):** un pedido de "quiero mezclar mi rama a `main`, revísenla antes". Es el
  único camino permitido para que algo llegue a `main` en este proyecto (ver `CONTRIBUTING.md`
  Regla #1).
- **Merge:** cuando el PR se aprueba y los cambios se mezclan a `main`.
- **Issue:** un ítem de trabajo o un problema reportado en GitHub — una historia planificada, o
  un bug/gap/deuda técnica encontrado sobre la marcha (ver §7).

## 1. Setup — una sola vez

1. **Instalar Git:** https://git-scm.com/downloads (Windows: instalador normal, dejar las
   opciones por defecto).
2. **Instalar GitHub CLI (`gh`):** https://cli.github.com/ — facilita todo lo de abajo. Después
   de instalarlo, abrir una terminal y ejecutar:
   ```
   gh auth login
   ```
   Elegir `GitHub.com` → `HTTPS` → `Login with a web browser`, y seguir las instrucciones.
3. **Confirmar membresía en la organización** — debería haber llegado un mail de invitación a
   `softserve-leads-inmobiliaria`. Si no llegó, avisar para que envíen la invitación.
4. **Clonar el repo** (bajarlo a tu computadora, una sola vez):
   ```
   git clone https://github.com/softserve-leads-inmobiliaria/SoftServe-Agente-Lead-Inmobiliaro.git
   cd SoftServe-Agente-Lead-Inmobiliaro
   ```
5. **Configurar nombre y email** (así los commits te identifican a ti):
   ```
   git config --global user.name "Tu Nombre"
   git config --global user.email "tu-email@ejemplo.com"
   ```

## 2. Encontrar qué hacer

1. Ir al Project: **https://github.com/orgs/softserve-leads-inmobiliaria/projects/1**
2. Entrar a la vista **Table**, agrupada por **Track** — buscar el track correspondiente
   (Core / Borde / Datos).
3. Elegir una historia con Status = **Todo**, respetando el orden de
   [`docs/STORIES/ROADMAP.md`](STORIES/ROADMAP.md) (no elegir una que dependa de otra que
   todavía no está hecha — la columna "Depende de" del Issue indica si hay que esperar).
4. **Antes de tocar código**, leer el archivo completo de esa historia en
   `docs/STORIES/<ID>.md` (ej. `docs/STORIES/A-04.md`) — ahí está la Historia de Usuario, los
   Criterios de Aceptación, y la Definition of Done específica. El Issue de GitHub es solo un
   resumen/tracking, el `.md` es la fuente real.

## 3. Flujo paso a paso para UNA historia

Ejemplo con la historia `CT-1`. Reemplazar por el ID real que corresponda.

### 3.1 Actualizar la copia local y crear la rama

```
git checkout main
git pull
git checkout -b core/ct-1-contrato-evento-canonico
```

- `git checkout main` — te ubica sobre la última versión de `main`.
- `git pull` — trae los cambios que otros ya mergearon, para no partir de una versión vieja.
- `git checkout -b <nombre>` — crea la rama nueva y te mueve a ella. El nombre sigue el patrón
  `<track>/<ID>-slug-corto` (ver `CONTRIBUTING.md`).

### 3.2 Hacer los cambios

Editar/crear los archivos que necesite la historia. Se puede ir guardando avances con commits
chicos en vez de uno solo gigante al final:

```
git add contracts/event.schema.json
git commit -m "Agregar JSON Schema del evento canónico"
```

- `git add <archivo>` — marca ese archivo para el próximo commit (`git add .` marca todos los
  cambios, útil pero conviene revisar antes con `git status` que no se cuele algo que no
  debería).
- `git commit -m "mensaje"` — guarda el punto. El mensaje explica el *por qué*, no repite el
  nombre del archivo.

Repetir `add` + `commit` las veces que sea necesario mientras se trabaja.

### 3.3 Subir la rama a GitHub

```
git push -u origin core/ct-1-contrato-evento-canonico
```

La primera vez se usa `-u origin <rama>` (crea la rama en GitHub y la "conecta" con la local).
Las próximas veces, con solo `git push` alcanza.

### 3.4 Abrir el Pull Request

Con `gh` (más rápido):
```
gh pr create --title "[CT-1] Congelar el contrato del evento de mensaje canónico" --body "Closes #1"
```
- `Closes #1` (con el número del Issue de GitHub) hace que el Issue se cierre solo cuando el PR
  se mergea — así no hace falta actualizarlo a mano.

O desde la web: entrar a la URL que `git push` mostró en la terminal (algo como
`https://github.com/.../pull/new/core/ct-1-...`), completar título/descripción, y clic en
**"Create pull request"**.

En ambos casos, el PR ya viene con una plantilla precargada
(`.github/PULL_REQUEST_TEMPLATE.md`): un checklist de Definition of Done y una sección "Cómo
probarlo". No es texto libre para escribir desde cero — completar los checkboxes que apliquen y
borrar los que no.

### 3.5 Pedir revisión

- Asignar un reviewer al PR (idealmente quien va a consumir lo que se hizo — ver
  `CONTRIBUTING.md`). Desde la web: panel derecho del PR → **Reviewers** → elegir persona.
- Avisar por el chat del equipo que el PR está listo — no asumir que se va a notar solo.

### 3.6 Responder comentarios (si los hay)

Si piden un cambio: hacer el cambio en la misma rama local, y otro `add` + `commit` + `push` —
se agrega solo al mismo PR, no hace falta abrir uno nuevo.

### 3.7 Mergear

Cuando el PR está aprobado y los checks de CI están en verde: botón **"Merge pull request"** en
la web (o `gh pr merge` desde la terminal). El Issue se cierra solo si se puso `Closes #N`.

### 3.8 Volver a `main` y limpiar

```
git checkout main
git pull
git branch -D core/ct-1-contrato-evento-canonico
```

Los PR se mergean con *squash* (todos los commits de la rama quedan en uno solo en `main`), así que
Git no reconoce la rama como mergeada y `git branch -d` se niega a borrarla: por eso `-D`. Si el
PR se mergeó con `--delete-branch`, la rama remota ya no existe; `git fetch --prune` limpia la
referencia local.

Y se arranca de nuevo desde el paso 3.1 con la siguiente historia.

## 4. Comandos que se usan todo el tiempo (resumen)

| Comando | Qué hace |
| :--- | :--- |
| `git status` | Qué cambió, qué está listo para commitear — usarlo seguido, no rompe nada |
| `git pull` | Traer lo nuevo de GitHub a la copia local |
| `git checkout -b <rama>` | Crear una rama nueva y moverse a ella |
| `git add <archivo>` | Marcar un cambio para el próximo commit |
| `git commit -m "..."` | Guardar un punto con mensaje |
| `git push` | Subir los commits a GitHub |
| `gh pr create` | Abrir un Pull Request |
| `gh pr view --web` | Abrir el PR propio en el navegador |

## 5. Errores comunes (y cómo evitarlos)

- **"Hice cambios pero `git status` no muestra nada"** — probablemente se está en `main` en vez
  de la rama propia. Revisar con `git branch` (la rama actual tiene un `*` al lado).
- **"`git push` da error"** — normalmente porque alguien más pusheó a `main` mientras se
  trabajaba. Solución: `git checkout main && git pull`, después volver a la rama y
  `git merge main` (o preguntar al equipo, es normal que pase).
- **Nunca** hacer `git push` directo a `main` — no debería estar permitido según `CONTRIBUTING.md`
  Regla #1, pero por las dudas: siempre rama → PR.
- **Si algo se rompió y no se sabe cómo seguir:** no borrar nada ni forzar (`git reset --hard`,
  `git push --force`) sin preguntar antes — avisar al equipo, casi siempre hay una forma de
  arreglarlo sin perder trabajo.

## 6. Dudas

Preguntar en el chat del equipo antes de quedar trabado más de 15-20 minutos con algo de Git —
es casi siempre más rápido preguntar que buscar en Google el mensaje de error exacto.

## 7. Reportar un bug, gap o deuda técnica (no una historia planificada)

Todo lo de arriba asume una historia que ya existe en `docs/STORIES/`. Pero durante el desarrollo
va a aparecer trabajo que **no** estaba planificado: un bug, algo que se asumía cubierto y no lo
está, una limitación de infraestructura descubierta, o deuda técnica. Para eso no se crea un
archivo en `docs/STORIES/` — se abre un Issue directamente:

1. En GitHub, botón **"New issue"** (o `gh issue create` desde la terminal). El repo tiene
   `blank_issues_enabled: false`, así que siempre se va a pedir elegir entre dos plantillas — no
   se puede crear un Issue en blanco:
   - **"Historia de usuario"** — para una historia nueva que sí se va a formalizar en
     `docs/STORIES/` (caso poco común para alguien nuevo en el equipo).
   - **"Gap, bug o deuda técnica"** — la que aplica en la mayoría de los casos: bug encontrado,
     algo que faltaba, deuda técnica, constraint de infra. El título queda con el prefijo
     `[gap]`, `[bug]`, `[tech-debt]`, `[DevOps]` o `[constraint]` según corresponda (la plantilla
     lo explica).
2. Completar la plantilla: qué pasa y por qué importa, cómo reproducirlo, prioridad. Asignar
   labels `track:*` y `prio:*` igual que a cualquier historia (`epic:*` y `points:*` son
   opcionales en este tipo de Issue).
3. Si durante el trabajo de una historia se encuentra uno de estos casos, se puede resolver en el
   mismo PR (si es chico) o abrir el Issue aparte y seguir con la historia original — ver
   "Un PR = una historia (o un `[gap]`/`[tech-debt]` chico)" en `CONTRIBUTING.md`.
