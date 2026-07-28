# Continuous Integration & Release

How Reach is tested, built, signed, and published. Everything is done by GitHub Actions - there are
no local or manual pushes - and every release is immutable, signed, and attested. For **verifying**
a release (as a consumer), see [SUPPORT.md](SUPPORT.md); this doc is the producer/maintainer view.

## Overview

Reach ships three independently-versioned components, each with its own pipeline:

| Component                   | Pipeline                                                         | Version source                   | Distributables                                                         |
| --------------------------- | ---------------------------------------------------------------- | -------------------------------- | ---------------------------------------------------------------------- |
| **Backend** (control plane) | [`.github/workflows/backend.yml`](.github/workflows/backend.yml) | `backend/version.py`             | image `nabeemdev/reach`, Helm chart `reach`, Lambda package            |
| **Agent**                   | [`.github/workflows/agent.yml`](.github/workflows/agent.yml)     | `agent/main.go` (`agentVersion`) | image `nabeemdev/reach-agent`, host binaries, Helm chart `reach-agent` |
| **CLI**                     | [`.github/workflows/cli.yml`](.github/workflows/cli.yml)         | `cli/reach/__init__.py`          | Python wheel                                                           |

Design principles:

- **Path-filtered.** Each pipeline triggers only on changes under its own directories - a CLI
  change never runs the backend pipeline, etc.
- **PR = verify, merge = release.** Pull requests run the version-bump checks + tests and publish
  nothing. A push to `main` (a merge) publishes and tags - but only for what actually changed.
- **Immutable + guarded.** A version is published once; the pipeline refuses to overwrite an
  existing tag or artifact (you must bump the version).
- **Signed by default.** Images and checksums are cosign-signed (keyless/OIDC), with SBOMs and
  SLSA provenance, and a formal GitHub Release per tag.

## Anatomy of a pipeline

Every pipeline has the same three jobs.

### 1. `checks` - version-bump guard (PR **and** push)

Credential-free (just `git` + file reads), so it runs on every PR (including forks) for early
feedback. It:

1. **Detects what changed** - `code` vs `chart` - by diffing against the PR base (on PRs) or the
   previous commit (on push).
2. **Reads the versions** - component version + chart `version`/`appVersion` - and exposes them as
   job outputs the `release` job reuses (nothing is recomputed).
3. **Enforces the bump.** If the version already has a git tag, or `appVersion` != the code version,
   it fails with a specific "bump X" message **at PR time** - so a forgotten bump is caught before
   merge, not after.

### 2. `test` - the component's tests (PR **and** push)

- **backend:** `pytest` (backend) + `vitest` (UI, since the image bundles it).
- **agent:** `go test ./...`.
- **cli:** `pytest`.

### 3. `release` - publish + sign + tag (merge to `main` only)

Runs `if: github.event_name == 'push'` and `needs: [checks, test]`, so it only fires after both
pass on a merge. Ordered so nothing is pushed until the guards pass:

1. **Artifact existence check** - on top of the tag check, refuses to overwrite the actual image /
   wheel / chart / binaries for this version (guards a prior run that published then died before
   tagging).
2. **Build + publish** what changed:
   - _code change:_ multi-arch image → Docker Hub (+ backend: Lambda template+code → S3, UI bundle
     - setup scripts → S3; agent: host binaries + `install.sh` + `versions.json` → S3); chart → S3.
   - _chart-only change:_ just the chart → S3 (image reused).
3. **Sign + attest** (images): `cosign sign` (keyless) + SPDX SBOM attestation + SLSA provenance.
4. **Release integrity** ([composite action](.github/actions/release-integrity)): `SHA256SUMS`
   over the file artifacts, a keyless `cosign sign-blob` over it, a source SBOM, a provenance
   attestation, and a **GitHub Release** with auto-generated notes.
5. **Tag** the repo (annotated).
6. **Invalidate the CDN** for the mutable paths (no-op until `RELEASES_CF_DISTRIBUTION_ID` is set).

The shared file-artifact steps (checksums → SBOM → signature → provenance → GitHub Release) live in
one place - the `release-integrity` composite action - so the three pipelines don't duplicate them.

## Versioning & tags

The bump rules (enforced by `checks`):

- **Code change** (`backend/**`, `ui/**`, `Dockerfile` / `agent/**` / `cli/**`) requires a **new
  component version**. For backend/agent this **also** requires a chart `version` bump **and**
  `appVersion` updated to equal the new code version (so a chart release pins an exact build).
- **Chart-only change** (`deploy/helm/reach[-agent]/**`) requires only a chart `version` bump; the
  image is reused.

Tags produced on merge:

| Change             | Tags                                               |
| ------------------ | -------------------------------------------------- |
| backend code       | `backend-<version>`, `backend-helm-<chartVersion>` |
| backend chart-only | `backend-helm-<chartVersion>`                      |
| agent code         | `agent-<version>`, `agent-helm-<chartVersion>`     |
| agent chart-only   | `agent-helm-<chartVersion>`                        |
| cli                | `cli-<version>`                                    |

## Supply-chain integrity

Every release carries (see [SUPPORT.md → Verifying a release](SUPPORT.md#verifying-a-release) for
the verification commands):

- **Signed images** - `cosign sign` over the image digest, keyless via the workflow's OIDC identity
  (Sigstore/Fulcio - no long-lived key).
- **Signed checksums** - one `SHA256SUMS` per release, cosign-signed; verifying it authenticates
  every file.
- **SBOMs** - SPDX per image (attached as a cosign attestation) + a source SBOM on the Release.
- **SLSA provenance** - `actions/attest-build-provenance` for image digests and file artifacts,
  verifiable with `gh attestation verify`.

The keyless signer identity is the producing workflow, e.g.
`https://github.com/Beem0807/reach/.github/workflows/agent.yml@refs/heads/main`. The agent installer
verifies downloads against it; forks override it via `RELEASES_SIGNER_ID_REGEXP`.

## Distribution & hosting

Two buckets, provisioned by [`deploy/cloudformation/releases.yaml`](deploy/cloudformation/releases.yaml):

- **`reach-releases`** - the public artifact host, served over a CDN at
  **`https://releases.reach.nabeem.com`** (CloudFront over the private bucket, OAC-locked). CI
  writes to S3; users read via the domain (`RELEASES_BASE_URL`). Everything is edge-cached; the
  pipelines invalidate the mutable pointers (`index.yaml`, `*/latest/*`, `versions.json`, setup
  scripts) on publish.
- **`reach-deployments`** - the SAM/Lambda **template + code**. Not CDN-fronted, because
  CloudFormation reads them straight from `s3://` during a Lambda deploy (`--template-url` requires
  an S3 URL). Opt-in public-read (`MakeLambdaCodePublic`) for cross-account deploys.

## Prerequisites (one-time setup)

**Repo secrets:**

| Secret                                  | Used for                                              |
| --------------------------------------- | ----------------------------------------------------- |
| `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` | pushing images to Docker Hub                          |
| `AWS_ROLE_ARN`                          | the IAM role assumed via GitHub OIDC (no static keys) |

**Repo variable:**

| Variable                      | Used for                                                                         |
| ----------------------------- | -------------------------------------------------------------------------------- |
| `RELEASES_CF_DISTRIBUTION_ID` | the CloudFront distribution ID, so the invalidation step fires (no-op until set) |

**AWS (CloudFormation):**

1. Deploy [`deploy/cloudformation/releases.yaml`](deploy/cloudformation/releases.yaml) in
   **us-east-1** with your ACM cert ARN (cert must be in us-east-1). Note its
   `ReleasesDistributionId` output → set the repo variable above. Set `MakeLambdaCodePublic=true`
   if end users deploy the Lambda backend from their own accounts.
2. Deploy [`deploy/cloudformation/github-actions.yaml`](deploy/cloudformation/github-actions.yaml)
   (the GitHub OIDC provider + CI role) with that distribution ID. The role grants S3 write to
   `reach-releases` + `reach-deployments` and `cloudfront:CreateInvalidation` on the distribution,
   scoped to `repo:<org>/reach:ref:refs/heads/main`.

**Images** go to Docker Hub (`nabeemdev/*`) via the Docker Hub token, not AWS - the OIDC role is
S3/CloudFront only.

## Cutting a release

Do the bump in a PR; merging to `main` releases it.

- **Backend:** bump `__version__` in `backend/version.py` **and** `version:` + `appVersion:` in
  `deploy/helm/reach/Chart.yaml` (appVersion must equal the new backend version).
- **Agent:** bump `agentVersion` in `agent/main.go` **and** `version:` + `appVersion:` in
  `deploy/helm/reach-agent/Chart.yaml`.
- **CLI:** bump `__version__` in `cli/reach/__init__.py`.
- **Chart only:** bump just `version:` in the relevant `Chart.yaml`, and add a line to its
  `annotations.artifacthub.io/changes` (Artifact Hub's per-version changelog).

Forget a bump and the PR's `checks` job fails with the exact fix. Merge, and the pipeline publishes,
signs, attests, tags, and cuts the GitHub Release.

## Release notes & changelog

- **GitHub Releases are the changelog.** Each tag gets a formal GitHub Release with
  auto-generated notes (`generate_release_notes` - from the PRs/commits since the last tag). Because
  releases are per-component and per-version, this is a natural, low-maintenance history.
- **Charts** additionally use the `artifacthub.io/changes` annotation in `Chart.yaml`, which
  Artifact Hub renders per chart version.

A separate hand-maintained `CHANGELOG.md` is **not required** and is generally redundant here - see
the note at the end of this doc.

## Troubleshooting

| Symptom                                                 | Cause / fix                                                                                                              |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `checks` fails: "tag `<x>` already exists - bump …"     | You changed code/chart without bumping the version. Bump it.                                                             |
| `checks` fails: "chart appVersion must equal … version" | The chart `appVersion` drifted from the code version. Align them.                                                        |
| `release` fails: "image/wheel/chart already exists"     | A prior run published this version but didn't tag (partial failure). Bump the version, or manually complete the release. |
| Invalidation step skipped                               | `RELEASES_CF_DISTRIBUTION_ID` repo variable isn't set yet.                                                               |
| AWS step denied                                         | The OIDC role is missing a permission or the trust isn't scoped to this repo/branch.                                     |

## File reference

- `.github/workflows/{backend,agent,cli}.yml` - the three pipelines.
- `.github/actions/release-integrity/action.yml` - shared checksums/SBOM/sign/provenance/Release.
- `deploy/cloudformation/releases.yaml` - CDN + release/deployment buckets.
- `deploy/cloudformation/github-actions.yaml` - GitHub OIDC provider + CI role.
- `deploy/lambda/template.yaml` - the SAM template `sam package` processes.
- `agent/install.sh`, `scripts/{local,lambda}-setup.sh` - consumer installers (verify + deploy).
