import pytest
from shared.tags import former_fleet_tag, validate_tags


def test_empty_list_is_valid():
    assert validate_tags([]) is None


def test_former_fleet_tag_is_valid_and_slugified():
    # Always produces a valid lowercase key:value tag, slugifying the name.
    for name, expected in [
        ("test", "oldfleet:test"),
        ("web-prod", "oldfleet:web-prod"),
        ("My Fleet!", "oldfleet:my-fleet"),          # uppercase/space/punct -> slug
        ("fleet_dpBsh35B_pt7Ew", "oldfleet:fleet_dpbsh35b_pt7ew"),  # id has uppercase
        ("", "oldfleet:unknown"),                    # empty -> unknown
    ]:
        tag = former_fleet_tag(name)
        assert tag == expected
        assert validate_tags([tag]) is None


def test_single_valid_tag():
    assert validate_tags(["env:prod"]) is None


def test_multiple_valid_tags():
    assert validate_tags(["env:prod", "region:us-east-1", "team:infra"]) is None


def test_digits_in_key_and_value():
    assert validate_tags(["k8s:1-2-3", "version:1.2.3"]) is None


def test_hyphens_and_underscores():
    assert validate_tags(["my-key:my-value", "my_key:my_value"]) is None


def test_dot_allowed_in_value():
    assert validate_tags(["host:api.example.com"]) is None


def test_no_colon_is_invalid():
    err = validate_tags(["envprod"])
    assert err is not None
    assert "envprod" in err


def test_uppercase_key_is_invalid():
    err = validate_tags(["ENV:prod"])
    assert err is not None


def test_uppercase_value_is_invalid():
    err = validate_tags(["env:PROD"])
    assert err is not None


def test_space_is_invalid():
    err = validate_tags(["env:prod staging"])
    assert err is not None


def test_dot_not_allowed_in_key():
    err = validate_tags(["my.key:value"])
    assert err is not None


def test_non_list_input():
    assert validate_tags("env:prod") is not None
    assert validate_tags(None) is not None
    assert validate_tags({"env": "prod"}) is not None


def test_non_string_element():
    err = validate_tags([123])
    assert err is not None


def test_mixed_valid_and_invalid_reports_invalid():
    err = validate_tags(["env:prod", "BAD"])
    assert err is not None
    assert "BAD" in err


def test_empty_key_is_invalid():
    assert validate_tags([":value"]) is not None


def test_empty_value_is_invalid():
    assert validate_tags(["key:"]) is not None


def test_multiple_colons_invalid():
    # key:val:extra is invalid (value contains colon - not in allowed chars)
    assert validate_tags(["key:val:extra"]) is not None
