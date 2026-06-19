#!/usr/bin/env bash
# Start the reach backend locally using Docker.
# Can optionally expose it publicly via cloudflared or ngrok.
#
# Usage:
#   curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash
#   ./scripts/local-setup.sh
#   ./scripts/local-setup.sh --down   # stop and remove the local stack

set -euo pipefail

WORK_DIR="$HOME/.reach/local"
COMPOSE_FILE="$WORK_DIR/docker-compose.yml"
API_PORT=8000

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
ok()   { printf "  [OK]      %s\n" "$1"; }
miss() { printf "  [MISSING] %s\n" "$1"; }
info() { printf "  [INFO]    %s\n" "$1"; }

# ---------------------------------------------------------------------------
# --down: tear down the local stack
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--down" ]]; then
  if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "Error: no local stack found at $COMPOSE_FILE"
    exit 1
  fi
  if docker compose version &>/dev/null 2>&1; then
    COMPOSE="docker compose"
  else
    COMPOSE="docker-compose"
  fi
  echo "==> Stopping reach local backend..."
  $COMPOSE -f "$COMPOSE_FILE" down
  echo "==> Done."
  exit 0
fi

# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------
echo ""
echo "==> Checking dependencies..."

MISSING=0

if command -v docker &>/dev/null; then
  ok "docker"
else
  miss "docker  →  https://docs.docker.com/get-docker/"
  MISSING=1
fi

if docker compose version &>/dev/null 2>&1; then
  COMPOSE="docker compose"
  ok "docker compose"
elif command -v docker-compose &>/dev/null; then
  COMPOSE="docker-compose"
  ok "docker-compose"
else
  miss "docker compose  →  included with Docker Desktop"
  MISSING=1
fi

if command -v openssl &>/dev/null; then
  ok "openssl"
else
  miss "openssl  →  required to generate secure tokens"
  MISSING=1
fi

if command -v curl &>/dev/null; then
  ok "curl"
else
  miss "curl"
  MISSING=1
fi

# Optional tunnel tools
HAS_CLOUDFLARED=false
HAS_NGROK=false
if command -v cloudflared &>/dev/null; then
  ok "cloudflared (tunnel available)"
  HAS_CLOUDFLARED=true
fi
if command -v ngrok &>/dev/null; then
  ok "ngrok (tunnel available)"
  HAS_NGROK=true
fi
if [[ "$HAS_CLOUDFLARED" == false && "$HAS_NGROK" == false ]]; then
  info "no tunnel tool found - backend will be local only"
  info "install cloudflared: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
  info "install ngrok:       https://ngrok.com/download"
fi

if [[ $MISSING -eq 1 ]]; then
  echo ""
  echo "Error: missing required dependencies above. Install them and re-run."
  exit 1
fi

# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------
echo ""
echo "==> Configuration"
echo ""

read -rp "  Release tag  [latest]: " IMAGE_TAG < /dev/tty
IMAGE="nabeemdev/reach:${IMAGE_TAG:-latest}"
echo "    Using image: $IMAGE"

echo ""
read -rp "  TOKEN_PEPPER (leave blank to generate): " TOKEN_PEPPER < /dev/tty
if [[ -z "$TOKEN_PEPPER" ]]; then
  TOKEN_PEPPER=$(openssl rand -hex 32)
  echo "    Generated TOKEN_PEPPER."
fi

echo ""
read -rp "  ADMIN_TOKEN  (leave blank to generate): " ADMIN_TOKEN < /dev/tty
if [[ -z "$ADMIN_TOKEN" ]]; then
  ADMIN_TOKEN=$(openssl rand -hex 32)
  echo "    Generated ADMIN_TOKEN."
fi

echo ""
echo "  APPROVAL_RETENTION_DAYS: how many days to keep denied/expired approval records"
echo "  before the daily cleanup deletes them (default: 7)."
read -rp "  APPROVAL_RETENTION_DAYS [7]: " APPROVAL_RETENTION_DAYS < /dev/tty
APPROVAL_RETENTION_DAYS="${APPROVAL_RETENTION_DAYS:-7}"

TUNNEL_CMD=""
NGROK_DOMAIN=""
USE_TUNNEL=false

if [[ "$HAS_CLOUDFLARED" == true && "$HAS_NGROK" == true ]]; then
  echo ""
  echo "    cloudflared - no account needed (quick tunnel)"
  echo "    ngrok       - requires free account + authtoken (supports static domains)"
  while true; do
    read -rp "  Tunnel tool - cloudflared / ngrok / none [none]: " tunnel_choice < /dev/tty
    case "$tunnel_choice" in
      cloudflared) TUNNEL_CMD="cloudflared"; USE_TUNNEL=true; break ;;
      ngrok)       TUNNEL_CMD="ngrok";       USE_TUNNEL=true; break ;;
      none|"")     break ;;
      *)           echo "    Invalid choice. Enter cloudflared, ngrok, or none." ;;
    esac
  done
elif [[ "$HAS_CLOUDFLARED" == true ]]; then
  echo ""
  read -rp "  Expose publicly via cloudflared? [y/N]: " yn < /dev/tty
  if [[ "$yn" == "y" || "$yn" == "Y" ]]; then
    TUNNEL_CMD="cloudflared"
    USE_TUNNEL=true
  fi
elif [[ "$HAS_NGROK" == true ]]; then
  echo ""
  echo "    Note: ngrok requires a free account and authtoken (ngrok config add-authtoken <token>)"
  read -rp "  Expose publicly via ngrok? [y/N]: " yn < /dev/tty
  if [[ "$yn" == "y" || "$yn" == "Y" ]]; then
    TUNNEL_CMD="ngrok"
    USE_TUNNEL=true
  fi
else
  echo ""
  read -rp "  No tunnel available. Proceed with local-only backend? [Y/n]: " yn < /dev/tty
  if [[ "$yn" == "n" || "$yn" == "N" ]]; then
    echo "Aborting. Install cloudflared or ngrok and re-run."
    exit 0
  fi
fi

if [[ "$TUNNEL_CMD" == "ngrok" ]]; then
  # Check if authtoken is configured
  NGROK_CFG="${XDG_CONFIG_HOME:-$HOME/.config}/ngrok/ngrok.yml"
  if ! grep -q "authtoken:" "$NGROK_CFG" 2>/dev/null; then
    echo ""
    echo "    ngrok requires a free account and authtoken."
    echo "    Sign up at https://ngrok.com → Dashboard → Your Authtoken"
    echo ""
    read -rp "  Paste your ngrok authtoken (leave blank to skip tunnel): " ngrok_token < /dev/tty
    if [[ -n "$ngrok_token" ]]; then
      ngrok config add-authtoken "$ngrok_token"
    else
      echo ""
      if [[ "$HAS_CLOUDFLARED" == true ]]; then
        read -rp "  Use cloudflared instead (no account needed)? [Y/n]: " yn < /dev/tty
        if [[ "$yn" != "n" && "$yn" != "N" ]]; then
          TUNNEL_CMD="cloudflared"
        else
          read -rp "  Proceed without tunnel (local-only)? [Y/n]: " yn < /dev/tty
          if [[ "$yn" == "n" || "$yn" == "N" ]]; then
            echo "Aborting. Set up your ngrok authtoken and re-run."
            exit 0
          fi
          USE_TUNNEL=false
          TUNNEL_CMD=""
        fi
      else
        read -rp "  Proceed without tunnel (local-only)? [Y/n]: " yn < /dev/tty
        if [[ "$yn" == "n" || "$yn" == "N" ]]; then
          echo "Aborting. Set up your ngrok authtoken and re-run."
          exit 0
        fi
        USE_TUNNEL=false
        TUNNEL_CMD=""
      fi
    fi
  fi
fi

if [[ "$TUNNEL_CMD" == "ngrok" ]]; then
  echo ""
  read -rp "  ngrok static domain (leave blank for random URL): " NGROK_DOMAIN < /dev/tty
fi

# ---------------------------------------------------------------------------
# Write nginx config and compose file
# ---------------------------------------------------------------------------
mkdir -p "$WORK_DIR"

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
      TOKEN_PEPPER: "${TOKEN_PEPPER}"
      ADMIN_TOKEN: "${ADMIN_TOKEN}"
      DATABASE_URL: postgresql://reach:reach@db:5432/reach
      STORAGE_BACKEND: postgres
      APPROVAL_RETENTION_DAYS: "${APPROVAL_RETENTION_DAYS:-7}"
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

# ---------------------------------------------------------------------------
# Pull and start
# ---------------------------------------------------------------------------
echo ""
echo "==> Pulling images..."
docker pull "$IMAGE" -q
docker pull nginx:alpine -q

echo "==> Starting backend..."
$COMPOSE -f "$COMPOSE_FILE" up -d

echo "==> Waiting for backend to be healthy..."
for i in $(seq 1 30); do
  if curl -sf "http://localhost:${API_PORT}/health" &>/dev/null; then
    break
  fi
  if [[ $i -eq 30 ]]; then
    echo "Error: backend did not become healthy in time."
    echo "Logs:"
    $COMPOSE -f "$COMPOSE_FILE" logs backend
    exit 1
  fi
  sleep 2
done

API_URL="http://localhost:${API_PORT}"

# ---------------------------------------------------------------------------
# Optional tunnel
# ---------------------------------------------------------------------------
TUNNEL_PID=""
if [[ "$USE_TUNNEL" == true ]]; then
  echo "==> Starting $TUNNEL_CMD tunnel..."

  if [[ "$TUNNEL_CMD" == "cloudflared" ]]; then
    cloudflared tunnel --url "http://localhost:${API_PORT}" > /tmp/reach-tunnel.log 2>&1 &
    TUNNEL_PID=$!
    echo "    Waiting for tunnel URL..."
    for i in $(seq 1 20); do
      PUBLIC_URL=$(grep -o 'https://[^ ]*\.trycloudflare\.com' /tmp/reach-tunnel.log 2>/dev/null | head -1 || true)
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
      echo "    Waiting for tunnel URL..."
      for i in $(seq 1 15); do
        PUBLIC_URL=$(curl -sf http://localhost:4040/api/tunnels 2>/dev/null \
          | python3 -c "import sys,json; t=json.load(sys.stdin)['tunnels']; print(next(x['public_url'] for x in t if x['proto']=='https'))" 2>/dev/null || true)
        [[ -n "$PUBLIC_URL" ]] && break
        sleep 2
      done
    fi
  fi

  if [[ -n "${PUBLIC_URL:-}" ]]; then
    API_URL="$PUBLIC_URL"
    echo "    Public URL: $API_URL"
  else
    echo "    Warning: could not detect public URL. Tunnel logs:"
    echo ""
    tail -20 /tmp/reach-tunnel.log 2>/dev/null || echo "    (no log file found)"
    echo ""
  fi
fi

# ---------------------------------------------------------------------------
# Save config
# ---------------------------------------------------------------------------
cat > "$WORK_DIR/env" <<EOF
TOKEN_PEPPER=${TOKEN_PEPPER}
ADMIN_TOKEN=${ADMIN_TOKEN}
API_URL=${API_URL}
IMAGE=${IMAGE}
APPROVAL_RETENTION_DAYS=${APPROVAL_RETENTION_DAYS}
EOF

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "┌─────────────────────────────────────────────────────────────┐"
echo "│              reach local backend is running                 │"
echo "└─────────────────────────────────────────────────────────────┘"
echo ""
echo "  API URL:      $API_URL"
echo "  Image:        $IMAGE"
echo "  ADMIN_TOKEN:  $ADMIN_TOKEN"
echo "  TOKEN_PEPPER: $TOKEN_PEPPER"
echo ""
echo "  (saved to $WORK_DIR/env)"
echo ""
echo "  ── Install the CLI ─────────────────────────────────────────"
echo ""
if command -v reach &>/dev/null; then
  echo "  reach is already installed."
else
  echo "  pip install https://reach-releases.s3.amazonaws.com/cli/latest/reach-0.1.0-py3-none-any.whl"
fi
echo ""
echo "  ── Next steps ──────────────────────────────────────────────"
echo ""
echo "  1. Create a tenant:"
echo "     curl -s -X POST $API_URL/admin/tenants \\"
echo "       -H 'Authorization: Bearer $ADMIN_TOKEN' | python3 -m json.tool"
echo ""
echo "  2. Create a user:"
echo "     curl -s -X POST $API_URL/admin/tenants/<tenant_id>/users \\"
echo "       -H 'Authorization: Bearer $ADMIN_TOKEN' \\"
echo "       -H 'Content-Type: application/json' \\"
echo "       -d '{\"name\": \"alice\"}' | python3 -m json.tool"
echo ""
echo "  3. Create an agent:"
echo "     curl -s -X POST $API_URL/admin/agents \\"
echo "       -H 'Authorization: Bearer $ADMIN_TOKEN' \\"
echo "       -H 'Content-Type: application/json' \\"
echo "       -d '{\"tenant_id\": \"<tenant_id>\"}' | python3 -m json.tool"
echo ""
echo "  4. Log in and set default agent:"
echo "     reach login --api-url '$API_URL' --token <user-token>"
echo "     reach agents use <agent_id>"
echo ""
echo "  5. Install the agent on the remote machine."
echo "     The step 3 response includes a 'commands' field with the ready-to-run install command."
echo ""
echo "  6. Test it:"
echo "     reach exec -- hostname"
echo ""
echo "  ── Manage ──────────────────────────────────────────────────"
echo ""
echo "  Logs:    $COMPOSE -f $COMPOSE_FILE logs -f"
echo "  Stop:    curl -fsSL https://reach-releases.s3.amazonaws.com/local-setup.sh | bash -s -- --down"
echo "  Restart: $COMPOSE -f $COMPOSE_FILE restart"
echo ""
