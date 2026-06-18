#!/usr/bin/env bash
# Deploy or manage the reach backend on AWS Lambda + DynamoDB via CloudFormation.
#
# Usage:
#   curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash                      # fresh deploy
#   curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash -s -- --update       # update stack
#   curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash -s -- --down         # delete stack
#
#   ./scripts/lambda-setup.sh            # fresh deploy
#   ./scripts/lambda-setup.sh --update   # update an existing stack (release tag and/or ADMIN_TOKEN rotation)
#   ./scripts/lambda-setup.sh --down     # delete the CloudFormation stack (data retained in DynamoDB)

set -euo pipefail

S3_BASE="https://reach-releases.s3.amazonaws.com"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
ok()   { printf "  [OK]      %s\n" "$1"; }
miss() { printf "  [MISSING] %s\n" "$1"; }
info() { printf "  [INFO]    %s\n" "$1"; }

# ---------------------------------------------------------------------------
# --down: delete the stack
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--down" ]]; then
  echo ""
  read -rp "AWS profile    [blank to use env]: " AWS_PROFILE_INPUT < /dev/tty
  AWS_PROFILE="${AWS_PROFILE_INPUT:-}"
  [[ -n "$AWS_PROFILE" ]] && export AWS_PROFILE
  read -rp "AWS region     [us-east-1]: " AWS_REGION_INPUT < /dev/tty
  AWS_REGION="${AWS_REGION_INPUT:-us-east-1}"
  read -rp "Stack name     [reach-platform]: " STACK_NAME_INPUT < /dev/tty
  STACK_NAME="${STACK_NAME_INPUT:-reach-platform}"

  echo ""
  echo "==> Deleting stack '$STACK_NAME'..."
  echo "    WARNING: DynamoDB tables use DeletionPolicy: Retain."
  echo "    All reach data (agents, users, jobs) will be preserved in DynamoDB."
  echo "    Delete the tables manually in the AWS console if you want to remove all data."
  echo ""
  read -rp "  Proceed? [y/N]: " confirm < /dev/tty
  if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
  fi

  aws cloudformation delete-stack \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION"

  echo "==> Stack deletion initiated. Monitor progress:"
  echo "    aws cloudformation describe-stacks --stack-name $STACK_NAME --region $AWS_REGION"
  exit 0
fi

# ---------------------------------------------------------------------------
# --update: update an existing stack (release tag and/or ADMIN_TOKEN rotation)
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--update" ]]; then
  echo ""
  echo "==> Configuration"
  echo ""
  read -rp "  AWS profile  [blank to use env]: " AWS_PROFILE_INPUT < /dev/tty
  AWS_PROFILE="${AWS_PROFILE_INPUT:-}"
  [[ -n "$AWS_PROFILE" ]] && export AWS_PROFILE
  read -rp "  AWS region   [us-east-1]: " AWS_REGION_INPUT < /dev/tty
  AWS_REGION="${AWS_REGION_INPUT:-us-east-1}"

  echo ""
  echo "==> Verifying AWS credentials (profile: ${AWS_PROFILE:-<env>}, region: $AWS_REGION)..."
  if ! aws sts get-caller-identity --region "$AWS_REGION" &>/dev/null; then
    echo "Error: could not authenticate with AWS."
    if [[ -n "$AWS_PROFILE" ]]; then
      echo "  Check that profile '$AWS_PROFILE' exists and has valid credentials:"
      echo "    aws configure --profile $AWS_PROFILE"
    else
      echo "  No profile specified - check that AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are exported,"
      echo "  or run: aws configure"
    fi
    exit 1
  fi

  echo ""
  echo "==> Existing CloudFormation stacks in $AWS_REGION:"
  aws cloudformation list-stacks \
    --region "$AWS_REGION" \
    --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE UPDATE_ROLLBACK_COMPLETE \
    --query "StackSummaries[].[StackName,StackStatus]" \
    --output table 2>/dev/null || echo "    (none found)"

  echo ""
  read -rp "  Stack name   [reach-platform]: " STACK_NAME_INPUT < /dev/tty
  STACK_NAME="${STACK_NAME_INPUT:-reach-platform}"

  read -rp "  Release tag  [latest]: " RELEASE_TAG_INPUT < /dev/tty
  RELEASE_TAG="${RELEASE_TAG_INPUT:-latest}"
  TEMPLATE_URL="${S3_BASE}/lambda/${RELEASE_TAG}/template.yaml"
  echo "    Using template: $TEMPLATE_URL"

  echo ""
  read -rp "  New ADMIN_TOKEN (leave blank to keep existing): " NEW_ADMIN_TOKEN < /dev/tty
  if [[ -n "$NEW_ADMIN_TOKEN" ]]; then
    ADMIN_TOKEN_PARAM="ParameterKey=AdminToken,ParameterValue=$NEW_ADMIN_TOKEN"
    echo "    ADMIN_TOKEN will be rotated."
  else
    ADMIN_TOKEN_PARAM="ParameterKey=AdminToken,UsePreviousValue=true"
    echo "    ADMIN_TOKEN unchanged."
  fi

  echo ""
  echo "==> Updating stack '$STACK_NAME'..."
  aws cloudformation update-stack \
    --stack-name "$STACK_NAME" \
    --template-url "$TEMPLATE_URL" \
    --parameters \
      ParameterKey=TokenPepper,UsePreviousValue=true \
      "$ADMIN_TOKEN_PARAM" \
    --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
    --region "$AWS_REGION"

  echo "==> Waiting for update to complete..."
  if ! aws cloudformation wait stack-update-complete \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION"; then
    echo ""
    echo "Error: stack update failed. Check the CloudFormation console for details:"
    echo "  https://$AWS_REGION.console.aws.amazon.com/cloudformation/home?region=$AWS_REGION#/stacks"
    aws cloudformation describe-stack-events \
      --stack-name "$STACK_NAME" \
      --region "$AWS_REGION" \
      --query "StackEvents[?ResourceStatus=='UPDATE_FAILED'].[LogicalResourceId,ResourceStatusReason]" \
      --output table
    exit 1
  fi

  API_URL=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
    --output text)

  echo ""
  echo "==> Stack '$STACK_NAME' updated to $RELEASE_TAG."
  echo "    API URL: $API_URL"
  if [[ -n "$NEW_ADMIN_TOKEN" ]]; then
    echo "    ADMIN_TOKEN rotated. Update any scripts or automations that use the old token."
  fi
  exit 0
fi

# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------
echo ""
echo "==> Checking dependencies..."

MISSING=0

if command -v aws &>/dev/null; then
  ok "aws cli ($(aws --version 2>&1 | head -1))"
else
  miss "aws cli  →  https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
  MISSING=1
fi

if command -v openssl &>/dev/null; then
  ok "openssl"
else
  miss "openssl  →  required to generate secure tokens"
  MISSING=1
fi

if [[ $MISSING -eq 1 ]]; then
  echo ""
  echo "Error: missing required dependencies above. Install them and re-run."
  exit 1
fi

# ---------------------------------------------------------------------------
# Configuration prompts
# ---------------------------------------------------------------------------
echo ""
echo "==> Configuration"
echo ""

read -rp "  AWS profile  [blank to use env]: " AWS_PROFILE_INPUT < /dev/tty
AWS_PROFILE="${AWS_PROFILE_INPUT:-}"
[[ -n "$AWS_PROFILE" ]] && export AWS_PROFILE

read -rp "  AWS region   [us-east-1]: " AWS_REGION_INPUT < /dev/tty
AWS_REGION="${AWS_REGION_INPUT:-us-east-1}"

read -rp "  Stack name   [reach-platform]: " STACK_NAME_INPUT < /dev/tty
STACK_NAME="${STACK_NAME_INPUT:-reach-platform}"

read -rp "  Release tag  [latest]: " RELEASE_TAG_INPUT < /dev/tty
RELEASE_TAG="${RELEASE_TAG_INPUT:-latest}"
TEMPLATE_URL="${S3_BASE}/lambda/${RELEASE_TAG}/template.yaml"
echo "    Using template: $TEMPLATE_URL"

echo ""

# Verify credentials work before continuing
echo "==> Verifying AWS credentials (profile: ${AWS_PROFILE:-<env>}, region: $AWS_REGION)..."
if ! aws sts get-caller-identity --region "$AWS_REGION" &>/dev/null; then
  echo ""
  echo "Error: could not authenticate with AWS."
  if [[ -n "$AWS_PROFILE" ]]; then
    echo "  Check that profile '$AWS_PROFILE' exists and has valid credentials:"
    echo "    aws configure --profile $AWS_PROFILE"
  else
    echo "  No profile specified - check that AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are exported,"
    echo "  or run: aws configure"
  fi
  exit 1
fi
CALLER=$(aws sts get-caller-identity --region "$AWS_REGION" \
  --query "Account" --output text 2>/dev/null)
ok "authenticated (account: $CALLER)"

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
  echo "    Stack already exists (status: $EXISTING_STATUS)."
  echo ""
  echo "    To update: curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash -s -- --update"
  echo "    To tear down: curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash -s -- --down"
  exit 0
fi

# ---------------------------------------------------------------------------
# Deploy
# ---------------------------------------------------------------------------
echo "==> Deploying CloudFormation stack '$STACK_NAME' to $AWS_REGION..."
aws cloudformation create-stack \
  --stack-name "$STACK_NAME" \
  --template-url "$TEMPLATE_URL" \
  --parameters \
    ParameterKey=TokenPepper,ParameterValue="$TOKEN_PEPPER" \
    ParameterKey=AdminToken,ParameterValue="$ADMIN_TOKEN" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --region "$AWS_REGION"

# ---------------------------------------------------------------------------
# Wait for completion
# ---------------------------------------------------------------------------
echo "==> Waiting for stack to be ready (this takes 1-3 minutes)..."
if ! aws cloudformation wait stack-create-complete \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION"; then
  echo ""
  echo "Error: stack creation failed. Check the CloudFormation console for details:"
  echo "  https://$AWS_REGION.console.aws.amazon.com/cloudformation/home?region=$AWS_REGION#/stacks"
  echo ""
  echo "Stack events:"
  aws cloudformation describe-stack-events \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query "StackEvents[?ResourceStatus=='CREATE_FAILED'].[LogicalResourceId,ResourceStatusReason]" \
    --output table
  exit 1
fi

# ---------------------------------------------------------------------------
# Fetch outputs
# ---------------------------------------------------------------------------
API_URL=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
  --output text)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "┌─────────────────────────────────────────────────────────────┐"
echo "│           reach Lambda backend is deployed                  │"
echo "└─────────────────────────────────────────────────────────────┘"
echo ""
echo "  API URL:      $API_URL"
echo "  Stack:        $STACK_NAME ($AWS_REGION, $RELEASE_TAG)"
echo "  ADMIN_TOKEN:  $ADMIN_TOKEN"
echo "  TOKEN_PEPPER: $TOKEN_PEPPER"
echo ""
echo "  Save TOKEN_PEPPER securely - it cannot be changed without"
echo "  invalidating all agent and user tokens."
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
echo "  Upgrade:   curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash -s -- --update"
echo "  Tear down: curl -fsSL https://reach-releases.s3.amazonaws.com/lambda-setup.sh | bash -s -- --down"
echo ""
