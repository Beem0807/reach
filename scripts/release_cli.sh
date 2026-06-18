#!/usr/bin/env bash
# Build reach wheel and upload to S3.
# Usage: ./scripts/release_cli.sh [--bucket reach-releases]

set -euo pipefail

BUCKET="reach-releases"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bucket) BUCKET="$2"; shift 2 ;;
    *) echo "Usage: $0 [--bucket <s3-bucket>]"; exit 1 ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

VERSION=$(grep '__version__' "$ROOT_DIR/cli/reach/__init__.py" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
if [[ -z "$VERSION" ]]; then
  echo "Error: could not read __version__ from cli/reach/__init__.py"
  exit 1
fi
echo "==> Version: $VERSION"

DIST_DIR="$(mktemp -d)"

echo "==> Building wheel..."
if command -v uv &>/dev/null; then
  uv build "$ROOT_DIR/cli" --wheel --out-dir "$DIST_DIR" -q
else
  pip wheel "$ROOT_DIR/cli" --no-deps -w "$DIST_DIR" -q
fi

WHEEL=$(ls "$DIST_DIR"/*.whl | head -1)
WHEEL_FILE=$(basename "$WHEEL")

echo "==> Uploading to s3://$BUCKET/cli/v${VERSION}/..."
aws s3 cp "$WHEEL" "s3://$BUCKET/cli/v${VERSION}/$WHEEL_FILE"

echo "==> Uploading to s3://$BUCKET/cli/latest/..."
aws s3 cp "$WHEEL" "s3://$BUCKET/cli/latest/$WHEEL_FILE"

rm -rf "$DIST_DIR"

echo ""
echo "==> Done. Published v${VERSION}:"
echo "    uv tool install https://$BUCKET.s3.amazonaws.com/cli/v${VERSION}/$WHEEL_FILE"
echo "    pip install https://$BUCKET.s3.amazonaws.com/cli/v${VERSION}/$WHEEL_FILE"
echo ""
echo "    cli/latest/ also updated."
