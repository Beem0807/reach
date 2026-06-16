import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from handlers.admin_bootstrap import handle_admin_bootstrap
from handlers.admin_policy import (
    handle_add_command,
    handle_get_policy,
    handle_remove_command,
    handle_set_mode,
)
from handlers.agent_claim import handle_agent_claim
from handlers.agent_job_result import handle_agent_job_result
from handlers.agent_sync import handle_agent_sync
from handlers.create_job import handle_create_job
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


scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(handle_heartbeat_check, "interval", minutes=5, id="heartbeat")
    scheduler.start()
    logger.info("Heartbeat scheduler started (every 5 minutes)")
    yield
    scheduler.shutdown()


app = FastAPI(title="reach API", version="1.0.0", lifespan=lifespan)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")


# ---------------------------------------------------------------------------
# Agent endpoints
# ---------------------------------------------------------------------------

@app.post("/agent/claim")
async def agent_claim(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_agent_claim(body))


@app.post("/agent/sync")
async def agent_sync(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    return _resp(handle_agent_sync(body, token))


@app.post("/agent/jobs/{job_id}/result")
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

@app.post("/jobs", status_code=201)
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
    try:
        limit = max(1, min(int(qs.get("limit", 20)), 100))
    except (ValueError, TypeError):
        limit = 20
    return _resp(handle_list_jobs(token, agent_filter, limit))


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

@app.post("/admin/bootstrap", status_code=201)
async def admin_bootstrap(request: Request):
    token = _token(request)
    if not token:
        return JSONResponse({"error": "missing Authorization header"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    api_url = str(request.base_url).rstrip("/")
    return _resp(handle_admin_bootstrap(body, token, api_url))


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
