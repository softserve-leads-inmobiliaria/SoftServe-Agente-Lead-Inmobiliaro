#!/usr/bin/env bash
# Levanta el stack para una demo en vivo (Telegram + el LLM del .env) y deja a mano lo que hay que abrir.
#
# Uso (desde la raíz del repo; en Windows, desde Git Bash o PowerShell):
#   bash scripts/demo/up.sh
#
# Qué hace:
#   1. Arranca el daemon de Docker si no responde (`docker desktop start`).
#   2. Levanta core, panel y phoenix sin el arnés: sus guiones esperan el agente fake y con Bedrock
#      fallan y gastan cupo. Si el .env no trae TELEGRAM_BOT_USERNAME, antes se lo pide a Telegram con el
#      token, para que la landing pública (B-16) muestre los botones del chat.
#   3. Corre el seed (asesores, propiedades y promociones), que `up` de esos servicios no ejecuta, y
#      siembra la historia sintética de seis meses (N-9) para la vista de negocio. La cola no la ve.
#   4. Comprueba core, panel, catálogo y bot sin crear leads, para que la cola del panel quede limpia.
#   5. Imprime cómo entrar al panel (B-17): el usuario de cada asesor y el del gerente, con sus contraseñas
#      de demo; además el enlace opcional de Telegram de cada asesor y el del anuncio para empezar.
#
# El puerto del panel sale de PANEL_PORT en el .env. Para apagar: bash scripts/demo/down.sh.

set -euo pipefail

cd "$(dirname "$0")/../.."

env_value() {  # valor de una clave del .env (sin \r de Windows), o el default
  local value=""
  [[ -f .env ]] && value=$(grep -E "^$1=" .env | tail -n1 | cut -d= -f2- | tr -d '\r')
  echo "${value:-$2}"
}

wait_for() {  # wait_for <descripción> <segundos> <comando...>
  local what=$1 limit=$2 waited=0
  shift 2
  until "$@" >/dev/null 2>&1; do
    if (( waited >= limit )); then
      echo "ERROR: $what no respondió en ${limit}s" >&2
      exit 1
    fi
    sleep 3
    waited=$((waited + 3))
  done
}

psql_value() {
  docker compose exec -T postgres psql -U "$(env_value POSTGRES_USER leads)" \
    -d "$(env_value POSTGRES_DB leads)" -Atc "$1"
}

PANEL_PORT=$(env_value PANEL_PORT 8080)
CORE_PORT=$(env_value CORE_PORT 8000)
PHOENIX_PORT=$(env_value PHOENIX_UI_PORT 6006)
LLM_PROVIDER=$(env_value LLM_PROVIDER fake)
BOT_TOKEN=$(env_value TELEGRAM_BOT_TOKEN "")
ADVISOR_PASSWORD=$(env_value PANEL_ADVISOR_PASSWORD asesor-demo)
MANAGER_PASSWORD=$(env_value PANEL_MANAGER_PASSWORD gerente-demo)
BOT_USERNAME=$(env_value TELEGRAM_BOT_USERNAME "")

bot_username() {  # el @usuario del bot según Telegram (getMe), o vacío si no responde
  curl -s --max-time 10 "https://api.telegram.org/bot$BOT_TOKEN/getMe" | grep -o '"username":"[^"]*"' | cut -d'"' -f4 || true
}

echo "==> Docker"
if ! docker info >/dev/null 2>&1; then
  echo "    El daemon no responde: docker desktop start"
  docker desktop start --timeout 180 >/dev/null
  wait_for "el daemon de Docker" 180 docker info
fi

# La landing (B-16) arma los botones de Telegram con el usuario del bot. Si el .env no lo trae, se le pregunta
# a Telegram; exportado, pisa al .env en `config/panel.env`, y el .env no se toca.
if [[ -z "$BOT_USERNAME" && -n "$BOT_TOKEN" ]]; then
  BOT_USERNAME=$(bot_username)
  [[ -n "$BOT_USERNAME" ]] && export TELEGRAM_BOT_USERNAME="$BOT_USERNAME"
fi

echo "==> Stack (core, panel, phoenix y sus dependencias; sin el arnés)"
if ! docker compose --progress quiet up -d --build --quiet-pull core panel phoenix; then
  echo "ERROR: no se pudo levantar el stack. Si el error es 'ports are not available', otro programa" >&2
  echo "       ocupa ese puerto: cambiarlo en el .env (PANEL_PORT, CORE_PORT, POSTGRES_PORT o" >&2
  echo "       PHOENIX_UI_PORT) y volver a correr el script." >&2
  exit 1
fi

echo "==> Seed: asesores e inventario de demo"
docker compose --progress quiet run --rm --build seed >/dev/null
echo "==> Historia sintética de seis meses para la vista de negocio (N-9)"
docker compose --progress quiet run --rm seed python scripts/seed_history.py >/dev/null

echo "==> Comprobaciones"
wait_for "core (http://localhost:$CORE_PORT/health)" 120 curl -sf "http://localhost:$CORE_PORT/health"
wait_for "el panel (http://localhost:$PANEL_PORT/health)" 120 curl -sf "http://localhost:$PANEL_PORT/health"
properties=$(psql_value "select count(*) from properties")
advisors=$(psql_value "select count(*) from advisors")
echo "    core y panel responden; catálogo con $properties propiedades y $advisors asesores"
if [[ "$properties" == "0" ]]; then
  echo "ERROR: el catálogo quedó vacío; revisar 'docker compose run --rm seed'" >&2
  exit 1
fi

bot=""
if [[ -n "$BOT_TOKEN" ]]; then
  wait_for "el bot de Telegram" 60 sh -c "docker compose logs core | grep -q 'bot de Telegram escuchando'"
  bot=$(bot_username)
  echo "    bot @$bot escuchando por long polling"
else
  echo "    AVISO: sin TELEGRAM_BOT_TOKEN en el .env: no hay bot, solo POST /events"
fi
if [[ -z "$BOT_USERNAME" ]]; then
  echo "    AVISO: sin TELEGRAM_BOT_USERNAME: la landing pública se ve sin los botones de Telegram"
fi
if [[ "$LLM_PROVIDER" != "bedrock" ]]; then
  echo "    AVISO: LLM_PROVIDER=$LLM_PROVIDER: las respuestas salen de plantillas, no de un LLM"
fi

echo
echo "================================================================================"
echo " Listo para la demo (LLM: $LLM_PROVIDER)"
echo "================================================================================"
echo
echo "Asesores: trabajan desde el panel; los avisos de cada lead quedan en su sección Avisos (DT-42)."
[[ -n "$bot" ]] && echo "Telegram es opcional: el enlace solo agrega una copia de cada aviso al chat de quien lo abra."
echo
echo "Panel: http://localhost:$PANEL_PORT/login   (para cambiar de rol: Log out y entrar con otro usuario)"
while IFS='|' read -r id name zone token username; do
  echo "  $id  $name ($zone)"
  echo "      usuario:  $username / $ADVISOR_PASSWORD"
  [[ -n "$bot" ]] && echo "      telegram: https://t.me/$bot?start=adv_$token   (opcional)"
done < <(psql_value "select a.id, a.name, a.zone, a.access_token, u.username from advisors a
                     join panel_users u on u.advisor_id = a.id order by a.id")
echo "  gerente: usuario $(psql_value "select username from panel_users where role = 'gerente' order by id limit 1") / $MANAGER_PASSWORD   (negocio y funnel)"
echo
if [[ -n "$bot" ]]; then
  echo "Lead, entrando por el anuncio: https://t.me/$bot?start=prop_00231"
fi
echo "Landing pública:               http://localhost:$PANEL_PORT/properties"
echo "Negocio, con la historia demo: http://localhost:$PANEL_PORT/negocio?demo=1   (como gerente)"
echo "Trazas (Phoenix):              http://localhost:$PHOENIX_PORT"
echo "Ensayo automático:             python scripts/demo/conversar.py"
echo "Al terminar:                   bash scripts/demo/down.sh"
