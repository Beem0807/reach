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
#     curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash
#     ./scripts/local-setup.sh
#
#   Check if everything is running:
#     curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash -s -- --status
#     ./scripts/local-setup.sh --status
#
#   Register another agent against the running stack:
#     curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash -s -- --create-agent
#     ./scripts/local-setup.sh --create-agent
#
#   Update backend image only (keeps all data):
#     curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash -s -- --update
#     ./scripts/local-setup.sh --update
#
#   Stop backend, keep database:
#     curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash -s -- --down
#     ./scripts/local-setup.sh --down
#
#   Stop backend and delete database:
#     curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash -s -- --reset
#     ./scripts/local-setup.sh --reset
#
#   Remove everything, optionally uninstall CLI:
#     curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash -s -- --purge
#     ./scripts/local-setup.sh --purge
#
#   Rotate the platform admin password:
#     curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash -s -- --rotate-password
#     ./scripts/local-setup.sh --rotate-password
#
#   Rotate the session signing key (forces console re-login):
#     curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash -s -- --rotate-session-key
#     ./scripts/local-setup.sh --rotate-session-key
#
# Notes:
#
#   --status             checks containers, backend health, API key auth, and agent state.
#   --create-agent       registers another agent (host or k8s) via a tenant login; optionally
#                        starts a tunnel so the install command uses a public URL.
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
#   - jq
#   - python3 3.10+  (only for the optional Reach CLI install; setup itself does not need it)
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
# Resolve public tunnel hostnames over DoH. macOS curl's system resolver can fail
# on freshly-created *.trycloudflare.com names that a browser (which uses DoH)
# reaches fine; `curl --doh-url` sidesteps that. Not used for localhost. Only
# applied when this curl supports it (>= 7.62), so older curl still works.
DOH_URL="${DOH_URL:-https://cloudflare-dns.com/dns-query}"
DOH_ARGS=""
if curl --help all 2>/dev/null | grep -q -- '--doh-url'; then
  DOH_ARGS="--doh-url $DOH_URL"
fi

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

  # Force HTTP/1.1 (curl over HTTP/2 to a cloudflared quick tunnel often stalls),
  # and resolve public hostnames over DoH (the OS resolver can fail on fresh
  # *.trycloudflare.com names). Both are no-ops / skipped for localhost.
  local doh=""
  case "$url" in
    *localhost*|*127.0.0.1*) : ;;
    https://*) doh="$DOH_ARGS" ;;
  esac
  local code
  if [[ -n "$body" ]]; then
    code=$(curl -sS --http1.1 $doh -o "$tmp" -w "%{http_code}" -X "$method" "$url" "$@" -d "$body")
  else
    code=$(curl -sS --http1.1 $doh -o "$tmp" -w "%{http_code}" -X "$method" "$url" "$@")
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
  local hint

  # Show the default with the standard [Y/n] / [y/N] convention (capital = default).
  case "$default" in
    y|Y|yes|YES) hint="[Y/n]" ;;
    *)           hint="[y/N]" ;;
  esac

  while true; do
    read -rp "  $label $hint: " value < /dev/tty
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
    value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"  # lowercase (portable; macOS ships bash 3.2)
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

# Build a flat JSON object from key/value pairs using jq. "true"/"false" become
# JSON booleans; everything else is a JSON string (jq handles all escaping).
mkjson() {
  jq -nc '
    reduce range(0; ($ARGS.positional | length); 2) as $i
      ({}; .[$ARGS.positional[$i]] =
        ($ARGS.positional[$i + 1]
          | if . == "true" then true elif . == "false" then false else . end))
  ' --args "$@"
}

# Extract a value from JSON on stdin with a jq filter, e.g. json_get '.token'.
json_get() {
  jq -r "$1"
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

  # Defaults for older env files so downstream refs are safe.
  RELEASES_CHART_REPO="${RELEASES_CHART_REPO:-}"
  LOCAL_API_URL="${LOCAL_API_URL:-http://localhost:${API_PORT}}"
  PUBLIC_API_URL="${PUBLIC_API_URL:-}"
  API_URL="${API_URL:-$LOCAL_API_URL}"

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
  # Only emit RELEASES_CHART_REPO when set; an empty value would override the
  # backend's default (derived from RELEASES_S3_BASE) with an empty string.
  local chart_repo_env=""
  [[ -n "${RELEASES_CHART_REPO:-}" ]] && chart_repo_env="
      RELEASES_CHART_REPO: \"${RELEASES_CHART_REPO}\""
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
      AGENT_HISTORY_RETENTION_DAYS: "$AGENT_HISTORY_RETENTION_DAYS"${chart_repo_env}
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
LOCAL_API_URL=${LOCAL_API_URL:-http://localhost:${API_PORT}}
PUBLIC_API_URL=${PUBLIC_API_URL:-}
IMAGE=${IMAGE}
TOKEN_PEPPER=${TOKEN_PEPPER}
SESSION_SIGNING_KEY=${SESSION_SIGNING_KEY}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
APPROVAL_RETENTION_DAYS=${APPROVAL_RETENTION_DAYS}
JOB_RETENTION_DAYS=${JOB_RETENTION_DAYS}
AUDIT_RETENTION_DAYS=${AUDIT_RETENTION_DAYS}
AGENT_HISTORY_RETENTION_DAYS=${AGENT_HISTORY_RETENTION_DAYS}
RELEASES_CHART_REPO=${RELEASES_CHART_REPO:-}
TENANT_ID=${TENANT_ID:-}
TENANT_NAME=${SETUP_TENANT:-${TENANT_NAME:-}}
USERNAME=${SETUP_USERNAME:-${USERNAME:-}}
API_KEY=${API_KEY:-}
CREATE_AGENT=${CREATE_AGENT:-}
AGENT_ID=${AGENT_ID:-}
AGENT_TYPE=${AGENT_TYPE:-}
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

wait_for_public_api_login() {
  local url="$1"
  local login_body=""
  local login_code=""
  local health_code=""
  local login_tmp=""
  local health_tmp=""

  # Do not accept grep/binary-warning text as a URL.
  if [[ ! "$url" =~ ^https://[^[:space:]]+$ ]]; then
    warn "Invalid tunnel URL discovered: $url"
    return 1
  fi

  printf "  Waiting for the tunnel to become reachable (Cloudflare can take a minute or two)"

  login_tmp="$(mktemp)"
  health_tmp="$(mktemp)"

  # Cloudflare quick tunnels can take a couple of minutes to become globally
  # resolvable + routable. Wait patiently (~4 min of fast-failing probes) before
  # handing back to the caller's retry prompt.
  local max_attempts=120
  for i in $(seq 1 "$max_attempts"); do
    # First verify the tunnel can route to the backend at all. Force HTTP/1.1
    # (curl+HTTP/2 to a quick tunnel often stalls) and resolve over DoH (the OS
    # resolver can fail on the fresh hostname a browser reaches fine).
    health_code=$(curl -k -L -sS --http1.1 $DOH_ARGS --max-time 5 -o "$health_tmp" -w "%{http_code}" "${url}/health" 2>/dev/null || true)

    # Then verify the public URL can issue a real tenant login token.
    login_code=$(curl -k -L -sS --http1.1 $DOH_ARGS --max-time 8 -o "$login_tmp" -w "%{http_code}" \
      -X POST "${url}/tenant/login" \
      -H "Content-Type: application/json" \
      -d "$(mkjson tenant_name "$SETUP_TENANT" username "$SETUP_USERNAME" password "$SETUP_PASSWORD")" \
      2>/dev/null || true)

    login_body="$(cat "$login_tmp" 2>/dev/null || true)"

    if [[ "$login_code" == "200" ]] && echo "$login_body" | jq -e '.token and (.token | length > 0)' >/dev/null 2>&1; then
      rm -f "$login_tmp" "$health_tmp"
      printf "\n"
      ok "Public API is reachable: $url"
      return 0
    fi

    # Give a progress hint every ~20 seconds without spamming the terminal, so a
    # slow tunnel looks like it's still working rather than hung.
    if (( i % 10 == 0 )); then
      printf "(%ss elapsed, health:%s login:%s)" "$((i * 2))" "${health_code:-000}" "${login_code:-000}"
    else
      printf "."
    fi

    sleep 2
  done

  printf "\n"
  warn "Public API did not become ready in time: $url"
  warn "Last /health HTTP status: ${health_code:-unknown}"
  warn "Last /tenant/login HTTP status: ${login_code:-unknown}"

  if [[ -s "$health_tmp" ]]; then
    echo ""
    echo "  Last /health response:"
    sed 's/^/    /' "$health_tmp" | head -20 || true
  fi

  if [[ -s "$login_tmp" ]]; then
    echo ""
    echo "  Last /tenant/login response:"
    sed 's/^/    /' "$login_tmp" | head -20 || true
  fi

  echo ""
  echo "  Last cloudflared/ngrok log lines:"
  tail -20 /tmp/reach-tunnel.log 2>/dev/null | sed 's/^/    /' || true

  rm -f "$login_tmp" "$health_tmp"
  return 1
}

extract_trycloudflare_url() {
  local log_file="$1"

  # cloudflared logs can contain control characters; grep may otherwise print
  # "Binary file ... matches" instead of the actual URL. LC_ALL=C + grep -a
  # forces text-mode scanning.
  LC_ALL=C grep -aEo 'https://[^[:space:]]+\.trycloudflare\.com' "$log_file" 2>/dev/null     | head -1     | tr -d '\r'
}

rewrite_agent_commands_for_public_url() {
  # Agent commands are generated by the backend using the request host. Since
  # setup bootstraps through localhost for reliability, rewrite only the printed
  # install commands to use the public tunnel URL when one was discovered.
  [[ -n "${AGENT_API_URL:-}" ]] || return 0
  [[ -n "${BOOTSTRAP_API_URL:-}" ]] || return 0
  [[ "$AGENT_API_URL" == "$BOOTSTRAP_API_URL" ]] && return 0

  if [[ -n "${INSTALL_AGENT:-}" ]]; then
    INSTALL_AGENT="${INSTALL_AGENT//$BOOTSTRAP_API_URL/$AGENT_API_URL}"
  fi

  if [[ -n "${CLI_USE_CMD:-}" ]]; then
    CLI_USE_CMD="${CLI_USE_CMD//$BOOTSTRAP_API_URL/$AGENT_API_URL}"
  fi
}

start_public_tunnel() {
  PUBLIC_URL=""
  rm -f /tmp/reach-tunnel.log

  echo ""
  ok "Starting tunnel..."

  if [[ "$TUNNEL_CMD" == "cloudflared" ]]; then
    # Force HTTP/2 because QUIC quick tunnels can register and still flap briefly
    # with control-stream/datagram errors on some networks.
    cloudflared tunnel --protocol http2 --url "http://localhost:${API_PORT}" > /tmp/reach-tunnel.log 2>&1 &
    TUNNEL_PID=$!
    for i in $(seq 1 25); do
      PUBLIC_URL=$(extract_trycloudflare_url /tmp/reach-tunnel.log || true)
      [[ -n "$PUBLIC_URL" ]] && break
      sleep 2
    done
  elif [[ "$TUNNEL_CMD" == "ngrok" ]]; then
    if [[ -n "$NGROK_DOMAIN" ]]; then
      ngrok http "$API_PORT" --domain="$NGROK_DOMAIN" --log=stdout > /tmp/reach-tunnel.log 2>&1 &
      TUNNEL_PID=$!
      PUBLIC_URL="https://$NGROK_DOMAIN"
    else
      ngrok http "$API_PORT" --log=stdout > /tmp/reach-tunnel.log 2>&1 &
      TUNNEL_PID=$!
      for i in $(seq 1 20); do
        PUBLIC_URL=$(curl -sf http://localhost:4040/api/tunnels 2>/dev/null \
          | jq -r 'first(.tunnels[] | select(.proto == "https") | .public_url) // empty' 2>/dev/null || true)
        [[ -n "$PUBLIC_URL" ]] && break
        sleep 2
      done
    fi
  fi

  if [[ -n "$PUBLIC_URL" && "$PUBLIC_URL" =~ ^https://[^[:space:]]+$ ]]; then
    PUBLIC_API_URL="$PUBLIC_URL"
    ok "Public tunnel URL: $PUBLIC_API_URL"
    return 0
  fi

  warn "Could not get a valid tunnel URL."
  tail -10 /tmp/reach-tunnel.log 2>/dev/null || true
  return 1
}

# `--create-agent`: register another agent against an already-running local stack
# (e.g. when the first one was skipped because the tunnel was slow). Self-contained
# so it can run as a subcommand, reusing the shared login/tunnel/create helpers.
create_agent_subcommand() {
  load_env_file

  if ! curl -sf "${LOCAL_API_URL}/health" &>/dev/null; then
    fail "Local backend is not responding at ${LOCAL_API_URL}. Start it first (re-run setup, or 'docker compose up -d')."
  fi

  # Confirm which workspace + admin to act as (defaults come from setup, but let
  # them override in case the values changed or a different admin should be used).
  local tenant_name admin_user pw
  echo ""
  read -rp "  Workspace (tenant) name [${TENANT_NAME:-}]: " tenant_name < /dev/tty
  tenant_name="${tenant_name:-${TENANT_NAME:-}}"
  [[ -n "$tenant_name" ]] || fail "Workspace name is required."
  read -rp "  Admin username [${USERNAME:-}]: " admin_user < /dev/tty
  admin_user="${admin_user:-${USERNAME:-}}"
  [[ -n "$admin_user" ]] || fail "Admin username is required."

  # Password is never stored; read one to obtain a session token (the persisted
  # API key can't create agents - that needs a tenant login). Verify the login
  # up front so bad credentials fail before configuring the agent; wrong password
  # just re-prompts, and we only give up after several tries.
  local user_token="" attempts=0
  while true; do
    read -rsp "  Password for ${admin_user}@${tenant_name}: " pw < /dev/tty
    echo ""
    user_token=$(request_json POST "${LOCAL_API_URL}/tenant/login" \
      "$(mkjson tenant_name "$tenant_name" username "$admin_user" password "$pw")" \
      -H "Content-Type: application/json" 2>/dev/null | json_get '.token' 2>/dev/null || true)
    [[ -n "$user_token" && "$user_token" != "null" ]] && break
    attempts=$((attempts + 1))
    if (( attempts >= 3 )); then
      fail "Login failed after ${attempts} attempts. Double-check the workspace name and admin username too, then re-run."
    fi
    warn "Login failed - wrong password? Try again (${attempts}/3)."
  done

  echo ""
  ok "Signed in. Creating a new agent in workspace '${tenant_name}' as '${admin_user}'."

  # Type / mode / grants (defaults come from the last agent you created).
  local a_type a_mode g_docker="false" g_svc="false"
  echo ""
  echo "    host - a machine (Linux/macOS), installed via install.sh"
  echo "    k8s  - a Kubernetes cluster, installed via Helm"
  echo ""
  while true; do
    read -rp "  Agent type [host/k8s] (default: ${AGENT_TYPE:-host}): " a_type < /dev/tty
    a_type="${a_type:-${AGENT_TYPE:-host}}"
    case "$a_type" in host|k8s) break ;; *) echo "    Enter host or k8s." ;; esac
  done
  while true; do
    read -rp "  Agent mode [wild/readonly/approved] (default: ${AGENT_MODE:-wild}): " a_mode < /dev/tty
    a_mode="${a_mode:-${AGENT_MODE:-wild}}"
    case "$a_mode" in wild|readonly|approved) break ;; *) echo "    Enter wild, readonly, or approved." ;; esac
  done
  if [[ "$a_type" == "host" ]]; then
    g_docker=$(prompt_yes_no "Grant Docker access?" "N")
    g_svc=$(prompt_yes_no "Grant systemctl access?" "N")
  fi

  # wait_for_public_api_login logs in using these SETUP_* globals; in a subcommand
  # they are unset, so point them at the credentials we just used.
  SETUP_TENANT="$tenant_name"
  SETUP_USERNAME="$admin_user"
  SETUP_PASSWORD="$pw"

  # Pick the URL the agent will use to reach this backend. They may have replaced
  # their tunnel since setup, so let them confirm the saved one, paste a different
  # URL, start a fresh tunnel, or stay local. A chosen public URL is verified and
  # re-logged-in through so the generated install command embeds a URL that works.
  local target_url="$LOCAL_API_URL" target_token="$user_token"
  local has_cf=false has_ng=false
  command -v cloudflared &>/dev/null && has_cf=true
  command -v ngrok &>/dev/null && has_ng=true

  echo ""
  echo "  How will the agent reach this backend?"
  echo "    - paste a public URL (a tunnel/domain you already run)"
  { [[ "$has_cf" == true || "$has_ng" == true ]]; } && echo "    - type 'tunnel' to start a new one now"
  echo "    - blank or 'local' for this machine / local network"
  [[ -n "${PUBLIC_API_URL:-}" ]] && echo "    (saved from setup: ${PUBLIC_API_URL})"
  echo ""
  local answer
  read -rp "  URL${PUBLIC_API_URL:+ [${PUBLIC_API_URL}]}: " answer < /dev/tty
  answer="${answer:-${PUBLIC_API_URL:-local}}"

  case "$answer" in
    local|localhost|none)
      : # keep the local URL
      ;;
    tunnel)
      if [[ "$has_cf" != true && "$has_ng" != true ]]; then
        warn "No tunnel tool (cloudflared/ngrok) found - using the local URL."
      else
        local default_tool; default_tool=$([[ "$has_cf" == true ]] && echo cloudflared || echo ngrok)
        TUNNEL_CMD=""; NGROK_DOMAIN=""
        echo ""
        [[ "$has_cf" == true ]] && echo "    cloudflared - no account needed"
        [[ "$has_ng" == true ]] && echo "    ngrok       - requires account"
        echo ""
        local choice
        while true; do
          read -rp "  Tunnel [cloudflared/ngrok] (default: ${default_tool}): " choice < /dev/tty
          choice="${choice:-$default_tool}"
          case "$choice" in
            cloudflared) [[ "$has_cf" == true ]] && { TUNNEL_CMD=cloudflared; break; } || echo "    cloudflared not installed." ;;
            ngrok)       [[ "$has_ng" == true ]] && { TUNNEL_CMD=ngrok; break; }       || echo "    ngrok not installed." ;;
            *) echo "    Enter cloudflared or ngrok." ;;
          esac
        done
        if start_public_tunnel && wait_for_public_api_login "$PUBLIC_API_URL"; then
          local t
          t=$(request_json POST "${PUBLIC_API_URL}/tenant/login" \
            "$(mkjson tenant_name "$tenant_name" username "$admin_user" password "$pw")" \
            -H "Content-Type: application/json" 2>/dev/null | json_get '.token' 2>/dev/null || true)
          [[ -n "$t" && "$t" != "null" ]] && { target_url="$PUBLIC_API_URL"; target_token="$t"; }
        else
          warn "Tunnel not reachable - using the local URL instead."
        fi
      fi
      ;;
    https://*)
      local clean="${answer%/}"
      echo ""
      echo "  Verifying ${clean} ..."
      if wait_for_public_api_login "$clean"; then
        local t
        t=$(request_json POST "${clean}/tenant/login" \
          "$(mkjson tenant_name "$tenant_name" username "$admin_user" password "$pw")" \
          -H "Content-Type: application/json" 2>/dev/null | json_get '.token' 2>/dev/null || true)
        if [[ -n "$t" && "$t" != "null" ]]; then
          target_url="$clean"; target_token="$t"
        else
          warn "Could not log in through ${clean} - using the local URL instead."
        fi
      else
        warn "${clean} is not reachable - using the local URL instead."
      fi
      ;;
    *)
      warn "Unrecognized value '${answer}' (public URLs must start with https://) - using the local URL."
      ;;
  esac

  local resp agent_id install_cmd cli_use
  resp=$(request_json POST "${target_url}/tenant/agents" \
    "$(mkjson type "$a_type" mode "$a_mode" grant_service_mgmt "$g_svc" grant_docker "$g_docker")" \
    -H "Authorization: Bearer $target_token" \
    -H "Content-Type: application/json")
  agent_id=$(echo "$resp" | json_get '.agent_id')
  install_cmd=$(echo "$resp" | json_get '.commands.helm // .commands.agent // empty')
  cli_use=$(echo "$resp" | json_get '.commands.cli_use // empty')
  [[ -n "$agent_id" && "$agent_id" != "null" ]] || fail "Agent creation failed. Response: $resp"

  echo ""
  ok "Agent created: ${agent_id} (type=${a_type}, mode=${a_mode}) via ${target_url}"
  echo ""
  echo "  Run this on the target machine to install the agent:"
  echo ""
  echo "    ${install_cmd}"
  echo ""
  [[ -n "$cli_use" ]] && echo "  Select it in the CLI:  ${cli_use}"
  echo ""
}

# ---------------------------------------------------------------------------
# Subcommands (--status, --update, --rotate-password, --down, --reset, --purge, --create-agent)
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
      # compose --format json is JSONL (one object per line) or a single array;
      # -s slurp + flatten handles both, then pick this service's State.
      state=$($COMPOSE -f "$COMPOSE_FILE" ps --format json 2>/dev/null \
        | jq -rs --arg s "$svc" 'flatten | map(select(.Service == $s)) | (.[0].State // "missing")' \
        2>/dev/null || echo "unknown")
      [[ -z "$state" ]] && state="unknown"
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
      if curl -k -L -sf --http1.1 "$API_URL/health" &>/dev/null; then
        ok "public URL: $API_URL"
      else
        warn "public URL: $API_URL (not reachable - tunnel may have stopped)"
      fi
    fi

    # API key auth
    if [[ -n "${API_KEY:-}" && -n "${API_URL:-}" ]]; then
      me_resp=$(curl -sf -H "Authorization: Bearer $API_KEY" "$LOCAL_URL/me" 2>/dev/null || true)
      if [[ -n "$me_resp" ]]; then
        username=$(echo "$me_resp" | jq -r '.username // "?"' 2>/dev/null || echo "?")
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
        status=$(echo "$agent_resp" | jq -r '.status // "?"' 2>/dev/null || echo "?")
        hostname=$(echo "$agent_resp" | jq -r '.hostname // "not claimed"' 2>/dev/null || echo "?")
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

  --create-agent)
    [[ -f "$COMPOSE_FILE" ]] || fail "no local stack found at $COMPOSE_FILE. Run setup first."
    create_agent_subcommand
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
command -v jq       &>/dev/null || { warn "jq missing  →  https://jqlang.github.io/jq/download/"; MISSING=1; }

# python3 is NOT needed for setup (JSON is handled by jq). It is only used to
# install the Reach CLI (a Python package requiring 3.10+). If it's missing or
# too old, we skip the optional CLI install rather than failing setup.
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
else
  warn "python3 not found - Reach CLI install will be skipped (setup itself does not need it)."
  CLI_PYTHON_OK=false
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
AGENT_TYPE="host"
AGENT_MODE="wild"
GRANT_DOCKER="false"
GRANT_SERVICE_MGMT="false"
if [[ "$CREATE_AGENT" == "true" ]]; then
  echo ""
  echo "    host - a machine (Linux/macOS), installed via install.sh"
  echo "    k8s  - a Kubernetes cluster, installed via Helm"
  echo ""
  while true; do
    read -rp "  Agent type [host/k8s] (default: host): " AGENT_TYPE < /dev/tty
    AGENT_TYPE="${AGENT_TYPE:-host}"
    case "$AGENT_TYPE" in
      host|k8s) break ;;
      *) echo "    Enter host or k8s." ;;
    esac
  done
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
  # Docker / service-management grants are host-only; k8s access is governed by RBAC.
  if [[ "$AGENT_TYPE" == "host" ]]; then
    GRANT_DOCKER=$(prompt_yes_no "Grant Docker access?" "N")
    GRANT_SERVICE_MGMT=$(prompt_yes_no "Grant systemctl access?" "N")
  fi
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
    read -rp "  Tunnel [cloudflared/ngrok/none] (default: none): " tunnel_choice < /dev/tty
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

# Chart repo defaults to <RELEASES_S3_BASE>/charts/reach-agent. Self-hosting the
# Helm repo is rare, so it's an env override (RELEASES_CHART_REPO=…) rather than a
# prompt. Agent/chart versions are chosen per-agent in the console.
RELEASES_CHART_REPO="${RELEASES_CHART_REPO:-}"

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
echo "  Agent:     ${CREATE_AGENT} $([ "$CREATE_AGENT" = "true" ] && echo "($AGENT_TYPE, $AGENT_MODE mode)" || true)"
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

LOCAL_API_URL="http://localhost:${API_PORT}"
BOOTSTRAP_API_URL="$LOCAL_API_URL"
PUBLIC_API_URL=""
API_URL="$LOCAL_API_URL"
TUNNEL_REACHABLE=false
AGENT_CREATE_API_URL="$LOCAL_API_URL"
AGENT_CREATE_TOKEN=""
AGENT_SKIPPED_REASON=""

ok "Bootstrapping workspace through local backend..."

ADMIN_TOKEN=$(request_json POST "$BOOTSTRAP_API_URL/admin/login" \
  "$(mkjson password "$ADMIN_PASSWORD")" \
  -H "Content-Type: application/json" \
  | json_get '.token')

TENANT_RESP=$(request_json POST "$BOOTSTRAP_API_URL/admin/tenants" \
  "$(mkjson name "$SETUP_TENANT")" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json")
TENANT_ID=$(echo "$TENANT_RESP" | json_get '.tenant_id')

USER_RESP=$(request_json POST "$BOOTSTRAP_API_URL/admin/tenants/${TENANT_ID}/admin-users" \
  "$(mkjson username "$SETUP_USERNAME" role admin)" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json")

TEMP_PASS=$(echo "$USER_RESP" | json_get '.temp_password // .temporary_password // .password // .user.temp_password // .user.temporary_password // empty')
[[ -n "$TEMP_PASS" ]] || fail "Admin user created but no temp password returned. Response: $USER_RESP"

TEMP_TOKEN=$(request_json POST "$BOOTSTRAP_API_URL/tenant/login" \
  "$(mkjson tenant_name "$SETUP_TENANT" username "$SETUP_USERNAME" password "$TEMP_PASS")" \
  -H "Content-Type: application/json" | json_get '.token')

request_json POST "$BOOTSTRAP_API_URL/tenant/me/password" \
  "$(mkjson current_password "$TEMP_PASS" new_password "$SETUP_PASSWORD")" \
  -H "Authorization: Bearer $TEMP_TOKEN" \
  -H "Content-Type: application/json" > /dev/null

USER_TOKEN=$(request_json POST "$BOOTSTRAP_API_URL/tenant/login" \
  "$(mkjson tenant_name "$SETUP_TENANT" username "$SETUP_USERNAME" password "$SETUP_PASSWORD")" \
  -H "Content-Type: application/json" | json_get '.token')

API_KEY=$(request_json POST "$BOOTSTRAP_API_URL/tenant/api-tokens" \
  "$(mkjson name "default-cli")" \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H "Content-Type: application/json" | json_get '.token')

ok "Workspace bootstrapped"

# Install/login CLI immediately after local workspace bootstrap. Tunnel and
# agent creation are handled after this so tunnel flakiness never blocks the
# core local setup experience.
AGENT_ID=""
INSTALL_AGENT=""
CLI_USE_CMD=""
TUNNEL_REACHABLE=false
AGENT_CREATE_API_URL="$LOCAL_API_URL"
AGENT_CREATE_TOKEN="$USER_TOKEN"
AGENT_SKIPPED_REASON=""
TUNNEL_PID=""

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
  if reach login --api-url "$LOCAL_API_URL" --api-key "$API_KEY"; then
    CLI_LOGGED_IN=true
    ok "CLI login ready"
  else
    warn "CLI login did not update the default profile. It may already exist; keeping existing CLI profile."
  fi

  ok "CLI ready"
fi

create_agent_with_current_target() {
  [[ -n "$AGENT_CREATE_TOKEN" ]] || AGENT_CREATE_TOKEN="$USER_TOKEN"

  # The bootstrap agent always installs the latest version; pin a specific
  # version per-agent in the console at create time if you need to.
  AGENT_RESP=$(request_json POST "$AGENT_CREATE_API_URL/tenant/agents" \
    "$(mkjson type "$AGENT_TYPE" mode "$AGENT_MODE" grant_service_mgmt "$GRANT_SERVICE_MGMT" grant_docker "$GRANT_DOCKER")" \
    -H "Authorization: Bearer $AGENT_CREATE_TOKEN" \
    -H "Content-Type: application/json")
  AGENT_ID=$(echo "$AGENT_RESP" | json_get '.agent_id')
  # host agents return commands.agent (install.sh); k8s agents return commands.helm.
  INSTALL_AGENT=$(echo "$AGENT_RESP" | json_get '.commands.helm // .commands.agent // empty')
  CLI_USE_CMD=$(echo "$AGENT_RESP" | json_get '.commands.cli_use // empty')
  ok "Agent created using: $AGENT_CREATE_API_URL"
}

maybe_select_cli_agent() {
  [[ "$CLI_READY" == "true" ]] || return 0
  [[ -n "$AGENT_ID" ]] || return 0

  if [[ -n "$CLI_USE_CMD" ]]; then
    $CLI_USE_CMD || warn "Could not set default CLI agent with returned command."
  else
    reach agents use "$AGENT_ID" || warn "Could not set default CLI agent."
  fi
}

# Agent creation happens after CLI/local setup. If a tunnel is required, the
# user gets an interactive retry loop instead of a hard failure.
if [[ "$CREATE_AGENT" == "true" ]]; then
  if [[ "$USE_TUNNEL" == true ]]; then
    while [[ -z "$AGENT_ID" ]]; do
      # On retry, stop the previous quick-tunnel process before starting a new
      # one so we do not leave multiple cloudflared/ngrok processes running.
      if [[ -n "${TUNNEL_PID:-}" ]] && kill -0 "$TUNNEL_PID" 2>/dev/null; then
        kill "$TUNNEL_PID" 2>/dev/null || true
        sleep 1
      fi

      if start_public_tunnel && wait_for_public_api_login "$PUBLIC_API_URL"; then
        TUNNEL_REACHABLE=true
        API_URL="$PUBLIC_API_URL"
        ok "Tunnel: $PUBLIC_API_URL"

        # Re-login through the public URL before agent creation so the backend
        # generates install commands with the public host, not localhost.
        AGENT_CREATE_TOKEN=$(request_json POST "$PUBLIC_API_URL/tenant/login" \
          "$(mkjson tenant_name "$SETUP_TENANT" username "$SETUP_USERNAME" password "$SETUP_PASSWORD")" \
          -H "Content-Type: application/json" | json_get '.token')
        AGENT_CREATE_API_URL="$PUBLIC_API_URL"
        create_agent_with_current_target
        maybe_select_cli_agent
        break
      fi

      warn "Public tunnel is not reachable enough to create the agent safely."
      warn "Local backend, workspace, API key, and CLI setup are complete."

      retry_agent=$(prompt_yes_no "Retry tunnel check and agent creation now?" "Y")
      if [[ "$retry_agent" != "true" ]]; then
        warn "Agent creation skipped."
        AGENT_SKIPPED_REASON="Public tunnel was not reachable. User skipped retry."
        API_URL="$LOCAL_API_URL"
        break
      fi
    done
  else
    AGENT_CREATE_API_URL="$LOCAL_API_URL"
    AGENT_CREATE_TOKEN="$USER_TOKEN"
    create_agent_with_current_target
    maybe_select_cli_agent
    API_URL="$LOCAL_API_URL"
  fi
fi

# Prefer the public URL in saved metadata only when it is actually usable and
# the agent was created through it. Otherwise keep the local URL as default.
if [[ -n "$PUBLIC_API_URL" && "$TUNNEL_REACHABLE" == "true" ]]; then
  API_URL="$PUBLIC_API_URL"
else
  API_URL="$LOCAL_API_URL"
fi

save_env_file

echo ""
echo "┌──────────────────────────────────────────────────────────────┐"
echo "│                    Reach is ready                            │"
echo "└──────────────────────────────────────────────────────────────┘"
echo ""
echo "  API / Dashboard:"
echo "    Local:  $LOCAL_API_URL"
if [[ -n "${PUBLIC_API_URL:-}" ]]; then
  echo "    Public: $PUBLIC_API_URL"
  if [[ "$TUNNEL_REACHABLE" != "true" ]]; then
    echo "    Note:   Public tunnel was discovered but public API login was not stable during setup."
  fi
fi
echo ""
echo "  Tenant:"
echo "    $SETUP_TENANT"
echo ""
echo "  Admin user:"
echo "    $SETUP_USERNAME"
echo ""
echo "  Agent:"
if [[ -n "$AGENT_ID" ]]; then
  echo "    Created"
  echo "    Mode:                $AGENT_MODE"
  echo "    Docker access:       $GRANT_DOCKER"
  echo "    Service management:  $GRANT_SERVICE_MGMT"
  echo "    Created via:         $AGENT_CREATE_API_URL"
elif [[ "$CREATE_AGENT" == "true" ]]; then
  echo "    Skipped"
  [[ -n "${AGENT_SKIPPED_REASON:-}" ]] && echo "    Reason:              $AGENT_SKIPPED_REASON"
else
  echo "    Skipped"
fi
echo ""
echo "  Secrets saved to:"
echo "    $ENV_FILE"
echo ""

if [[ -n "$AGENT_ID" ]]; then
  if [[ "$AGENT_TYPE" == "k8s" ]]; then
    echo "  ── Install agent on your Kubernetes cluster ────────────────"
  else
    echo "  ── Install agent on a host machine ─────────────────────────"
  fi
  echo ""

  if [[ -n "$INSTALL_AGENT" ]]; then
    if [[ "$AGENT_TYPE" == "k8s" ]]; then
      echo "  Run this against your cluster (from the repo root):"
    else
      echo "  Run this on the host machine:"
    fi
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
  if [[ -n "$AGENT_ID" ]]; then
    echo "    reach exec -- hostname"
  else
    echo "    reach agents list"
  fi
else
  echo "    pip install $CLI_WHEEL_URL"
  echo "    reach login --api-url '$LOCAL_API_URL' --api-key '<API_KEY_FROM_$ENV_FILE>'"

  if [[ -n "$CLI_USE_CMD" ]]; then
    echo "    $CLI_USE_CMD"
  elif [[ -n "$AGENT_ID" ]]; then
    echo "    reach agents use $AGENT_ID"
  fi

  [[ -n "$AGENT_ID" ]] && echo "    reach exec -- hostname"
fi

echo ""

# Show management commands the way the user actually invoked us: the local script
# path when run from a checkout, otherwise the curl form (a `curl … | bash`
# install has no ./scripts/local-setup.sh on disk to re-run).
_setup_url="${RELEASES_S3_BASE:-https://reach-releases.s3.amazonaws.com}/local-setup.sh"
if [[ -f "$0" && "$0" == *local-setup.sh ]]; then
  SETUP_CMD="$0"
else
  SETUP_CMD="curl -fsSL $_setup_url | bash -s --"
fi

if [[ "$CREATE_AGENT" == "true" && -z "$AGENT_ID" && "$USE_TUNNEL" == true ]]; then
  echo "  ── Create agent later ──────────────────────────────────────"
  echo ""
  echo "    The local workspace is ready, but no agent was created because the tunnel was not healthy."
  echo "    Once your tunnel is reachable, register one with:"
  echo ""
  echo "      $SETUP_CMD --create-agent"
  echo ""
  echo "    It confirms your workspace/admin, (re)starts or reuses a public URL, then prints the install command."
  echo ""
fi

echo "  ── Manage local backend ────────────────────────────────────"
echo ""
echo "    Re-run with a flag:  $SETUP_CMD <flag>"
echo ""
echo "      --status               check if everything is running"
echo "      --create-agent         register another agent (host or k8s)"
echo "      --update               update the backend image only"
echo "      --rotate-password      rotate platform admin password"
echo "      --rotate-session-key   rotate session key (forces re-login)"
echo "      --down                 stop containers, keep DB/data"
echo "      --reset                delete local DB/data"
echo "      --purge                delete local setup (asks before CLI uninstall)"
echo ""
echo "    Logs:    $COMPOSE -f $COMPOSE_FILE logs -f"
echo "    Restart: $COMPOSE -f $COMPOSE_FILE restart"
echo ""