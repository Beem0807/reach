#!/usr/bin/env bash
# Reach local setup
#
# Starts the Reach backend locally using Docker, collects all configuration
# upfront, then automatically creates:
#
#   - workspace (tenant) + admin user
#   - CLI API key
#   - first agent (optional)
#   - Reach CLI install + login
#
# Usage:
#
#   Fresh setup:
#     ./scripts/local-setup.sh
#
#   Run from remote release (no local clone needed):
#     curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash
#
#   Check if everything is running:
#     ./scripts/local-setup.sh --status
#
#   Update backend image only (keeps all data):
#     ./scripts/local-setup.sh --update
#
#   Stop backend, keep database:
#     ./scripts/local-setup.sh --down
#
#   Stop backend and delete database:
#     ./scripts/local-setup.sh --reset
#
#   Remove everything, optionally uninstall CLI:
#     ./scripts/local-setup.sh --purge
#
#   Rotate the platform admin password:
#     ./scripts/local-setup.sh --rotate-password
#
#   Rotate the session signing key (forces console re-login):
#     ./scripts/local-setup.sh --rotate-session-key
#
# Notes:
#
#   --status             checks containers, backend health, API key auth, and agent state.
#   --down               stops containers but keeps the Postgres data volume.
#   --reset              removes containers and the Postgres data volume (data loss).
#   --purge              deletes ~/.reach/local and asks before uninstalling the Reach CLI.
#   --update             pulls a new backend image and restarts; keeps TOKEN_PEPPER,
#                        SESSION_SIGNING_KEY, ADMIN_PASSWORD, database, tenants, users, and agents.
#   --rotate-password    sets a new platform admin password and restarts the backend.
#   --rotate-session-key generates a new session signing key; only forces console re-login.
#
# Requirements:
#
#   - docker + docker compose
#   - curl
#   - openssl
#   - python3
#
# Optional (for exposing the backend publicly):
#
#   - cloudflared  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
#   - ngrok        https://ngrok.com/download

set -euo pipefail

WORK_DIR="$HOME/.reach/local"
COMPOSE_FILE="$WORK_DIR/docker-compose.yml"
ENV_FILE="$WORK_DIR/env"
API_PORT="${API_PORT:-8000}"
CLI_WHEEL_URL="https://reach-releases.s3.amazonaws.com/cli/latest/reach-0.1.0-py3-none-any.whl"

ok()   { printf "  [OK]      %s\n" "$1"; }
info() { printf "  [INFO]    %s\n" "$1"; }
warn() { printf "  [WARN]    %s\n" "$1"; }
fail() { printf "  [ERROR]   %s\n" "$1"; exit 1; }

trap 'echo ""; echo "[ERROR] Setup failed at line $LINENO"; echo "Backend logs:"; echo "  docker compose -f $COMPOSE_FILE logs backend"; echo ""; exit 1' ERR

get_compose() {
  if docker compose version &>/dev/null 2>&1; then
    echo "docker compose"
  elif command -v docker-compose &>/dev/null; then
    echo "docker-compose"
  else
    return 1
  fi
}

request_json() {
  local method="$1"
  local url="$2"
  local body="${3:-}"
  shift 3 || true

  local tmp
  tmp="$(mktemp)"

  local code
  if [[ -n "$body" ]]; then
    code=$(curl -sS -o "$tmp" -w "%{http_code}" -X "$method" "$url" "$@" -d "$body")
  else
    code=$(curl -sS -o "$tmp" -w "%{http_code}" -X "$method" "$url" "$@")
  fi

  if [[ "$code" -lt 200 || "$code" -ge 300 ]]; then
    echo ""
    echo "[ERROR] API request failed: $method $url"
    echo "HTTP $code"
    cat "$tmp"
    echo ""
    rm -f "$tmp"
    exit 1
  fi

  cat "$tmp"
  rm -f "$tmp"
}

prompt() {
  local label="$1"
  local default="${2:-}"
  local value=""

  if [[ -n "$default" ]]; then
    read -rp "  $label [$default]: " value < /dev/tty
    echo "${value:-$default}"
  else
    while [[ -z "$value" ]]; do
      read -rp "  $label: " value < /dev/tty
      [[ -z "$value" ]] && echo "    Value cannot be empty." > /dev/tty
    done
    echo "$value"
  fi
}

prompt_yes_no() {
  local label="$1"
  local default="${2:-N}"
  local value=""

  while true; do
    read -rp "  $label [$default]: " value < /dev/tty
    value="${value:-$default}"

    case "$value" in
      y|Y|yes|YES) echo "true"; return ;;
      n|N|no|NO) echo "false"; return ;;
      *) echo "    Enter y or n." > /dev/tty ;;
    esac
  done
}

prompt_password() {
  local p1=""
  local p2=""

  while true; do
    read -rsp "  Password: " p1 < /dev/tty
    echo "" > /dev/tty

    if [[ -z "$p1" ]]; then
      echo "    Password cannot be empty." > /dev/tty
      continue
    fi

    if [[ ${#p1} -lt 8 ]]; then
      echo "    Password must be at least 8 characters." > /dev/tty
      continue
    fi

    read -rsp "  Confirm password: " p2 < /dev/tty
    echo "" > /dev/tty

    if [[ "$p1" != "$p2" ]]; then
      echo "    Passwords do not match. Try again." > /dev/tty
      continue
    fi

    echo "$p1"
    return
  done
}

prompt_name() {
  local label="$1"
  local default="${2:-}"
  local value=""

  while true; do
    if [[ -n "$default" ]]; then
      read -rp "  $label [$default]: " value < /dev/tty
      value="${value:-$default}"
    else
      read -rp "  $label: " value < /dev/tty
    fi
    [[ -z "$value" ]] && { echo "    Value cannot be empty." > /dev/tty; continue; }
    if [[ "$value" =~ ^[A-Za-z\ ]+$ ]]; then
      echo "$value"
      return
    fi
    echo "    Only letters and spaces are allowed." > /dev/tty
  done
}

prompt_username() {
  local label="$1"
  local default="${2:-}"
  local value=""

  while true; do
    if [[ -n "$default" ]]; then
      read -rp "  $label [$default]: " value < /dev/tty
      value="${value:-$default}"
    else
      read -rp "  $label: " value < /dev/tty
    fi
    value="${value,,}"  # lowercase
    [[ -z "$value" ]] && { echo "    Username cannot be empty." > /dev/tty; continue; }
    [[ ${#value} -lt 2 ]]  && { echo "    Username must be at least 2 characters." > /dev/tty; continue; }
    [[ ${#value} -gt 32 ]] && { echo "    Username must be 32 characters or fewer." > /dev/tty; continue; }
    if [[ "$value" =~ ^[a-z0-9]+$ ]]; then
      echo "$value"
      return
    fi
    echo "    Username may only contain lowercase letters and numbers." > /dev/tty
  done
}

mkjson() {
  python3 -c '
import json, sys
d = {}
args = sys.argv[1:]
for i in range(0, len(args), 2):
    k = args[i]
    v = args[i + 1]
    if v.lower() == "true":
        d[k] = True
    elif v.lower() == "false":
        d[k] = False
    else:
        d[k] = v
print(json.dumps(d))
' "$@"
}

json_get() {
  python3 -c "import sys,json; print(json.load(sys.stdin)$1)"
}

load_env_file() {
  [[ -f "$ENV_FILE" ]] || fail "local env not found at $ENV_FILE. Run setup first."

  # shellcheck disable=SC1090
  set -a
  . "$ENV_FILE"
  set +a

  : "${IMAGE:?IMAGE missing from env file}"
  : "${TOKEN_PEPPER:?TOKEN_PEPPER missing from env file}"
  : "${ADMIN_PASSWORD:?ADMIN_PASSWORD missing from env file}"
  : "${APPROVAL_RETENTION_DAYS:?APPROVAL_RETENTION_DAYS missing from env file}"
  : "${JOB_RETENTION_DAYS:?JOB_RETENTION_DAYS missing from env file}"
  : "${AUDIT_RETENTION_DAYS:?AUDIT_RETENTION_DAYS missing from env file}"
  : "${AGENT_HISTORY_RETENTION_DAYS:?AGENT_HISTORY_RETENTION_DAYS missing from env file}"

  # SESSION_SIGNING_KEY was added after the initial release; generate and persist
  # one for older env files so session tokens aren't signed with a weak default.
  if [[ -z "${SESSION_SIGNING_KEY:-}" ]]; then
    SESSION_SIGNING_KEY="$(openssl rand -hex 32)"
    echo "SESSION_SIGNING_KEY=${SESSION_SIGNING_KEY}" >> "$ENV_FILE"
  fi
}

write_nginx_config() {
  mkdir -p "$WORK_DIR"
  chmod 700 "$WORK_DIR"

  cat > "$WORK_DIR/nginx.conf" <<'NGINXEOF'
server {
    listen 80;

    location / {
        proxy_pass         http://backend:8000;
        proxy_http_version 1.1;

        proxy_set_header Host              $http_host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;

        proxy_read_timeout 60s;
    }
}
NGINXEOF
}

write_compose_file() {
  cat > "$COMPOSE_FILE" <<EOF
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: reach
      POSTGRES_PASSWORD: reach
      POSTGRES_DB: reach
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U reach"]
      interval: 5s
      timeout: 5s
      retries: 5

  backend:
    image: $IMAGE
    environment:
      TOKEN_PEPPER: "$TOKEN_PEPPER"
      SESSION_SIGNING_KEY: "$SESSION_SIGNING_KEY"
      ADMIN_PASSWORD: "$ADMIN_PASSWORD"
      DATABASE_URL: postgresql://reach:reach@db:5432/reach
      STORAGE_BACKEND: postgres
      APPROVAL_RETENTION_DAYS: "$APPROVAL_RETENTION_DAYS"
      JOB_RETENTION_DAYS: "$JOB_RETENTION_DAYS"
      AUDIT_RETENTION_DAYS: "$AUDIT_RETENTION_DAYS"
      AGENT_HISTORY_RETENTION_DAYS: "$AGENT_HISTORY_RETENTION_DAYS"
    depends_on:
      db:
        condition: service_healthy

  nginx:
    image: nginx:alpine
    ports:
      - "${API_PORT}:80"
    volumes:
      - ${WORK_DIR}/nginx.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on:
      - backend

volumes:
  postgres_data:
EOF
}

save_env_file() {
  cat > "$ENV_FILE" <<EOF
API_URL=${API_URL:-http://localhost:${API_PORT}}
IMAGE=${IMAGE}
TOKEN_PEPPER=${TOKEN_PEPPER}
SESSION_SIGNING_KEY=${SESSION_SIGNING_KEY}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
APPROVAL_RETENTION_DAYS=${APPROVAL_RETENTION_DAYS}
JOB_RETENTION_DAYS=${JOB_RETENTION_DAYS}
AUDIT_RETENTION_DAYS=${AUDIT_RETENTION_DAYS}
AGENT_HISTORY_RETENTION_DAYS=${AGENT_HISTORY_RETENTION_DAYS}
TENANT_ID=${TENANT_ID:-}
TENANT_NAME=${SETUP_TENANT:-${TENANT_NAME:-}}
USERNAME=${SETUP_USERNAME:-${USERNAME:-}}
API_KEY=${API_KEY:-}
CREATE_AGENT=${CREATE_AGENT:-}
AGENT_ID=${AGENT_ID:-}
AGENT_MODE=${AGENT_MODE:-}
GRANT_DOCKER=${GRANT_DOCKER:-}
GRANT_SERVICE_MGMT=${GRANT_SERVICE_MGMT:-}
INSTALL_AGENT='${INSTALL_AGENT:-}'
CLI_USE_CMD='${CLI_USE_CMD:-}'
EOF

  chmod 600 "$ENV_FILE"
}

pull_with_dots() {
  local image="$1"
  printf "  Pulling %s" "$image"
  docker pull "$image" -q &
  local pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    printf "."
    sleep 3
  done
  printf "\n"
  wait "$pid"
}

wait_for_backend() {
  printf "  Waiting for backend"
  for i in $(seq 1 30); do
    if curl -sf "http://localhost:${API_PORT}/health" &>/dev/null; then
      printf "\n"
      ok "Backend is healthy"
      return
    fi

    if [[ "$i" -eq 30 ]]; then
      printf "\n"
      $COMPOSE -f "$COMPOSE_FILE" logs backend
      fail "backend did not become healthy."
    fi

    printf "."
    sleep 2
  done
}

# ---------------------------------------------------------------------------
# Subcommands (--status, --update, --rotate-password, --down, --reset, --purge)
# ---------------------------------------------------------------------------
case "${1:-}" in
  --down)
    [[ -f "$COMPOSE_FILE" ]] || fail "no local stack found at $COMPOSE_FILE"
    COMPOSE="$(get_compose)" || fail "docker compose is required"

    echo "==> Stopping Reach local backend..."
    $COMPOSE -f "$COMPOSE_FILE" down
    echo "==> Done. Data volume kept."
    exit 0
    ;;

  --reset)
    [[ -f "$COMPOSE_FILE" ]] || fail "no local stack found at $COMPOSE_FILE"
    COMPOSE="$(get_compose)" || fail "docker compose is required"

    echo "==> Resetting Reach local backend..."
    $COMPOSE -f "$COMPOSE_FILE" down -v
    rm -f "$ENV_FILE"
    echo "==> Done. Containers, network, DB volume, and local env removed."
    exit 0
    ;;

  --purge)
    if [[ -f "$COMPOSE_FILE" ]]; then
      COMPOSE="$(get_compose)" || fail "docker compose is required"
      echo "==> Stopping and deleting Reach local backend..."
      $COMPOSE -f "$COMPOSE_FILE" down -v || true
    fi

    echo "==> Removing local Reach setup directory..."
    rm -rf "$WORK_DIR"

    if command -v reach &>/dev/null; then
      echo ""
      read -rp "  Uninstall Reach CLI too? [y/N]: " uninstall_cli < /dev/tty
      if [[ "$uninstall_cli" =~ ^[Yy]$ ]]; then
        echo "==> Attempting to uninstall Reach CLI..."

        if command -v uv &>/dev/null; then
          uv tool uninstall reach 2>/dev/null || true
          uv tool uninstall reach-cli 2>/dev/null || true
        fi

        if command -v pipx &>/dev/null; then
          pipx uninstall reach 2>/dev/null || true
          pipx uninstall reach-cli 2>/dev/null || true
        fi

        if command -v python3 &>/dev/null; then
          python3 -m pip uninstall -y reach reach-cli 2>/dev/null || true
        fi

        if command -v pip3 &>/dev/null; then
          pip3 uninstall -y reach reach-cli 2>/dev/null || true
        fi

        if command -v pip &>/dev/null; then
          pip uninstall -y reach reach-cli 2>/dev/null || true
        fi

        if command -v reach &>/dev/null; then
          echo ""
          warn "Reach command still exists after package uninstall."
          echo ""
          echo "Found reach executable(s):"
          which -a reach || true
          echo ""

          read -rp "  Remove all remaining reach executables from PATH? [y/N]: " remove_bins < /dev/tty

          if [[ "$remove_bins" =~ ^[Yy]$ ]]; then
            while IFS= read -r reach_bin; do
              [[ -z "$reach_bin" ]] && continue
              echo "  Removing $reach_bin"
              rm -f "$reach_bin" || sudo rm -f "$reach_bin"
            done < <(which -a reach 2>/dev/null | sort -u)

            hash -r 2>/dev/null || true
            rehash 2>/dev/null || true

            if command -v reach &>/dev/null; then
              warn "reach is still found at: $(command -v reach)"
              warn "Open a new terminal and run: type -a reach"
            else
              ok "Reach CLI executable removed"
            fi
          else
            info "Remaining reach executable kept"
          fi
        else
          ok "Reach CLI uninstalled"
        fi   
      fi
    fi

    echo "==> Purge complete."
    exit 0
    ;;

  --status)
    [[ -f "$COMPOSE_FILE" ]] || fail "no local stack found at $COMPOSE_FILE"
    load_env_file
    COMPOSE="$(get_compose)" || fail "docker compose is required"

    echo ""
    echo "┌──────────────────────────────────────────────┐"
    echo "│              Reach Local Status              │"
    echo "└──────────────────────────────────────────────┘"
    echo ""

    # Docker containers
    ALL_RUNNING=true
    for svc in db backend nginx; do
      state=$($COMPOSE -f "$COMPOSE_FILE" ps --format json 2>/dev/null \
        | python3 -c "
import sys,json
lines=[l for l in sys.stdin.read().strip().splitlines() if l]
for line in lines:
    d=json.loads(line)
    if d.get('Service') == '$svc':
        print(d.get('State','unknown'))
        sys.exit(0)
print('missing')
" 2>/dev/null || echo "unknown")
      if [[ "$state" == "running" ]]; then
        ok "container: $svc ($state)"
      else
        warn "container: $svc ($state)"
        ALL_RUNNING=false
      fi
    done

    # Backend health
    LOCAL_URL="http://localhost:${API_PORT}"
    if curl -sf "$LOCAL_URL/health" &>/dev/null; then
      ok "backend health: $LOCAL_URL/health"
    else
      warn "backend health: not responding at $LOCAL_URL/health"
      ALL_RUNNING=false
    fi

    # Public URL if set
    if [[ -n "${API_URL:-}" && "$API_URL" != "$LOCAL_URL" ]]; then
      if curl -sf "$API_URL/health" &>/dev/null; then
        ok "public URL: $API_URL"
      else
        warn "public URL: $API_URL (not reachable - tunnel may have stopped)"
      fi
    fi

    # API key auth
    if [[ -n "${API_KEY:-}" && -n "${API_URL:-}" ]]; then
      me_resp=$(curl -sf -H "Authorization: Bearer $API_KEY" "$LOCAL_URL/me" 2>/dev/null || true)
      if [[ -n "$me_resp" ]]; then
        username=$(echo "$me_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('username','?'))" 2>/dev/null || echo "?")
        ok "API key: valid (user: $username)"
      else
        warn "API key: auth check failed"
        ALL_RUNNING=false
      fi
    else
      info "API key: not set in env file"
    fi

    # CLI
    if command -v reach &>/dev/null; then
      ok "CLI: installed ($(reach --version 2>/dev/null || echo 'version unknown'))"
    else
      info "CLI: not installed"
    fi

    # Agent
    if [[ -n "${AGENT_ID:-}" ]]; then
      agent_resp=$(curl -sf -H "Authorization: Bearer ${API_KEY:-}" "$LOCAL_URL/agents/$AGENT_ID" 2>/dev/null || true)
      if [[ -n "$agent_resp" ]]; then
        status=$(echo "$agent_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || echo "?")
        hostname=$(echo "$agent_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('hostname') or 'not claimed')" 2>/dev/null || echo "?")
        if [[ "$status" == "ACTIVE" ]]; then
          ok "agent: $AGENT_ID ($hostname, $status)"
        else
          warn "agent: $AGENT_ID ($hostname, $status)"
        fi
      else
        warn "agent: $AGENT_ID (could not fetch status)"
      fi
    else
      info "agent: none configured"
    fi

    echo ""
    if [[ "$ALL_RUNNING" == "true" ]]; then
      echo "  Everything looks good."
    else
      echo "  Some checks failed. Run: $COMPOSE -f $COMPOSE_FILE logs backend"
    fi
    echo ""
    exit 0
    ;;

  --rotate-password)
    [[ -f "$COMPOSE_FILE" ]] || fail "no local stack found at $COMPOSE_FILE"
    load_env_file
    COMPOSE="$(get_compose)" || fail "docker compose is required"

    echo ""
    echo "┌──────────────────────────────────────────────┐"
    echo "│         Rotate Platform Admin Password       │"
    echo "└──────────────────────────────────────────────┘"
    echo ""

    read -rsp "  New admin password [generate]: " NEW_ADMIN_PASSWORD < /dev/tty; echo "" > /dev/tty
    [[ -z "$NEW_ADMIN_PASSWORD" ]] && NEW_ADMIN_PASSWORD="$(openssl rand -hex 16)"
    read -rsp "  Confirm new password: " CONFIRM_PASSWORD < /dev/tty; echo "" > /dev/tty

    if [[ "$NEW_ADMIN_PASSWORD" != "$CONFIRM_PASSWORD" ]]; then
      fail "passwords do not match"
    fi

    ADMIN_PASSWORD="$NEW_ADMIN_PASSWORD"

    echo ""
    ok "Updating admin password..."

    write_nginx_config
    write_compose_file
    save_env_file

    $COMPOSE -f "$COMPOSE_FILE" up -d backend

    wait_for_backend

    echo ""
    echo "┌──────────────────────────────────────────────┐"
    echo "│          Admin password rotated              │"
    echo "└──────────────────────────────────────────────┘"
    echo ""
    echo "  New password saved to:"
    echo "    $ENV_FILE"
    echo ""
    echo "  Log in at:"
    echo "    ${API_URL:-http://localhost:${API_PORT}}"
    echo ""
    exit 0
    ;;

  --rotate-session-key)
    [[ -f "$COMPOSE_FILE" ]] || fail "no local stack found at $COMPOSE_FILE"
    load_env_file
    COMPOSE="$(get_compose)" || fail "docker compose is required"

    echo ""
    echo "==> Rotating SESSION_SIGNING_KEY..."
    SESSION_SIGNING_KEY="$(openssl rand -hex 32)"

    write_compose_file
    save_env_file
    $COMPOSE -f "$COMPOSE_FILE" up -d backend
    wait_for_backend

    echo ""
    ok "SESSION_SIGNING_KEY rotated. Active console sessions are invalidated - users log in again. No data impact."
    echo ""
    exit 0
    ;;

  --update)
    [[ -f "$COMPOSE_FILE" ]] || fail "no local stack found at $COMPOSE_FILE"
    load_env_file
    COMPOSE="$(get_compose)" || fail "docker compose is required"

    echo ""
    echo "┌──────────────────────────────────────────────┐"
    echo "│              Reach Image Update              │"
    echo "└──────────────────────────────────────────────┘"
    echo ""
    echo "  Current image:"
    echo "    $IMAGE"
    echo ""

    CURRENT_TAG="${IMAGE##*:}"
    NEW_TAG=$(prompt "New release tag" "$CURRENT_TAG")
    IMAGE="nabeemdev/reach:${NEW_TAG}"

    echo ""
    ok "Updating backend image to: $IMAGE"

    write_nginx_config
    write_compose_file

    pull_with_dots "$IMAGE"
    $COMPOSE -f "$COMPOSE_FILE" up -d

    wait_for_backend

    save_env_file

    echo ""
    echo "┌──────────────────────────────────────────────┐"
    echo "│              Reach updated                   │"
    echo "└──────────────────────────────────────────────┘"
    echo ""
    echo "  Backend image:"
    echo "    $IMAGE"
    echo ""
    echo "  Preserved:"
    echo "    TOKEN_PEPPER"
    echo "    ADMIN_PASSWORD"
    echo "    Database volume"
    echo "    Tenant/users/API keys/agents"
    echo "    Retention settings"
    echo ""
    echo "  Logs:"
    echo "    $COMPOSE -f $COMPOSE_FILE logs -f"
    echo ""
    exit 0
    ;;
esac

# ===========================================================================
# Phase 1 - Collect all inputs
# ===========================================================================

echo ""
echo "┌──────────────────────────────────────────────┐"
echo "│              Reach Local Setup               │"
echo "└──────────────────────────────────────────────┘"
echo ""

# ---------------------------------------------------------------------------
# Dependencies (fail fast before asking anything)
# ---------------------------------------------------------------------------
MISSING=0
command -v docker   &>/dev/null || { warn "docker missing  →  https://docs.docker.com/get-docker/"; MISSING=1; }
command -v curl     &>/dev/null || { warn "curl missing";   MISSING=1; }
command -v openssl  &>/dev/null || { warn "openssl missing"; MISSING=1; }
command -v python3  &>/dev/null || { warn "python3 missing"; MISSING=1; }

# Reach CLI requires Python 3.10+ (mcp dependency); check early so the user
# knows before setup starts, not after waiting for Docker.
if command -v python3 &>/dev/null; then
  PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
  PY_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
  if [[ "$PY_MAJOR" -lt 3 || ( "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10 ) ]]; then
    warn "Python $PY_VERSION detected - Reach CLI requires Python 3.10+. CLI install will be skipped."
    CLI_PYTHON_OK=false
  else
    CLI_PYTHON_OK=true
  fi
fi

if docker compose version &>/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose &>/dev/null; then
  COMPOSE="docker-compose"
else
  warn "docker compose missing  →  included with Docker Desktop"
  MISSING=1
fi

[[ "$MISSING" -eq 0 ]] || fail "install missing dependencies and re-run."

HAS_CLOUDFLARED=false
HAS_NGROK=false
command -v cloudflared &>/dev/null && HAS_CLOUDFLARED=true
command -v ngrok       &>/dev/null && HAS_NGROK=true

# ---------------------------------------------------------------------------
# All prompts
# ---------------------------------------------------------------------------
echo "  Answer the questions below - setup runs automatically when done."
echo ""

# Backend image
IMAGE_TAG=$(prompt "Release tag" "latest")
IMAGE="nabeemdev/reach:${IMAGE_TAG}"

# Secrets
echo ""
echo "  Platform secrets (protect access to the Reach backend itself):"
read -rsp "  Admin password [generate]: " ADMIN_PASSWORD < /dev/tty; echo "" > /dev/tty
[[ -z "$ADMIN_PASSWORD" ]] && ADMIN_PASSWORD="$(openssl rand -hex 16)"

read -rsp "  Token pepper   [generate]: " TOKEN_PEPPER < /dev/tty; echo "" > /dev/tty
[[ -z "$TOKEN_PEPPER" ]] && TOKEN_PEPPER="$(openssl rand -hex 32)"
SESSION_SIGNING_KEY="$(openssl rand -hex 32)"  # dedicated session-token signing key (safe to rotate)

# Workspace
echo ""
echo "  Workspace (your first tenant - this is your day-to-day login):"
SETUP_TENANT=$(prompt "Tenant / workspace name" "default")
SETUP_USERNAME=$(prompt_username "Admin username" "admin")
SETUP_PASSWORD=$(prompt_password)

# Agent
echo ""
CREATE_AGENT=$(prompt_yes_no "Create an agent?" "Y")
AGENT_MODE="wild"
GRANT_DOCKER="false"
GRANT_SERVICE_MGMT="false"
if [[ "$CREATE_AGENT" == "true" ]]; then
  echo ""
  echo "    wild     - run any command"
  echo "    readonly - read-only commands only"
  echo "    approved - require approval for write commands"
  echo ""
  while true; do
    read -rp "  Agent mode [wild]: " AGENT_MODE < /dev/tty
    AGENT_MODE="${AGENT_MODE:-wild}"
    case "$AGENT_MODE" in
      wild|readonly|approved) break ;;
      *) echo "    Enter wild, readonly, or approved." ;;
    esac
  done
  GRANT_DOCKER=$(prompt_yes_no "Grant Docker access?" "N")
  GRANT_SERVICE_MGMT=$(prompt_yes_no "Grant systemctl access?" "N")
fi

# Tunnel
echo ""
TUNNEL_CMD=""
NGROK_DOMAIN=""
USE_TUNNEL=false
if [[ "$HAS_CLOUDFLARED" == true || "$HAS_NGROK" == true ]]; then
  echo "  Tunnel exposes the backend publicly so a remote agent can reach it."
  echo ""
  [[ "$HAS_CLOUDFLARED" == true ]] && echo "    cloudflared - no account needed"
  [[ "$HAS_NGROK" == true ]]       && echo "    ngrok       - static domains, requires account"
  echo "    none        - local only"
  echo ""
  while true; do
    read -rp "  Tunnel [cloudflared/ngrok/none]: " tunnel_choice < /dev/tty
    tunnel_choice="${tunnel_choice:-none}"
    case "$tunnel_choice" in
      cloudflared)
        [[ "$HAS_CLOUDFLARED" == true ]] || { echo "    cloudflared is not installed."; continue; }
        TUNNEL_CMD="cloudflared"; USE_TUNNEL=true; break ;;
      ngrok)
        [[ "$HAS_NGROK" == true ]] || { echo "    ngrok is not installed."; continue; }
        TUNNEL_CMD="ngrok"; USE_TUNNEL=true; break ;;
      none) break ;;
      *) echo "    Invalid choice." ;;
    esac
  done
else
  info "No tunnel tool found - backend will be local only."
fi

if [[ "$TUNNEL_CMD" == "ngrok" ]]; then
  NGROK_CFG="${XDG_CONFIG_HOME:-$HOME/.config}/ngrok/ngrok.yml"
  if ! grep -q "authtoken:" "$NGROK_CFG" 2>/dev/null; then
    echo ""
    read -rsp "  ngrok authtoken: " ngrok_token < /dev/tty; echo "" > /dev/tty
    if [[ -n "$ngrok_token" ]]; then
      ngrok config add-authtoken "$ngrok_token"
    else
      warn "No authtoken - skipping tunnel."
      USE_TUNNEL=false; TUNNEL_CMD=""
    fi
  fi
  if [[ "$TUNNEL_CMD" == "ngrok" ]]; then
    read -rp "  ngrok static domain [blank for random]: " NGROK_DOMAIN < /dev/tty
  fi
fi

# Retention (advanced - most users skip)
echo ""
ADVANCED=$(prompt_yes_no "Configure data retention? (default: 7/7/90/30 days)" "N")
if [[ "$ADVANCED" == "true" ]]; then
  echo ""
  echo "  How long to keep each record type before auto-deleting:"
  APPROVAL_RETENTION_DAYS=$(prompt "Approval retention days  (command allow/deny records)" "7")
  JOB_RETENTION_DAYS=$(prompt "Job retention days       (stdout, stderr, exit codes)" "7")
  AUDIT_RETENTION_DAYS=$(prompt "Audit retention days     (who did what and when)" "90")
  AGENT_HISTORY_RETENTION_DAYS=$(prompt "Agent history days       (heartbeat and status snapshots)" "30")
else
  APPROVAL_RETENTION_DAYS=7
  JOB_RETENTION_DAYS=7
  AUDIT_RETENTION_DAYS=90
  AGENT_HISTORY_RETENTION_DAYS=30
fi

# CLI
echo ""
INSTALL_CLI="false"
if command -v reach &>/dev/null; then
  info "Reach CLI already installed"
elif [[ "${CLI_PYTHON_OK:-true}" == "false" ]]; then
  info "Reach CLI skipped - Python 3.10+ required (you have $PY_VERSION)"
  INSTALL_CLI="false"
else
  INSTALL_CLI=$(prompt_yes_no "Install Reach CLI?" "Y")
fi

# ---------------------------------------------------------------------------
# Confirm and go
# ---------------------------------------------------------------------------
echo ""
echo "  ────────────────────────────────────────────"
echo "  Image:     $IMAGE"
echo "  Workspace: $SETUP_TENANT"
echo "  User:      $SETUP_USERNAME"
echo "  Agent:     ${CREATE_AGENT} $([ "$CREATE_AGENT" = "true" ] && echo "($AGENT_MODE mode)" || true)"
echo "  Tunnel:    ${TUNNEL_CMD:-none}"
echo "  ────────────────────────────────────────────"
echo ""
read -rp "  Start setup? [Y/n]: " confirm < /dev/tty
[[ "${confirm:-Y}" =~ ^[Nn]$ ]] && { echo "  Aborted."; exit 0; }
echo ""

# ===========================================================================
# Phase 2 - Execute
# ===========================================================================

write_nginx_config
write_compose_file

pull_with_dots "$IMAGE"
pull_with_dots "nginx:alpine"

ok "Starting backend..."
$COMPOSE -f "$COMPOSE_FILE" up -d

wait_for_backend

API_URL="http://localhost:${API_PORT}"

if [[ "$USE_TUNNEL" == true ]]; then
  PUBLIC_URL=""
  if [[ "$TUNNEL_CMD" == "cloudflared" ]]; then
    cloudflared tunnel --url "http://localhost:${API_PORT}" > /tmp/reach-tunnel.log 2>&1 &
    for i in $(seq 1 25); do
      PUBLIC_URL=$(grep -o 'https://[^ ]*\.trycloudflare\.com' /tmp/reach-tunnel.log 2>/dev/null | head -1 || true)
      [[ -n "$PUBLIC_URL" ]] && break
      sleep 2
    done
  elif [[ "$TUNNEL_CMD" == "ngrok" ]]; then
    if [[ -n "$NGROK_DOMAIN" ]]; then
      ngrok http "$API_PORT" --domain="$NGROK_DOMAIN" --log=stdout > /tmp/reach-tunnel.log 2>&1 &
      PUBLIC_URL="https://$NGROK_DOMAIN"
      sleep 3
    else
      ngrok http "$API_PORT" --log=stdout > /tmp/reach-tunnel.log 2>&1 &
      for i in $(seq 1 20); do
        PUBLIC_URL=$(curl -sf http://localhost:4040/api/tunnels 2>/dev/null \
          | python3 -c "import sys,json; print(next(x['public_url'] for x in json.load(sys.stdin)['tunnels'] if x['proto']=='https'))" 2>/dev/null || true)
        [[ -n "$PUBLIC_URL" ]] && break
        sleep 2
      done
    fi
  fi
  if [[ -n "$PUBLIC_URL" ]]; then
    API_URL="$PUBLIC_URL"
    ok "Tunnel: $API_URL"
  else
    warn "Could not get tunnel URL - falling back to local URL."
    tail -10 /tmp/reach-tunnel.log 2>/dev/null || true
  fi
fi

ok "Bootstrapping workspace..."

ADMIN_TOKEN=$(request_json POST "$API_URL/admin/login" \
  "$(mkjson password "$ADMIN_PASSWORD")" \
  -H "Content-Type: application/json" \
  | json_get "['token']")

TENANT_RESP=$(request_json POST "$API_URL/admin/tenants" \
  "$(mkjson name "$SETUP_TENANT")" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json")
TENANT_ID=$(echo "$TENANT_RESP" | json_get "['tenant_id']")

USER_RESP=$(request_json POST "$API_URL/admin/tenants/${TENANT_ID}/admin-users" \
  "$(mkjson username "$SETUP_USERNAME" role admin)" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json")

TEMP_PASS=$(echo "$USER_RESP" | python3 -c '
import sys,json; d=json.load(sys.stdin)
print(d.get("temp_password") or d.get("temporary_password") or d.get("password")
      or d.get("user",{}).get("temp_password") or d.get("user",{}).get("temporary_password") or "")
')
[[ -n "$TEMP_PASS" ]] || fail "Admin user created but no temp password returned. Response: $USER_RESP"

TEMP_TOKEN=$(request_json POST "$API_URL/tenant/login" \
  "$(mkjson tenant_name "$SETUP_TENANT" username "$SETUP_USERNAME" password "$TEMP_PASS")" \
  -H "Content-Type: application/json" | json_get "['token']")

request_json POST "$API_URL/tenant/me/password" \
  "$(mkjson current_password "$TEMP_PASS" new_password "$SETUP_PASSWORD")" \
  -H "Authorization: Bearer $TEMP_TOKEN" \
  -H "Content-Type: application/json" > /dev/null

USER_TOKEN=$(request_json POST "$API_URL/tenant/login" \
  "$(mkjson tenant_name "$SETUP_TENANT" username "$SETUP_USERNAME" password "$SETUP_PASSWORD")" \
  -H "Content-Type: application/json" | json_get "['token']")

API_KEY=$(request_json POST "$API_URL/tenant/api-tokens" \
  "$(mkjson name "default-cli")" \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H "Content-Type: application/json" | json_get "['token']")

AGENT_ID=""
INSTALL_AGENT=""
CLI_USE_CMD=""

if [[ "$CREATE_AGENT" == "true" ]]; then
  AGENT_RESP=$(request_json POST "$API_URL/tenant/agents" \
    "$(mkjson mode "$AGENT_MODE" grant_service_mgmt "$GRANT_SERVICE_MGMT" grant_docker "$GRANT_DOCKER")" \
    -H "Authorization: Bearer $USER_TOKEN" \
    -H "Content-Type: application/json")
  AGENT_ID=$(echo "$AGENT_RESP" | json_get "['agent_id']")
  INSTALL_AGENT=$(echo "$AGENT_RESP" | python3 -c '
import sys,json; d=json.load(sys.stdin)
print(d.get("commands",{}).get("agent",""))' 2>/dev/null || true)
  CLI_USE_CMD=$(echo "$AGENT_RESP" | python3 -c '
import sys,json; d=json.load(sys.stdin)
print(d.get("commands",{}).get("cli_use",""))' 2>/dev/null || true)
fi

ok "Workspace bootstrapped"

CLI_READY=false
CLI_LOGGED_IN=false

if command -v reach &>/dev/null; then
  CLI_READY=true
elif [[ "$INSTALL_CLI" == "true" ]]; then
  if [[ "${CLI_PYTHON_OK:-true}" == "false" ]]; then
    warn "Skipping CLI install - Python 3.10+ is required. Install it and run: pip install $CLI_WHEEL_URL"
  else
    _cli_installed=false
    if command -v uv &>/dev/null; then
      uv tool install "$CLI_WHEEL_URL" --force && _cli_installed=true || true
    fi
    if [[ "$_cli_installed" == false ]] && command -v pipx &>/dev/null; then
      pipx install "$CLI_WHEEL_URL" --force && _cli_installed=true || true
    fi
    if [[ "$_cli_installed" == false ]] && command -v pip3 &>/dev/null; then
      pip3 install "$CLI_WHEEL_URL" && _cli_installed=true || true
    fi
    if [[ "$_cli_installed" == false ]]; then
      python3 -m pip install "$CLI_WHEEL_URL" && _cli_installed=true || true
    fi
    if [[ "$_cli_installed" == false ]]; then
      warn "CLI install failed. Install manually: pip install $CLI_WHEEL_URL"
    fi
  fi
  command -v reach &>/dev/null && CLI_READY=true || true
fi

if [[ "$CLI_READY" == "true" ]]; then
  reach login --api-url "$API_URL" --api-key "$API_KEY"
  if [[ -n "$AGENT_ID" ]]; then
    [[ -n "$CLI_USE_CMD" ]] && $CLI_USE_CMD || reach agents use "$AGENT_ID"
  fi
  CLI_LOGGED_IN=true
  ok "CLI ready"
fi

save_env_file

echo ""
echo "┌──────────────────────────────────────────────────────────────┐"
echo "│                    Reach is ready                            │"
echo "└──────────────────────────────────────────────────────────────┘"
echo ""
echo "  API / Dashboard:"
echo "    $API_URL"
echo ""
echo "  Tenant:"
echo "    $SETUP_TENANT"
echo ""
echo "  Admin user:"
echo "    $SETUP_USERNAME"
echo ""
echo "  Agent:"
if [[ "$CREATE_AGENT" == "true" ]]; then
  echo "    Created"
  echo "    Mode:                $AGENT_MODE"
  echo "    Docker access:       $GRANT_DOCKER"
  echo "    Service management:  $GRANT_SERVICE_MGMT"
else
  echo "    Skipped"
fi
echo ""
echo "  Secrets saved to:"
echo "    $ENV_FILE"
echo ""

if [[ "$CREATE_AGENT" == "true" ]]; then
  echo "  ── Install agent on remote machine ─────────────────────────"
  echo ""

  if [[ -n "$INSTALL_AGENT" ]]; then
    echo "  Run this on the remote machine:"
    echo ""
    echo "    $INSTALL_AGENT"
    echo ""
  else
    echo "  Agent install command was not returned by the backend."
    echo ""
  fi
fi

echo "  ── Test ────────────────────────────────────────────────────"
echo ""

if [[ "$CLI_LOGGED_IN" == "true" ]]; then
  if [[ "$CREATE_AGENT" == "true" ]]; then
    echo "    reach exec -- hostname"
  else
    echo "    reach agents list"
  fi
else
  echo "    pip install $CLI_WHEEL_URL"
  echo "    reach login --api-url '$API_URL' --api-key '<API_KEY_FROM_$ENV_FILE>'"

  if [[ -n "$CLI_USE_CMD" ]]; then
    echo "    $CLI_USE_CMD"
  elif [[ -n "$AGENT_ID" ]]; then
    echo "    reach agents use $AGENT_ID"
  fi

  [[ -n "$AGENT_ID" ]] && echo "    reach exec -- hostname"
fi

echo ""
echo "  ── Manage local backend ────────────────────────────────────"
echo ""
echo "    Status:   ./scripts/local-setup.sh --status            # check if everything is running"
echo "    Update:   ./scripts/local-setup.sh --update            # updates backend image only"
echo "    Password: ./scripts/local-setup.sh --rotate-password   # rotate platform admin password"
echo "    Sessions: ./scripts/local-setup.sh --rotate-session-key # rotate session key (forces re-login)"
echo "    Stop:     ./scripts/local-setup.sh --down              # keeps DB/data"
echo "    Reset:    ./scripts/local-setup.sh --reset             # deletes local DB/data"
echo "    Purge:    ./scripts/local-setup.sh --purge             # deletes local setup, asks before CLI uninstall"
echo ""
echo "    Logs:    $COMPOSE -f $COMPOSE_FILE logs -f"
echo "    Restart: $COMPOSE -f $COMPOSE_FILE restart"
echo ""