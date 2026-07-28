# Support & Release Integrity

Reach controls production machines, so the distribution path is held to the same bar as the
runtime. Every release is built by GitHub Actions (no local/manual pushes), and every artifact is
checksummed, signed, and accompanied by an SBOM and SLSA build provenance. This page covers which
versions are supported and how to verify what you download.

## Supported versions

Reach is released per component, each with its own version and tag:

| Component               | Version source                   | Tag(s)                                     | Distribution                                                             |
| ----------------------- | -------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------ |
| Backend (control plane) | `backend/version.py`             | `backend-<ver>`, `backend-helm-<chartVer>` | image `nabeemdev/reach`, chart `charts/reach`, Lambda bundle             |
| Agent                   | `agent/main.go` (`agentVersion`) | `agent-<ver>`, `agent-helm-<chartVer>`     | image `nabeemdev/reach-agent`, host binaries, chart `charts/reach-agent` |
| CLI                     | `cli/reach/__init__.py`          | `cli-<ver>`                                | wheel (`cli/v<ver>/`)                                                    |

Support policy:

- The **latest release** of each component is always supported and receives security and bug fixes.
- While Reach is pre-1.0 (`0.x`), fixes land on the latest `0.x` release; there is no long-term
  back-porting. Pin to a version you have verified, and upgrade forward to receive fixes.
- After `1.0`, the **current and previous minor** of each component receive security fixes;
  older minors are end-of-life. Breaking changes are confined to major bumps.
- The chart `appVersion` always equals the image/agent version it deploys (enforced in CI), so a
  chart release pins a specific, verifiable application build.

Report a vulnerability privately per [SECURITY.md](SECURITY.md) - do not open a public issue.

## Installing the agent

There are two install paths; both run only signed, verified artifacts.

### Host agent (Linux / macOS binary)

The tenant console generates a ready-to-run command (pinned to a version, or to `latest`). It
fetches the version's `install.sh` and pipes it to a privileged shell:

```bash
curl -fsSL https://releases.reach.nabeem.com/agent/v<ver>/install.sh \
  | sudo bash -s -- --api-url "https://api.example.com" --install-token "install_xxx" --yes
```

`install.sh` then downloads the matching `reach-agent-<os>-<arch>` binary and **verifies it before
installing** (see [`agent/install.sh`](agent/install.sh)):

1. Fetches `SHA256SUMS` from the same versioned directory and checks the binary's SHA-256. On any
   mismatch - or if the binary isn't listed - it refuses to install. **This check always runs.**
2. If `cosign` is installed, it also fetches `SHA256SUMS.sig` + `SHA256SUMS.pem` and runs
   `cosign verify-blob` against this repo's release-workflow identity (keyless/Sigstore). A failed
   signature check aborts the install. Without `cosign` it prints a note and proceeds on the
   checksum alone.

So **install `cosign` first** on any machine that matters, to get cryptographic (not just integrity)
verification:

```bash
# example - see https://docs.sigstore.dev/cosign/installation
brew install cosign            # macOS
# or download the release binary for Linux and put it on PATH
```

**Verify `install.sh` itself before running it** (recommended for production hosts - the script is
fetched over `curl | bash`, but it *is* part of the signed release, so you can authenticate it
first):

```bash
V=https://releases.reach.nabeem.com/agent/v<ver>
curl -fsSLO "$V/install.sh"
curl -fsSLO "$V/SHA256SUMS" -O "$V/SHA256SUMS.sig" -O "$V/SHA256SUMS.pem"
cosign verify-blob --certificate SHA256SUMS.pem --signature SHA256SUMS.sig \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp "^https://github.com/Beem0807/reach/\.github/workflows/agent\.yml@" \
  SHA256SUMS                                   # authenticate the checksum file
sha256sum --ignore-missing -c SHA256SUMS       # authenticate install.sh (and any files present)
sudo bash install.sh --api-url "…" --install-token "install_xxx" --yes
```

- **Pinning:** `agent/v<ver>/` installs an exact, immutable version; `agent/latest/` tracks the
  newest. Pin in production and upgrade deliberately.
- **Air-gapped hosts:** download `install.sh`, `reach-agent-<os>-<arch>`, `SHA256SUMS`,
  `SHA256SUMS.sig`, and `SHA256SUMS.pem` from `agent/v<ver>/` (or the GitHub Release) and verify
  them on a connected machine. Then either mirror that directory to an internal HTTP host and edit
  `BINARY_BASE_URL` at the top of `install.sh` to point at it (the script re-verifies against the
  mirrored `SHA256SUMS`), or place the verified binary on `PATH` and write the config by hand.
- `install.sh --uninstall` removes the agent, its service, user, and config.

### Kubernetes agent (Helm chart)

In-cluster agents run the signed container image via the `reach-agent` chart (the console emits a
`helm upgrade --install …` command). The chart's `appVersion` equals the agent version, so a pinned
chart pins an exact, signed image. Verify the image before rollout:

```bash
cosign verify nabeemdev/reach-agent:<ver> \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp "^https://github.com/Beem0807/reach/\.github/workflows/agent\.yml@"
```

For admission-time enforcement, wire this identity into a policy controller (e.g. Kyverno
`verifyImages` or the Sigstore policy-controller) so the cluster only admits `nabeemdev/reach-agent`
images signed by this repo's workflow. See "[Verifying a release](#verifying-a-release)" for the
image SBOM and provenance checks.

## What every release publishes

- **Formal GitHub Release** per tag, with auto-generated notes.
- **`SHA256SUMS`** over all file artifacts, plus a **keyless cosign signature** (`SHA256SUMS.sig`)
  and its short-lived certificate (`SHA256SUMS.pem`). Verifying the checksums file authenticates
  every file in the release.
- **Signed container images** - `cosign sign` (keyless) over the image digest.
- **SBOMs** - SPDX for each image (attached as a cosign attestation) and a source SBOM
  (`sbom.spdx.json`) attached to the Release.
- **SLSA build provenance** - attestations for image digests and for file artifacts, stored in
  GitHub's attestation store (verifiable with `gh attestation verify`).

Signing is **keyless** (Sigstore/Fulcio via the workflow's OIDC identity): there is no long-lived
signing key. The signer identity is this repository's release workflow, e.g.
`https://github.com/Beem0807/reach/.github/workflows/agent.yml@refs/heads/main`, issued by
`https://token.actions.githubusercontent.com`.

## Verifying a release

Set your repo once:

```bash
REPO=Beem0807/reach
ISSUER=https://token.actions.githubusercontent.com
```

### 1. Checksums + signature (binaries, wheel, chart)

Download `SHA256SUMS`, `SHA256SUMS.sig`, `SHA256SUMS.pem`, and the artifacts from the GitHub
Release (or, for agent binaries, from `https://releases.reach.nabeem.com/agent/v<ver>/`).

```bash
# Integrity: every listed file matches.
sha256sum -c SHA256SUMS

# Authenticity: the checksums were signed by the expected release workflow.
# Use the workflow that produced the artifact: agent.yml | backend.yml | cli.yml
cosign verify-blob \
  --certificate SHA256SUMS.pem --signature SHA256SUMS.sig \
  --certificate-oidc-issuer "$ISSUER" \
  --certificate-identity-regexp "^https://github.com/${REPO}/\.github/workflows/agent\.yml@" \
  SHA256SUMS
```

The agent installer (`install.sh`) performs both of these automatically and refuses to install on
mismatch; install `cosign` beforehand to get the signature check (the checksum check always runs).

### 2. Container images

```bash
IMG=nabeemdev/reach-agent:<ver>   # or nabeemdev/reach:<ver>

# Signature (use backend.yml for the backend image).
cosign verify "$IMG" \
  --certificate-oidc-issuer "$ISSUER" \
  --certificate-identity-regexp "^https://github.com/${REPO}/\.github/workflows/agent\.yml@"

# SBOM attestation (SPDX).
cosign verify-attestation --type spdxjson "$IMG" \
  --certificate-oidc-issuer "$ISSUER" \
  --certificate-identity-regexp "^https://github.com/${REPO}/\.github/workflows/agent\.yml@"
```

### 3. SLSA provenance (GitHub attestations)

```bash
# Image, by digest:
gh attestation verify oci://nabeemdev/reach-agent@<digest> --repo "$REPO"

# A downloaded file artifact (binary, wheel, chart tgz):
gh attestation verify ./reach-agent-linux-amd64 --repo "$REPO"
```

`<digest>` comes from `docker buildx imagetools inspect nabeemdev/reach-agent:<ver>` or the Release
notes. A successful verification proves the artifact was built by this repo's workflow from the
expected source, unmodified since.
