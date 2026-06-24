#!/usr/bin/env bash
# Install co2-logger on pi4-s-1.
# Run as a user with sudo, on the target machine.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "[co2-logger] Installing Python dependency (pyserial)..."
sudo pip3 install --break-system-packages pyserial

echo "[co2-logger] Copying logger to /opt/pi-co2-logger/..."
sudo mkdir -p /opt/pi-co2-logger/logger
sudo cp "${REPO_DIR}/logger/co2_logger.py" /opt/pi-co2-logger/logger/co2_logger.py
sudo cp "${REPO_DIR}/logger/geonicdb_sink.py" /opt/pi-co2-logger/logger/geonicdb_sink.py
sudo cp "${REPO_DIR}/logger/requirements.txt" /opt/pi-co2-logger/logger/requirements.txt

echo "[co2-logger] Installing systemd unit..."
sudo cp "${REPO_DIR}/logger/co2-logger.service" /etc/systemd/system/co2-logger.service

echo "[co2-logger] Enabling and starting service..."
sudo systemctl daemon-reload
sudo systemctl enable co2-logger.service
sudo systemctl restart co2-logger.service

echo "[co2-logger] Done. Status:"
sudo systemctl status co2-logger.service --no-pager -l
