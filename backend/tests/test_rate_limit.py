"""Rate limiter must stay up when its storage backend is unreachable.

With a shared store (e.g. Redis) configured via RATE_LIMIT_STORAGE_URI, a store
outage would otherwise raise on every rate-limited request and 500 the whole API,
turning the limiter into a hard dependency that can take the service down. The
Limiter is built with in_memory_fallback_enabled=True (transparently switch to a
per-process in-memory limiter during the outage, auto-recovering when the store
returns) plus swallow_errors=True as a final fail-open backstop. Either way a store
outage must never 500 a request.
"""
from unittest.mock import patch


class TestRateLimitFailOpen:
    def _client(self):
        from fastapi.testclient import TestClient
        from adapters.fastapi.main import app
        return TestClient(app, raise_server_exceptions=False)

    def test_limiter_configured_to_swallow_errors(self):
        from adapters.fastapi.main import limiter
        assert limiter._swallow_errors is True

    def test_store_outage_does_not_500_the_request(self):
        from adapters.fastapi.main import limiter

        c = self._client()
        # Simulate the rate-limit store being unreachable: every hit() raises.
        with patch.object(
            limiter.limiter, "hit", side_effect=ConnectionError("rate-limit store down")
        ):
            # /agent/claim is rate-limited (120/minute). A bad body returns 400 from the
            # handler - which proves the request got PAST the limiter rather than 500ing.
            r = c.post("/agent/claim", content=b"not json")
        assert r.status_code != 500, r.text
        assert r.status_code == 400

    def test_healthy_store_still_enforces_limits(self):
        # Sanity: with the store working, the decorator still counts requests (a hit()
        # is attempted) - i.e. swallow_errors didn't disable limiting outright.
        from adapters.fastapi.main import limiter

        c = self._client()
        with patch.object(limiter.limiter, "hit", wraps=limiter.limiter.hit) as spy:
            c.post("/agent/claim", content=b"not json")
        assert spy.called
