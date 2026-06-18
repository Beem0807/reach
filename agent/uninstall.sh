#!/usr/bin/env bash
# Uninstall reach-agent from a Linux machine.
#
# Usage:
#   curl -fsSL https://reach-releases.s3.amazonaws.com/uninstall.sh | sudo bash

set -euo pipefail

CONFIG_DIR="/etc/reach-agent"
BIN_PATH="/usr/local/bin/reach-agent"
SERVICE_FILE="/etc/systemd/system/reach-agent.service"
SERVICE_NAME="reach-agent"
SERVICE_USER="reach-agent"

if [[ $EUID -ne 0 ]]; then
  echo "Error: must run as root (use sudo)"
  exit 1
fi

OS=$(uname -s)
if [[ "$OS" != "Linux" ]]; then
  echo "Error: this script only supports Linux (detected: $OS)"
  exit 1
fi

echo "==> Stopping and disabling service..."
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
  systemctl stop "$SERVICE_NAME"
fi
if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
  systemctl disable "$SERVICE_NAME"
fi

echo "==> Removing service file..."
rm -f "$SERVICE_FILE"
systemctl daemon-reload

echo "==> Removing binary..."
rm -f "$BIN_PATH"

echo "==> Removing config..."
rm -rf "$CONFIG_DIR"

echo "==> Removing system user..."
if id "$SERVICE_USER" &>/dev/null; then
  userdel "$SERVICE_USER"
fi

echo ""
echo "==> reach-agent uninstalled."
