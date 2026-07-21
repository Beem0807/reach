"""Prometheus metrics for the FastAPI backend, served at GET /metrics.

Exposes HTTP RED metrics (rate, errors, duration) keyed by the matched **route template**
(so a per-id path like /jobs/{job_id} is one series, not one-per-id), plus the client's
default process / platform / GC collectors. These are read-only operational counters - no
request bodies, tokens, or command output are ever recorded, so the endpoint carries nothing
sensitive (same posture as the agent's /metrics). Restrict it to your monitoring network, or
set the METRICS_TOKEN env var to require a bearer token.
"""
import logging
import os
import time

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

_REQUESTS = Counter(
    "reach_backend_http_requests_total",
    "HTTP requests handled by the backend, by method, route template, and status.",
    ["method", "path", "status"],
)
_DURATION = Histogram(
    "reach_backend_http_request_duration_seconds",
    "HTTP request duration in seconds, by method and route template.",
    ["method", "path"],
)
_IN_PROGRESS = Gauge(
    "reach_backend_http_requests_in_progress",
    "In-flight HTTP requests, by method.",
    ["method"],
)
_INFO = Gauge("reach_backend_info", "Backend build info (constant 1).", ["version"])
_START = Gauge(
    "reach_backend_start_timestamp_seconds",
    "Backend process start time (unix seconds).",
)
_START.set(time.time())

# The endpoint's own path - never recorded (a self-scrape would inflate the counters).
_METRICS_PATH = "/metrics"

# Known HTTP methods. The `method` label comes straight off the wire and any valid token is
# accepted by the server (e.g. `curl -X FOOBAR` reaches the app and 405s), so an attacker could
# spray arbitrary methods to blow up label cardinality. Bucket anything unknown to "OTHER".
_HTTP_METHODS = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE", "CONNECT"}
)


def _method_label(scope: Scope) -> str:
    method = scope.get("method", "GET")
    return method if method in _HTTP_METHODS else "OTHER"


def set_build_info(version: str) -> None:
    """Record the running version as a constant `reach_backend_info{version="..."} 1`."""
    _INFO.labels(version=version or "unknown").set(1)


# Sub-apps mounted on the router (currently just the console's static files at /ui) serve
# requests without setting a route template, so they'd otherwise fall through to "<unmatched>"
# and be indistinguishable from real 404s. Bucket them by their mount prefix instead. ONLY known
# prefixes get a real label - an arbitrary not-found path must never become its own series.
_MOUNT_PREFIXES = ("/ui",)


def _route_template(scope: Scope, raw_path: str) -> str:
    """The matched route's path template (e.g. /jobs/{job_id}) to bound label cardinality.
    Falls back to a mounted sub-app's prefix (e.g. /ui), then to a single "<unmatched>" bucket
    for genuine not-found requests."""
    route = scope.get("route")
    template = getattr(route, "path", None)
    if template:
        return template
    for prefix in _MOUNT_PREFIXES:
        if raw_path == prefix or raw_path.startswith(prefix + "/"):
            return prefix
    return "<unmatched>"


class PrometheusMiddleware:
    """Pure-ASGI middleware: times each HTTP request and records count / duration / in-flight,
    keyed by the route TEMPLATE resolved downstream (so cardinality stays bounded)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") == _METRICS_PATH:
            await self.app(scope, receive, send)
            return

        method = _method_label(scope)
        raw_path = scope.get("path", "")  # captured before routing, for the mount-prefix fallback
        status = 500  # if the app never sends a response.start, it errored
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        _IN_PROGRESS.labels(method=method).inc()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _IN_PROGRESS.labels(method=method).dec()
            path = _route_template(scope, raw_path)
            _REQUESTS.labels(method=method, path=path, status=str(status)).inc()
            _DURATION.labels(method=method, path=path).observe(time.perf_counter() - start)


def metrics_authorized(auth_header: str) -> bool:
    """Open by default; if METRICS_TOKEN is set, require a matching bearer token."""
    token = os.getenv("METRICS_TOKEN")
    if not token:
        return True
    return auth_header == f"Bearer {token}"


def render_metrics() -> tuple[bytes, str]:
    """The exposition payload and its content type."""
    return generate_latest(), CONTENT_TYPE_LATEST


# ---------------------------------------------------------------------------
# Opt-in domain gauges (METRICS_DOMAIN_GAUGES=true)
# ---------------------------------------------------------------------------
# Point-in-time counts of domain state, refreshed by a background job (NOT per scrape, so
# /metrics stays cheap). They reveal deployment scale (e.g. tenant/agent counts), so they are
# OFF by default and only registered when explicitly enabled - keeping the baseline /metrics
# footprint to low-sensitivity HTTP/process telemetry. Aggregate-only: no per-tenant labels
# (avoids both cardinality blow-up and any cross-tenant signal).

_AGENT_STATUSES = ("ACTIVE", "INACTIVE", "CREATED", "REVOKED", "DELETED")
_domain: dict = {}


def enable_domain_gauges() -> None:
    """Register the opt-in domain gauges. Call once at startup when the feature is on;
    idempotent. When not called, these series never appear in the exposition."""
    if _domain:
        return
    _domain["agents"] = Gauge(
        "reach_backend_agents", "Agents by status, across all tenants.", ["status"]
    )
    _domain["fleets"] = Gauge("reach_backend_fleets", "Fleets, across all tenants.")
    _domain["tenants"] = Gauge("reach_backend_tenants", "Tenants (workspaces).")
    _domain["pending_approvals"] = Gauge(
        "reach_backend_pending_approvals", "Pending approval requests, across all tenants."
    )
    _domain["errors"] = Counter(
        "reach_backend_gauge_refresh_errors_total", "Domain-gauge refresh cycles that failed."
    )


def refresh_domain_gauges() -> None:
    """Recompute the domain gauges from the repos (aggregate across all tenants). Best-effort:
    never raises - a failure just increments reach_backend_gauge_refresh_errors_total. No-op
    when the gauges aren't enabled."""
    if not _domain:
        return
    try:
        from shared.store import agents_repo, approvals_repo, fleets_repo, tenants_repo

        tenants = tenants_repo.list_all()
        by_status = {s: 0 for s in _AGENT_STATUSES}
        fleets = 0
        pending = 0
        for t in tenants:
            tid = t.get("tenant_id")
            if not tid:
                continue
            for a in agents_repo.list_by_tenant(tid):
                st = a.get("status") or "UNKNOWN"
                by_status[st] = by_status.get(st, 0) + 1
            fleets += len(fleets_repo.list_by_tenant(tid))
            pending += len(approvals_repo.list_by_tenant(tid, status="pending"))

        for st, n in by_status.items():
            _domain["agents"].labels(status=st).set(n)
        _domain["fleets"].set(fleets)
        _domain["tenants"].set(len(tenants))
        _domain["pending_approvals"].set(pending)
    except Exception:  # a metrics refresh must never take down the scheduler
        _domain["errors"].inc()
        logger.exception("domain gauge refresh failed")
