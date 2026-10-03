"""Z-4: toda la documentación se alcanza desde el README raíz y ningún enlace relativo está roto."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# `tmp/` no se versiona y guarda salidas generadas, como los informes de la corrida real (OBS-6).
SKIP_DIRS = {".git", ".venv", "node_modules", ".pytest_cache", ".ruff_cache", "tmp"}
# Plantillas que GitHub muestra por su cuenta: no son documentación que alguien tenga que encontrar.
NOT_DOCS = {ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"}
LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")


def markdown_files():
    return [p for p in ROOT.rglob("*.md") if not SKIP_DIRS & set(p.relative_to(ROOT).parts)]


def relative_links(path):
    for target in LINK.findall(path.read_text(encoding="utf-8")):
        if not target.startswith(("http://", "https://", "mailto:")):
            yield target, (path.parent / target).resolve()


def test_no_relative_link_is_broken():
    """Un enlace que sale del repositorio cuenta como roto aunque exista en la máquina de quien lo corre:
    en CI y en GitHub no existe."""
    broken = [f"{p.relative_to(ROOT)} -> {target}"
              for p in markdown_files() for target, resolved in relative_links(p)
              if not resolved.is_relative_to(ROOT) or not resolved.exists()]
    assert broken == []


def test_every_document_is_reachable_from_the_root_readme():
    seen, pending = set(), [(ROOT / "README.md").resolve()]
    while pending:
        doc = pending.pop()
        if doc in seen:
            continue
        seen.add(doc)
        pending += [r for _, r in relative_links(doc) if r.suffix == ".md" and r.exists()]
    orphans = [str(p.relative_to(ROOT)) for p in markdown_files()
               if p.resolve() not in seen and p not in NOT_DOCS]
    assert orphans == [], "enlazarlos desde docs/README.md (o desde un documento ya enlazado)"


def test_the_docs_index_is_linked_from_the_first_section_of_the_root_readme():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    first_section = text.split("\n## ")[1]
    assert "](docs/README.md)" in first_section


# decisions.md solo se amplía, no se reescribe; CONTRIBUTING.md enuncia la regla y cita los ejemplos.
PAST_PROJECT_EXEMPT = {ROOT / "docs" / "DECISIONS" / "decisions.md", ROOT / "CONTRIBUTING.md"}
# "v1"/"v2" sueltos, no los de "/v1/traces", "stateDiagram-v2" o un ID de modelo "...-v1:0".
PAST_PROJECT = re.compile(r"(?<![\w/-])v[12]\b(?![/:])|retrospectiva|FINAL-PROJECT|proyecto anterior", re.IGNORECASE)


def test_documentation_does_not_reference_previous_projects():
    """CONTRIBUTING.md: cada documento se justifica por sí mismo, sin compararse con un proyecto previo."""
    hits = [f"{p.relative_to(ROOT)}:{n}: {line.strip()[:80]}"
            for p in markdown_files() if p not in PAST_PROJECT_EXEMPT
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if PAST_PROJECT.search(line)]
    assert hits == []
