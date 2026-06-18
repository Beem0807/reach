#!/usr/bin/env bash
# Install reach-agent on a Linux machine
#
# Usage:
#   curl -fsSL https://reach-releases.s3.amazonaws.com/install.sh | sudo bash -s -- \
#     --api-url  "https://api.yourapp.com" \
#     --agent-id "agent_xxx" \
#     --install-token "install_xxx"

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BINARY_BASE_URL="https://reach-releases.s3.amazonaws.com/__AGENT_VERSION__"
CONFIG_DIR="/etc/reach-agent"
CONFIG_FILE="$CONFIG_DIR/config.json"
BIN_PATH="/usr/local/bin/reach-agent"
SERVICE_FILE="/etc/systemd/system/reach-agent.service"
SERVICE_NAME="reach-agent"
SERVICE_USER="reach-agent"

API_URL=""
AGENT_ID=""
INSTALL_TOKEN=""

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
usage() {
  echo "Usage: $0 --api-url <url> --agent-id <id> --install-token <token>"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-url)       API_URL="$2";       shift 2 ;;
    --agent-id)      AGENT_ID="$2";      shift 2 ;;
    --install-token) INSTALL_TOKEN="$2"; shift 2 ;;
    --help|-h)       usage ;;
    *) echo "Unknown argument: $1"; usage ;;
  esac
done

[[ -z "$API_URL" || -z "$AGENT_ID" || -z "$INSTALL_TOKEN" ]] && usage

# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
  echo "Error: must run as root (use sudo)"
  exit 1
fi

OS=$(uname -s)
if [[ "$OS" != "Linux" ]]; then
  echo "Error: this installer only supports Linux (detected: $OS)"
  echo "       For macOS, run the agent directly with REACH_CONFIG_PATH."
  exit 1
fi

if ! command -v curl &>/dev/null; then
  echo "Error: curl is required but not installed"
  exit 1
fi

if ! command -v systemctl &>/dev/null; then
  echo "Error: systemctl not found - this installer requires systemd"
  exit 1
fi

# ---------------------------------------------------------------------------
# Architecture
# ---------------------------------------------------------------------------
ARCH=$(uname -m)
case "$ARCH" in
  x86_64)          GOARCH="amd64" ;;
  aarch64|arm64)   GOARCH="arm64" ;;
  armv7l|armv6l)   GOARCH="arm"   ;;
  *)
    echo "Error: unsupported architecture: $ARCH"
    exit 1
    ;;
esac

BINARY_NAME="reach-agent-linux-${GOARCH}"
DOWNLOAD_URL="${BINARY_BASE_URL}/${BINARY_NAME}"

# ---------------------------------------------------------------------------
# Create dedicated system user
# ---------------------------------------------------------------------------
if ! id "$SERVICE_USER" &>/dev/null; then
  echo "==> Creating system user: $SERVICE_USER"
  useradd \
    --system \
    --no-create-home \
    --shell /usr/sbin/nologin \
    --comment "Reach Agent" \
    "$SERVICE_USER"
fi

# ---------------------------------------------------------------------------
# Detect existing installation
# ---------------------------------------------------------------------------
REINSTALL=false
if [[ -f "$BIN_PATH" ]]; then
  echo "==> Existing installation detected - upgrading."
  REINSTALL=true
  systemctl stop "$SERVICE_NAME" 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# Download binary
# ---------------------------------------------------------------------------
echo "==> Downloading $BINARY_NAME from S3..."
TMP_BIN=$(mktemp)
if ! curl -fsSL -o "$TMP_BIN" "$DOWNLOAD_URL"; then
  echo "Error: failed to download $DOWNLOAD_URL"
  rm -f "$TMP_BIN"
  exit 1
fi
chmod +x "$TMP_BIN"
# Binary owned by root, executable by all
chown root:root "$TMP_BIN"
mv "$TMP_BIN" "$BIN_PATH"
echo "    Installed to $BIN_PATH"

# ---------------------------------------------------------------------------
# Write config
# Only write if not reinstalling (preserve existing agent_token after claim)
# ---------------------------------------------------------------------------
if [[ "$REINSTALL" == false ]]; then
  echo "==> Writing config..."
  mkdir -p "$CONFIG_DIR"
  printf '{\n  "api_url": "%s",\n  "agent_id": "%s",\n  "install_token": "%s"\n}\n' \
    "$API_URL" "$AGENT_ID" "$INSTALL_TOKEN" > "$CONFIG_FILE"
  # Config readable only by the service user
  chown "$SERVICE_USER:$SERVICE_USER" "$CONFIG_FILE"
  chmod 600 "$CONFIG_FILE"
  chown "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR"
  chmod 700 "$CONFIG_DIR"
  echo "    Config written to $CONFIG_FILE"
else
  echo "==> Skipping config (preserving existing $CONFIG_FILE)"
  # Ensure ownership is correct after upgrade
  chown "$SERVICE_USER:$SERVICE_USER" "$CONFIG_FILE" "$CONFIG_DIR"
fi

# ---------------------------------------------------------------------------
# Install systemd service
# ---------------------------------------------------------------------------
echo "==> Installing systemd service..."
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Reach Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/reach-agent
Restart=always
RestartSec=10

# Run as dedicated non-root user
User=$SERVICE_USER
Group=$SERVICE_USER

# Prevent the process from gaining new privileges via setuid/setgid
NoNewPrivileges=yes

# Isolated /tmp - child processes get their own private temp dir
PrivateTmp=yes

# Protect kernel tunables and control groups from writes
ProtectKernelTunables=yes
ProtectControlGroups=yes

# Drop all capabilities - the agent only needs network access
CapabilityBoundingSet=

Environment=REACH_COMMAND_TIMEOUT_SECONDS=60
Environment=REACH_MAX_OUTPUT_BYTES=50000

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "==> reach-agent installed and started (running as user: $SERVICE_USER)."
echo "    Status:  systemctl status $SERVICE_NAME"
echo "    Logs:    journalctl -u $SERVICE_NAME -f"
echo "    Config:  $CONFIG_FILE"
echo ""
echo "    Note: the agent runs as a non-root user."
echo "    To allow elevated commands, add the service user to relevant groups:"
echo "      usermod -aG docker $SERVICE_USER   # docker commands"
echo "      usermod -aG sudo $SERVICE_USER     # full sudo (not recommended)"
echo "    Or configure fine-grained sudo rules in /etc/sudoers.d/reach-agent"
