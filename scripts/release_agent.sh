#!/usr/bin/env bash
# Build the agent host binaries (-> S3) and the Kubernetes container image (-> registry).
#
# Host installs use the S3 binaries via install.sh; Kubernetes installs use the
# multi-arch image referenced by deploy/helm/reach-agent (image.repository).
#
# Usage:
#   ./scripts/release_agent.sh [--bucket reach-releases] [--image-repo nabeemdev/reach-agent]
#                              [--platforms linux/amd64,linux/arm64] [--skip-image] [--skip-binaries]

set -euo pipefail

BUCKET="reach-releases"
IMAGE_REPO="nabeemdev/reach-agent"
IMAGE_PLATFORMS="linux/amd64,linux/arm64"
SKIP_IMAGE=false
SKIP_BINARIES=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bucket)        BUCKET="$2";          shift 2 ;;
    --image-repo)    IMAGE_REPO="$2";      shift 2 ;;
    --platforms)     IMAGE_PLATFORMS="$2"; shift 2 ;;
    --skip-image)    SKIP_IMAGE=true;      shift ;;
    --skip-binaries) SKIP_BINARIES=true;   shift ;;
    *) echo "Usage: $0 [--bucket <s3-bucket>] [--image-repo <repo>] [--platforms <list>] [--skip-image] [--skip-binaries]"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Preflight: verify the tools each selected step needs, before any build/upload.
# ---------------------------------------------------------------------------
need() { command -v "$1" >/dev/null 2>&1 || { echo "Error: $1 is required$2"; exit 1; }; }
need go ""
if [[ "$SKIP_IMAGE" == false ]]; then
  need docker " for the multi-arch agent image (or pass --skip-image)"
  docker buildx version >/dev/null 2>&1 || { echo "Error: docker buildx is required for the multi-arch agent image (or pass --skip-image)"; exit 1; }
fi
if [[ "$SKIP_BINARIES" == false ]]; then
  need aws " to upload host binaries to S3 (or pass --skip-binaries)"
  need jq " to update agent/versions.json (or pass --skip-binaries)"
fi

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

# ---------------------------------------------------------------------------
# Kubernetes container image (multi-arch) -> registry
# Host installs never use this; it is what deploy/helm/reach-agent runs.
# ---------------------------------------------------------------------------
if [[ "$SKIP_IMAGE" == false ]]; then
  echo "==> Building + pushing image ${IMAGE_REPO}:${VERSION} (${IMAGE_PLATFORMS})..."
  # Multi-platform builds need a docker-container builder; create one if absent.
  if ! docker buildx inspect reach-builder >/dev/null 2>&1; then
    docker buildx create --name reach-builder --driver docker-container >/dev/null
  fi
  # Tagged with the bare version to match the chart's image.tag default (appVersion).
  docker buildx build \
    --builder reach-builder \
    --platform "${IMAGE_PLATFORMS}" \
    -t "${IMAGE_REPO}:${VERSION}" \
    -t "${IMAGE_REPO}:latest" \
    --push \
    "$AGENT_DIR"
  echo "    Pushed ${IMAGE_REPO}:${VERSION} and :latest"
fi

# ---------------------------------------------------------------------------
# Host binaries + install.sh -> S3
# ---------------------------------------------------------------------------
if [[ "$SKIP_BINARIES" == false ]]; then
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

  echo "==> Uploading agent/latest/..."
  aws s3 cp reach-agent-linux-amd64  "s3://$BUCKET/agent/latest/reach-agent-linux-amd64"
  aws s3 cp reach-agent-linux-arm64  "s3://$BUCKET/agent/latest/reach-agent-linux-arm64"
  aws s3 cp reach-agent-darwin-arm64 "s3://$BUCKET/agent/latest/reach-agent-darwin-arm64"
  aws s3 cp reach-agent-darwin-amd64 "s3://$BUCKET/agent/latest/reach-agent-darwin-amd64"
  aws s3 cp "$TMP_LATEST"            "s3://$BUCKET/agent/latest/install.sh"

  # Maintain agent/versions.json: the published index the create UI reads over
  # plain HTTP to populate its version dropdown (portable - no S3 API needed by
  # readers). Merge this release in, keep it unique and newest-first.
  echo "==> Updating agent/versions.json..."
  EXISTING=$(aws s3 cp "s3://$BUCKET/agent/versions.json" - 2>/dev/null || echo '[]')
  TMP_VERSIONS=$(mktemp)
  printf '%s' "$EXISTING" | jq --arg v "$VERSION" \
    '(. + [$v]) | unique | sort_by(split(".") | map(tonumber? // 0)) | reverse' > "$TMP_VERSIONS"
  aws s3 cp "$TMP_VERSIONS" "s3://$BUCKET/agent/versions.json" --content-type application/json

  rm -f "$TMP_VERSIONED" "$TMP_LATEST" "$TMP_VERSIONS"
fi

echo ""
echo "==> Done (v${VERSION})."
if [[ "$SKIP_BINARIES" == false ]]; then
  echo "    binaries: https://$BUCKET.s3.amazonaws.com/agent/v${VERSION}/ (+ agent/latest/)"
fi
if [[ "$SKIP_IMAGE" == false ]]; then
  echo "    image:    ${IMAGE_REPO}:${VERSION} (+ :latest)"
fi
