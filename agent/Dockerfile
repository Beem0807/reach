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
FROM golang:1.24-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
ARG TARGETOS=linux
ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} \
    go build -trimpath -ldflags="-s -w" -o /out/reach-agent .

# ---------------------------------------------------------------------------
# Stage 2: minimal runtime - kubectl + jq (+ busybox grep/head/tail/wc/...)
#
# kubectl is version-skew tolerant (+/-1 minor); bump KUBECTL_VERSION as needed.
# The default allowlist's other filters (grep, head, tail, wc, sort, uniq, cut,
# tr) are provided by Alpine's busybox; jq is added explicitly.
# ---------------------------------------------------------------------------
FROM alpine:3.20
ARG TARGETARCH
ARG KUBECTL_VERSION=v1.32.0
RUN apk add --no-cache ca-certificates jq \
 && wget -qO /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
 && chmod 0755 /usr/local/bin/kubectl

COPY --from=build /out/reach-agent /usr/local/bin/reach-agent

# Non-root by default; group 0 so an arbitrary assigned uid (OpenShift) still
# works. The agent writes nothing to disk (its token lives in a Secret), so a
# read-only root filesystem is fine. kubectl caches discovery under $HOME - point
# it at /tmp, which the chart backs with an emptyDir so caching/warnings are clean.
ENV HOME=/tmp
USER 10001:0
ENTRYPOINT ["/usr/local/bin/reach-agent"]
