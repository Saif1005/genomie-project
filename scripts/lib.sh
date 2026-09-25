# shellcheck shell=bash
# Fonctions communes aux scripts ZAYNB (à sourcer, pas à exécuter).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/Backend/.env"
ENV_EXAMPLE="$ROOT_DIR/Backend/.env.local.example"

if [ -t 1 ]; then
  C_OK=$'\e[32m'; C_KO=$'\e[31m'; C_WARN=$'\e[33m'; C_DIM=$'\e[2m'; C_RST=$'\e[0m'
else
  C_OK=""; C_KO=""; C_WARN=""; C_DIM=""; C_RST=""
fi

info() { echo "${C_DIM}==>${C_RST} $*"; }
warn() { echo "${C_WARN}[WARN]${C_RST} $*" >&2; }
die()  { echo "${C_KO}[ERREUR]${C_RST} $*" >&2; exit 1; }

# Charge Backend/.env (ou l'exemple si absent) dans l'environnement du script.
load_env() {
  local f="$ENV_FILE"
  [ -f "$f" ] || f="$ENV_EXAMPLE"
  if [ -f "$f" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$f"
    set +a
  fi
  LOCAL_DATA_ROOT="${LOCAL_DATA_ROOT:-/data/zaynb}"
  BIND_ADDRESS="${BIND_ADDRESS:-127.0.0.1}"
  FRONTEND_PORT="${FRONTEND_PORT:-3000}"
  BACKEND_PORT="${BACKEND_PORT:-8000}"
  # Adresse à utiliser pour joindre les services depuis le serveur lui-même
  if [ "$BIND_ADDRESS" = "0.0.0.0" ]; then ACCESS_HOST="127.0.0.1"; else ACCESS_HOST="$BIND_ADDRESS"; fi
  FRONTEND_URL="http://${ACCESS_HOST}:${FRONTEND_PORT}"
  BACKEND_URL="http://${ACCESS_HOST}:${BACKEND_PORT}"
}

ensure_env_file() {
  if [ ! -f "$ENV_FILE" ]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    info "Backend/.env créé depuis .env.local.example — relisez-le avant la mise en production."
  fi
}

# GPU NVIDIA utilisable par Docker ? (driver + NVIDIA Container Toolkit)
# Forçable avec ZAYNB_GPU=1 ou ZAYNB_GPU=0.
gpu_docker_ok() {
  case "${ZAYNB_GPU:-auto}" in
    1|true|yes) return 0 ;;
    0|false|no) return 1 ;;
  esac
  command -v nvidia-smi >/dev/null 2>&1 || return 1
  nvidia-smi -L >/dev/null 2>&1 || return 1
  docker info 2>/dev/null | grep -qi 'nvidia' || return 1
}

compose() {
  local files=(-f "$ROOT_DIR/docker-compose.local.yml")
  if gpu_docker_ok; then
    files+=(-f "$ROOT_DIR/docker-compose.gpu.yml")
  fi
  docker compose --project-directory "$ROOT_DIR" "${files[@]}" --env-file "$ENV_FILE" "$@"
}

# Demande confirmation (sautée avec --yes / ZAYNB_YES=1)
confirm() {
  [ "${ZAYNB_YES:-0}" = "1" ] && return 0
  local answer
  read -r -p "$1 [o/N] " answer
  [[ "$answer" =~ ^[oOyY]$ ]]
}
