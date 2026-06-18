import json
from decimal import Decimal

from shared.response import _err, _iso, _iso_offset, _now, _ok


def test_ok_default_status():
    r = _ok({"foo": "bar"})
    assert r["statusCode"] == 200
    assert json.loads(r["body"]) == {"foo": "bar"}


def test_ok_custom_status():
    r = _ok({"id": "x"}, 201)
    assert r["statusCode"] == 201


def test_ok_has_content_type():
    r = _ok({})
    assert r["headers"]["Content-Type"] == "application/json"


def test_err_default_status():
    r = _err("something went wrong")
    assert r["statusCode"] == 400
    assert json.loads(r["body"])["error"] == "something went wrong"


def test_err_custom_status():
    r = _err("not found", 404)
    assert r["statusCode"] == 404


def test_decimal_int_serialised():
    r = _ok({"n": Decimal("42")})
    assert json.loads(r["body"])["n"] == 42


def test_decimal_float_serialised():
    r = _ok({"n": Decimal("3.14")})
    assert abs(json.loads(r["body"])["n"] - 3.14) < 0.001


def test_now_returns_int():
    n = _now()
    assert isinstance(n, int)
    assert n > 0


def test_iso_returns_utc_string():
    s = _iso()
    assert "T" in s
    assert s.endswith("+00:00")


def test_iso_offset_future():
    import time
    before = time.time()
    s = _iso_offset(60)
    # The offset string should represent a time roughly 60s from now
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(s)
    assert dt.timestamp() > before + 50
