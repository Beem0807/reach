#!/usr/bin/env bash
# Package the reach backend Helm chart and publish it to the S3-hosted Helm repo at
# https://reach-releases.s3.amazonaws.com/charts/reach
# (that path holds index.yaml + the versioned reach-<chartVersion>.tgz files).
#
# Versioning (chart version and image version are separate on purpose):
#
#   - `appVersion:` is the backend image tag and MUST equal the released backend
#     version (the image `nabeemdev/reach:<appVersion>` is built from that code by
#     scripts/release_backend.sh). It changes ONLY when the backend image changes,
#     so every appVersion is a distinct, meaningful build.
#   - `version:` is the chart release counter. It bumps on EVERY release and is
#     usually ahead of appVersion (from prior chart-only releases).
#
#   - BACKEND change (new image): bump `appVersion:` to the new backend version AND
#     bump `version:`. Run scripts/release_backend.sh (build/push image), then this.
#
#   - CHART-ONLY change (templates/values/bundled deps/etc.): bump `version:` only,
#     leave `appVersion:`. The same image is reused.
#
# The chart's image.tag defaults to appVersion, so installing by chart version alone
# (`--version`) pins the image too - there is no separate image.tag to manage.
#
# `helm repo index --merge` preserves previously published chart versions, so old
# releases stay installable (e.g. `helm install ... --version 0.1.0`).
#
# Usage:  ./scripts/release_backend_chart.sh [--bucket reach-releases] [--force]
#   --force  overwrite an already-published chart version in S3 (by default the
#            script refuses, since repo versions are meant to be immutable). Use
#            only for local/testing re-pushes - clients may have cached the old
#            tgz and won't re-download it.
set -euo pipefail

BUCKET="reach-releases"
CHART_DIR="deploy/helm/reach"
CHART_NAME="reach"
FORCE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bucket) BUCKET="$2"; shift 2 ;;
    --force)  FORCE=true; shift ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

REPO_PATH="charts/${CHART_NAME}"
REPO_URL="https://${BUCKET}.s3.amazonaws.com/${REPO_PATH}"

command -v helm >/dev/null || { echo "Error: helm is required"; exit 1; }
command -v aws  >/dev/null || { echo "Error: aws cli is required"; exit 1; }
[[ -d "$CHART_DIR" ]] || { echo "Error: run from the repo root ($CHART_DIR not found)"; exit 1; }

VERSION=$(grep -E '^version:' "$CHART_DIR/Chart.yaml" | head -1 | awk '{print $2}' | tr -d '"')
APP_VERSION=$(grep -E '^appVersion:' "$CHART_DIR/Chart.yaml" | head -1 | awk '{print $2}' | tr -d '"')
[[ -n "$VERSION" ]] || { echo "Error: could not read chart version from Chart.yaml"; exit 1; }

echo "==> Chart version: $VERSION   (appVersion / image tag: $APP_VERSION)"
echo "==> Repo URL:      $REPO_URL"

# Repo versions are immutable - refuse to republish an existing chart version
# unless --force is given.
if aws s3 ls "s3://$BUCKET/$REPO_PATH/${CHART_NAME}-$VERSION.tgz" >/dev/null 2>&1; then
  if [[ "$FORCE" == true ]]; then
    echo "==> WARNING: chart $VERSION already exists - overwriting (--force)."
    echo "    Clients that cached ${CHART_NAME}-$VERSION.tgz may not re-download it."
  else
    echo "Error: chart $VERSION is already published (${CHART_NAME}-$VERSION.tgz exists)." >&2
    echo "       Bump 'version:' in $CHART_DIR/Chart.yaml, or pass --force to overwrite." >&2
    exit 1
  fi
fi

echo ""
echo "    Reminder: did you update annotations.artifacthub.io/changes in"
echo "    $CHART_DIR/Chart.yaml for $VERSION? It's the per-version changelog"
echo "    users see via 'helm show chart'. Ctrl-C to abort and edit it."
echo ""

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# --strict fails on warnings too; lint also validates values against values.schema.json. The
# dummy secrets satisfy the template's required-secret checks so lint can render.
helm lint "$CHART_DIR" --strict --set config.tokenPepper=x --set config.sessionSigningKey=x --set config.adminPassword=x
helm package "$CHART_DIR" -d "$WORK"   # -> $WORK/${CHART_NAME}-$VERSION.tgz (bundles values.schema.json)

# Merge with the currently published index so older chart versions survive.
if aws s3 cp "s3://$BUCKET/$REPO_PATH/index.yaml" "$WORK/old-index.yaml" >/dev/null 2>&1; then
  echo "==> Merging with existing index.yaml"
  helm repo index "$WORK" --url "$REPO_URL" --merge "$WORK/old-index.yaml"
else
  echo "==> No existing index.yaml - creating a fresh one"
  helm repo index "$WORK" --url "$REPO_URL"
fi

echo "==> Uploading to s3://$BUCKET/$REPO_PATH/"
aws s3 cp "$WORK/${CHART_NAME}-$VERSION.tgz" "s3://$BUCKET/$REPO_PATH/${CHART_NAME}-$VERSION.tgz"
aws s3 cp "$WORK/index.yaml"                 "s3://$BUCKET/$REPO_PATH/index.yaml"

echo ""
echo "==> Published chart $VERSION. Install with:"
echo "    helm repo add reach $REPO_URL --force-update"
echo "    helm install reach reach/${CHART_NAME} --namespace reach --create-namespace --version $VERSION \\"
echo "      --set config.tokenPepper=<hex> --set config.sessionSigningKey=<hex> --set config.adminPassword=<pw>"
echo "    (image tag $APP_VERSION comes from the chart's appVersion - no --set image.tag needed)"
echo "    Self-contained by default (bundled Postgres + Redis); point config.databaseUrl at a"
echo "    managed Postgres for production."
