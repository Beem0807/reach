import os

_backend = os.environ.get("STORAGE_BACKEND", "dynamo")

if _backend == "postgres":
    from .repos.sql import AgentRepo, JobRepo, TokenRepo
else:
    from .repos.dynamo import AgentRepo, JobRepo, TokenRepo

agents_repo: AgentRepo = AgentRepo()
jobs_repo: JobRepo = JobRepo()
tokens_repo: TokenRepo = TokenRepo()
