#!/usr/bin/env bash
# Build and publish the reach backend: Docker image + Lambda package.
# Usage: ./scripts/release_backend.sh [--image nabeemdev/reach] [--bucket reach-releases] [--push]

set -euo pipefail

IMAGE="nabeemdev/reach"
BUCKET="reach-releases"
PUSH=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image)  IMAGE="$2";  shift 2 ;;
    --bucket) BUCKET="$2"; shift 2 ;;
    --push)   PUSH=true;   shift   ;;
    *) echo "Usage: $0 [--image <repo/name>] [--bucket <s3-bucket>] [--push]"; exit 1 ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

VERSION=$(grep '__version__' "$ROOT_DIR/backend/version.py" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
if [[ -z "$VERSION" ]]; then
  echo "Error: could not read __version__ from backend/version.py"
  exit 1
fi
echo "==> Version: $VERSION"

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
echo "==> Running tests..."
(
  cd "$ROOT_DIR/backend"
  STORAGE_BACKEND=postgres DATABASE_URL="sqlite:///:memory:" \
    ADMIN_TOKEN=test-admin-token TOKEN_PEPPER=test-pepper \
    python3 -m pytest tests/ -q --tb=short
)

# ---------------------------------------------------------------------------
# Docker image
# ---------------------------------------------------------------------------
echo "==> Building $IMAGE:$VERSION and $IMAGE:latest..."
docker build \
  --platform linux/amd64,linux/arm64 \
  -t "$IMAGE:$VERSION" \
  -t "$IMAGE:latest" \
  "$ROOT_DIR"

# ---------------------------------------------------------------------------
# Lambda package
# ---------------------------------------------------------------------------
PACKAGED_TEMPLATE="/tmp/reach-lambda-packaged.yaml"

echo "==> Packaging Lambda (sam package)..."
sam package \
  --template-file "$ROOT_DIR/deploy/lambda/template.yaml" \
  --s3-bucket "$BUCKET" \
  --s3-prefix "lambda/code" \
  --output-template-file "$PACKAGED_TEMPLATE" \
  --no-progressbar

echo "==> Uploading Lambda template..."
aws s3 cp "$PACKAGED_TEMPLATE" "s3://$BUCKET/lambda/v${VERSION}/template.yaml"
aws s3 cp "$PACKAGED_TEMPLATE" "s3://$BUCKET/lambda/latest/template.yaml"

# ---------------------------------------------------------------------------
# Setup scripts
# ---------------------------------------------------------------------------
echo "==> Uploading local-setup.sh..."
aws s3 cp "$ROOT_DIR/scripts/local-setup.sh" "s3://$BUCKET/local-setup.sh"

echo "==> Uploading lambda-setup.sh..."
aws s3 cp "$ROOT_DIR/scripts/lambda-setup.sh" "s3://$BUCKET/lambda-setup.sh"

# ---------------------------------------------------------------------------
# Docker push
# ---------------------------------------------------------------------------
if [[ "$PUSH" == true ]]; then
  echo "==> Pushing $IMAGE:$VERSION..."
  docker push "$IMAGE:$VERSION"
  echo "==> Pushing $IMAGE:latest..."
  docker push "$IMAGE:latest"
  echo ""
  echo "==> Done. Published:"
  echo "    $IMAGE:$VERSION"
  echo "    $IMAGE:latest"
  echo "    s3://$BUCKET/lambda/v${VERSION}/template.yaml"
  echo "    s3://$BUCKET/lambda/latest/template.yaml"
  echo "    s3://$BUCKET/lambda/code/ (function zips)"
  echo "    s3://$BUCKET/local-setup.sh"
  echo "    s3://$BUCKET/lambda-setup.sh"
else
  echo ""
  echo "==> Built locally (pass --push to publish Docker image):"
  echo "    $IMAGE:$VERSION"
  echo "    $IMAGE:latest"
  echo "    s3://$BUCKET/lambda/v${VERSION}/template.yaml (uploaded)"
  echo "    s3://$BUCKET/lambda/latest/template.yaml (uploaded)"
  echo "    s3://$BUCKET/lambda/code/ (function zips uploaded)"
  echo "    s3://$BUCKET/local-setup.sh (uploaded)"
  echo "    s3://$BUCKET/lambda-setup.sh (uploaded)"
fi
