#!/usr/bin/env bash
# Activa protección de rama sobre `main`: exige Pull Request + 1 aprobación,
# prohíbe force-push y borrado de la rama. No exige un status check de CI
# todavía porque .github/workflows/ci.yml no existe en este repo aún — una
# vez que exista, agregar su nombre de "job" a required_status_checks acá.
#
# Uso:
#   ./protect_main.sh <owner/repo> [rama]   # rama por defecto: main
#
# Requiere: gh CLI autenticado con permiso admin sobre el repo.

set -euo pipefail

REPO="${1:?Uso: protect_main.sh <owner/repo> [rama]}"
BRANCH="${2:-main}"

echo "Repo: $REPO   Rama: $BRANCH"
echo

gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  "repos/$REPO/branches/$BRANCH/protection" \
  --input - <<'JSON'
{
  "required_status_checks": null,
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON

echo
echo "Listo. '$BRANCH' ahora exige PR + 1 aprobación, sin force-push ni borrado."
echo "enforce_admins=true: ni siquiera el/la admin del repo puede saltarse esto sin"
echo "desactivarlo primero en Settings → Branches — es deliberado (ver CONTRIBUTING.md)."
