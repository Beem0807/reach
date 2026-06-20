import os

_backend = os.environ.get("STORAGE_BACKEND", "dynamo")

if _backend == "postgres":
    from .repos.sql import AgentHistoryRepo, AgentRepo, ApiTokenRepo, ApprovalRepo, AuditRepo, JobRepo, TenantRepo, UserRepo
else:
    from .repos.dynamo import AgentHistoryRepo, AgentRepo, ApiTokenRepo, ApprovalRepo, AuditRepo, JobRepo, TenantRepo, UserRepo

agent_history_repo: AgentHistoryRepo = AgentHistoryRepo()
agents_repo: AgentRepo = AgentRepo()
api_tokens_repo: ApiTokenRepo = ApiTokenRepo()
approvals_repo: ApprovalRepo = ApprovalRepo()
audit_repo: AuditRepo = AuditRepo()
jobs_repo: JobRepo = JobRepo()
tenants_repo: TenantRepo = TenantRepo()
users_repo: UserRepo = UserRepo()
