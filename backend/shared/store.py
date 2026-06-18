import os

_backend = os.environ.get("STORAGE_BACKEND", "dynamo")

if _backend == "postgres":
    from .repos.sql import AgentRepo, ApprovalRepo, JobRepo, TenantRepo, UserRepo
else:
    from .repos.dynamo import AgentRepo, ApprovalRepo, JobRepo, TenantRepo, UserRepo

agents_repo: AgentRepo = AgentRepo()
approvals_repo: ApprovalRepo = ApprovalRepo()
jobs_repo: JobRepo = JobRepo()
tenants_repo: TenantRepo = TenantRepo()
users_repo: UserRepo = UserRepo()
