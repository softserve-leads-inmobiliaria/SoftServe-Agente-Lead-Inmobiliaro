# Guía de Git/GitHub para el equipo

Esta guía asume que nunca trabajaste con Git/GitHub en equipo. Cubre el flujo completo de este
repo, paso a paso, desde clonar hasta que tu código quede en `main`. Las *reglas* (qué no hacer)
están en [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — esto es el *cómo*, con comandos concretos.

## 0. Conceptos en una frase

- **Repo (repositorio):** la carpeta del proyecto completo, con todo su historial de cambios.
- **Rama (branch):** una copia de trabajo separada de `main` donde hacés tus cambios sin afectar
  el código de los demás hasta que estés listo.
- **Commit:** un "punto de guardado" con un mensaje que explica qué cambiaste y por qué.
- **Push:** subir tus commits de tu computadora a GitHub.
- **Pull Request (PR):** un pedido de "quiero mezclar mi rama a `main`, revísenla antes". Es el
  único camino permitido para que algo llegue a `main` en este proyecto (ver `CONTRIBUTING.md`
  Regla #1).
- **Merge:** cuando el PR se aprueba y tus cambios se mezclan a `main`.

## 1. Setup — una sola vez

1. **Instalar Git:** https://git-scm.com/downloads (Windows: instalador normal, dejá las opciones
   por defecto).
2. **Instalar GitHub CLI (`gh`):** https://cli.github.com/ — facilita todo lo de abajo. Después de
   instalarlo, abrí una terminal y corré:
   ```
   gh auth login
   ```
   Elegí `GitHub.com` → `HTTPS` → `Login with a web browser`, y seguí las instrucciones.
3. **Confirmá que sos miembro de la organización** — deberías haber recibido un mail de invitación
   a `softserve-leads-inmobiliaria`. Si no, avisá para que te inviten.
4. **Clonar el repo** (bajarlo a tu computadora, una sola vez):
   ```
   git clone https://github.com/softserve-leads-inmobiliaria/SoftServe-Agente-Lead-Inmobiliaro.git
   cd SoftServe-Agente-Lead-Inmobiliaro
   ```
5. **Configurar tu nombre y email** (así tus commits te identifican a vos):
   ```
   git config --global user.name "Tu Nombre"
   git config --global user.email "tu-email@ejemplo.com"
   ```

## 2. Encontrar qué hacer

1. Andá al Project: **https://github.com/orgs/softserve-leads-inmobiliaria/projects/1**
2. Entrá a la vista **Table**, agrupada por **Track** — buscá tu track (Core / Borde / Datos).
3. Elegí una historia con Status = **Todo**, respetando el orden de
   [`docs/STORIES/ROADMAP.md`](STORIES/ROADMAP.md) (no elijas una que dependa de otra que todavía
   no está hecha — la columna "Depende de" del Issue te dice si hay que esperar).
4. **Antes de tocar código**, leé el archivo completo de esa historia en
   `docs/STORIES/<ID>.md` (ej. `docs/STORIES/A-04.md`) — ahí está la Historia de Usuario, los
   Criterios de Aceptación, y la Definition of Done específica. El Issue de GitHub es solo un
   resumen/tracking, el `.md` es la fuente real.

## 3. Flujo paso a paso para UNA historia

Ejemplo con la historia `CT-1`. Reemplazá por el ID real que estés haciendo.

### 3.1 Actualizar tu copia local y crear tu rama

```
git checkout main
git pull
git checkout -b core/ct-1-contrato-evento-canonico
```

- `git checkout main` — te parás sobre la última versión de `main`.
- `git pull` — bajás los cambios que otros ya mergearon, para no partir de una versión vieja.
- `git checkout -b <nombre>` — crea tu rama nueva y te mueve a ella. El nombre sigue el patrón
  `<track>/<ID>-slug-corto` (ver `CONTRIBUTING.md`).

### 3.2 Hacer tus cambios

Editá/creá los archivos que necesite la historia. Podés ir guardando avances con commits chicos
en vez de uno solo gigante al final:

```
git add contracts/event.schema.json
git commit -m "Agregar JSON Schema del evento canónico"
```

- `git add <archivo>` — marca ese archivo para el próximo commit (`git add .` marca todos los
  cambios, útil pero revisá antes con `git status` que no se cuele algo que no debería).
- `git commit -m "mensaje"` — guarda el punto. El mensaje explica el *por qué*, no repitas el
  nombre del archivo.

Repetí `add` + `commit` las veces que necesites mientras trabajás.

### 3.3 Subir tu rama a GitHub

```
git push -u origin core/ct-1-contrato-evento-canonico
```

La primera vez usás `-u origin <rama>` (crea la rama en GitHub y la "conecta" con la tuya). Las
próximas veces, con solo `git push` alcanza.

### 3.4 Abrir el Pull Request

Con `gh` (más rápido):
```
gh pr create --title "[CT-1] Congelar el contrato del evento de mensaje canónico" --body "Closes #1"
```
- `Closes #1` (con el número del Issue de GitHub) hace que el Issue se cierre solo cuando el PR
  se mergea — así no hay que actualizarlo a mano.

O desde la web: entrá a la URL que `git push` te mostró en la terminal (algo como
`https://github.com/.../pull/new/core/ct-1-...`), completá título/descripción, y clic en
**"Create pull request"**.

### 3.5 Pedir revisión

- Asignale un reviewer al PR (idealmente quien va a consumir lo que hiciste — ver
  `CONTRIBUTING.md`). Desde la web: panel derecho del PR → **Reviewers** → elegir persona.
- Avisale por el chat del equipo que el PR está listo — no asumas que lo va a ver solo.

### 3.6 Responder comentarios (si los hay)

Si te piden un cambio: hacé el cambio en tu misma rama local, y otro `add` + `commit` + `push` —
se agrega solo al mismo PR, no hace falta abrir uno nuevo.

### 3.7 Mergear

Cuando el PR está aprobado y los checks de CI están en verde: botón **"Merge pull request"** en la
web (o `gh pr merge` desde la terminal). El Issue se cierra solo si pusiste `Closes #N`.

### 3.8 Volver a `main` y limpiar

```
git checkout main
git pull
git branch -d core/ct-1-contrato-evento-canonico
```

Y arrancás de nuevo desde el paso 3.1 con la siguiente historia.

## 4. Comandos que vas a usar todo el tiempo (resumen)

| Comando | Qué hace |
| :--- | :--- |
| `git status` | Qué cambiaste, qué está listo para commitear — usalo seguido, no rompe nada |
| `git pull` | Traer lo nuevo de GitHub a tu copia local |
| `git checkout -b <rama>` | Crear una rama nueva y moverte a ella |
| `git add <archivo>` | Marcar un cambio para el próximo commit |
| `git commit -m "..."` | Guardar un punto con mensaje |
| `git push` | Subir tus commits a GitHub |
| `gh pr create` | Abrir un Pull Request |
| `gh pr view --web` | Abrir tu PR en el navegador |

## 5. Errores comunes (y cómo evitarlos)

- **"Hice cambios pero `git status` no muestra nada"** — probablemente estás en `main` en vez de
  tu rama. Fijate con `git branch` (la rama actual tiene un `*` al lado).
- **"`git push` me tira error"** — normalmente porque alguien más pusheó a `main` mientras
  trabajabas. Solución: `git checkout main && git pull`, después volvé a tu rama y `git merge main`
  (o preguntá al equipo, es normal que pase).
- **Nunca** `git push` directo a `main` — no debería ni dejarte según `CONTRIBUTING.md` Regla #1,
  pero por las dudas: siempre rama → PR.
- **Si algo se rompió y no sabés cómo seguir:** no borres nada ni fuerces (`git reset --hard`,
  `git push --force`) sin preguntar antes — avisá al equipo, casi siempre hay una forma de
  arreglarlo sin perder trabajo.

## 6. Dudas

Preguntá en el chat del equipo antes de quedarte trabado más de 15-20 minutos con algo de Git —
es casi siempre más rápido preguntar que buscar en Google el mensaje de error exacto.
