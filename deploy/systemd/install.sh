#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/token-logger}"
SRC_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
DATA_DIR="/var/lib/token-logger"

sudo useradd --system --home-dir "$DATA_DIR" --no-create-home --shell /usr/sbin/nologin token-logger 2>/dev/null || true
sudo mkdir -p "$APP_DIR" "$DATA_DIR"
sudo cp -r "$SRC_DIR/pyproject.toml" "$SRC_DIR/config.example.toml" "$SRC_DIR/src" "$APP_DIR/"
sudo cp "$SRC_DIR/config.example.toml" "$APP_DIR/config.toml"
python3 -m venv "$APP_DIR/.venv"
sudo "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo "$APP_DIR/.venv/bin/pip" install "$APP_DIR"
sudo chown -R token-logger:token-logger "$APP_DIR" "$DATA_DIR"
sudo cp "$SRC_DIR/deploy/systemd/token-logger.service" /etc/systemd/system/token-logger.service
sudo systemctl daemon-reload
sudo systemctl enable --now token-logger
echo "CA cert: $DATA_DIR/mitmproxy/ca.pem (trust it system-wide, see README)"
systemctl status token-logger --no-pager || true
