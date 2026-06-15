#!/usr/bin/env bash
# Build all agent binaries and upload to S3.
# Usage: ./scripts/release_agent.sh [--bucket reach-releases]

set -euo pipefail

BUCKET="reach-releases"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bucket) BUCKET="$2"; shift 2 ;;
    *) echo "Usage: $0 [--bucket <s3-bucket>]"; exit 1 ;;
  esac
done

AGENT_DIR="$(cd "$(dirname "$0")/../agent" && pwd)"
cd "$AGENT_DIR"

echo "==> Building linux/amd64..."
GOOS=linux  GOARCH=amd64 go build -o reach-agent-linux-amd64 .

echo "==> Building linux/arm64..."
GOOS=linux  GOARCH=arm64 go build -o reach-agent-linux-arm64 .

echo "==> Building darwin/arm64 (Apple Silicon)..."
GOOS=darwin GOARCH=arm64 go build -o reach-agent-darwin-arm64 .

echo "==> Building darwin/amd64 (Intel Mac)..."
GOOS=darwin GOARCH=amd64 go build -o reach-agent-darwin-amd64 .

echo "==> Uploading to s3://$BUCKET/..."
aws s3 cp reach-agent-linux-amd64   "s3://$BUCKET/reach-agent-linux-amd64"
aws s3 cp reach-agent-linux-arm64   "s3://$BUCKET/reach-agent-linux-arm64"
aws s3 cp reach-agent-darwin-arm64  "s3://$BUCKET/reach-agent-darwin-arm64"
aws s3 cp reach-agent-darwin-amd64  "s3://$BUCKET/reach-agent-darwin-amd64"
aws s3 cp install.sh                "s3://$BUCKET/install.sh"

echo ""
echo "==> Done. Published:"
echo "    https://$BUCKET.s3.amazonaws.com/reach-agent-linux-amd64"
echo "    https://$BUCKET.s3.amazonaws.com/reach-agent-linux-arm64"
echo "    https://$BUCKET.s3.amazonaws.com/reach-agent-darwin-arm64"
echo "    https://$BUCKET.s3.amazonaws.com/reach-agent-darwin-amd64"
echo "    https://$BUCKET.s3.amazonaws.com/install.sh"
