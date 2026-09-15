#!/usr/bin/env bash
# Crea (o actualiza) labels de GitHub en un repo a partir de un CSV.
#
# Uso:
#   ./create_labels.sh <owner/repo> [ruta-al-csv]
#
# Por defecto usa labels.csv (mismo directorio que este script).
# Reutilizable en otro proyecto: pasá un CSV distinto como segundo argumento,
# o editá labels.csv si es una variación del mismo proyecto.
#
# Formato del CSV: name|color|description  (líneas que empiezan con # se ignoran)
#
# Requiere: gh CLI autenticado (gh auth status) con permiso sobre el repo.

set -euo pipefail

REPO="${1:?Uso: create_labels.sh <owner/repo> [labels.csv]}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CSV="${2:-$SCRIPT_DIR/labels.csv}"

if [ ! -f "$CSV" ]; then
  echo "No existe el archivo de labels: $CSV" >&2
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI no está instalado o no está en PATH." >&2
  exit 1
fi

echo "Repo destino: $REPO"
echo "Archivo de labels: $CSV"
echo

count=0
while IFS='|' read -r name color description; do
  # saltar líneas vacías o comentarios
  case "$name" in
    ""|\#*) continue ;;
  esac
  echo "→ $name"
  gh label create "$name" --repo "$REPO" --color "$color" --description "$description" --force
  count=$((count + 1))
done < "$CSV"

echo
echo "Listo — $count labels creados/actualizados en $REPO."
