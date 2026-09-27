#!/usr/bin/env bash
# Stops GermlineIQ. Data (LOCAL_DATA_ROOT) and models are kept.
# Usage : bash scripts/stop.sh
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

ensure_env_file
load_env
compose down
# Compute containers started by the backend (in case a job was interrupted)
docker ps -aq --filter name=parabricks- | xargs -r docker rm -f >/dev/null
info "GermlineIQ stopped (data kept in $LOCAL_DATA_ROOT)."
