import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from handlers.admin_agents import (
    handle_add_agent_tags,
    handle_create_agent,
    handle_delete_agent,
    handle_get_agent_tags,
    handle_list_agents_admin,
    handle_reissue_install_token,
    handle_remove_agent,
    handle_remove_agent_tags,
    handle_revoke_agent,
    handle_set_agent_tags,
)
from handlers.admin_jobs import handle_list_jobs_admin
from handlers.admin_tenants import handle_create_tenant, handle_list_tenants
from handlers.admin_users import (
    handle_create_user,
    handle_delete_user,
    handle_grant_agent_access,
    handle_get_user_agents,
    handle_list_users,
    handle_revoke_agent_access,
    handle_rotate_user_token,
    handle_set_user_agents,
)
from handlers.admin_approvals import handle_delete_approval, handle_list_approvals, handle_pre_approve_command, handle_review_approval
from handlers.admin_policy import (
    handle_get_policy,
    handle_set_mode,
)
from handlers.agent_claim import handle_agent_claim
from handlers.agent_job_result import handle_agent_job_result
from handlers.agent_rotate_token import handle_agent_rotate_token
from handlers.agent_sync import handle_agent_sync
from handlers.create_job import handle_create_job
from handlers.me import handle_me
from handlers.get_agent import handle_get_agent
from handlers.get_job import handle_get_job
from handlers.heartbeat import handle_heartbeat_check
from handlers.list_agents import handle_list_agents
from handlers.list_jobs import handle_list_jobs
from handlers.tenant_approvals import handle_list_agent_approved, handle_list_my_pending

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


limiter = Limiter(key_func=_rate_limit_key)


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
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)


# ---------------------------------------------------------------------------
# Agent endpoints
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
# Tenant (CLI) endpoints
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
# Admin endpoints - protected by ADMIN_TOKEN
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
    return _resp(handle_create_tenant(body, token))


@app.get("/admin/tenants")
@limiter.limit("120/minute")
async def list_tenants(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_list_tenants(token))


@app.post("/admin/tenants/{tenant_id}/users", status_code=201)
@limiter.limit("20/minute")
async def create_user(tenant_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    api_url = str(request.base_url).rstrip("/")
    return _resp(handle_create_user(tenant_id, body, token, api_url))


@app.get("/admin/tenants/{tenant_id}/users")
@limiter.limit("120/minute")
async def list_users(tenant_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_list_users(tenant_id, token))


@app.delete("/admin/tenants/{tenant_id}/users/{user_id}")
@limiter.limit("30/minute")
async def delete_user(tenant_id: str, user_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_delete_user(tenant_id, user_id, token))


@app.post("/admin/tenants/{tenant_id}/users/{user_id}/rotate-token")
@limiter.limit("10/minute")
async def rotate_user_token(tenant_id: str, user_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    api_url = str(request.base_url).rstrip("/")
    return _resp(handle_rotate_user_token(tenant_id, user_id, token, api_url))


@app.get("/admin/tenants/{tenant_id}/users/{user_id}/agents")
@limiter.limit("120/minute")
async def get_user_agents(tenant_id: str, user_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_get_user_agents(tenant_id, user_id, token))


@app.put("/admin/tenants/{tenant_id}/users/{user_id}/agents")
@limiter.limit("30/minute")
async def set_user_agents(tenant_id: str, user_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_set_user_agents(tenant_id, user_id, body, token))


@app.post("/admin/tenants/{tenant_id}/users/{user_id}/agents/{agent_id}")
@limiter.limit("30/minute")
async def grant_agent_access(tenant_id: str, user_id: str, agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_grant_agent_access(tenant_id, user_id, agent_id, token))


@app.delete("/admin/tenants/{tenant_id}/users/{user_id}/agents/{agent_id}")
@limiter.limit("30/minute")
async def revoke_agent_access(tenant_id: str, user_id: str, agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_revoke_agent_access(tenant_id, user_id, agent_id, token))


@app.get("/admin/jobs")
@limiter.limit("120/minute")
async def list_jobs_admin(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    qs = request.query_params
    agent_id = qs.get("agent_id", "")
    tenant_id = qs.get("tenant_id", "")
    created_by = qs.get("created_by", "")
    cursor = qs.get("cursor")
    try:
        limit = max(1, min(int(qs.get("limit", 20)), 100))
    except (ValueError, TypeError):
        limit = 20
    return _resp(handle_list_jobs_admin(token, agent_id, tenant_id, created_by, limit, cursor))


@app.get("/admin/agents")
@limiter.limit("120/minute")
async def list_agents_admin(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    tenant_id = request.query_params.get("tenant_id", "")
    tag = request.query_params.get("tag") or None
    return _resp(handle_list_agents_admin(tenant_id, token, tag))


@app.post("/admin/agents", status_code=201)
@limiter.limit("20/minute")
async def create_agent(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    api_url = str(request.base_url).rstrip("/")
    return _resp(handle_create_agent(body, token, api_url))


@app.post("/admin/agents/{agent_id}/reissue-install-token", status_code=201)
@limiter.limit("10/minute")
async def reissue_install_token(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    api_url = str(request.base_url).rstrip("/")
    return _resp(handle_reissue_install_token(agent_id, body, token, api_url))


@app.get("/admin/agents/{agent_id}/tags")
@limiter.limit("120/minute")
async def get_agent_tags(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_get_agent_tags(agent_id, token))


@app.put("/admin/agents/{agent_id}/tags")
@limiter.limit("30/minute")
async def set_agent_tags(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_set_agent_tags(agent_id, body, token))


@app.post("/admin/agents/{agent_id}/tags")
@limiter.limit("30/minute")
async def add_agent_tags(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_add_agent_tags(agent_id, body, token))


@app.delete("/admin/agents/{agent_id}/tags")
@limiter.limit("30/minute")
async def remove_agent_tags(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_remove_agent_tags(agent_id, body, token))


@app.post("/admin/agents/{agent_id}/revoke")
@limiter.limit("30/minute")
async def revoke_agent(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_revoke_agent(agent_id, token))


@app.delete("/admin/agents/{agent_id}/remove")
@limiter.limit("30/minute")
async def remove_agent(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_remove_agent(agent_id, token))


@app.delete("/admin/agents/{agent_id}")
@limiter.limit("30/minute")
async def delete_agent(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_delete_agent(agent_id, token))


@app.get("/admin/agents/{agent_id}/policy")
@limiter.limit("120/minute")
async def get_policy(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_get_policy(agent_id, token))


@app.put("/admin/agents/{agent_id}/policy/mode")
@limiter.limit("30/minute")
async def set_mode(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_set_mode(agent_id, body, token))


@app.get("/admin/approvals")
@limiter.limit("120/minute")
async def list_approvals(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_list_approvals(dict(request.query_params), token))


@app.post("/admin/approvals", status_code=201)
@limiter.limit("30/minute")
async def pre_approve_command(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_pre_approve_command(body, token))


@app.put("/admin/approvals/{approval_id}/approve")
@limiter.limit("60/minute")
async def approve_approval(approval_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    return _resp(handle_review_approval(approval_id, "approve", token, body))


@app.put("/admin/approvals/{approval_id}/deny")
@limiter.limit("60/minute")
async def deny_approval(approval_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    return _resp(handle_review_approval(approval_id, "deny", token, body))


@app.delete("/admin/approvals/{approval_id}")
@limiter.limit("30/minute")
async def delete_approval(approval_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_delete_approval(approval_id, token))


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
@limiter.limit("120/minute")
async def health(request: Request):
    return {"status": "ok"}
