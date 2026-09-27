#!/usr/bin/env bash
# Follows the logs.
# Usage: bash scripts/logs.sh            (all services)
#        bash scripts/logs.sh backend    (one service: backend | frontend | ollama)
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

ensure_env_file
load_env
compose logs -f --tail=200 "$@"
