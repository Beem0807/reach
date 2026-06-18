import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("TOKEN_PEPPER", "test-pepper")
# Use SQLite so handler imports don't require a real DB or AWS credentials
os.environ.setdefault("STORAGE_BACKEND", "postgres")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
