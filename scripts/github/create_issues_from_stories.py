#!/usr/bin/env python3
"""Crea Issues de GitHub en masa a partir de docs/STORIES/*.md.

Parsea cada historia (formato fijado por docs/STORIES/PLANTILLA-HISTORIA.md):

    # <ID> — <Título>

    **Épica:** <E0..E5> · <nombre>
    **Prioridad:** <P0|P1|P2> · **Puntos:** <n> · **Track:** <A|B|C|Cross>
    **Depende de:** <IDs separados por coma, o —>

    ## Historia de usuario
    ...

y crea un Issue por historia: título "[ID] Título", cuerpo = el resto del
archivo, labels = prio:*/track:*/epic:* según la tabla de docs/STORIES/README.md.

Dos fases (podés correrlas juntas o por separado):

  1. Creación — una Issue por historia que todavía no esté en el archivo de
     mapeo (idempotente: correr el script de nuevo no duplica).
  2. Enlazado — reemplaza la línea "**Depende de:** A-3, C-1" del cuerpo por
     "**Depende de:** A-3 (#12), C-1 (#8)" usando los números de Issue reales,
     una vez que TODAS las dependencias de esa historia ya tienen Issue creada.

Uso:
    python create_issues_from_stories.py --repo owner/repo                # dry-run (no toca GitHub)
    python create_issues_from_stories.py --repo owner/repo --apply        # crea + enlaza
    python create_issues_from_stories.py --repo owner/repo --apply --limit 9   # solo las primeras 9 sin mapear
    python create_issues_from_stories.py --repo owner/repo --apply --only CT-1,CT-2
    python create_issues_from_stories.py --repo owner/repo --apply --link-only # solo repasa el enlazado
    python create_issues_from_stories.py --repo owner/repo --apply --relink    # fuerza re-enlazado de todas

Reutilizable en otro proyecto: --stories-dir apunta a cualquier carpeta con el
mismo formato de historia; solo el mapeo TRACK_LABELS/EPIC_LABELS de abajo es
específico de este proyecto (track:core/borde/datos/cross, epic:*) — ajustalo
si el próximo proyecto usa otra convención de labels.

Requiere: gh CLI autenticado con permiso sobre el repo (gh auth status).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# En Windows la consola suele venir en cp1252 y se cae con acentos/≥/etc. en
# los títulos de las historias — forzar UTF-8 en stdout/stderr.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

TRACK_LABELS = {
    "A": "track:core",
    "B": "track:borde",
    "C": "track:datos",
    "CROSS": "track:cross",
}

EPIC_LABELS = {
    "E0": "epic:contratos",
    "E1": "epic:motor-agente",
    "E2": "epic:canal-borde",
    "E3": "epic:datos",
    "E4": "epic:observabilidad",
    "E5": "epic:cierre",
}

EXCLUDE_STEMS = {"README", "EPICAS", "PLANTILLA-HISTORIA", "ROADMAP"}

HEADER_RE = re.compile(
    r"^#\s+(?P<id>\S+)\s+—\s+(?P<title>.+?)\s*$"
    r".*?"
    r"\*\*Épica:\*\*\s*(?P<epic>E\d)\s*·.*?$"
    r".*?"
    r"\*\*Prioridad:\*\*\s*(?P<prio>P\d)\s*·\s*\*\*Puntos:\*\*\s*(?P<points>\d+)\s*·\s*\*\*Track:\*\*\s*(?P<track>\S+)\s*$"
    r".*?"
    r"\*\*Depende de:\*\*\s*(?P<depends>.+?)\s*$",
    re.MULTILINE | re.DOTALL,
)

DEPENDS_LINE_RE = re.compile(r"^\*\*Depende de:\*\*.*$", re.MULTILINE)


class Story:
    def __init__(self, path: Path, m: re.Match, body: str):
        self.path = path
        self.id = m.group("id")
        self.title = m.group("title")
        self.epic = m.group("epic")
        self.prio = m.group("prio")
        self.points = int(m.group("points"))
        self.track = m.group("track").strip().upper()
        raw_depends = m.group("depends").strip()
        self.depends = [] if raw_depends in ("—", "-", "") else [
            d.strip() for d in raw_depends.split(",") if d.strip()
        ]
        self.body = body

    @property
    def issue_title(self) -> str:
        return f"[{self.id}] {self.title}"

    @property
    def labels(self) -> list[str]:
        # points:N queda como label (no solo en el cuerpo markdown) para que
        # una futura migración a Jira pueda leer Story Points vía API
        # (gh issue list --json labels) sin parsear texto libre — el
        # importador CSV de Jira pierde el dato si no llega en su propia
        # columna/campo estructurado.
        out = [f"prio:{self.prio}", f"points:{self.points}"]
        track_label = TRACK_LABELS.get(self.track)
        if track_label:
            out.append(track_label)
        epic_label = EPIC_LABELS.get(self.epic)
        if epic_label:
            out.append(epic_label)
        return out


def parse_story(path: Path) -> Story | None:
    text = path.read_text(encoding="utf-8")
    m = HEADER_RE.search(text)
    if not m:
        print(f"AVISO: no se pudo parsear el encabezado de {path.name}, se omite.", file=sys.stderr)
        return None
    # cuerpo = todo el archivo menos la primera línea (el "# ID — Título" ya
    # va en el título del Issue) — conserva el bloque Épica/Prioridad/Puntos/
    # Track/Depende de, que antes se perdía por arrancar en "## Historia de
    # usuario" (esa línea viene DESPUÉS del bloque, no antes).
    _, _, rest = text.partition("\n")
    body = rest.lstrip("\n").rstrip() + "\n"
    return Story(path, m, body)


def load_stories(stories_dir: Path) -> dict[str, Story]:
    stories: dict[str, Story] = {}
    for path in sorted(stories_dir.glob("*.md")):
        if path.stem.upper() in EXCLUDE_STEMS:
            continue
        story = parse_story(path)
        if story:
            stories[story.id] = story
    return stories


def load_map(map_file: Path) -> dict:
    if map_file.exists():
        return json.loads(map_file.read_text(encoding="utf-8"))
    return {}


def save_map(map_file: Path, data: dict) -> None:
    map_file.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def gh_issue_create(repo: str, title: str, body: str, labels: list[str]) -> int:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(body)
        body_file = f.name
    try:
        cmd = [
            "gh", "issue", "create",
            "--repo", repo,
            "--title", title,
            "--body-file", body_file,
        ]
        for label in labels:
            cmd += ["--label", label]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    finally:
        Path(body_file).unlink(missing_ok=True)
    url = result.stdout.strip().splitlines()[-1]
    number = int(url.rstrip("/").rsplit("/", 1)[-1])
    return number


def gh_issue_edit_body(repo: str, number: int, body: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(body)
        body_file = f.name
    try:
        cmd = ["gh", "issue", "edit", str(number), "--repo", repo, "--body-file", body_file]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    finally:
        Path(body_file).unlink(missing_ok=True)


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    default_stories_dir = script_dir.parent.parent / "docs" / "STORIES"

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="owner/repo destino")
    ap.add_argument("--stories-dir", default=str(default_stories_dir), help="carpeta con los .md de historias")
    ap.add_argument("--map-file", default=None, help="archivo JSON de mapeo ID->#issue (por defecto uno por repo, junto a este script)")
    ap.add_argument("--apply", action="store_true", help="ejecuta de verdad (sin esto, solo muestra qué haría)")
    ap.add_argument("--limit", type=int, default=None, help="máximo de historias nuevas a crear en esta corrida")
    ap.add_argument("--only", default=None, help="lista de IDs separados por coma a procesar (ej: CT-1,CT-2)")
    ap.add_argument("--link-only", action="store_true", help="saltea la creación, solo repasa el enlazado de dependencias")
    ap.add_argument("--relink", action="store_true", help="fuerza re-generar el cuerpo/enlazado aunque ya esté marcado como enlazado")
    ap.add_argument("--sync-bodies", action="store_true", help="reescribe el cuerpo de TODOS los issues mapeados (tengan o no dependencias) desde el .md fuente — usar tras editar una historia, o para reparar issues creados con una versión vieja del script")
    args = ap.parse_args()

    stories_dir = Path(args.stories_dir)
    if not stories_dir.is_dir():
        print(f"No existe la carpeta de historias: {stories_dir}", file=sys.stderr)
        return 1

    map_file = Path(args.map_file) if args.map_file else script_dir / f".issue-map.{args.repo.replace('/', '__')}.json"

    stories = load_stories(stories_dir)
    if not stories:
        print("No se encontró ninguna historia parseable.", file=sys.stderr)
        return 1

    issue_map = load_map(map_file)  # id -> {"number": int, "linked": bool}

    only_ids = set(s.strip() for s in args.only.split(",")) if args.only else None

    candidates = [
        s for s in stories.values()
        if s.id not in issue_map and (only_ids is None or s.id in only_ids)
    ]
    if args.limit is not None:
        candidates = candidates[: args.limit]

    print(f"Historias totales encontradas: {len(stories)}")
    print(f"Ya creadas (en {map_file.name}): {len(issue_map)}")
    print(f"Candidatas a crear en esta corrida: {len(candidates)}")
    print()

    # --- Fase 1: creación ---
    if not args.link_only:
        for story in candidates:
            print(f"[{story.id}] {story.title}  labels={story.labels}")
            if args.apply:
                number = gh_issue_create(args.repo, story.issue_title, story.body, story.labels)
                issue_map[story.id] = {"number": number, "linked": False}
                save_map(map_file, issue_map)
                print(f"   → creada #{number}")
        print()

    # --- Fase 2: enlazado de dependencias / sincronización de cuerpo ---
    linked_now = 0
    pending = 0
    for story in stories.values():
        if story.id not in issue_map:
            continue
        entry = issue_map[story.id]

        if story.depends:
            missing = [d for d in story.depends if d not in issue_map]
            if missing:
                pending += 1
                print(f"[{story.id}] pendiente de enlazar — todavía falta crear: {', '.join(missing)}")
                continue
            resolved = ", ".join(f"{d} (#{issue_map[d]['number']})" for d in story.depends)
            new_body = DEPENDS_LINE_RE.sub(f"**Depende de:** {resolved}", story.body, count=1)
            already_done = entry.get("linked") and not args.relink and not args.sync_bodies
            action_desc = f"Depende de: {resolved}"
            verb = "enlazando"
        else:
            new_body = story.body
            already_done = entry.get("body_synced") and not args.sync_bodies
            action_desc = "(sin dependencias — solo sincroniza cuerpo)"
            verb = "sincronizando"

        if already_done:
            continue

        print(f"[{story.id}] {verb} #{entry['number']} → {action_desc}")
        if args.apply:
            gh_issue_edit_body(args.repo, entry["number"], new_body)
            entry["linked"] = True
            entry["body_synced"] = True
            save_map(map_file, issue_map)
        linked_now += 1

    print()
    if not args.apply:
        print("Esto fue un dry-run — no se creó ni editó nada en GitHub. Agregá --apply para ejecutar de verdad.")
    print(f"Enlazadas en esta corrida: {linked_now}. Pendientes de dependencias (correr de nuevo después): {pending}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
