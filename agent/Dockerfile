# syntax=docker/dockerfile:1
#
# Container image for the Reach agent's KUBERNETES mode only (nabeemdev/reach-agent).
#
# This image is exclusively for running the agent as a Deployment in a cluster
# (see deploy/helm/reach-agent). Host / VM agents do NOT use it - they install
# the released binary directly via install.sh under systemd/launchd.
#
# In a cluster the agent is a gated kubectl runner: jobs execute WITHOUT a shell,
# limited to an allowlist of kubectl + read-only filters connected by pipes
# (see agent/k8s_exec.go). So there is no /bin/bash here on purpose - the runtime
# is just the agent binary, kubectl, and the few filters the model permits.
# Access is bounded by Kubernetes RBAC and the agent's policy mode.
#
# Build (buildkit auto-populates TARGETOS/TARGETARCH):
#   docker build -t nabeemdev/reach-agent:0.1.0 agent/

# ---------------------------------------------------------------------------
# Stage 1: build a static agent binary
# ---------------------------------------------------------------------------
# --platform=$BUILDPLATFORM: run the compiler natively on the build host and
# cross-compile via GOARCH below, so a multi-arch build never emulates the Go
# toolchain under QEMU.
FROM --platform=$BUILDPLATFORM golang:1.25-alpine@sha256:56961d79ea8129efddcc0b8643fd8a5416b4e6228cfd477e3fd61deb2672c587 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
ARG TARGETOS=linux
ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} \
    go build -trimpath -ldflags="-s -w" -o /out/reach-agent .

# ---------------------------------------------------------------------------
# Stage 2: build kubectl from source with patched golang.org/x/net + x/text.
#
# The official kubectl *release* binaries vendor golang.org/x/net and x/text
# versions that carry fixable HIGH CVEs (x/net/html, /idna, /http2; x/text norm),
# and no released kubectl - through the latest - vendors the fixes yet (they land
# in x/net >= 0.55 / x/text >= 0.39, newer than any Kubernetes release ships). So
# rather than download a release binary and suppress the findings, we build
# kubectl from the pinned Kubernetes source tag with those two libraries bumped to
# their fixed versions: the CVEs are *eliminated*, not ignored. Cross-compiled for
# TARGETARCH with CGO off, so it builds natively on the builder without QEMU.
#
# The KUBECTL_VERSION tag and the XNET/XTEXT pins are validated together (the gate
# scan proves 0 fixable HIGH/CRITICAL). When bumping the kubectl tag, re-check the
# pins - a newer tag may already vendor >= the fixed versions, making the replaces
# no-ops. Kubernetes only *supports* +/-1 minor skew from the API server, but
# kubectl keeps strong backward compatibility with older servers and reach only
# runs long-stable read verbs + structured writes, so this default works across a
# wide cluster range; override with --build-arg KUBECTL_VERSION=vX.Y.Z to match.
# ---------------------------------------------------------------------------
FROM --platform=$BUILDPLATFORM golang:1.25-alpine@sha256:56961d79ea8129efddcc0b8643fd8a5416b4e6228cfd477e3fd61deb2672c587 AS kubectl
ARG TARGETARCH
ARG KUBECTL_VERSION=v1.34.10
ARG XNET_VERSION=v0.55.0
ARG XTEXT_VERSION=v0.39.0
RUN apk add --no-cache git
RUN git clone --depth 1 --branch "${KUBECTL_VERSION}" \
      https://github.com/kubernetes/kubernetes /go/src/k8s.io/kubernetes
WORKDIR /go/src/k8s.io/kubernetes
RUN go mod edit \
      -replace golang.org/x/net=golang.org/x/net@${XNET_VERSION} \
      -replace golang.org/x/text=golang.org/x/text@${XTEXT_VERSION}
# GOWORK=off: the k8s repo is a go workspace, which rejects -mod=mod (needed so the
# dep bumps update go.sum during the build).
RUN GOWORK=off GOFLAGS=-mod=mod CGO_ENABLED=0 GOOS=linux GOARCH=${TARGETARCH} \
    go build -trimpath -ldflags="-s -w \
      -X k8s.io/client-go/pkg/version.gitVersion=${KUBECTL_VERSION} \
      -X k8s.io/component-base/version.gitVersion=${KUBECTL_VERSION}" \
      -o /out/kubectl ./cmd/kubectl

# ---------------------------------------------------------------------------
# Stage 3: minimal runtime - agent binary + kubectl + jq (+ busybox filters)
#
# The default allowlist's other filters (grep, head, tail, wc, sort, uniq, cut,
# tr) are provided by Alpine's busybox; jq is added explicitly.
# ---------------------------------------------------------------------------
FROM alpine:3.24@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b
RUN apk add --no-cache ca-certificates jq

COPY --chmod=0755 --from=kubectl /out/kubectl /usr/local/bin/kubectl
COPY --from=build /out/reach-agent /usr/local/bin/reach-agent

# Non-root by default; group 0 so an arbitrary assigned uid (OpenShift) still
# works. The agent writes nothing to disk (its token lives in a Secret), so a
# read-only root filesystem is fine. kubectl caches discovery under $HOME - point
# it at /tmp, which the chart backs with an emptyDir so caching/warnings are clean.
ENV HOME=/tmp
USER 10001:0
ENTRYPOINT ["/usr/local/bin/reach-agent"]
