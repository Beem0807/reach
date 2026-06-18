#!/usr/bin/env bash
# Install (or uninstall) reach-agent.
#
# Linux       - installs as a systemd service (runs on boot). Requires sudo.
# macOS       - foreground by default (runs in this terminal, stops on close).
# macOS --background - installs as a LaunchDaemon under a dedicated reach-agent
#                      system user, same security model as Linux. Starts on boot.
#
# --- Interactive (prompts for any missing required values, then for each grant) ---
#
#   sudo bash install.sh
#   sudo bash install.sh --background            # macOS: skip background prompt (answer yes)
#
#   Pre-answer individual grant prompts while keeping everything else interactive:
#   sudo bash install.sh --no-grant-service-mgmt # skip service mgmt prompt (answer no)
#   sudo bash install.sh --grant-service-mgmt    # skip service mgmt prompt (answer yes)
#   sudo bash install.sh --no-grant-docker       # skip docker prompt (answer no)
#   sudo bash install.sh --grant-docker          # skip docker prompt (answer yes)
#
# --- Non-interactive (piped through bash or CI) ---
#
#   --yes applies prompt defaults (service mgmt on, docker off). Required flags
#   must be provided; the script exits with an error if any are missing.
#
#   sudo bash install.sh \
#     --api-url https://api.example.com \
#     --agent-id agent_xxx \
#     --install-token install_xxx \
#     --yes
#
#   sudo bash install.sh ... --yes --grant-docker          # also grant docker
#   sudo bash install.sh ... --yes --no-grant-service-mgmt # no service mgmt, no docker
#   sudo bash install.sh ... --background --yes            # macOS background, defaults
#
# --- Other ---
#
#   sudo bash install.sh ... --force    # overwrite existing config without prompting
#   sudo bash install.sh --uninstall    # remove agent, service, user, and config (Linux + macOS)

set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BINARY_BASE_URL="https://reach-releases.s3.amazonaws.com/__AGENT_VERSION__"
CONFIG_DIR="/etc/reach-agent"
CONFIG_FILE="$CONFIG_DIR/config.json"
BIN_PATH="/usr/local/bin/reach-agent"

# Linux (systemd)
SERVICE_FILE="/etc/systemd/system/reach-agent.service"
SERVICE_NAME="reach-agent"
SERVICE_USER="reach-agent"

# macOS background (LaunchDaemon with dedicated system user)
PLIST_LABEL="com.reach-agent"
MACOS_DAEMON_PLIST="/Library/LaunchDaemons/${PLIST_LABEL}.plist"
MACOS_DAEMON_LOG="/var/log/reach-agent.log"
MACOS_AGENT_USER="reach-agent"

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
API_URL=""
AGENT_ID=""
INSTALL_TOKEN=""
FORCE=false
UNINSTALL=false
BACKGROUND=false
GRANT_SERVICE_MGMT=false
GRANT_SERVICE_MGMT_SET=false
GRANT_DOCKER=false
GRANT_DOCKER_SET=false
YES=false

usage() {
  echo "Usage: $0 [--api-url <url>] [--agent-id <id>] [--install-token <token>] [--force] [--background] [--grant-service-mgmt] [--no-grant-service-mgmt] [--grant-docker] [--no-grant-docker] [--yes] [--uninstall]"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-url)               API_URL="$2";                                     shift 2 ;;
    --agent-id)              AGENT_ID="$2";                                    shift 2 ;;
    --install-token)         INSTALL_TOKEN="$2";                               shift 2 ;;
    --force)                 FORCE=true;                                        shift ;;
    --background)            BACKGROUND=true;                                   shift ;;
    --grant-service-mgmt)    GRANT_SERVICE_MGMT=true;  GRANT_SERVICE_MGMT_SET=true; shift ;;
    --no-grant-service-mgmt) GRANT_SERVICE_MGMT=false; GRANT_SERVICE_MGMT_SET=true; shift ;;
    --grant-docker)          GRANT_DOCKER=true;         GRANT_DOCKER_SET=true; shift ;;
    --no-grant-docker)       GRANT_DOCKER=false;        GRANT_DOCKER_SET=true; shift ;;
    --yes|-y)                YES=true;                                          shift ;;
    --uninstall)             UNINSTALL=true;                                    shift ;;
    --help|-h)               usage ;;
    *) echo "Unknown argument: $1"; usage ;;
  esac
done

# ---------------------------------------------------------------------------
# Detect OS and architecture
# ---------------------------------------------------------------------------
OS=$(uname -s)
ARCH=$(uname -m)

case "$OS" in
  Linux)  GOOS="linux"  ;;
  Darwin) GOOS="darwin" ;;
  *)
    echo "Error: unsupported OS: $OS"
    exit 1
    ;;
esac

case "$ARCH" in
  x86_64)        GOARCH="amd64" ;;
  aarch64|arm64) GOARCH="arm64" ;;
  *)
    echo "Error: unsupported architecture: $ARCH"
    exit 1
    ;;
esac

# ---------------------------------------------------------------------------
# Root check
# ---------------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
  echo "Error: must run as root (use sudo)"
  exit 1
fi

# ---------------------------------------------------------------------------
# macOS foreground: resolve the real (non-root) user
# Needed only for foreground exec - background uses the dedicated agent user.
# ---------------------------------------------------------------------------
REAL_USER="$(whoami)"
REAL_HOME="$HOME"
if [[ "$OS" == "Darwin" ]]; then
  if [[ -n "${SUDO_USER:-}" ]]; then
    REAL_USER="$SUDO_USER"
    REAL_HOME=$(eval echo "~$SUDO_USER")
  elif [[ "$REAL_USER" == "root" ]]; then
    echo "Error: run this script via sudo from your normal user account, not as root directly"
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# curl check
# ---------------------------------------------------------------------------
if ! command -v curl &>/dev/null; then
  echo "Error: curl is required but not installed"
  exit 1
fi

# ---------------------------------------------------------------------------
# macOS: find a free UID in the hidden system range (< 500)
# ---------------------------------------------------------------------------
_find_free_uid() {
  local uid=300
  while dscl . -list /Users UniqueID 2>/dev/null | awk '{print $2}' | grep -qx "$uid"; do
    uid=$((uid + 1))
  done
  echo "$uid"
}

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------
if [[ "$UNINSTALL" == true ]]; then
  echo "==> Uninstalling reach-agent..."

  if [[ "$OS" == "Linux" ]]; then
    if ! command -v systemctl &>/dev/null; then
      echo "Error: systemctl not found"
      exit 1
    fi
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
      echo "    Stopping service..."
      systemctl stop "$SERVICE_NAME"
    fi
    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
      systemctl disable "$SERVICE_NAME"
    fi
    rm -f "$SERVICE_FILE"
    systemctl daemon-reload
    if id "$SERVICE_USER" &>/dev/null; then
      echo "    Removing system user $SERVICE_USER..."
      userdel "$SERVICE_USER"
    fi
  fi

  if [[ "$OS" == "Darwin" ]]; then
    if [[ -f "$MACOS_DAEMON_PLIST" ]]; then
      echo "    Unloading LaunchDaemon..."
      launchctl unload "$MACOS_DAEMON_PLIST" 2>/dev/null || true
      rm -f "$MACOS_DAEMON_PLIST"
    fi
    if dscl . -read "/Users/$MACOS_AGENT_USER" &>/dev/null 2>&1; then
      echo "    Removing system user $MACOS_AGENT_USER..."
      dscl . -delete "/Users/$MACOS_AGENT_USER"
    fi
  fi

  if [[ -f /etc/sudoers.d/reach-agent ]]; then
    echo "    Removing sudoers entry..."
    rm -f /etc/sudoers.d/reach-agent
  fi

  echo "    Removing binary..."
  rm -f "$BIN_PATH"

  echo "    Removing config..."
  rm -rf "$CONFIG_DIR"

  echo ""
  echo "==> reach-agent uninstalled."
  exit 0
fi

# ---------------------------------------------------------------------------
# Interactive prompts (only when stdin is a terminal)
# ---------------------------------------------------------------------------
IS_TTY=false
[[ -t 0 ]] && IS_TTY=true

_prompt() {
  local varname="$1"
  local label="$2"
  if [[ "$IS_TTY" == false ]]; then
    local flag
    flag=$(echo "$varname" | tr '[:upper:]_' '[:lower:]-')
    echo "Error: --${flag} is required when running non-interactively"
    exit 1
  fi
  local _val="${!varname}"
  while [[ -z "$_val" ]]; do
    read -rp "$label: " _val
  done
  printf -v "$varname" '%s' "$_val"
}

[[ -z "$API_URL" ]]       && _prompt API_URL       "API URL (e.g. https://api.example.com)"
[[ -z "$AGENT_ID" ]]      && _prompt AGENT_ID      "Agent ID (e.g. agent_abc)"
[[ -z "$INSTALL_TOKEN" ]] && _prompt INSTALL_TOKEN "Install token (install_xxx)"

if [[ "$OS" == "Darwin" ]] && [[ "$IS_TTY" == true ]] && [[ "$YES" == false ]] && [[ "$BACKGROUND" == false ]]; then
  read -rp "Run as a background service (starts on boot, dedicated system user)? [y/N] " _bg
  [[ "${_bg:-}" =~ ^[Yy]$ ]] && BACKGROUND=true
fi

if { [[ "$OS" == "Linux" ]] || [[ "$BACKGROUND" == true ]]; }; then
  if [[ "$IS_TTY" == true ]] && [[ "$YES" == false ]]; then
    if [[ "$GRANT_SERVICE_MGMT_SET" == false ]]; then
      read -rp "Grant service management permissions (systemctl/launchctl restart, start, stop)? [Y/n] " _svc
      [[ ! "${_svc:-}" =~ ^[Nn]$ ]] && GRANT_SERVICE_MGMT=true
    fi
    if [[ "$GRANT_DOCKER_SET" == false ]]; then
      read -rp "Grant docker permissions (add reach-agent to docker group)? [y/N] " _docker
      [[ "${_docker:-}" =~ ^[Yy]$ ]] && GRANT_DOCKER=true
    fi
  elif [[ "$YES" == true ]]; then
    # apply prompt defaults: service mgmt yes [Y/n], docker no [y/N]
    [[ "$GRANT_SERVICE_MGMT_SET" == false ]] && GRANT_SERVICE_MGMT=true
  fi
fi

# ---------------------------------------------------------------------------
# Linux: systemd check
# ---------------------------------------------------------------------------
if [[ "$OS" == "Linux" ]] && ! command -v systemctl &>/dev/null; then
  echo "Error: systemctl not found - this installer requires systemd"
  exit 1
fi

# ---------------------------------------------------------------------------
# Detect existing installation
# ---------------------------------------------------------------------------
EXISTING_INSTALL=false
[[ -f "$BIN_PATH" ]] && EXISTING_INSTALL=true

WRITE_CONFIG=true
if [[ -f "$CONFIG_FILE" ]]; then
  if [[ "$FORCE" == true ]]; then
    echo "==> Config exists, overwriting (--force)"
  elif [[ "$IS_TTY" == true ]] && [[ "$YES" == false ]]; then
    read -rp "Config already exists at $CONFIG_FILE. Overwrite? [y/N] " _confirm
    if [[ ! "${_confirm:-}" =~ ^[Yy]$ ]]; then
      echo "    Keeping existing config."
      WRITE_CONFIG=false
    fi
  else
    echo "==> Config exists, keeping (use --force to overwrite)"
    WRITE_CONFIG=false
  fi
fi

# ---------------------------------------------------------------------------
# Stop existing service before replacing binary
# ---------------------------------------------------------------------------
if [[ "$EXISTING_INSTALL" == true ]]; then
  echo "==> Existing installation detected - upgrading."
  if [[ "$OS" == "Linux" ]]; then
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
  fi
  if [[ "$OS" == "Darwin" ]] && [[ -f "$MACOS_DAEMON_PLIST" ]]; then
    launchctl unload "$MACOS_DAEMON_PLIST" 2>/dev/null || true
  fi
fi

# ---------------------------------------------------------------------------
# Download binary
# ---------------------------------------------------------------------------
BINARY_NAME="reach-agent-${GOOS}-${GOARCH}"
DOWNLOAD_URL="${BINARY_BASE_URL}/${BINARY_NAME}"

echo "==> Detected: ${OS} / ${ARCH}"
echo "==> Downloading ${BINARY_NAME}..."
TMP_BIN=$(mktemp)
if ! curl -fsSL -o "$TMP_BIN" "$DOWNLOAD_URL"; then
  echo "Error: failed to download $DOWNLOAD_URL"
  rm -f "$TMP_BIN"
  exit 1
fi
chmod +x "$TMP_BIN"
mv "$TMP_BIN" "$BIN_PATH"
echo "    Installed to $BIN_PATH"

# ---------------------------------------------------------------------------
# Write config
# ---------------------------------------------------------------------------
if [[ "$WRITE_CONFIG" == true ]]; then
  echo "==> Writing config..."
  mkdir -p "$CONFIG_DIR"
  printf '{\n  "api_url": "%s",\n  "agent_id": "%s",\n  "install_token": "%s"\n}\n' \
    "$API_URL" "$AGENT_ID" "$INSTALL_TOKEN" > "$CONFIG_FILE"
  chmod 600 "$CONFIG_FILE"
  chmod 700 "$CONFIG_DIR"
  echo "    Config written to $CONFIG_FILE"
fi

# ---------------------------------------------------------------------------
# Linux: systemd service
# ---------------------------------------------------------------------------
if [[ "$OS" == "Linux" ]]; then
  if ! id "$SERVICE_USER" &>/dev/null; then
    echo "==> Creating system user: $SERVICE_USER"
    useradd --system --no-create-home --shell /usr/sbin/nologin \
            --comment "Reach Agent" "$SERVICE_USER"
  fi

  chown "$SERVICE_USER:$SERVICE_USER" "$CONFIG_FILE" "$CONFIG_DIR"

  if [[ "$GRANT_SERVICE_MGMT" == true ]]; then
    echo "==> Granting service management permissions..."
    printf 'reach-agent ALL=(ALL) NOPASSWD: /bin/systemctl, /usr/sbin/service\n' \
      > /etc/sudoers.d/reach-agent
    chmod 440 /etc/sudoers.d/reach-agent
  fi

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
User=$SERVICE_USER
Group=$SERVICE_USER
NoNewPrivileges=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
CapabilityBoundingSet=
Environment=REACH_COMMAND_TIMEOUT_SECONDS=60
Environment=REACH_MAX_OUTPUT_BYTES=50000

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME"
  systemctl restart "$SERVICE_NAME"

  echo ""
  echo "==> reach-agent installed and started."
  echo "    Status:    systemctl status $SERVICE_NAME"
  echo "    Logs:      journalctl -u $SERVICE_NAME -f"
  echo "    Config:    $CONFIG_FILE"
  echo "    Uninstall: sudo bash install.sh --uninstall"
  echo ""
  echo "    The agent runs as user '$SERVICE_USER'."

  if [[ "$GRANT_DOCKER" == true ]]; then
    if getent group docker &>/dev/null; then
      usermod -aG docker "$SERVICE_USER"
      systemctl restart "$SERVICE_NAME"
      echo "    Docker access granted."
    else
      echo "    Docker group not found - install Docker first, then run: usermod -aG docker $SERVICE_USER && systemctl restart $SERVICE_NAME"
    fi
  else
    echo "    To allow docker commands:  usermod -aG docker $SERVICE_USER && systemctl restart $SERVICE_NAME"
    echo "    Or re-run the installer with: --grant-docker"
  fi

  if [[ "$GRANT_SERVICE_MGMT" == false ]]; then
    echo ""
    echo "    To grant service management (systemctl restart/start/stop) later:"
    echo "      echo 'reach-agent ALL=(ALL) NOPASSWD: /bin/systemctl, /usr/sbin/service' \\"
    echo "        | sudo tee /etc/sudoers.d/reach-agent && sudo chmod 440 /etc/sudoers.d/reach-agent"
    echo "    Or re-run the installer with: --grant-service-mgmt"
  fi
fi

# ---------------------------------------------------------------------------
# macOS: background (LaunchDaemon with dedicated system user)
# Same security model as Linux: minimal-privilege system user, starts on boot.
# ---------------------------------------------------------------------------
if [[ "$OS" == "Darwin" ]] && [[ "$BACKGROUND" == true ]]; then
  if ! dscl . -read "/Users/$MACOS_AGENT_USER" &>/dev/null 2>&1; then
    echo "==> Creating system user: $MACOS_AGENT_USER"
    local_uid=$(_find_free_uid)
    dscl . -create "/Users/$MACOS_AGENT_USER"
    dscl . -create "/Users/$MACOS_AGENT_USER" UserShell /usr/bin/false
    dscl . -create "/Users/$MACOS_AGENT_USER" RealName "Reach Agent"
    dscl . -create "/Users/$MACOS_AGENT_USER" UniqueID "$local_uid"
    dscl . -create "/Users/$MACOS_AGENT_USER" PrimaryGroupID 20
    dscl . -create "/Users/$MACOS_AGENT_USER" NFSHomeDirectory /var/empty
    dscl . -create "/Users/$MACOS_AGENT_USER" IsHidden 1
  fi

  chown "$MACOS_AGENT_USER" "$CONFIG_FILE" "$CONFIG_DIR"

  if [[ "$GRANT_SERVICE_MGMT" == true ]]; then
    echo "==> Granting service management permissions..."
    printf 'reach-agent ALL=(ALL) NOPASSWD: /bin/launchctl\n' \
      > /etc/sudoers.d/reach-agent
    chmod 440 /etc/sudoers.d/reach-agent
  fi

  cat > "$MACOS_DAEMON_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${BIN_PATH}</string>
    </array>
    <key>UserName</key>
    <string>${MACOS_AGENT_USER}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${MACOS_DAEMON_LOG}</string>
    <key>StandardErrorPath</key>
    <string>${MACOS_DAEMON_LOG}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>REACH_COMMAND_TIMEOUT_SECONDS</key>
        <string>60</string>
        <key>REACH_MAX_OUTPUT_BYTES</key>
        <string>50000</string>
    </dict>
</dict>
</plist>
EOF

  launchctl load "$MACOS_DAEMON_PLIST"

  echo ""
  echo "==> reach-agent installed and running in the background."
  echo "    Logs:      tail -f $MACOS_DAEMON_LOG"
  echo "    Stop:      launchctl unload $MACOS_DAEMON_PLIST"
  echo "    Start:     launchctl load $MACOS_DAEMON_PLIST"
  echo "    Config:    $CONFIG_FILE"
  echo "    Uninstall: sudo bash install.sh --uninstall"
  echo ""
  echo "    The agent runs as user '$MACOS_AGENT_USER'."

  if [[ "$GRANT_DOCKER" == true ]]; then
    if dscl . -read /Groups/docker &>/dev/null 2>&1; then
      dseditgroup -o edit -a "$MACOS_AGENT_USER" -t user docker
      launchctl unload "$MACOS_DAEMON_PLIST" 2>/dev/null || true
      launchctl load "$MACOS_DAEMON_PLIST"
      echo "    Docker access granted."
    else
      echo "    Docker group not found - install Docker first, then run: dseditgroup -o edit -a $MACOS_AGENT_USER -t user docker"
    fi
  else
    echo "    To allow docker commands:  dseditgroup -o edit -a $MACOS_AGENT_USER -t user docker"
    echo "    Or re-run the installer with: --grant-docker"
  fi

  if [[ "$GRANT_SERVICE_MGMT" == false ]]; then
    echo ""
    echo "    To grant service management (launchctl restart/start/stop) later:"
    echo "      echo 'reach-agent ALL=(ALL) NOPASSWD: /bin/launchctl' \\"
    echo "        | sudo tee /etc/sudoers.d/reach-agent && sudo chmod 440 /etc/sudoers.d/reach-agent"
    echo "    Or re-run the installer with: --grant-service-mgmt"
  fi
fi

# ---------------------------------------------------------------------------
# macOS: foreground (default)
# ---------------------------------------------------------------------------
if [[ "$OS" == "Darwin" ]] && [[ "$BACKGROUND" == false ]]; then
  chown "$REAL_USER" "$CONFIG_FILE" "$CONFIG_DIR"

  echo ""
  echo "==> reach-agent ready. Starting in this terminal..."
  echo "    Config:     $CONFIG_FILE"
  echo "    Press Ctrl+C to stop."
  echo "    Tip: re-run with --background to install as a persistent background service."
  echo ""
  exec sudo -u "$REAL_USER" "$BIN_PATH"
fi
