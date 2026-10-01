#!/usr/bin/env bash
# Apaga el stack de la demo sin perder las conversaciones: primero las exporta a txt.
#
# Uso:
#   bash scripts/demo/down.sh            # exporta, apaga y borra contenedores y volúmenes (la base)
#   bash scripts/demo/down.sh --purge    # además borra las imágenes (el próximo up las reconstruye)
#
# Las transcripciones quedan en tmp/demo/transcripciones/ (no se versiona). Con la base abajo no hay
# nada que exportar y se apaga igual.

set -euo pipefail

cd "$(dirname "$0")/../.."

PURGE=false
case "${1:-}" in
  "") ;;
  --purge) PURGE=true ;;
  *) echo "Uso: bash scripts/demo/down.sh [--purge]" >&2; exit 2 ;;
esac

python_cmd() {  # el primer intérprete que de verdad corre (en Windows, python3 puede ser un atajo vacío)
  local candidate
  for candidate in python python3; do
    if "$candidate" -c "import sys" >/dev/null 2>&1; then
      echo "$candidate"
      return
    fi
  done
  echo "ERROR: no hay un intérprete de Python para exportar las transcripciones" >&2
  exit 1
}

if docker compose ps --status running --services 2>/dev/null | grep -qx postgres; then
  echo "==> Exportando las conversaciones de las últimas 24 h"
  PYTHONIOENCODING=utf-8 "$(python_cmd)" scripts/demo/exportar.py 1440
else
  echo "==> Postgres no está corriendo: no hay conversaciones que exportar"
fi

echo "==> Apagando el stack y borrando los volúmenes"
if $PURGE; then
  docker compose --profile '*' down -v --remove-orphans --rmi all
else
  docker compose --profile '*' down -v --remove-orphans
fi
echo "Listo. Transcripciones en tmp/demo/transcripciones/"
