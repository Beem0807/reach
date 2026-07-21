"""
Route parity between the two backend adapters.

The backend runs in two deployments that define their routes independently:

  * Docker / FastAPI -> ``backend/adapters/fastapi/main.py`` (``@app.<method>("...")``)
  * AWS Lambda       -> ``deploy/lambda/template.yaml``      (HttpApi ``Path:``/``Method:``)

Nothing forces these two lists to agree, so they can silently drift -- an
endpoint wired in FastAPI but forgotten in the SAM template (or a method
mismatch) ships a feature that works on Docker and 404s on Lambda. These tests
fail the moment the two adapters expose a different ``{method, path}`` set, so
the drift is caught in CI instead of in production.

Both adapters are parsed from source (no import side effects): the FastAPI
decorators by regex, the SAM template by pairing each ``Path:`` with the
``Method:`` that follows it. Path parameters are normalised (``{anything}`` ->
``{}``) so ``{id}`` and ``{user_id}`` compare equal.

Two differences are intentional and encoded explicitly below:

  1. ``GET /``, ``GET /health`` and ``GET /metrics`` are FastAPI-only. On Lambda the
     root and health/liveness concerns are handled by API Gateway / CloudFront, and
     metrics go to CloudWatch rather than a Prometheus pull endpoint.
  2. Lambda routes approve+deny through one combined ``PUT
     /tenant/approvals/{approval_id}/{action}`` handler; FastAPI splits it into
     ``.../approve`` and ``.../deny``. Functionally identical -- clients hit the
     same concrete paths -- so the combined route is expanded before comparing.

If you add an endpoint, add it to *both* adapters. If a genuinely new
intentional difference appears, update the constants below (and say why).
"""
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_MAIN_PY = os.path.join(_HERE, "..", "adapters", "fastapi", "main.py")
_TEMPLATE = os.path.join(_HERE, "..", "..", "deploy", "lambda", "template.yaml")

_METHODS = ("get", "post", "put", "patch", "delete")


def _norm(path: str) -> str:
    """Collapse every path parameter to ``{}`` so names don't matter."""
    return re.sub(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", "{}", path)


def _fastapi_routes() -> set:
    routes = set()
    decorator = re.compile(r'@app\.(' + "|".join(_METHODS) + r')\("([^"]+)"')
    with open(_MAIN_PY) as fh:
        for line in fh:
            m = decorator.search(line)
            if m:
                routes.add((m.group(1).upper(), _norm(m.group(2))))
    return routes


def _lambda_routes() -> set:
    """Pair each HttpApi ``Path:`` with the ``Method:`` line that follows it."""
    routes = set()
    pending_path = None
    path_re = re.compile(r"Path:\s*(\S+)")
    method_re = re.compile(r"Method:\s*(\S+)")
    with open(_TEMPLATE) as fh:
        for line in fh:
            pm = path_re.search(line)
            if pm:
                pending_path = _norm(pm.group(1).strip())
                continue
            mm = method_re.search(line)
            if mm and pending_path is not None:
                routes.add((mm.group(1).strip().upper(), pending_path))
                pending_path = None
    return routes


# --- intentional, documented differences -----------------------------------

# FastAPI-only application routes (handled by infra, not app code, on Lambda).
# /metrics is a Prometheus PULL endpoint, which doesn't fit Lambda's ephemeral model
# (metrics there go to CloudWatch), so it's exposed only by the container backend.
FASTAPI_ONLY = {
    ("GET", "/"),
    ("GET", "/health"),
    ("GET", "/metrics"),
}

# Lambda's combined approval-review route and the concrete FastAPI equivalents.
APPROVALS_COMBINED = ("PUT", "/tenant/approvals/{}/{}")
APPROVALS_EXPANDED = {
    ("PUT", "/tenant/approvals/{}/approve"),
    ("PUT", "/tenant/approvals/{}/deny"),
}


def _reconciled():
    """Return (fastapi_set, lambda_set) with intentional differences applied."""
    fast = _fastapi_routes() - FASTAPI_ONLY
    lam = _lambda_routes()
    if APPROVALS_COMBINED in lam:
        lam = (lam - {APPROVALS_COMBINED}) | APPROVALS_EXPANDED
    return fast, lam


def _fmt(routes) -> str:
    return "\n".join(f"  {m} {p}" for m, p in sorted(routes)) or "  (none)"


# --- tests ------------------------------------------------------------------

def test_parsers_find_routes():
    """Guard against a broken parser making the parity test vacuously pass."""
    assert len(_fastapi_routes()) > 40, "FastAPI route parser found too few routes"
    assert len(_lambda_routes()) > 40, "Lambda template parser found too few routes"


def test_fastapi_and_lambda_expose_the_same_routes():
    fast, lam = _reconciled()

    missing_in_lambda = fast - lam
    missing_in_fastapi = lam - fast

    assert not missing_in_lambda and not missing_in_fastapi, (
        "FastAPI and Lambda adapters have drifted.\n\n"
        "In FastAPI (main.py) but NOT wired in the SAM template "
        "(deploy/lambda/template.yaml):\n"
        f"{_fmt(missing_in_lambda)}\n\n"
        "In the SAM template but NOT in FastAPI:\n"
        f"{_fmt(missing_in_fastapi)}\n\n"
        "Add the endpoint to both adapters, or update the intentional-difference "
        "constants in this test if the divergence is deliberate."
    )


def test_intentional_difference_constants_are_still_real():
    """If these stop being true, the equivalences above are stale and the parity
    test could pass for the wrong reason -- fail loudly so they get updated."""
    fast_raw = _fastapi_routes()
    lam_raw = _lambda_routes()

    assert FASTAPI_ONLY <= fast_raw, (
        "FASTAPI_ONLY lists routes that no longer exist in main.py: "
        f"{sorted(FASTAPI_ONLY - fast_raw)}"
    )
    assert APPROVALS_COMBINED in lam_raw, (
        "Expected combined approvals route "
        f"{APPROVALS_COMBINED} is no longer in the SAM template."
    )
    assert APPROVALS_EXPANDED <= fast_raw, (
        "FastAPI no longer defines the split approve/deny routes: "
        f"{sorted(APPROVALS_EXPANDED - fast_raw)}"
    )
