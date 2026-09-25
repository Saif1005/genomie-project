#!/usr/bin/env bash
# Arrête ZAYNB. Les données (LOCAL_DATA_ROOT) et les modèles sont conservés.
# Usage : bash scripts/stop.sh
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

ensure_env_file
load_env
compose down
# Conteneurs de calcul lancés par le backend (au cas où un job a été interrompu)
docker ps -aq --filter name=parabricks- | xargs -r docker rm -f >/dev/null
info "ZAYNB arrêté (données conservées dans $LOCAL_DATA_ROOT)."
