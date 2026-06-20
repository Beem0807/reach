import logging

from shared.auth import _bearer, _verify_tenant_token
from shared.response import _err, _ok

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_me(raw_token: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)

    return _ok({
        "user_id": user["user_id"],
        "tenant_id": user["tenant_id"],
        "name": user.get("name"),
        "username": user.get("username"),
        "role": user.get("role"),
        "created_at": user.get("created_at"),
    })


def me_handler(event, context):
    logger.info("GET /me")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    return handle_me(token)
