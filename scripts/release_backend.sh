#!/usr/bin/env bash
# Build and push the reach backend Docker image.
# Usage: ./scripts/release_backend.sh [--image nabeemdev/reach] [--push]

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

echo "==> Building $IMAGE:$VERSION and $IMAGE:latest..."
docker build \
  --platform linux/amd64,linux/arm64 \
  -t "$IMAGE:$VERSION" \
  -t "$IMAGE:latest" \
  "$ROOT_DIR"

echo "==> Uploading local-setup.sh to s3://$BUCKET/..."
aws s3 cp "$ROOT_DIR/scripts/local-setup.sh" "s3://$BUCKET/local-setup.sh"

if [[ "$PUSH" == true ]]; then
  echo "==> Pushing $IMAGE:$VERSION..."
  docker push "$IMAGE:$VERSION"
  echo "==> Pushing $IMAGE:latest..."
  docker push "$IMAGE:latest"
  echo ""
  echo "==> Done. Published:"
  echo "    $IMAGE:$VERSION"
  echo "    $IMAGE:latest"
else
  echo ""
  echo "==> Built locally (pass --push to publish):"
  echo "    $IMAGE:$VERSION"
  echo "    $IMAGE:latest"
fi
