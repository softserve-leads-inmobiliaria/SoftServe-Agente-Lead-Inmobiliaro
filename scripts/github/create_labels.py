#!/usr/bin/env python3
"""Crea (o actualiza) labels de GitHub en un repo a partir de un CSV.

Reemplaza a create_labels.sh: en Windows/Git-Bash, invocar `gh` con texto no
ASCII (tildes, em-dash) directo en la línea de comandos de Bash corrompe el
dato en GitHub (confirmado: los primeros 13 labels de este proyecto se
crearon mal por eso). Pasar los mismos argumentos vía Python subprocess con
una lista (no una cadena de shell) evita el problema — por eso este script
existe en vez del .sh.

Uso:
    python create_labels.py <owner/repo> [ruta-al-csv]

Formato del CSV: name|color|description  (líneas que empiezan con # se ignoran)
Por defecto usa labels.csv (mismo directorio que este script) — reutilizable
en otro proyecto pasando un CSV distinto.

Requiere: gh CLI autenticado (gh auth status) con permiso sobre el repo.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def main() -> int:
    if len(sys.argv) < 2:
        print("Uso: create_labels.py <owner/repo> [labels.csv]", file=sys.stderr)
        return 1
    repo = sys.argv[1]
    script_dir = Path(__file__).resolve().parent
    csv_path = Path(sys.argv[2]) if len(sys.argv) > 2 else script_dir / "labels.csv"

    if not csv_path.is_file():
        print(f"No existe el archivo de labels: {csv_path}", file=sys.stderr)
        return 1

    print(f"Repo destino: {repo}")
    print(f"Archivo de labels: {csv_path}\n")

    count = 0
    for line in csv_path.read_text(encoding="utf-8").splitlines():
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        name, color, description = line.split("|", 2)
        print(f"→ {name}")
        subprocess.run(
            ["gh", "label", "create", name, "--repo", repo,
             "--color", color, "--description", description, "--force"],
            check=True,
        )
        count += 1

    print(f"\nListo — {count} labels creados/actualizados en {repo}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
