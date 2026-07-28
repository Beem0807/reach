import json

from shared.error_page import NOT_FOUND_HTML


def not_found_handler(event, context):
    """Catch-all for paths that match no defined route (wired to `ANY /{proxy+}`).

    Content-negotiated: a browser (Accept: text/html) gets a friendly page; everything else keeps
    JSON, so API clients that hit a wrong path still parse a normal error body. Replaces API
    Gateway's bare ``{"message":"Not Found"}`` for browser navigations.
    """
    headers = event.get("headers") or {}
    # API Gateway (HTTP API) lowercases header keys, but be defensive either way.
    accept = headers.get("accept") or headers.get("Accept") or ""
    if "text/html" in accept:
        return {
            "statusCode": 404,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": NOT_FOUND_HTML,
        }
    return {
        "statusCode": 404,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": "not found"}),
    }
