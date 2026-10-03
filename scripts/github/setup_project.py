#!/usr/bin/env python3
"""Termina de configurar la gestión del backlog en GitHub: milestones por
épica, y — en el GitHub Project — los campos Priority/Track/Estimate/Lote
seteados automáticamente en cada una de las 54 Issues, a partir de sus
labels (creados por create_issues_from_stories.py) y del mapeo de lotes de
docs/STORIES/ROADMAP.md §4.

Requiere haber corrido antes create_issues_from_stories.py --apply (usa el
mismo archivo de mapeo ID->#issue).

Lo que SÍ hace este script (todo vía `gh`, API pública de Projects v2):
  - Crea (si no existen) 6 milestones, uno por épica E0..E5, y asigna cada
    Issue a su milestone.
  - Crea (si no existe) el Project y lo linkea al repo.
  - Crea (si no existen) los campos custom: Priority, Track, Estimate, Lote.
  - Agrega las 54 Issues al Project (si no están ya) y setea sus 4 campos.

Lo que NO hace (no es posible vía API pública, hay que hacerlo a mano una
vez en la UI — instrucciones al final de la corrida):
  - Crear las Vistas (Board agrupado por Status, Table agrupada por Track).
  - Los workflows automáticos del Project (Item added → Status: Todo, etc.)

Uso:
    python setup_project.py --repo owner/repo --project-owner org_or_user
    python setup_project.py --repo owner/repo --project-owner org_or_user --apply
    python setup_project.py --repo owner/repo --project-owner org_or_user --apply --only CT-1,CT-2
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import create_issues_from_stories as cis  # noqa: E402  (reusa el parser de historias)

EPIC_MILESTONE = {
    "E0": ("E0 — Contratos y entorno",
           "Congelar la forma de datos entre componentes y levantar un esqueleto end-to-end 100% fake."),
    "E1": ("E1 — Motor del agente",
           "FSM del agente, orquestador, scoring BANT-E, guardrails de entrada/salida."),
    "E2": ("E2 — Canal y borde",
           "Bot de Telegram, conversión al evento canónico, identidad del lead, panel de asesores."),
    "E3": ("E3 — Datos y conocimiento",
           "Catalog Service real, esquema de datos, reglas BANT-E, FAQ, gold sets de guardrails."),
    "E4": ("E4 — Observabilidad y calidad",
           "Trazas, métricas del funnel, cobertura de FSM, gate de CI, contract tests, E2E, logs."),
    "E5": ("E5 — Cierre y demo",
           "Hardening final, demo en vivo, documento de arquitectura, documentación publicada."),
    "E6": ("E6 — Inteligencia comercial",
           "Historial de inventario, operaciones cerradas, atribución al bot y demanda insatisfecha."),
}

TRACK_DISPLAY = {"A": "Core", "B": "Borde", "C": "Datos", "CROSS": "Cross"}

FIELD_SPECS = [
    ("Priority", "SINGLE_SELECT", ["P0", "P1", "P2"]),
    ("Track", "SINGLE_SELECT", ["Core", "Borde", "Datos", "Cross"]),
    ("Estimate", "NUMBER", None),
    ("Lote", "SINGLE_SELECT", ["Lote 1", "Lote 2", "Lote 3", "Lote 4", "Lote 5", "Lote 6",
                                  "Lote 7", "Lote 8", "Lote 9", "Z-cierre", "Backlog"]),
]

# Mapeo de docs/STORIES/ROADMAP.md §4 (batches que respetan dependencias) +
# §6 (cierre). Todo lo que no aparece acá es "Backlog" (§5: resto de P0/P1/P2,
# sin semana fija todavía).
LOTE_MAP = {
    "CT-1": "Lote 1", "CT-2": "Lote 1", "CT-3": "Lote 1", "CT-5": "Lote 1", "CT-6": "Lote 1", "C-4": "Lote 1",
    "A-9": "Lote 2", "CT-7": "Lote 2", "CT-9": "Lote 2", "CT-8": "Lote 2", "C-1": "Lote 2", "C-3": "Lote 2", "CT-4": "Lote 2",
    "A-1": "Lote 3", "B-1": "Lote 3", "A-3": "Lote 3", "C-1a": "Lote 3",
    "A-4": "Lote 4", "B-5": "Lote 4", "C-1b": "Lote 4", "C-7": "Lote 4", "C-9": "Lote 4",
    "A-5": "Lote 5", "B-7": "Lote 5",
    "A-6": "Lote 6", "A-12": "Lote 6",
    # ROADMAP.md §8: propuestas de octubre (E6 y lo que sale de docs/DESIGN/propuestas.md).
    "N-1": "Lote 7", "N-2": "Lote 7", "A-15": "Lote 7",
    "N-3": "Lote 8", "N-4": "Lote 8", "N-5": "Lote 8", "N-6": "Lote 8", "N-7": "Lote 8", "N-9": "Lote 8",
    "B-10": "Lote 8",
    "N-8": "Lote 9", "B-11": "Lote 9",
    "Z-1": "Z-cierre", "Z-2": "Z-cierre", "Z-3": "Z-cierre", "Z-4": "Z-cierre",
}


PACE_SECONDS = 2.0  # pausa entre escrituras — GitHub corta por rate limit secundario con ráfagas
RETRY_WAITS = [30, 60, 120, 180, 240]  # backoff creciente si aun así se activa


def run_gh(args: list[str], input_json: dict | None = None) -> str:
    cmd = ["gh"] + args
    for attempt, wait in enumerate([0] + RETRY_WAITS):
        if wait:
            print(f"   (rate limit de GitHub, esperando {wait}s antes de reintentar...)", file=sys.stderr)
            time.sleep(wait)
        if input_json is not None:
            result = subprocess.run(cmd, input=json.dumps(input_json), capture_output=True, text=True, encoding="utf-8")
        else:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if result.returncode == 0:
            return result.stdout
        rate_limited = "rate limit" in result.stderr.lower()
        if not rate_limited:
            raise RuntimeError(f"gh {' '.join(args)} falló:\n{result.stderr}")
    raise RuntimeError(f"gh {' '.join(args)} agotó los reintentos por rate limit")


def gh_json(args: list[str]):
    return json.loads(run_gh(args + ["--format", "json"]))


# ---------------------------------------------------------------- milestones

def ensure_milestones(repo: str, apply: bool) -> dict[str, str]:
    """Devuelve {epic_code: milestone_title}, creando los que falten."""
    # state=all: sin esto la API solo lista los abiertos, y uno cerrado (E0) se intentaría crear de nuevo.
    existing = json.loads(run_gh(["api", f"repos/{repo}/milestones?state=all", "--paginate"]))
    existing_titles = {m["title"] for m in existing}

    result = {}
    for code, (title, desc) in EPIC_MILESTONE.items():
        result[code] = title
        if title in existing_titles:
            continue
        print(f"[milestone] crear '{title}'")
        if apply:
            run_gh(["api", "--method", "POST", f"repos/{repo}/milestones",
                    "-f", f"title={title}", "-f", f"description={desc}"])
    return result


def assign_milestones(repo: str, stories: dict, apply: bool, only_ids=None) -> None:
    for story in stories.values():
        if only_ids is not None and story.id not in only_ids:
            continue
        title, _ = EPIC_MILESTONE[story.epic]
        print(f"[milestone] {story.id} → {title}")
        if apply:
            run_gh(["issue", "edit", str(story.gh_number), "--repo", repo, "--milestone", title])
            time.sleep(PACE_SECONDS)


# -------------------------------------------------------------------- project

def ensure_project(owner: str, title: str, apply: bool) -> int | None:
    projects = gh_json(["project", "list", "--owner", owner])["projects"]
    for p in projects:
        if p["title"] == title:
            print(f"[project] ya existe #{p['number']}: {title}")
            return p["number"]
    print(f"[project] crear '{title}'")
    if not apply:
        return None
    created = gh_json(["project", "create", "--owner", owner, "--title", title])
    return created["number"]


FIELD_OPTIONS_QUERY = """
query($id: ID!) {
  node(id: $id) { ... on ProjectV2SingleSelectField { options { id name color description } } }
}
"""

FIELD_OPTIONS_MUTATION = """
mutation($fieldId: ID!, $options: [ProjectV2SingleSelectFieldOptionInput!]) {
  updateProjectV2Field(input: {fieldId: $fieldId, singleSelectOptions: $options}) { clientMutationId }
}
"""


def ensure_options(field_id: str, name: str, wanted: list[str], apply: bool) -> None:
    """Agrega a un campo single-select ya existente las opciones que le falten.

    Reenvía las opciones actuales con su `id`: sin él, GitHub las recrea y los items pierden su valor.
    """
    data = json.loads(run_gh(["api", "graphql", "-f", f"query={FIELD_OPTIONS_QUERY}", "-f", f"id={field_id}"]))
    current = data["data"]["node"]["options"]
    missing = [o for o in wanted if o not in {c["name"] for c in current}]
    if not missing:
        return
    print(f"[field] '{name}': agregar opciones {', '.join(missing)}")
    if not apply:
        return
    options = [{"id": c["id"], "name": c["name"], "color": c["color"], "description": c["description"]}
               for c in current]
    options += [{"name": o, "color": "GRAY", "description": ""} for o in missing]
    run_gh(["api", "graphql", "--input", "-"],
           input_json={"query": FIELD_OPTIONS_MUTATION, "variables": {"fieldId": field_id, "options": options}})


def ensure_fields(number: int, owner: str, apply: bool) -> None:
    fields = gh_json(["project", "field-list", str(number), "--owner", owner])["fields"]
    existing = {f["name"]: f["id"] for f in fields}
    for name, dtype, options in FIELD_SPECS:
        if name in existing:
            if options:
                ensure_options(existing[name], name, options, apply)
            continue
        print(f"[field] crear '{name}' ({dtype})")
        if not apply:
            continue
        args = ["project", "field-create", str(number), "--owner", owner, "--name", name, "--data-type", dtype]
        if options:
            args += ["--single-select-options", ",".join(options)]
        run_gh(args)


def link_project(number: int, owner: str, repo: str, apply: bool) -> None:
    if not apply:
        print(f"[project] linkear a {repo}")
        return
    try:
        run_gh(["project", "link", str(number), "--owner", owner, "--repo", repo])
    except RuntimeError as e:
        if "already linked" not in str(e).lower():
            raise


def existing_items(number: int, owner: str) -> dict[str, str]:
    """url -> item node id, para no tener que volver a buscar el id de items ya agregados."""
    items = gh_json(["project", "item-list", str(number), "--owner", owner, "--limit", "500"])["items"]
    return {i["content"]["url"]: i["id"] for i in items if "content" in i and "url" in i.get("content", {})}


def get_project_node_id(number: int, owner: str) -> str:
    return gh_json(["project", "view", str(number), "--owner", owner])["id"]


def get_field_meta(number: int, owner: str) -> dict:
    fields = gh_json(["project", "field-list", str(number), "--owner", owner])["fields"]
    return {
        f["name"]: {"id": f["id"], "options": {o["name"]: o["id"] for o in f.get("options", [])}}
        for f in fields if f.get("name") in ("Priority", "Track", "Estimate", "Lote")
    }


FIELD_UPDATE_MUTATION = """
mutation($projectId: ID!, $itemId: ID!,
         $priorityField: ID!, $priorityOption: String!,
         $trackField: ID!, $trackOption: String!,
         $estimateField: ID!, $estimateValue: Float!,
         $loteField: ID!, $loteOption: String!) {
  priority: updateProjectV2ItemFieldValue(input: {projectId: $projectId, itemId: $itemId, fieldId: $priorityField, value: {singleSelectOptionId: $priorityOption}}) { projectV2Item { id } }
  track: updateProjectV2ItemFieldValue(input: {projectId: $projectId, itemId: $itemId, fieldId: $trackField, value: {singleSelectOptionId: $trackOption}}) { projectV2Item { id } }
  estimate: updateProjectV2ItemFieldValue(input: {projectId: $projectId, itemId: $itemId, fieldId: $estimateField, value: {number: $estimateValue}}) { projectV2Item { id } }
  lote: updateProjectV2ItemFieldValue(input: {projectId: $projectId, itemId: $itemId, fieldId: $loteField, value: {singleSelectOptionId: $loteOption}}) { projectV2Item { id } }
}
"""


def sync_items(number: int, owner: str, repo: str, stories: dict, apply: bool, only_ids=None) -> None:
    if not apply:
        for story in stories.values():
            if only_ids is not None and story.id not in only_ids:
                continue
            track = TRACK_DISPLAY[story.track]
            lote = LOTE_MAP.get(story.id, "Backlog")
            print(f"[item] {story.id} (#{story.gh_number}) → Priority={story.prio} Track={track} "
                  f"Estimate={story.points} Lote={lote}")
        return

    project_id = get_project_node_id(number, owner)
    field_meta = get_field_meta(number, owner)
    already = existing_items(number, owner)

    for story in stories.values():
        if only_ids is not None and story.id not in only_ids:
            continue
        url = f"https://github.com/{repo}/issues/{story.gh_number}"
        track = TRACK_DISPLAY[story.track]
        lote = LOTE_MAP.get(story.id, "Backlog")
        print(f"[item] {story.id} (#{story.gh_number}) → Priority={story.prio} Track={track} "
              f"Estimate={story.points} Lote={lote}")

        item_id = already.get(url)
        if item_id is None:
            added = gh_json(["project", "item-add", str(number), "--owner", owner, "--url", url])
            item_id = added["id"]
            time.sleep(PACE_SECONDS)

        variables = {
            "projectId": project_id,
            "itemId": item_id,
            "priorityField": field_meta["Priority"]["id"],
            "priorityOption": field_meta["Priority"]["options"][story.prio],
            "trackField": field_meta["Track"]["id"],
            "trackOption": field_meta["Track"]["options"][track],
            "estimateField": field_meta["Estimate"]["id"],
            "estimateValue": float(story.points),
            "loteField": field_meta["Lote"]["id"],
            "loteOption": field_meta["Lote"]["options"][lote],
        }
        run_gh(["api", "graphql", "--input", "-"], input_json={"query": FIELD_UPDATE_MUTATION, "variables": variables})
        time.sleep(PACE_SECONDS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="owner/repo")
    ap.add_argument("--project-owner", required=True, help="dueño del Project: login de la org o usuario")
    ap.add_argument("--project-title", default="Agente de Leads — Backlog")
    ap.add_argument("--stories-dir", default=str(Path(__file__).resolve().parent.parent.parent / "docs" / "STORIES"))
    ap.add_argument("--map-file", default=None, help="mapeo ID->#issue de create_issues_from_stories.py")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default=None, help="IDs separados por coma, ej: CT-1,CT-2")
    ap.add_argument("--skip-milestones", action="store_true", help="saltear la fase de milestones (ya completa) al resumir")
    args = ap.parse_args()

    script_dir = Path(__file__).resolve().parent
    map_file = Path(args.map_file) if args.map_file else script_dir / f".issue-map.{args.repo.replace('/', '__')}.json"
    if not map_file.exists():
        print(f"No existe {map_file} — ejecutar primero create_issues_from_stories.py --apply", file=sys.stderr)
        return 1
    issue_map = json.loads(map_file.read_text(encoding="utf-8"))

    stories = cis.load_stories(Path(args.stories_dir))
    missing = [sid for sid in stories if sid not in issue_map]
    if missing:
        print(f"AVISO: {len(missing)} historias sin Issue creada todavía (se omiten): {', '.join(missing)}", file=sys.stderr)
    for sid, entry in issue_map.items():
        if sid in stories:
            stories[sid].gh_number = entry["number"]
    stories = {sid: s for sid, s in stories.items() if hasattr(s, "gh_number")}

    only_ids = set(x.strip() for x in args.only.split(",")) if args.only else None

    if args.skip_milestones:
        print("=== Milestones === (saltado por --skip-milestones)")
    else:
        print("=== Milestones ===")
        ensure_milestones(args.repo, args.apply)
        assign_milestones(args.repo, stories, args.apply, only_ids)

    print("\n=== Project ===")
    number = ensure_project(args.project_owner, args.project_title, args.apply)
    if number is None:
        print("\n(dry-run sin project existente — no se puede previsualizar fields/items; ejecutar --apply o crear el project primero)")
        return 0
    ensure_fields(number, args.project_owner, args.apply)
    link_project(number, args.project_owner, args.repo, args.apply)

    print("\n=== Items ===")
    sync_items(number, args.project_owner, args.repo, stories, args.apply, only_ids)

    if not args.apply:
        print("\nEsto fue un dry-run — agregá --apply para ejecutar de verdad.")

    print(f"""
=== Falta a mano (no scriptable vía API pública) ===
En https://github.com/orgs/{args.project_owner}/projects/{number or '<N>'}:
  1. Vista "Board" (ya viene por defecto) — agruparla por Status si no lo está.
  2. Vista nueva "Table" (botón + junto a las pestañas de vista) → Layout: Table
     → agrupar por "Track" (Group by → Track) para ver el trabajo de cada persona.
  3. (Opcional) Settings del Project → Workflows → activar "Item added to project"
     → Set Status: Todo, e "Item closed" → Set Status: Done.
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
