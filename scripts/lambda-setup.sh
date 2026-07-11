#!/usr/bin/env bash
# Deploy or manage the reach backend on AWS Lambda + DynamoDB via CloudFormation.
#
# Usage:
#
#   Fresh deploy:
#     curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash
#     ./scripts/lambda-setup.sh
#
#   Update stack (new release tag and/or password rotation):
#     curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash -s -- --update
#     ./scripts/lambda-setup.sh --update
#
#   Delete stack (data retained in DynamoDB):
#     curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash -s -- --down
#     ./scripts/lambda-setup.sh --down
#
# Notes:
#
#   --update  deploys a new release tag and/or rotates ADMIN_PASSWORD.
#             TOKEN_PEPPER and all data (tenants, users, agents) are preserved.
#   --down    deletes the CloudFormation stack. DynamoDB tables use
#             DeletionPolicy: Retain - all reach data is preserved.
#             Delete the tables manually in the AWS console to remove all data.
#
# Requirements:
#
#   - aws cli
#   - curl
#   - jq
#   - openssl
#   - python3 3.10+  (only for the optional Reach CLI install; deploy itself does not need it)

set -euo pipefail

S3_BASE="https://reach-releases.s3.amazonaws.com"
CLI_WHEEL_URL="https://reach-releases.s3.amazonaws.com/cli/latest/reach-0.1.0-py3-none-any.whl"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
ok()   { printf "  [OK]      %s\n" "$1"; }
miss() { printf "  [MISSING] %s\n" "$1"; }
info() { printf "  [INFO]    %s\n" "$1"; }
warn() { printf "  [WARN]    %s\n" "$1"; }
fail() { printf "  [ERROR]   %s\n" "$1"; exit 1; }

trap 'echo ""; echo "[ERROR] Setup failed at line $LINENO"; exit 1' ERR

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
      y|Y|yes|YES) echo "true";  return ;;
      n|N|no|NO)   echo "false"; return ;;
      *) echo "    Enter y or n." > /dev/tty ;;
    esac
  done
}

prompt_password() {
  local p1="" p2=""
  while true; do
    read -rsp "  Password: " p1 < /dev/tty; echo "" > /dev/tty
    [[ -z "$p1" ]] && { echo "    Password cannot be empty." > /dev/tty; continue; }
    [[ ${#p1} -lt 8 ]] && { echo "    Password must be at least 8 characters." > /dev/tty; continue; }
    read -rsp "  Confirm password: " p2 < /dev/tty; echo "" > /dev/tty
    [[ "$p1" == "$p2" ]] && { echo "$p1"; return; }
    echo "    Passwords do not match. Try again." > /dev/tty
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

request_json() {
  local method="$1" url="$2" body="${3:-}"
  shift 3 || true
  local tmp; tmp="$(mktemp)"
  local code
  if [[ -n "$body" ]]; then
    code=$(curl -sS -o "$tmp" -w "%{http_code}" -X "$method" "$url" "$@" -d "$body")
  else
    code=$(curl -sS -o "$tmp" -w "%{http_code}" -X "$method" "$url" "$@")
  fi
  if [[ "$code" -lt 200 || "$code" -ge 300 ]]; then
    echo "" >&2
    echo "[ERROR] API request failed: $method $url (HTTP $code)" >&2
    cat "$tmp" >&2; echo "" >&2
    rm -f "$tmp"; exit 1
  fi
  cat "$tmp"; rm -f "$tmp"
}

prompt_aws() {
  read -rp "  AWS profile  [blank to use env]: " _aws_profile_in < /dev/tty
  AWS_PROFILE="${_aws_profile_in:-}"
  [[ -n "$AWS_PROFILE" ]] && export AWS_PROFILE

  AWS_REGION=$(prompt "AWS region" "us-east-1")

  if [[ "$AWS_REGION" != "us-east-1" ]]; then
    echo ""
    fail "only us-east-1 is supported at this time."
  fi
}

verify_aws() {
  echo ""
  echo "==> Verifying AWS credentials (profile: ${AWS_PROFILE:-<env>}, region: $AWS_REGION)..."
  if ! aws sts get-caller-identity --region "$AWS_REGION" &>/dev/null; then
    if [[ -n "${AWS_PROFILE:-}" ]]; then
      fail "could not authenticate. Check profile '${AWS_PROFILE}': aws configure --profile ${AWS_PROFILE}"
    else
      fail "could not authenticate. Export AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or run: aws configure"
    fi
  fi
  CALLER=$(aws sts get-caller-identity --region "$AWS_REGION" --query "Account" --output text 2>/dev/null)
  ok "authenticated (account: $CALLER)"
}

deploy_ui() {
  local release_tag="$1" bucket="$2" dist_id="$3"
  local ui_tmp; ui_tmp=$(mktemp -d)
  local tarball="${S3_BASE}/lambda/${release_tag}/ui.tar.gz"
  if curl -fsSL "$tarball" | tar -xz -C "$ui_tmp" 2>/dev/null; then
    aws s3 sync "$ui_tmp/" "s3://$bucket/ui/" --delete --region "$AWS_REGION"
    ok "UI deployed"
    if [[ -n "$dist_id" && "$dist_id" != "None" ]]; then
      echo "==> Invalidating CloudFront cache (/ui/*)..."
      aws cloudfront create-invalidation \
        --distribution-id "$dist_id" \
        --paths "/ui/*" >/dev/null
      ok "Cache invalidated"
    fi
  else
    warn "could not fetch UI assets from $tarball"
  fi
  rm -rf "$ui_tmp"
}

stack_output() {
  local key="$1"
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='${key}'].OutputValue" \
    --output text
}

cf_wait() {
  local label="$1"; shift
  printf "  %s" "$label"
  "$@" &
  local pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    printf "."
    sleep 5
  done
  printf "\n"
  wait "$pid"
}

# ---------------------------------------------------------------------------
# --down: delete the stack
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--down" ]]; then
  echo ""
  echo "┌──────────────────────────────────────────────┐"
  echo "│            Delete Lambda Stack               │"
  echo "└──────────────────────────────────────────────┘"
  echo ""
  echo "  DynamoDB tables use DeletionPolicy: Retain."
  echo "  All reach data (agents, users, jobs) will be preserved."
  echo "  Delete the tables manually in the AWS console to remove all data."
  echo ""

  prompt_aws

  STACK_NAME=$(prompt "Stack name" "reach-platform")
  verify_aws

  echo ""
  CONFIRM=$(prompt_yes_no "Delete stack '$STACK_NAME'?" "N")
  [[ "$CONFIRM" != "true" ]] && { echo "  Aborted."; exit 0; }

  ADMIN_UI_BUCKET=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='AdminUiBucketName'].OutputValue" \
    --output text 2>/dev/null || true)

  if [[ -n "$ADMIN_UI_BUCKET" && "$ADMIN_UI_BUCKET" != "None" ]]; then
    echo ""
    echo "==> Emptying S3 bucket '$ADMIN_UI_BUCKET'..."
    aws s3 rm "s3://$ADMIN_UI_BUCKET" --recursive --region "$AWS_REGION"
    ok "Bucket emptied"
  fi

  echo "==> Deleting stack '$STACK_NAME'..."
  aws cloudformation delete-stack \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION"

  if ! cf_wait "Waiting for stack to be deleted" \
      aws cloudformation wait stack-delete-complete \
        --stack-name "$STACK_NAME" \
        --region "$AWS_REGION"; then
    die "Stack deletion failed. Check CloudFormation console for details."
  fi

  echo ""
  ok "Stack '$STACK_NAME' deleted."
  echo ""
  exit 0
fi

# ---------------------------------------------------------------------------
# --update: update release and/or rotate password
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--update" ]]; then
  echo ""
  echo "┌──────────────────────────────────────────────┐"
  echo "│            Update Lambda Stack               │"
  echo "└──────────────────────────────────────────────┘"
  echo ""

  prompt_aws
  verify_aws

  echo ""
  echo "==> Existing CloudFormation stacks in $AWS_REGION:"
  aws cloudformation list-stacks \
    --region "$AWS_REGION" \
    --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE UPDATE_ROLLBACK_COMPLETE \
    --query "StackSummaries[].[StackName,StackStatus]" \
    --output table 2>/dev/null || echo "    (none found)"
  echo ""

  STACK_NAME=$(prompt "Stack name" "reach-platform")

  RELEASE_TAG=$(prompt "Release tag" "latest")
  TEMPLATE_URL="${S3_BASE}/lambda/${RELEASE_TAG}/template.yaml"
  echo "    Using template: $TEMPLATE_URL"

  echo ""
  echo "  Platform secrets (leave blank to keep existing value):"
  read -rsp "  New ADMIN_PASSWORD [keep existing]: " NEW_ADMIN_PASSWORD < /dev/tty; echo "" > /dev/tty
  if [[ -n "$NEW_ADMIN_PASSWORD" ]]; then
    ADMIN_PASSWORD_PARAM="ParameterKey=AdminPassword,ParameterValue=$NEW_ADMIN_PASSWORD"
    ok "ADMIN_PASSWORD will be rotated"
  else
    ADMIN_PASSWORD_PARAM="ParameterKey=AdminPassword,UsePreviousValue=true"
    info "ADMIN_PASSWORD unchanged"
  fi

  read -rp "  Rotate SESSION_SIGNING_KEY? (forces console re-login, no data impact) [y/N]: " _rotsk < /dev/tty
  if [[ "$_rotsk" =~ ^[Yy]$ ]]; then
    SESSION_SIGNING_PARAM="ParameterKey=SessionSigningKey,ParameterValue=$(openssl rand -hex 32)"
    ok "SESSION_SIGNING_KEY will be rotated"
  else
    SESSION_SIGNING_PARAM="ParameterKey=SessionSigningKey,UsePreviousValue=true"
    info "SESSION_SIGNING_KEY unchanged"
  fi

  echo ""
  echo "  Per-tenant retention (approval/job/run/audit/agent-history) and the fan-out cap"
  echo "  are now tenant settings, managed in the console. Only the platform-level audit"
  echo "  trail (cross-tenant, tenant_id IS NULL) is set here:"
  read -rp "  Platform audit retention days [keep existing]: " _ard < /dev/tty
  if [[ -n "$_ard" ]]; then
    AUDIT_RETENTION_PARAM="ParameterKey=AuditRetentionDays,ParameterValue=$_ard"
    ok "Platform audit retention → $_ard days"
  else
    AUDIT_RETENTION_PARAM="ParameterKey=AuditRetentionDays,UsePreviousValue=true"
    info "Platform audit retention unchanged"
  fi

  # Agent / chart version pins. UsePreviousValue only works once a parameter is
  # already on the stack, so for params added by a newer template (not yet on an
  # older stack) fall back to an explicit default on this first update.
  EXISTING_PARAMS=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" --query "Stacks[0].Parameters[].ParameterKey" \
    --output text 2>/dev/null | tr '\t' ' ' || echo "")
  _keep() {  # $1=ParameterKey  $2=default-if-not-yet-on-stack
    case " $EXISTING_PARAMS " in
      *" $1 "*) echo "ParameterKey=$1,UsePreviousValue=true" ;;
      *)        echo "ParameterKey=$1,ParameterValue=$2" ;;
    esac
  }

  # Chart repo is an env-only override; keep whatever the stack already has.
  RELEASES_CHART_REPO_PARAM="$(_keep ReleasesChartRepo '')"

  echo ""
  echo "==> Updating stack '$STACK_NAME' to $RELEASE_TAG..."
  aws cloudformation update-stack \
    --stack-name "$STACK_NAME" \
    --template-url "$TEMPLATE_URL" \
    --parameters \
      ParameterKey=TokenPepper,UsePreviousValue=true \
      ParameterKey=ReleasesS3Base,UsePreviousValue=true \
      "$SESSION_SIGNING_PARAM" \
      "$ADMIN_PASSWORD_PARAM" \
      "$AUDIT_RETENTION_PARAM" \
      "$RELEASES_CHART_REPO_PARAM" \
    --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
    --region "$AWS_REGION"

  echo "==> Updating..."
  if ! cf_wait "Waiting for update to complete" \
    aws cloudformation wait stack-update-complete \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION"; then
    echo ""
    echo "Error: stack update failed. Check CloudFormation events:"
    aws cloudformation describe-stack-events \
      --stack-name "$STACK_NAME" \
      --region "$AWS_REGION" \
      --query "StackEvents[?ResourceStatus=='UPDATE_FAILED'].[LogicalResourceId,ResourceStatusReason]" \
      --output table
    echo "  https://$AWS_REGION.console.aws.amazon.com/cloudformation/home?region=$AWS_REGION#/stacks"
    exit 1
  fi

  API_URL=$(stack_output "ApiUrl")
  ADMIN_UI_BUCKET=$(stack_output "AdminUiBucketName")
  CF_DISTRIBUTION_ID=$(stack_output "CloudFrontDistributionId")

  echo "==> Deploying UI..."
  deploy_ui "$RELEASE_TAG" "$ADMIN_UI_BUCKET" "$CF_DISTRIBUTION_ID"

  echo ""
  echo "┌──────────────────────────────────────────────┐"
  echo "│              Stack updated                   │"
  echo "└──────────────────────────────────────────────┘"
  echo ""
  echo "  Stack:    $STACK_NAME ($AWS_REGION, $RELEASE_TAG)"
  echo "  API URL:  $API_URL"
  echo ""
  if [[ -n "$NEW_ADMIN_PASSWORD" ]]; then
    echo "  ADMIN_PASSWORD rotated. Existing admin sessions are now invalid."
    echo ""
  fi
  echo "  Preserved: TOKEN_PEPPER, all tenants, users, agents, and data."
  echo ""
  echo "  ── Manage ───────────────────────────────────────────────────"
  echo ""
  echo "  Update:    curl -fsSL ${S3_BASE}/lambda-setup.sh | bash -s -- --update"
  echo "  Tear down: curl -fsSL ${S3_BASE}/lambda-setup.sh | bash -s -- --down"
  echo ""
  exit 0
fi

# ---------------------------------------------------------------------------
# Dependency checks (fail fast before asking anything)
# ---------------------------------------------------------------------------
echo ""
echo "┌──────────────────────────────────────────────┐"
echo "│          Reach Lambda Setup                  │"
echo "└──────────────────────────────────────────────┘"
echo ""
echo "==> Checking dependencies..."

MISSING=0
if command -v aws &>/dev/null; then
  ok "aws cli ($(aws --version 2>&1 | head -1))"
else
  miss "aws cli  →  https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
  MISSING=1
fi
command -v curl    &>/dev/null && ok "curl"    || { miss "curl";    MISSING=1; }
command -v jq      &>/dev/null && ok "jq"      || { miss "jq  →  https://jqlang.github.io/jq/download/"; MISSING=1; }
command -v openssl &>/dev/null && ok "openssl" || { miss "openssl  →  required to generate secure tokens"; MISSING=1; }
# python3 is NOT needed for the deploy itself (JSON is handled by jq) - only for
# the optional Reach CLI install (a Python package). Warn, don't fail.
command -v python3 &>/dev/null && ok "python3 (for optional CLI install)" || warn "python3 not found - the optional Reach CLI install will be skipped"
[[ "$MISSING" -eq 1 ]] && fail "install missing dependencies and re-run."

# ===========================================================================
# Phase 1 - Collect all inputs
# ===========================================================================
echo ""
echo "  Answer the questions below - deployment runs automatically when done."
echo ""

# AWS
prompt_aws
STACK_NAME=$(prompt "Stack name" "reach-platform")
RELEASE_TAG=$(prompt "Release tag" "latest")
TEMPLATE_URL="${S3_BASE}/lambda/${RELEASE_TAG}/template.yaml"
echo "    Using template: $TEMPLATE_URL"

verify_aws

# Platform secrets
echo ""
echo "  Platform secrets (protect access to the Reach backend itself):"
read -rsp "  TOKEN_PEPPER [generate]: " TOKEN_PEPPER < /dev/tty; echo "" > /dev/tty
[[ -z "$TOKEN_PEPPER" ]] && { TOKEN_PEPPER=$(openssl rand -hex 32); info "Generated TOKEN_PEPPER"; }
SESSION_SIGNING_KEY=$(openssl rand -hex 32)  # dedicated session-token signing key (safe to rotate)

read -rsp "  ADMIN_PASSWORD [generate]: " ADMIN_PASSWORD < /dev/tty; echo "" > /dev/tty
[[ -z "$ADMIN_PASSWORD" ]] && { ADMIN_PASSWORD=$(openssl rand -hex 16); info "Generated ADMIN_PASSWORD"; }

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
GRANT_SERVICE_MGMT="false"
GRANT_DOCKER="false"
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
    echo ""
    echo "  Agent permissions (applied to the install command):"
    GRANT_SERVICE_MGMT=$(prompt_yes_no "Grant systemctl/service management access?" "N")
    GRANT_DOCKER=$(prompt_yes_no "Grant Docker access?" "N")
    if [[ "$GRANT_SERVICE_MGMT" == "true" || "$GRANT_DOCKER" == "true" ]]; then
      echo "  Note: the install command will require sudo (elevated access needs root)."
    fi
  fi
fi

# Platform audit retention (advanced). Per-tenant retention (approval/job/run/audit/
# agent-history) and the fan-out cap are tenant settings, managed in the console; only
# the platform-level audit trail (cross-tenant, tenant_id IS NULL) is set at deploy time.
echo ""
ADVANCED=$(prompt_yes_no "Configure platform audit retention? (default: 90 days)" "N")
if [[ "$ADVANCED" == "true" ]]; then
  echo ""
  AUDIT_RETENTION_DAYS=$(prompt "Platform audit retention days (cross-tenant admin trail)" "90")
else
  AUDIT_RETENTION_DAYS=90
fi

# Chart repo defaults to <ReleasesS3Base>/charts/reach-agent. Self-hosting the
# Helm repo is rare, so it's an env override (RELEASES_CHART_REPO=…) rather than a
# prompt. Agent/chart versions are chosen per-agent in the console.
RELEASES_CHART_REPO="${RELEASES_CHART_REPO:-}"

# CLI
echo ""
CLI_INSTALL="false"
if command -v reach &>/dev/null; then
  info "Reach CLI already installed"
else
  CLI_INSTALL=$(prompt_yes_no "Install Reach CLI?" "Y")
fi

# ---------------------------------------------------------------------------
# Check for existing stack
# ---------------------------------------------------------------------------
echo ""
echo "==> Checking for existing stack '$STACK_NAME'..."
EXISTING_STATUS=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --query "Stacks[0].StackStatus" \
  --output text 2>/dev/null || echo "NONE")

if [[ "$EXISTING_STATUS" != "NONE" ]]; then
  echo ""
  warn "Stack '$STACK_NAME' already exists (status: $EXISTING_STATUS)."
  echo ""
  echo "  To update: curl -fsSL ${S3_BASE}/lambda-setup.sh | bash -s -- --update"
  echo "  To delete: curl -fsSL ${S3_BASE}/lambda-setup.sh | bash -s -- --down"
  echo ""
  exit 0
fi

# ---------------------------------------------------------------------------
# Confirm and go
# ---------------------------------------------------------------------------
echo ""
echo "  ────────────────────────────────────────────"
echo "  Stack:     $STACK_NAME ($AWS_REGION, $RELEASE_TAG)"
echo "  Workspace: $SETUP_TENANT"
echo "  User:      $SETUP_USERNAME"
if [[ "$CREATE_AGENT" == "true" ]]; then
  echo "  Agent:     $AGENT_TYPE  $AGENT_MODE  service_mgmt=$GRANT_SERVICE_MGMT  docker=$GRANT_DOCKER"
else
  echo "  Agent:     none"
fi
echo "  Retention: ${AUDIT_RETENTION_DAYS}d platform audit (per-tenant retention set in the console)"
echo "  ────────────────────────────────────────────"
echo ""
read -rp "  Start deployment? [Y/n]: " _confirm < /dev/tty
[[ "${_confirm:-Y}" =~ ^[Nn]$ ]] && { echo "  Aborted."; exit 0; }
echo ""

# ===========================================================================
# Phase 2 - Deploy
# ===========================================================================
echo "==> Deploying CloudFormation stack '$STACK_NAME' to $AWS_REGION..."
aws cloudformation create-stack \
  --stack-name "$STACK_NAME" \
  --template-url "$TEMPLATE_URL" \
  --parameters \
    ParameterKey=TokenPepper,ParameterValue="$TOKEN_PEPPER" \
    ParameterKey=SessionSigningKey,ParameterValue="$SESSION_SIGNING_KEY" \
    ParameterKey=AdminPassword,ParameterValue="$ADMIN_PASSWORD" \
    ParameterKey=AuditRetentionDays,ParameterValue="$AUDIT_RETENTION_DAYS" \
    ParameterKey=ReleasesChartRepo,ParameterValue="${RELEASES_CHART_REPO:-}" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --region "$AWS_REGION"

if ! cf_wait "Waiting for stack to be ready (this takes 5-10 minutes)" \
  aws cloudformation wait stack-create-complete \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION"; then
  echo ""
  echo "Error: stack creation failed. Check CloudFormation events:"
  aws cloudformation describe-stack-events \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query "StackEvents[?ResourceStatus=='CREATE_FAILED'].[LogicalResourceId,ResourceStatusReason]" \
    --output table
  echo "  https://$AWS_REGION.console.aws.amazon.com/cloudformation/home?region=$AWS_REGION#/stacks"
  exit 1
fi

API_URL=$(stack_output "ApiUrl")
UI_URL=$(stack_output "AdminUiUrl")
ADMIN_UI_BUCKET=$(stack_output "AdminUiBucketName")
CF_DISTRIBUTION_ID=$(stack_output "CloudFrontDistributionId")

echo "==> Deploying UI..."
deploy_ui "$RELEASE_TAG" "$ADMIN_UI_BUCKET" "$CF_DISTRIBUTION_ID"

# ---------------------------------------------------------------------------
# Bootstrap: tenant → user → API key → agent
# ---------------------------------------------------------------------------
echo "==> Bootstrapping workspace..."

ADMIN_TOKEN=$(request_json POST "$API_URL/admin/login" \
  "$(mkjson password "$ADMIN_PASSWORD")" \
  -H "Content-Type: application/json" | json_get '.token')

TENANT_RESP=$(request_json POST "$API_URL/admin/tenants" \
  "$(mkjson name "$SETUP_TENANT")" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json")
TENANT_ID=$(echo "$TENANT_RESP" | json_get '.tenant_id')
ok "Tenant:   $SETUP_TENANT ($TENANT_ID)"

USER_RESP=$(request_json POST "$API_URL/admin/tenants/${TENANT_ID}/admin-users" \
  "$(mkjson username "$SETUP_USERNAME" role admin)" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json")
TEMP_PASS=$(echo "$USER_RESP" | json_get '.temp_password // .temporary_password // .password // .user.temp_password // empty')
[[ -n "$TEMP_PASS" ]] || fail "Admin user created but no temp password returned."
ok "User:     $SETUP_USERNAME (role: admin)"

TEMP_TOKEN=$(request_json POST "$API_URL/tenant/login" \
  "$(mkjson tenant_name "$SETUP_TENANT" username "$SETUP_USERNAME" password "$TEMP_PASS")" \
  -H "Content-Type: application/json" | json_get '.token')

request_json POST "$API_URL/tenant/me/password" \
  "$(mkjson current_password "$TEMP_PASS" new_password "$SETUP_PASSWORD")" \
  -H "Authorization: Bearer $TEMP_TOKEN" \
  -H "Content-Type: application/json" > /dev/null
ok "Password: set"

USER_TOKEN=$(request_json POST "$API_URL/tenant/login" \
  "$(mkjson tenant_name "$SETUP_TENANT" username "$SETUP_USERNAME" password "$SETUP_PASSWORD")" \
  -H "Content-Type: application/json" | json_get '.token')

API_KEY=$(request_json POST "$API_URL/tenant/api-tokens" \
  "$(mkjson name "default-cli")" \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H "Content-Type: application/json" | json_get '.token')
ok "API key:  created"

AGENT_ID=""
INSTALL_AGENT=""
if [[ "$CREATE_AGENT" == "true" ]]; then
  # The bootstrap agent always installs the latest version; pin a specific
  # version per-agent in the console at create time if you need to.
  AGENT_RESP=$(request_json POST "$API_URL/tenant/agents" \
    "$(mkjson type "$AGENT_TYPE" mode "$AGENT_MODE" grant_service_mgmt "$GRANT_SERVICE_MGMT" grant_docker "$GRANT_DOCKER")" \
    -H "Authorization: Bearer $USER_TOKEN" \
    -H "Content-Type: application/json")
  AGENT_ID=$(echo "$AGENT_RESP" | json_get '.agent_id')
  # host agents return commands.agent (install.sh); k8s agents return commands.helm.
  INSTALL_AGENT=$(echo "$AGENT_RESP" | json_get '.commands.helm // .commands.agent // empty')
  ok "Agent:    $AGENT_ID"
fi

# ---------------------------------------------------------------------------
# CLI install
# ---------------------------------------------------------------------------
echo ""
echo "==> Reach CLI"
echo ""
CLI_READY=false
CLI_LOGGED_IN=false

if command -v reach &>/dev/null; then
  CLI_READY=true
  ok "reach already installed"
elif [[ "$CLI_INSTALL" == "true" ]]; then
  _installed=false
  if command -v uv &>/dev/null; then
    uv tool install "$CLI_WHEEL_URL" --force && _installed=true || true
  fi
  if [[ "$_installed" == false ]] && command -v pipx &>/dev/null; then
    pipx install "$CLI_WHEEL_URL" --force && _installed=true || true
  fi
  if [[ "$_installed" == false ]] && command -v pip3 &>/dev/null; then
    pip3 install "$CLI_WHEEL_URL" && _installed=true || true
  fi
  if [[ "$_installed" == false ]]; then
    python3 -m pip install "$CLI_WHEEL_URL" && _installed=true || true
  fi
  if [[ "$_installed" == false ]]; then
    warn "CLI install failed. Install manually: pip install $CLI_WHEEL_URL"
  fi
  command -v reach &>/dev/null && CLI_READY=true || true
fi

if [[ "$CLI_READY" == "true" ]]; then
  reach login --api-url "$API_URL" --api-key "$API_KEY"
  if [[ -n "$AGENT_ID" ]]; then
    reach agents use "$AGENT_ID"
  fi
  CLI_LOGGED_IN=true
  ok "CLI ready"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "┌─────────────────────────────────────────────────────────────────┐"
echo "│             Reach Lambda backend is deployed                    │"
echo "└─────────────────────────────────────────────────────────────────┘"
echo ""
echo "  Stack:    $STACK_NAME ($AWS_REGION, $RELEASE_TAG)"
echo "  API URL:  $API_URL"
echo "  UI:       $UI_URL"
echo ""
echo "  ── Credentials ──────────────────────────────────────────────────"
echo ""
echo "  Admin password: $ADMIN_PASSWORD"
echo "  Token pepper:   $TOKEN_PEPPER"
echo "  Tenant:         $SETUP_TENANT  ($TENANT_ID)"
echo "  Username:       $SETUP_USERNAME"
echo "  API Key:        $API_KEY"
if [[ -n "$AGENT_ID" ]]; then
  echo "  Agent ID:       $AGENT_ID"
fi
echo ""
echo "  Save TOKEN_PEPPER securely - it cannot be changed without"
echo "  invalidating all agent and user tokens."
echo ""
echo "  ── CLI ──────────────────────────────────────────────────────────"
echo ""
if [[ "$CLI_LOGGED_IN" == "true" ]]; then
  echo "  reach is installed and logged in. You're ready to go:"
  echo ""
  if [[ -n "$AGENT_ID" ]]; then
    echo "    reach exec -- hostname"
  else
    echo "    reach agents list"
  fi
else
  echo "    pip install $CLI_WHEEL_URL"
  echo "    reach login --api-url '$API_URL' --api-key '$API_KEY'"
  if [[ -n "$AGENT_ID" ]]; then
    echo "    reach agents use $AGENT_ID"
  fi
fi
echo ""
if [[ "$CREATE_AGENT" == "true" ]]; then
  if [[ "$AGENT_TYPE" == "k8s" ]]; then
    echo "  ── Install agent on your Kubernetes cluster ─────────────────────"
  else
    echo "  ── Install agent on a host machine ──────────────────────────────"
  fi
  echo ""
  if [[ -n "$INSTALL_AGENT" ]]; then
    if [[ "$AGENT_TYPE" == "k8s" ]]; then
      echo "  Run this against your cluster (from the repo root):"
    else
      echo "  Run this on the host machine (Linux or macOS):"
    fi
    echo ""
    echo "    $INSTALL_AGENT"
    echo ""
  fi
fi
echo "  ── Manage ───────────────────────────────────────────────────────"
echo ""
echo "  Update:    curl -fsSL ${S3_BASE}/lambda-setup.sh | bash -s -- --update"
echo "  Tear down: curl -fsSL ${S3_BASE}/lambda-setup.sh | bash -s -- --down"
echo ""
