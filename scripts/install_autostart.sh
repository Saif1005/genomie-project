#!/usr/bin/env bash
# Installs GermlineIQ as a systemd *user* service (native deployment): Ollama + backend + frontend
# start automatically with the user's systemd session and restart cleanly with systemctl.
#
# Usage: bash scripts/install_autostart.sh [--uninstall]
# Afterwards:
#   systemctl --user status germlineiq      # state
#   systemctl --user restart germlineiq     # restart everything
#   journalctl --user -u germlineiq         # start/stop log (service logs: $LOCAL_DATA_ROOT/tmp/*.log)
#
# Start without an open terminal (at WSL / server boot): `sudo loginctl enable-linger $USER` (once).
# On WSL2, WSL itself must be running: see the Windows logon task printed at the end.
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/germlineiq.service"

command -v systemctl >/dev/null || die "systemd not available (WSL2: set [boot] systemd=true in /etc/wsl.conf, then wsl --shutdown)"
systemctl --user show-environment >/dev/null 2>&1 || die "no systemd user session (systemctl --user)"

if [ "${1:-}" = "--uninstall" ]; then
  systemctl --user disable --now germlineiq 2>/dev/null || true
  rm -f "$UNIT"
  systemctl --user daemon-reload
  info "germlineiq.service removed (data kept)."
  exit 0
fi

mkdir -p "$UNIT_DIR"
cat >"$UNIT" <<EOF
[Unit]
Description=GermlineIQ — Ollama, backend (FastAPI) and frontend (Next.js)
After=default.target

[Service]
# start_native.sh starts the three services in the background, waits for /health, then exits
Type=forking
GuessMainPID=no
RemainAfterExit=yes
WorkingDirectory=$ROOT_DIR
Environment=HOME=$HOME
# systemd's minimal PATH lacks the WSL2 GPU tools (nvidia-smi in /usr/lib/wsl/lib)
Environment=PATH=/usr/lib/wsl/lib:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/bin/bash $ROOT_DIR/scripts/start_native.sh
ExecStop=/bin/bash $ROOT_DIR/scripts/start_native.sh --stop
TimeoutStartSec=300
TimeoutStopSec=60

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable germlineiq.service >/dev/null
info "Installed and enabled: $UNIT"

linger=$(loginctl show-user "$USER" --property=Linger --value 2>/dev/null || echo no)
echo
echo "Next steps:"
echo "  systemctl --user start germlineiq          # start now (or restart: systemctl --user restart germlineiq)"
if [ "$linger" != "yes" ]; then
  echo "  sudo loginctl enable-linger $USER          # once: start at boot, without an open terminal"
fi
if grep -qi microsoft /proc/version 2>/dev/null; then
  echo
  echo "WSL2 — keep WSL running from Windows logon (PowerShell, once):"
  echo "  schtasks /create /tn GermlineIQ-WSL /sc onlogon /rl limited /tr \"wsl.exe -d ${WSL_DISTRO_NAME:-Ubuntu} -u $USER -- sleep infinity\""
  echo "  (Docker Desktop must also start with Windows: Settings → General → Start Docker Desktop when you sign in)"
fi
