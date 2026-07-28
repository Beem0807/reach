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

A fourth pipeline, [`.github/workflows/scripts.yml`](.github/workflows/scripts.yml), owns the
**unversioned setup scripts** (`local-setup.sh`, `lambda-setup.sh`). They're rolling "latest" tooling
served at the CDN root (they take the release tag as *input*), so they're **decoupled** from the
component versions: editing a script publishes + signs it on its own - no backend version bump. It
runs `shellcheck` on PRs and, on merge, signs a `SHA256SUMS` over both scripts (keyless cosign) and
publishes them to the bucket root. (The agent's `install.sh` is version-baked, so it stays with
`agent.yml`.)

Design principles:

- **Path-filtered.** Each pipeline triggers only on changes under its own directories - a CLI
  change never runs the backend pipeline, etc.
- **PR = verify + build, merge = release.** Pull requests run the version-bump checks, the tests,
  and a full **multi-arch image build (no push)** plus artifact-existence checks - so a broken build
  or a forgotten bump is caught at PR time, not after merge. PRs publish nothing. A push to `main` (a
  merge) publishes and tags - but only for what actually changed.
- **Immutable + guarded.** A version is published once; the pipeline refuses to overwrite an
  existing tag or artifact (you must bump the version). The existence checks run on the PR too.
- **Signed by default.** Every distributable - images, host binaries, the CLI wheel, the Helm
  charts, and the backend's Lambda template + UI bundle - is cosign-signed (keyless/OIDC) with an
  SBOM and SLSA provenance. Each release tag gets its own formal GitHub Release, and the setup
  scripts (`install.sh`, `local-setup.sh`, `lambda-setup.sh`) verify what they download.

## Anatomy of a pipeline

Every pipeline has four jobs: `checks` and `test` run on **PR and push**; `build` runs on **PRs**;
`release` runs on **merge** (push to `main`).

### 1. `checks` - secret + version-bump guard (PR **and** push)

Credential-free (just `git` + file reads), so it runs on every PR for early feedback. It:

1. **Validates required secrets exist** (`DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`/`AWS_ROLE_ARN` for
   backend & agent; `AWS_ROLE_ARN` for CLI) and fails with a clear message if one is missing - so a
   misconfigured repo is caught before merge, not in the release job.
2. **Detects what changed** - `code` vs `chart` - by diffing against the PR base (on PRs) or the
   previous commit (on push).
3. **Reads the versions** - component version + chart `version`/`appVersion` - and exposes them as
   job outputs the `build` and `release` jobs reuse (nothing is recomputed).
4. **Enforces the bump.** If the version already has a git tag, or `appVersion` != the code version,
   it fails with a specific "bump X" message **at PR time**.

### 2. `test` - the component's tests (PR **and** push)

- **backend:** `pytest` (backend) + `vitest` (UI, since the image bundles it).
- **agent:** `go test ./...`.
- **cli:** `pytest` on a **Python matrix** - the minimum supported version (`pyproject`'s
  `requires-python`, currently 3.10) and the latest - so a version-floor incompatibility (e.g. a
  3.12+ f-string reused on 3.10) fails CI instead of shipping a wheel users below the newest Python
  can't import.

### 3. `build` - build + existence check (PRs only)

`if: github.event_name == 'pull_request'`, `needs: [checks, test]`. Proves the release will succeed
**without publishing anything**:

- **Builds the image** exactly as the release will - **both** `linux/amd64` **and** `linux/arm64`
  (via QEMU) with `push: false` - so an arch-specific Dockerfile failure fails the PR, not the merge.
  (CLI has no image; it builds the wheel instead.)
- **Runs the artifact-existence checks credential-free.** PRs can't assume the main-only AWS role,
  so existence is checked against the **public registry** (`docker buildx imagetools inspect`) and
  the **public CDN** (`curl` on `releases.reach.nabeem.com` / the deployments bucket) rather than
  `aws s3 ls`. Same "bump the version" failure an author would otherwise only see on merge.
- **Layer-cached.** Both `build` and `release` use `type=gha` cache (scoped per image), so PR and
  merge builds reuse layers - the emulated arm64 build isn't repeated from scratch each run.
- **Scans the image (Trivy).** The built image is loaded and scanned via the shared
  [`trivy-scan`](.github/actions/trivy-scan) action: SARIF is uploaded to the **Security tab**
  (code-scanning alerts, per-image category) and the PR **fails on fixable `CRITICAL`/`HIGH`**
  vulnerabilities (unfixable base-image CVEs are reported but don't block). CLI has no image.

### 4. `release` - publish + sign + tag (merge to `main` only)

Runs `if: github.event_name == 'push'` and `needs: [checks, test]`. Ordered so nothing is pushed
until the guards pass:

1. **Artifact existence check** - on top of the tag check, refuses to overwrite the actual image /
   wheel / chart / binaries / Lambda bundle for this version (guards a prior run that published then
   died before tagging).
2. **Build + publish** what changed:
   - _code change:_ multi-arch image → Docker Hub (+ backend: Lambda template+code + UI bundle → S3;
     agent: host binaries + `install.sh` + `versions.json` → S3); chart → S3. (The `local/lambda-setup.sh`
     scripts are published separately by `scripts.yml` - see below.)
   - _chart-only change:_ just the chart → S3 (image reused).
3. **Scan the pushed image (Trivy)** - the same `trivy-scan` action, on the exact pushed `image@digest`,
   **before** tag/publish. A fixable `CRITICAL`/`HIGH` disclosed since the PR fails the release before
   it's finalized (the image is on Docker Hub but gets no git tag / GitHub Release). SARIF → Security tab.
4. **Sign + attest** (images): `cosign sign` (keyless) + SPDX SBOM attestation + SLSA provenance.
5. **Release integrity** ([composite action](.github/actions/release-integrity)) - run **once per
   release tag**, so a code merge produces **two** GitHub Releases:
   - `<comp>-<ver>` (code): the file artifacts (backend: Lambda `template.yaml` + `ui.tar.gz`; agent:
     host binaries + `install.sh`).
   - `<comp>-helm-<chartVer>` (chart): the chart `.tgz`.

   Each gets its **own** `SHA256SUMS`, a keyless `cosign sign-blob` over it, a source SBOM, a
   provenance attestation, and a GitHub Release with auto-generated notes. A chart-only merge
   produces just the `*-helm-*` release.
6. **Publish the signed `SHA256SUMS` to the CDN** next to the artifacts the setup scripts fetch -
   `lambda/v<ver>/` + `lambda/latest/` (backend) and `cli/v<ver>/` + `cli/latest/` (cli), matching
   how the agent already publishes to `agent/v<ver>/` - so `lambda-setup.sh` / `local-setup.sh` can
   verify the template, UI bundle, and wheel at deploy time.
7. **Tag** the repo (annotated).
8. **Invalidate the CDN** for the mutable paths (no-op until `RELEASES_CF_DISTRIBUTION_ID` is set).

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

**Each tag gets its own signed GitHub Release.** A code merge therefore cuts **two** releases - the
code one (`<comp>-<version>`) and a standalone chart one (`<comp>-helm-<chartVersion>`) - so the
Helm chart is never an orphan tag and always has its own verifiable release, whether it shipped with
code or on its own.

## Supply-chain integrity

Every release carries (see [SUPPORT.md → Verifying a release](SUPPORT.md#verifying-a-release) for
the verification commands):

- **Signed images** - `cosign sign` over the image digest, keyless via the workflow's OIDC identity
  (Sigstore/Fulcio - no long-lived key).
- **Signed checksums** - one `SHA256SUMS` per release (code and chart each get their own),
  cosign-signed; verifying it authenticates every file in that release. The backend's `SHA256SUMS`
  covers the **Lambda template + UI bundle** (signing the packaged template also pins the Lambda
  code, which `sam` references by content-hashed S3 key); the agent's covers the host binaries +
  `install.sh`; the CLI's covers the wheel.
- **SBOMs** - SPDX per image (attached as a cosign attestation) + an artifact SBOM on the code Release
  (agent → the compiled Go binary's embedded modules; CLI → the built wheel).
- **SLSA provenance** - `actions/attest-build-provenance` for image digests and file artifacts,
  verifiable with `gh attestation verify`.
- **Vulnerability scanning** - Trivy ([`trivy-scan`](.github/actions/trivy-scan)) on PR **and** release:
  - **Images** (backend, agent) - scanned on the PR (loaded build) and on the pushed `image@digest`
    at release, **before** tag/finalize. The agent image scan also covers the shipped Go **host
    binaries** (same embedded modules); the backend image covers the Lambda's Python deps.
  - **CLI** (no image) - its resolved Python **dependency tree** is scanned with `trivy fs` (install
    to a temp dir → scan exact versions) on PR and before publish.
  - Fixable `CRITICAL`/`HIGH` vulnerabilities fail the build; SARIF is uploaded to the repo's
    **Security tab** (code-scanning alerts, per-component category).
- **Deploy-time verification** - the signed `SHA256SUMS` (+ `.sig`/`.pem`) is also published to the
  CDN next to the artifacts, so the setup scripts verify before use: `install.sh` checks each agent
  binary, `lambda-setup.sh` checks the CloudFormation template + UI bundle, and `local-setup.sh`
  `cosign verify`s the backend image. Checksums are mandatory; the cosign signature is checked when
  `cosign` is installed.

The keyless signer identity is the producing workflow, e.g.
`https://github.com/Beem0807/reach/.github/workflows/agent.yml@refs/heads/main`. The installers and
setup scripts verify downloads against it; forks override the repo via `REACH_REPO` (scripts) or the
signer regexp via `RELEASES_SIGNER_ID_REGEXP` (backend-generated commands).

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

Forget a bump and the PR's `checks` job fails with the exact fix; a broken Dockerfile fails the PR's
`build` job (both arches). Merge, and the pipeline publishes, signs, attests, tags, and cuts the
GitHub Release(s) - two on a code merge (`<comp>-<ver>` + `<comp>-helm-<chartVer>`), one on a
chart-only merge.

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
| `npm ci` fails: "can only install … in sync" | `ui/package-lock.json` drifted from `package.json`. Regenerate (`npm install` in `ui/`) and commit. CI installs `npm@11` before `npm ci`, so a lock written by any npm version works - only *staleness* fails. See [ui/README.md](ui/README.md). |

## File reference

- `.github/workflows/{backend,agent,cli}.yml` - the three component pipelines.
- `.github/workflows/scripts.yml` - signs + publishes the unversioned setup scripts.
- `.github/actions/release-integrity/action.yml` - shared checksums/SBOM/sign/provenance/Release.
- `.github/actions/trivy-scan/action.yml` - shared image vulnerability scan (SARIF upload + gate).
- `deploy/cloudformation/releases.yaml` - CDN + release/deployment buckets.
- `deploy/cloudformation/github-actions.yaml` - GitHub OIDC provider + CI role.
- `deploy/lambda/template.yaml` - the SAM template `sam package` processes.
- `agent/install.sh`, `scripts/{local,lambda}-setup.sh` - consumer installers (verify + deploy).
