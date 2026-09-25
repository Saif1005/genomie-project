#!/usr/bin/env bash
# Affiche les logs en continu.
# Usage : bash scripts/logs.sh            (tous les services)
#         bash scripts/logs.sh backend    (un service : backend | frontend | ollama)
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

ensure_env_file
load_env
compose logs -f --tail=200 "$@"
