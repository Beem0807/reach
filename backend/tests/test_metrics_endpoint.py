import os
from unittest.mock import patch


class TestMetricsEndpoint:
    def _client(self):
        from fastapi.testclient import TestClient
        from adapters.fastapi.main import app
        return TestClient(app, raise_server_exceptions=False)

    def test_exposes_prometheus_exposition(self):
        c = self._client()
        c.get("/health")  # produce at least one recorded request
        r = c.get("/metrics")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        body = r.text
        assert "reach_backend_http_requests_total" in body
        assert "reach_backend_http_request_duration_seconds" in body
        assert 'reach_backend_info{version=' in body
        # default client collectors are exported too
        assert "python_info" in body or "process_" in body

    def test_records_route_template_not_raw_id(self):
        # A per-id path must be recorded under its ROUTE TEMPLATE, never the raw id, so the
        # label set can't explode to one-series-per-agent.
        c = self._client()
        c.get("/agents/agent_raw_id_should_not_leak")
        body = c.get("/metrics").text
        assert "agent_raw_id_should_not_leak" not in body
        assert 'path="/agents/{agent_id}"' in body

    def test_unknown_http_method_is_bucketed(self):
        # A valid-token but non-standard method reaches the app (405) and must NOT leak as its
        # own label - it's bucketed to OTHER so the `method` label can't be blown up.
        c = self._client()
        c.request("FOOBAR", "/health")
        body = c.get("/metrics").text
        assert 'method="FOOBAR"' not in body
        assert 'method="OTHER"' in body

    def test_mount_traffic_labeled_by_prefix_not_unmatched(self):
        # Static-file mount traffic (/ui) has no route template; it must be bucketed as "/ui",
        # distinct from a genuine 404 (which stays "<unmatched>").
        c = self._client()
        c.get("/ui/")
        c.get("/definitely-not-a-real-path")
        body = c.get("/metrics").text
        assert 'path="/ui"' in body
        assert 'path="<unmatched>",status="404"' in body
        # a real 404 path must never leak as its own series
        assert "definitely-not-a-real-path" not in body

    def test_metrics_path_is_not_self_recorded(self):
        c = self._client()
        c.get("/metrics")
        body = c.get("/metrics").text
        assert 'path="/metrics"' not in body

    def test_token_gate_when_configured(self):
        c = self._client()
        with patch.dict(os.environ, {"METRICS_TOKEN": "s3cret"}):
            assert c.get("/metrics").status_code == 401
            assert c.get(
                "/metrics", headers={"Authorization": "Bearer s3cret"}
            ).status_code == 200
            assert c.get(
                "/metrics", headers={"Authorization": "Bearer wrong"}
            ).status_code == 401

    def test_open_by_default(self):
        c = self._client()
        # No METRICS_TOKEN in env -> open
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("METRICS_TOKEN", None)
            assert c.get("/metrics").status_code == 200

    def test_domain_gauges_when_enabled(self):
        # Opt-in gauges: once enabled + refreshed, they appear and reflect real counts
        # (aggregate, no per-tenant labels).
        from prometheus_client import REGISTRY

        from adapters.fastapi.metrics import enable_domain_gauges, refresh_domain_gauges
        from shared.store import tenants_repo

        enable_domain_gauges()
        refresh_domain_gauges()  # must not raise, even against an empty/seeded store

        assert REGISTRY.get_sample_value("reach_backend_tenants") == float(
            len(tenants_repo.list_all())
        )
        body = self._client().get("/metrics").text
        assert "reach_backend_agents" in body
        assert "reach_backend_fleets" in body
        assert "reach_backend_pending_approvals" in body
