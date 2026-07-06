#!/usr/bin/env bash
# Build and publish the reach backend: Docker image + Lambda package + UI.
#
# Usage:
#   ./scripts/release_backend.sh                         # build + upload S3 artifacts (no Docker push)
#   ./scripts/release_backend.sh --push                  # build + upload + push Docker image
#   ./scripts/release_backend.sh --push --no-cache       # same, without Docker layer cache
#   ./scripts/release_backend.sh --image myorg/reach     # override Docker image name
#   ./scripts/release_backend.sh --bucket my-bucket      # override S3 bucket
#   ./scripts/release_backend.sh --scripts               # publish ONLY the setup scripts (no build)

set -euo pipefail

IMAGE="nabeemdev/reach"
BUCKET="reach-releases"
PUSH=false
NO_CACHE=""
SCRIPTS_ONLY=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image)          IMAGE="$2";        shift 2 ;;
    --bucket)         BUCKET="$2";       shift 2 ;;
    --push)           PUSH=true;         shift   ;;
    --no-cache)       NO_CACHE="--no-cache"; shift ;;
    --scripts|--script) SCRIPTS_ONLY=true; shift ;;
    *) echo "Usage: $0 [--image <repo/name>] [--bucket <s3-bucket>] [--push] [--no-cache] [--scripts]"; exit 1 ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ---------------------------------------------------------------------------
# --scripts: publish only the setup scripts. Skips tests, Docker, Lambda, and the
# UI build - handy for iterating on local-setup.sh / lambda-setup.sh alone.
# ---------------------------------------------------------------------------
if [[ "$SCRIPTS_ONLY" == true ]]; then
  command -v aws &>/dev/null || { echo "Error: 'aws' is required but not installed."; exit 1; }

  echo "==> Syntax-checking setup scripts..."
  bash -n "$ROOT_DIR/scripts/local-setup.sh"
  bash -n "$ROOT_DIR/scripts/lambda-setup.sh"

  echo "==> Uploading setup scripts to s3://$BUCKET ..."
  aws s3 cp "$ROOT_DIR/scripts/local-setup.sh"  "s3://$BUCKET/local-setup.sh"
  aws s3 cp "$ROOT_DIR/scripts/lambda-setup.sh" "s3://$BUCKET/lambda-setup.sh"

  echo ""
  echo "  Published (scripts only):"
  echo "    s3://$BUCKET/local-setup.sh"
  echo "    s3://$BUCKET/lambda-setup.sh"
  exit 0
fi

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
VERSION=$(grep '__version__' "$ROOT_DIR/backend/version.py" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
if [[ -z "$VERSION" ]]; then
  echo "Error: could not read __version__ from backend/version.py"
  exit 1
fi
echo "==> Version: $VERSION"

# Build metadata stamped into the image's OCI labels.
VCS_REF=$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)
BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------
echo "==> Checking dependencies..."

for cmd in docker aws sam npm python3; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "Error: '$cmd' is required but not installed."
    exit 1
  fi
done

if ! docker buildx version &>/dev/null 2>&1; then
  echo "Error: docker buildx is required. Install Docker Desktop or the buildx plugin."
  exit 1
fi

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
echo "==> Running backend tests..."
(
  cd "$ROOT_DIR/backend"
  STORAGE_BACKEND=postgres DATABASE_URL="sqlite:///:memory:" \
    ADMIN_PASSWORD=test-password TOKEN_PEPPER=test-pepper SESSION_SIGNING_KEY=test-signing-key \
    "${UV:-$(command -v uv || echo uv)}" run --with-requirements requirements-dev.txt pytest tests/ -q --tb=short
)

echo "==> Running frontend tests..."
(
  cd "$ROOT_DIR/ui"
  npm ci --silent
  npm test -- --run
)

# ---------------------------------------------------------------------------
# Docker image
# ---------------------------------------------------------------------------
if [[ "$PUSH" == true ]]; then
  echo "==> Building and pushing $IMAGE:$VERSION and $IMAGE:latest (linux/amd64, linux/arm64)..."
  docker buildx build \
    --platform linux/amd64,linux/arm64 \
    $NO_CACHE \
    --build-arg VERSION="$VERSION" \
    --build-arg VCS_REF="$VCS_REF" \
    --build-arg BUILD_DATE="$BUILD_DATE" \
    -t "$IMAGE:$VERSION" \
    -t "$IMAGE:latest" \
    --push \
    "$ROOT_DIR"
else
  # Multi-platform requires --push; build native platform only for local testing
  NATIVE=$(docker info --format '{{.Architecture}}' \
    | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/')
  echo "==> Building $IMAGE:$VERSION locally (linux/$NATIVE)..."
  echo "    (pass --push to build linux/amd64+arm64 and push to registry)"
  docker buildx build \
    --platform "linux/$NATIVE" \
    $NO_CACHE \
    --build-arg VERSION="$VERSION" \
    --build-arg VCS_REF="$VCS_REF" \
    --build-arg BUILD_DATE="$BUILD_DATE" \
    -t "$IMAGE:$VERSION" \
    -t "$IMAGE:latest" \
    --load \
    "$ROOT_DIR"
fi

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
# UI
# ---------------------------------------------------------------------------
echo "==> Building UI..."
(
  cd "$ROOT_DIR/ui"
  npm run build
)

UI_TARBALL="/tmp/reach-ui-${VERSION}.tar.gz"
tar -czf "$UI_TARBALL" -C "$ROOT_DIR/ui/dist" .

echo "==> Uploading UI..."
aws s3 cp "$UI_TARBALL" "s3://$BUCKET/lambda/v${VERSION}/ui.tar.gz"
aws s3 cp "$UI_TARBALL" "s3://$BUCKET/lambda/latest/ui.tar.gz"
rm -f "$UI_TARBALL"

# ---------------------------------------------------------------------------
# Setup scripts
# ---------------------------------------------------------------------------
echo "==> Uploading setup scripts..."
aws s3 cp "$ROOT_DIR/scripts/local-setup.sh"  "s3://$BUCKET/local-setup.sh"
aws s3 cp "$ROOT_DIR/scripts/lambda-setup.sh" "s3://$BUCKET/lambda-setup.sh"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "┌─────────────────────────────────────────────────────────────────┐"
echo "│                    release complete                             │"
echo "└─────────────────────────────────────────────────────────────────┘"
echo ""
echo "  Version: $VERSION"
echo ""
echo "  ── Docker ───────────────────────────────────────────────────────"
if [[ "$PUSH" == true ]]; then
  echo "  $IMAGE:$VERSION         (pushed)"
  echo "  $IMAGE:latest           (pushed)"
else
  echo "  $IMAGE:$VERSION         (local only - pass --push to publish)"
  echo "  $IMAGE:latest           (local only)"
fi
echo ""
echo "  ── S3: $BUCKET ──────────────────────────────────────────────────"
echo "  lambda/v${VERSION}/template.yaml"
echo "  lambda/latest/template.yaml"
echo "  lambda/code/              (function zips)"
echo "  lambda/v${VERSION}/ui.tar.gz"
echo "  lambda/latest/ui.tar.gz"
echo "  local-setup.sh"
echo "  lambda-setup.sh"
echo ""
