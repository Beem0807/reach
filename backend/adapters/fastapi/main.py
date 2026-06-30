import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from handlers.admin_agents import handle_list_agents_admin
from handlers.admin_tenants import (
    handle_create_tenant, handle_list_tenants,
    handle_disable_tenant, handle_enable_tenant, handle_delete_tenant,
    handle_create_tenant_admin_user, handle_platform_reset_user_password, handle_platform_disable_user,
    handle_platform_set_user_role, handle_platform_update_user_name,
)
from handlers.tenant_login import handle_tenant_login, handle_change_password, handle_tenant_me
from handlers.tenant_users import (
    handle_list_tenant_users, handle_create_tenant_user,
    handle_disable_tenant_user, handle_enable_tenant_user, handle_delete_tenant_user,
    handle_set_user_role, handle_reset_user_password,
    handle_get_user_agents, handle_set_user_agents,
)
from handlers.tenant_tokens import handle_create_api_token, handle_list_api_tokens, handle_revoke_api_token, handle_rename_api_token, handle_revoke_all_user_tokens
from handlers.tenant_agents import (
    handle_acknowledge_capability,
    handle_create_tenant_agent,
    handle_reissue_tenant_install_token,
    handle_revoke_tenant_agent,
    handle_delete_tenant_agent,
    handle_remove_tenant_agent,
    handle_set_tenant_agent_tags,
    handle_set_tenant_agent_mode,
    handle_request_agent_rotation,
    handle_get_agent_history,
    handle_list_agent_versions,
)
from handlers.tenant_approvals import (
    handle_list_my_pending,
    handle_list_agent_approved,
    handle_tenant_list_all_approvals,
    handle_tenant_review_approval,
    handle_tenant_create_approval,
    handle_tenant_delete_approval,
)
from handlers.audit_logs import handle_list_platform_audit_logs, handle_list_tenant_audit_logs
from handlers.admin_users import handle_list_users
from shared.tenant_auth import verify_tenant_token
from shared.auth import _verify_tenant_token
from handlers.agent_claim import handle_agent_claim
from handlers.agent_job_result import handle_agent_job_result
from handlers.agent_rotate_token import handle_agent_rotate_token
from handlers.agent_sync import handle_agent_sync
from handlers.create_job import handle_create_job
from handlers.me import handle_me
from handlers.get_agent import handle_get_agent
from handlers.get_job import handle_get_job
from handlers.heartbeat import handle_heartbeat_check
from shared.admin_auth import verify_session_token
from handlers.admin_login import handle_admin_login
from handlers.list_agents import handle_list_agents
from handlers.list_jobs import handle_list_jobs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _resp(result: dict) -> JSONResponse:
    """Convert Lambda-style response dict to a FastAPI JSONResponse."""
    return JSONResponse(
        content=json.loads(result["body"]),
        status_code=result["statusCode"],
    )


def _token(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _rate_limit_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.client.host if request.client else None) or "unknown"


# In-memory counters are per-process, so they only enforce correctly on a single
# instance. To rate limit correctly across multiple backend replicas, set
# RATE_LIMIT_STORAGE_URI to a shared store, e.g. redis://host:6379. Defaults to
# in-memory, which is right for a single instance or local dev.
_RATE_LIMIT_STORAGE = os.environ.get("RATE_LIMIT_STORAGE_URI", "memory://")
limiter = Limiter(key_func=_rate_limit_key, storage_uri=_RATE_LIMIT_STORAGE)


async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse({"error": "rate limit exceeded"}, status_code=429)


scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(handle_heartbeat_check, "interval", minutes=1, id="heartbeat")
    scheduler.start()
    logger.info("Heartbeat scheduler started (every 1 minute)")
    yield
    scheduler.shutdown()


app = FastAPI(title="reach API", version="1.0.0", lifespan=lifespan)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

# Docker image copies built UI assets to ui_dist; local dev uses ui/dist directly
_UI_DIST = os.path.join(os.path.dirname(__file__), "..", "..", "ui_dist")
if not os.path.isdir(_UI_DIST):
    _UI_DIST = os.path.join(os.path.dirname(__file__), "..", "..", "..", "ui", "dist")
if os.path.isdir(_UI_DIST):
    app.mount("/ui", StaticFiles(directory=_UI_DIST, html=True), name="ui")


# ---------------------------------------------------------------------------
# Agent endpoints (agent-to-server)
# ---------------------------------------------------------------------------

@app.post("/agent/claim")
@limiter.limit("5/hour")
async def agent_claim(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_agent_claim(body))


@app.post("/agent/sync")
@limiter.limit("60/minute")
async def agent_sync(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_agent_sync(body, token))


@app.post("/agent/rotate-token")
@limiter.limit("10/hour")
async def agent_rotate_token(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_agent_rotate_token(body, token))


@app.post("/agent/jobs/{job_id}/result")
@limiter.limit("60/minute")
async def agent_job_result(job_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_agent_job_result(job_id, body, token))


# ---------------------------------------------------------------------------
# Tenant (CLI / SDK) endpoints
# ---------------------------------------------------------------------------

@app.get("/me")
@limiter.limit("120/minute")
async def me(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_me(token))


@app.post("/jobs", status_code=201)
@limiter.limit("30/minute")
async def create_job(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_create_job(body, token))


@app.get("/jobs")
@limiter.limit("120/minute")
async def list_jobs(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    qs = request.query_params
    agent_filter = qs.get("agent_id")
    cursor = qs.get("cursor")
    try:
        limit = max(1, min(int(qs.get("limit", 20)), 100))
    except (ValueError, TypeError):
        limit = 20
    return _resp(handle_list_jobs(token, agent_filter, limit, cursor))


@app.get("/jobs/{job_id}")
@limiter.limit("120/minute")
async def get_job(job_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_get_job(job_id, token))


@app.get("/agents")
@limiter.limit("60/minute")
async def list_agents(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    tag = request.query_params.get("tag")
    return _resp(handle_list_agents(token, tag))


@app.get("/agents/{agent_id}")
@limiter.limit("120/minute")
async def get_agent(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_get_agent(agent_id, token))


@app.get("/approvals/pending")
@limiter.limit("60/minute")
async def list_my_pending(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    query = dict(request.query_params)
    return _resp(handle_list_my_pending(query, token))


@app.get("/agents/{agent_id}/approved-commands")
@limiter.limit("60/minute")
async def list_agent_approved(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    status = request.query_params.get("status", "approved")
    return _resp(handle_list_agent_approved(agent_id, token, status=status))


# ---------------------------------------------------------------------------
# Platform admin login
# ---------------------------------------------------------------------------

@app.post("/admin/login")
@limiter.limit("10/minute")
async def admin_login(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    ip = request.client.host if request.client else ""
    return _resp(handle_admin_login(body, ip))


# ---------------------------------------------------------------------------
# Platform admin - tenant management
# ---------------------------------------------------------------------------

@app.post("/admin/tenants", status_code=201)
@limiter.limit("20/minute")
async def create_tenant(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ip = request.client.host if request.client else ""
    return _resp(handle_create_tenant(body, token, ip))


@app.get("/admin/tenants")
@limiter.limit("120/minute")
async def list_tenants(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_list_tenants(token))


@app.post("/admin/tenants/{tenant_id}/disable")
@limiter.limit("20/minute")
async def disable_tenant(tenant_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    ip = request.client.host if request.client else ""
    return _resp(handle_disable_tenant(tenant_id, token, ip))


@app.post("/admin/tenants/{tenant_id}/enable")
@limiter.limit("20/minute")
async def enable_tenant(tenant_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    ip = request.client.host if request.client else ""
    return _resp(handle_enable_tenant(tenant_id, token, ip))


@app.delete("/admin/tenants/{tenant_id}")
@limiter.limit("10/minute")
async def delete_tenant(tenant_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_delete_tenant(tenant_id, token))


@app.post("/admin/tenants/{tenant_id}/admin-users", status_code=201)
@limiter.limit("20/minute")
async def create_platform_tenant_user(tenant_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ip = request.client.host if request.client else ""
    return _resp(handle_create_tenant_admin_user(tenant_id, body, token, ip))


@app.get("/admin/tenants/{tenant_id}/users")
@limiter.limit("120/minute")
async def list_users(tenant_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_list_users(tenant_id, token))


@app.post("/admin/tenants/{tenant_id}/users/{user_id}/reset-password")
@limiter.limit("10/minute")
async def platform_reset_user_password(tenant_id: str, user_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    ip = request.client.host if request.client else ""
    return _resp(handle_platform_reset_user_password(tenant_id, user_id, token, ip))


@app.post("/admin/tenants/{tenant_id}/users/{user_id}/disable")
@limiter.limit("20/minute")
async def platform_disable_user(tenant_id: str, user_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    ip = request.client.host if request.client else ""
    return _resp(handle_platform_disable_user(tenant_id, user_id, token, ip))


@app.patch("/admin/tenants/{tenant_id}/users/{user_id}/role")
@limiter.limit("20/minute")
async def platform_set_user_role(tenant_id: str, user_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ip = request.client.host if request.client else ""
    return _resp(handle_platform_set_user_role(tenant_id, user_id, body, token, ip))


@app.patch("/admin/tenants/{tenant_id}/users/{user_id}/name")
@limiter.limit("20/minute")
async def platform_update_user_name(tenant_id: str, user_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    return _resp(handle_platform_update_user_name(tenant_id, user_id, body, token))


@app.get("/admin/agents")
@limiter.limit("120/minute")
async def list_agents_admin(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    tenant_id = request.query_params.get("tenant_id", "")
    tag = request.query_params.get("tag") or None
    return _resp(handle_list_agents_admin(tenant_id, token, tag))


@app.get("/admin/audit-logs")
@limiter.limit("60/minute")
async def platform_audit_logs(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    qs = request.query_params
    cursor = qs.get("cursor")
    try:
        limit = max(1, min(int(qs.get("limit", 100)), 200))
    except (ValueError, TypeError):
        limit = 100
    return _resp(handle_list_platform_audit_logs(token, limit, cursor,
        action=qs.get("action"), actor=qs.get("actor"),
        resource=qs.get("resource"), ip=qs.get("ip"),
        since=qs.get("since"), until=qs.get("until")))


# ---------------------------------------------------------------------------
# Tenant admin - auth
# ---------------------------------------------------------------------------

@app.post("/tenant/login")
@limiter.limit("10/minute")
async def tenant_login(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    ip = request.client.host if request.client else ""
    return _resp(handle_tenant_login(body, ip))


@app.post("/tenant/me/password")
@limiter.limit("10/minute")
async def tenant_change_password(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    payload = verify_tenant_token(token)
    if not payload:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    ip = request.client.host if request.client else ""
    return _resp(handle_change_password(body, payload, ip))


@app.get("/tenant/me")
@limiter.limit("120/minute")
async def tenant_me(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    # API-key-aware so both console sessions and CLI/MCP API tokens can introspect.
    user = _verify_tenant_token(token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return _resp(handle_tenant_me(user))


# ---------------------------------------------------------------------------
# Tenant admin - user management
# ---------------------------------------------------------------------------

@app.get("/tenant/users")
@limiter.limit("60/minute")
async def tenant_list_users(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    payload = verify_tenant_token(token)
    if not payload:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return _resp(handle_list_tenant_users(payload))


@app.post("/tenant/users", status_code=201)
@limiter.limit("20/minute")
async def tenant_create_user(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    payload = verify_tenant_token(token)
    if not payload:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ip = request.client.host if request.client else ""
    return _resp(handle_create_tenant_user(body, payload, ip))


@app.post("/tenant/users/{user_id}/disable")
@limiter.limit("20/minute")
async def tenant_disable_user(user_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    payload = verify_tenant_token(token)
    if not payload:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    ip = request.client.host if request.client else ""
    return _resp(handle_disable_tenant_user(user_id, payload, ip))


@app.post("/tenant/users/{user_id}/enable")
@limiter.limit("20/minute")
async def tenant_enable_user(user_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    payload = verify_tenant_token(token)
    if not payload:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    ip = request.client.host if request.client else ""
    return _resp(handle_enable_tenant_user(user_id, payload, ip))


@app.delete("/tenant/users/{user_id}")
@limiter.limit("20/minute")
async def tenant_delete_user(user_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    payload = verify_tenant_token(token)
    if not payload:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    ip = request.client.host if request.client else ""
    return _resp(handle_delete_tenant_user(user_id, payload, ip))


@app.post("/tenant/users/{user_id}/revoke-tokens")
@limiter.limit("20/minute")
async def tenant_revoke_user_tokens(user_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    payload = verify_tenant_token(token)
    if not payload:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    ip = request.client.host if request.client else ""
    return _resp(handle_revoke_all_user_tokens(user_id, payload, ip))


@app.put("/tenant/users/{user_id}/role")
@limiter.limit("20/minute")
async def tenant_set_user_role(user_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    payload = verify_tenant_token(token)
    if not payload:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ip = request.client.host if request.client else ""
    return _resp(handle_set_user_role(user_id, body, payload, ip))


@app.post("/tenant/users/{user_id}/reset-password")
@limiter.limit("10/minute")
async def tenant_reset_user_password(user_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    payload = verify_tenant_token(token)
    if not payload:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    ip = request.client.host if request.client else ""
    return _resp(handle_reset_user_password(user_id, payload, ip))


@app.get("/tenant/users/{user_id}/agents")
@limiter.limit("60/minute")
async def tenant_get_user_agents(user_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    payload = verify_tenant_token(token)
    if not payload:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return _resp(handle_get_user_agents(user_id, payload))


@app.put("/tenant/users/{user_id}/agents")
@limiter.limit("30/minute")
async def tenant_set_user_agents(user_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    payload = verify_tenant_token(token)
    if not payload:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    return _resp(handle_set_user_agents(user_id, body, payload))


# ---------------------------------------------------------------------------
# Tenant admin - agent management
# ---------------------------------------------------------------------------

@app.get("/tenant/agent-versions")
async def tenant_agent_versions(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    agent_type = request.query_params.get("type", "host")
    return _resp(handle_list_agent_versions(agent_type, token))


@app.post("/tenant/agents", status_code=201)
@limiter.limit("20/minute")
async def tenant_create_agent(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    api_url = str(request.base_url).rstrip("/")
    return _resp(handle_create_tenant_agent(body, token, api_url))


@app.post("/tenant/agents/{agent_id}/reissue-install-token", status_code=201)
@limiter.limit("10/minute")
async def tenant_reissue_install_token(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    api_url = str(request.base_url).rstrip("/")
    return _resp(handle_reissue_tenant_install_token(agent_id, body, token, api_url))


@app.post("/tenant/agents/{agent_id}/revoke")
@limiter.limit("30/minute")
async def tenant_revoke_agent(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_revoke_tenant_agent(agent_id, token))


@app.delete("/tenant/agents/{agent_id}/remove")
@limiter.limit("20/minute")
async def tenant_remove_agent(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_remove_tenant_agent(agent_id, token))


@app.delete("/tenant/agents/{agent_id}")
@limiter.limit("30/minute")
async def tenant_delete_agent(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_delete_tenant_agent(agent_id, token))


@app.put("/tenant/agents/{agent_id}/tags")
@limiter.limit("30/minute")
async def tenant_set_agent_tags(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_set_tenant_agent_tags(agent_id, body, token))


@app.post("/tenant/agents/{agent_id}/request-rotation")
@limiter.limit("10/minute")
async def tenant_request_agent_rotation(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_request_agent_rotation(agent_id, token))


@app.post("/tenant/agents/{agent_id}/acknowledge-capability")
@limiter.limit("30/minute")
async def tenant_acknowledge_capability(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_acknowledge_capability(agent_id, body, token))


@app.put("/tenant/agents/{agent_id}/policy/mode")
@limiter.limit("30/minute")
async def tenant_set_agent_mode(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_set_tenant_agent_mode(agent_id, body, token))


@app.get("/tenant/agents/{agent_id}/history")
@limiter.limit("60/minute")
async def tenant_agent_history(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    # Delegate to the shared handler so tenant-boundary + per-user agent scope are
    # enforced consistently with the Lambda adapter.
    return _resp(handle_get_agent_history(agent_id, token))


# ---------------------------------------------------------------------------
# Tenant admin - approval management
# ---------------------------------------------------------------------------

@app.get("/tenant/approvals")
@limiter.limit("60/minute")
async def tenant_list_all_approvals(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_tenant_list_all_approvals(dict(request.query_params), token))


@app.post("/tenant/approvals", status_code=201)
@limiter.limit("30/minute")
async def tenant_create_approval(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_tenant_create_approval(body, token))


@app.put("/tenant/approvals/{approval_id}/approve")
@limiter.limit("60/minute")
async def tenant_approve_approval(approval_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    return _resp(handle_tenant_review_approval(approval_id, "approve", token, body))


@app.put("/tenant/approvals/{approval_id}/deny")
@limiter.limit("60/minute")
async def tenant_deny_approval(approval_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    return _resp(handle_tenant_review_approval(approval_id, "deny", token, body))


@app.delete("/tenant/approvals/{approval_id}")
@limiter.limit("30/minute")
async def tenant_delete_approval(approval_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_tenant_delete_approval(approval_id, token))


# ---------------------------------------------------------------------------
# Tenant admin - API tokens
# ---------------------------------------------------------------------------

@app.get("/tenant/api-tokens")
@limiter.limit("60/minute")
async def tenant_list_tokens(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    payload = verify_tenant_token(token)
    if not payload:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return _resp(handle_list_api_tokens(payload))


@app.post("/tenant/api-tokens", status_code=201)
@limiter.limit("20/minute")
async def tenant_create_token(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    payload = verify_tenant_token(token)
    if not payload:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ip = request.client.host if request.client else ""
    return _resp(handle_create_api_token(body, payload, ip))


@app.patch("/tenant/api-tokens/{token_id}")
@limiter.limit("30/minute")
async def tenant_rename_token(token_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    payload = verify_tenant_token(token)
    if not payload:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    ip = request.client.host if request.client else ""
    return _resp(handle_rename_api_token(token_id, body, payload, ip))


@app.delete("/tenant/api-tokens/{token_id}")
@limiter.limit("20/minute")
async def tenant_revoke_token(token_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    payload = verify_tenant_token(token)
    if not payload:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    ip = request.client.host if request.client else ""
    return _resp(handle_revoke_api_token(token_id, payload, ip))


# ---------------------------------------------------------------------------
# Tenant admin - audit logs
# ---------------------------------------------------------------------------

@app.get("/tenant/audit-logs")
@limiter.limit("60/minute")
async def tenant_audit_logs(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    qs = request.query_params
    cursor = qs.get("cursor")
    try:
        limit = max(1, min(int(qs.get("limit", 100)), 200))
    except (ValueError, TypeError):
        limit = 100
    return _resp(handle_list_tenant_audit_logs(token, limit, cursor,
        action=qs.get("action"), actor=qs.get("actor"),
        resource=qs.get("resource"), ip=qs.get("ip"),
        since=qs.get("since"), until=qs.get("until")))


# ---------------------------------------------------------------------------
# Root redirect → UI
# ---------------------------------------------------------------------------

@app.get("/")
@limiter.limit("120/minute")
async def root(request: Request):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/ui/", status_code=301)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
@limiter.limit("120/minute")
async def health(request: Request):
    return {"status": "ok"}
