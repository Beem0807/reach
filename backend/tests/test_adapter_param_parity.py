"""Query-param forwarding parity between the two adapters.

test_adapter_parity guards the *route* set ({method, path}); it does NOT check that
each adapter forwards the same query parameters to the shared handler. A FastAPI route
that silently drops a `?filter=` the Lambda handler reads (the exact bug behind the audit
`tenant` filter) would pass that test but break in one deployment.

This test invokes BOTH adapters for each filtered GET endpoint with the same superset of
query params, patches the shared handler, and asserts both adapters bind identical
arguments onto it.
"""
import inspect
import os

import pytest

pytest.importorskip("fastapi")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from unittest.mock import patch  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import adapters.fastapi.main as fa  # noqa: E402
from adapters.fastapi.main import app  # noqa: E402
import handlers.audit_logs as audit_logs  # noqa: E402
import handlers.cli_fleets as cli_fleets  # noqa: E402
import handlers.jobs_fanout as jobs_fanout  # noqa: E402
import handlers.list_agents as list_agents_mod  # noqa: E402
import handlers.list_jobs as list_jobs_mod  # noqa: E402

_OK = {"statusCode": 200, "headers": {}, "body": "{}"}

# A superset of query params; each endpoint ignores the ones it doesn't read. The point
# is that whatever it *does* read, both adapters read the same way.
_QS = {
    "action": "user.login", "actor": "alice", "resource": "res_1", "ip": "1.2.3.4",
    "tenant": "tenant_x", "since": "2026-01-01", "until": "2026-02-01",
    "agent_id": "agent_1", "fleet_id": "fleet_1", "run_id": "run_1", "q": "docker",
    "tag": "env:prod", "mode": "wild", "access": "open", "type": "host", "fleet": "fleet_x",
    "limit": "50", "cursor": "cur_1", "offset": "5",
}

# (fastapi path, concrete path params, lambda handler fn, shared handle fn, its module)
CASES = [
    ("/admin/audit-logs", {}, audit_logs.platform_audit_logs_handler,
     audit_logs.handle_list_platform_audit_logs, audit_logs),
    ("/tenant/audit-logs", {}, audit_logs.tenant_audit_logs_handler,
     audit_logs.handle_list_tenant_audit_logs, audit_logs),
    ("/fleets/fleet_1/runs", {"fleet_id": "fleet_1"}, cli_fleets.list_fleet_runs_handler,
     cli_fleets.handle_cli_list_fleet_runs, cli_fleets),
    ("/jobs/runs", {}, jobs_fanout.list_tag_runs_handler,
     jobs_fanout.handle_list_tag_runs, jobs_fanout),
    ("/jobs", {}, list_jobs_mod.list_jobs_handler,
     list_jobs_mod.handle_list_jobs, list_jobs_mod),
    ("/agents", {}, list_agents_mod.list_agents_handler,
     list_agents_mod.handle_list_agents, list_agents_mod),
]


def _bound(fn, call):
    """Normalize a call (positional or keyword) into the handler's argument dict."""
    ba = inspect.signature(fn).bind(*call.args, **call.kwargs)
    ba.apply_defaults()
    return dict(ba.arguments)


@pytest.mark.parametrize("path, path_params, lambda_fn, handle_fn, handle_mod",
                         CASES, ids=[c[0] for c in CASES])
def test_adapters_forward_same_query_params(path, path_params, lambda_fn, handle_fn, handle_mod):
    client = TestClient(app, raise_server_exceptions=False)
    name = handle_fn.__name__
    # Same token string via both paths so the token arg matches and only param forwarding differs.
    with patch.object(fa, name) as mf, patch.object(handle_mod, name) as ml:
        mf.return_value = _OK
        ml.return_value = _OK
        client.get(path, params=_QS, headers={"authorization": "Bearer tok"})
        lambda_fn({"headers": {"authorization": "Bearer tok"},
                   "queryStringParameters": dict(_QS),
                   "pathParameters": path_params,
                   "requestContext": {}}, None)

    assert mf.call_args is not None, f"FastAPI route {path} never reached the handler"
    assert ml.call_args is not None, f"Lambda handler for {path} never reached the handler"
    fa_args = _bound(handle_fn, mf.call_args)
    lambda_args = _bound(handle_fn, ml.call_args)
    assert fa_args == lambda_args, (
        f"{path}: adapters forward different arguments\n"
        f"  FastAPI: {fa_args}\n  Lambda:  {lambda_args}"
    )
