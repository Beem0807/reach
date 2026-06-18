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

from handlers.admin_agents import handle_create_agent, handle_delete_agent, handle_list_agents_admin, handle_reissue_install_token
from handlers.admin_jobs import handle_list_jobs_admin
from handlers.admin_tenants import handle_create_tenant, handle_list_tenants
from handlers.admin_users import (
    handle_create_user,
    handle_delete_user,
    handle_list_users,
    handle_rotate_user_token,
)
from handlers.admin_policy import (
    handle_add_command,
    handle_get_policy,
    handle_remove_command,
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
async def get_job(job_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_get_job(job_id, token))


@app.get("/agents")
async def list_agents(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_list_agents(token))


@app.get("/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_get_agent(agent_id, token))


# ---------------------------------------------------------------------------
# Admin endpoints - protected by ADMIN_TOKEN
# ---------------------------------------------------------------------------

@app.post("/admin/tenants", status_code=201)
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
async def list_tenants(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_list_tenants(token))


@app.post("/admin/tenants/{tenant_id}/users", status_code=201)
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
async def list_users(tenant_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_list_users(tenant_id, token))


@app.delete("/admin/tenants/{tenant_id}/users/{user_id}")
async def delete_user(tenant_id: str, user_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_delete_user(tenant_id, user_id, token))


@app.post("/admin/tenants/{tenant_id}/users/{user_id}/rotate-token")
async def rotate_user_token(tenant_id: str, user_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    api_url = str(request.base_url).rstrip("/")
    return _resp(handle_rotate_user_token(tenant_id, user_id, token, api_url))


@app.get("/admin/jobs")
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
async def list_agents_admin(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    tenant_id = request.query_params.get("tenant_id", "")
    return _resp(handle_list_agents_admin(tenant_id, token))


@app.post("/admin/agents", status_code=201)
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


@app.delete("/admin/agents/{agent_id}")
async def delete_agent(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    return _resp(handle_delete_agent(agent_id, body, token))


@app.get("/admin/agents/{agent_id}/policy")
async def get_policy(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    return _resp(handle_get_policy(agent_id, token))


@app.put("/admin/agents/{agent_id}/policy/mode")
async def set_mode(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_set_mode(agent_id, body, token))


@app.post("/admin/agents/{agent_id}/policy/commands")
async def add_command(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_add_command(agent_id, body, token))


@app.delete("/admin/agents/{agent_id}/policy/commands")
async def remove_command(agent_id: str, request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_remove_command(agent_id, body, token))


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}
