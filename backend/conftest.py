import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("ADMIN_PASSWORD", "test-password")
os.environ.setdefault("TOKEN_PEPPER", "test-pepper")
os.environ.setdefault("SESSION_SIGNING_KEY", "test-signing-key")
# Use SQLite so handler imports don't require a real DB or AWS credentials
os.environ.setdefault("STORAGE_BACKEND", "postgres")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

# Create all ORM tables in the in-memory SQLite DB so handlers that write to
# repos without mocking (e.g. agent_history_repo) don't hit "no such table".
from shared.repos.sql import _Base, engine
_Base.metadata.create_all(engine)

# Valid JWT session token for use across admin tests
from shared.admin_auth import create_session_token
ADMIN_TOKEN = create_session_token()
