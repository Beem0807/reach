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
DIST_DIR="$(mktemp -d)"

echo "==> Building wheel..."
if command -v uv &>/dev/null; then
  uv build "$ROOT_DIR/cli" --wheel --out-dir "$DIST_DIR" -q
else
  pip wheel "$ROOT_DIR/cli" --no-deps -w "$DIST_DIR" -q
fi

WHEEL=$(ls "$DIST_DIR"/*.whl | head -1)
WHEEL_FILE=$(basename "$WHEEL")

echo "==> Uploading $WHEEL_FILE to s3://$BUCKET/..."
aws s3 cp "$WHEEL" "s3://$BUCKET/$WHEEL_FILE"

echo ""
echo "==> Done."
echo "    uv tool install https://$BUCKET.s3.amazonaws.com/$WHEEL_FILE"
echo "    pip install https://$BUCKET.s3.amazonaws.com/$WHEEL_FILE"

rm -rf "$DIST_DIR"
