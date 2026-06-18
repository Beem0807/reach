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

VERSION=$(grep 'agentVersion' main.go | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
if [[ -z "$VERSION" ]]; then
  echo "Error: could not read agentVersion from main.go"
  exit 1
fi
echo "==> Version: $VERSION"

echo "==> Running tests..."
(
  cd "$AGENT_DIR"
  go test ./... -count=1 -timeout 120s
)

echo "==> Building linux/amd64..."
GOOS=linux  GOARCH=amd64 go build -o reach-agent-linux-amd64 .

echo "==> Building linux/arm64..."
GOOS=linux  GOARCH=arm64 go build -o reach-agent-linux-arm64 .

echo "==> Building darwin/arm64 (Apple Silicon)..."
GOOS=darwin GOARCH=arm64 go build -o reach-agent-darwin-arm64 .

echo "==> Building darwin/amd64 (Intel Mac)..."
GOOS=darwin GOARCH=amd64 go build -o reach-agent-darwin-amd64 .

echo "==> Baking version into install.sh..."
TMP_VERSIONED=$(mktemp)
TMP_LATEST=$(mktemp)
sed "s|__AGENT_VERSION__|agent/v${VERSION}|" install.sh > "$TMP_VERSIONED"
sed "s|__AGENT_VERSION__|agent/latest|"      install.sh > "$TMP_LATEST"

echo "==> Uploading agent/v${VERSION}/..."
aws s3 cp reach-agent-linux-amd64  "s3://$BUCKET/agent/v${VERSION}/reach-agent-linux-amd64"
aws s3 cp reach-agent-linux-arm64  "s3://$BUCKET/agent/v${VERSION}/reach-agent-linux-arm64"
aws s3 cp reach-agent-darwin-arm64 "s3://$BUCKET/agent/v${VERSION}/reach-agent-darwin-arm64"
aws s3 cp reach-agent-darwin-amd64 "s3://$BUCKET/agent/v${VERSION}/reach-agent-darwin-amd64"
aws s3 cp "$TMP_VERSIONED"         "s3://$BUCKET/agent/v${VERSION}/install.sh"
aws s3 cp uninstall.sh             "s3://$BUCKET/agent/v${VERSION}/uninstall.sh"

echo "==> Uploading agent/latest/..."
aws s3 cp reach-agent-linux-amd64  "s3://$BUCKET/agent/latest/reach-agent-linux-amd64"
aws s3 cp reach-agent-linux-arm64  "s3://$BUCKET/agent/latest/reach-agent-linux-arm64"
aws s3 cp reach-agent-darwin-arm64 "s3://$BUCKET/agent/latest/reach-agent-darwin-arm64"
aws s3 cp reach-agent-darwin-amd64 "s3://$BUCKET/agent/latest/reach-agent-darwin-amd64"
aws s3 cp "$TMP_LATEST"            "s3://$BUCKET/agent/latest/install.sh"
aws s3 cp uninstall.sh             "s3://$BUCKET/agent/latest/uninstall.sh"

rm -f "$TMP_VERSIONED" "$TMP_LATEST"

echo ""
echo "==> Done. Published v${VERSION}:"
echo "    https://$BUCKET.s3.amazonaws.com/agent/v${VERSION}/reach-agent-linux-amd64"
echo "    https://$BUCKET.s3.amazonaws.com/agent/v${VERSION}/reach-agent-linux-arm64"
echo "    https://$BUCKET.s3.amazonaws.com/agent/v${VERSION}/reach-agent-darwin-arm64"
echo "    https://$BUCKET.s3.amazonaws.com/agent/v${VERSION}/reach-agent-darwin-amd64"
echo "    https://$BUCKET.s3.amazonaws.com/agent/v${VERSION}/install.sh"
echo "    https://$BUCKET.s3.amazonaws.com/agent/v${VERSION}/uninstall.sh"
echo ""
echo "    agent/latest/ also updated."
