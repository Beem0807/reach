"""Guard: the Alembic migrations and the SQLAlchemy models must describe the same
schema.

Docker runs `alembic upgrade head` to build the database, while the test suite builds
it from `_Base.metadata` (`create_all`). Nothing forced the two to agree, so a model
column added without a matching migration column passed every test yet broke the seed
in Docker (the `argv`/`host_rule` column drift). This test closes that gap: any table
or column that exists in the models but not the migrations (or vice versa) fails here.

The migrations are parsed statically (Alembic isn't a test-time dependency - it only
runs in the Docker image), replaying `create_table` / `add_column` / `drop_column` /
`drop_table` in filename order, which matches the revision chain (`0001_`, `0002_`, ...).
"""
import ast
import os

from shared.repos import sql

VERSIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "alembic", "versions")


def _column_name(call: ast.Call):
    """First positional arg of an `sa.Column('name', ...)` call, else None."""
    if isinstance(call, ast.Call) and getattr(call.func, "attr", None) == "Column" and call.args:
        arg = call.args[0]
        if isinstance(arg, ast.Constant):
            return arg.value
    return None


def _migration_schema() -> dict[str, set[str]]:
    """Replay every migration's DDL statically into {table: {columns}}."""
    tables: dict[str, set[str]] = {}
    files = sorted(f for f in os.listdir(VERSIONS_DIR) if f.endswith(".py") and not f.startswith("__"))
    for fname in files:
        tree = ast.parse(open(os.path.join(VERSIONS_DIR, fname)).read())
        # Only replay upgrade() - downgrade()'s drop_table/drop_column would undo it.
        upgrade = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"), None)
        if upgrade is None:
            continue
        for node in ast.walk(upgrade):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            op = node.func.attr
            if op == "create_table":
                tname = node.args[0].value
                tables[tname] = {c for a in node.args[1:] if (c := _column_name(a)) is not None}
            elif op == "add_column":
                tname = node.args[0].value
                if (c := _column_name(node.args[1])) is not None:
                    tables.setdefault(tname, set()).add(c)
            elif op == "drop_column":
                tname, col = node.args[0].value, node.args[1].value
                tables.get(tname, set()).discard(col)
            elif op == "drop_table":
                tables.pop(node.args[0].value, None)
    return tables


def _model_schema() -> dict[str, set[str]]:
    return {t.name: {c.name for c in t.columns} for t in sql._Base.metadata.tables.values()}


def test_migration_tables_match_models():
    mig, model = _migration_schema(), _model_schema()
    assert set(mig) == set(model), (
        f"table drift - migration-only: {sorted(set(mig) - set(model))}, "
        f"model-only: {sorted(set(model) - set(mig))}"
    )


def test_migration_columns_match_models():
    mig, model = _migration_schema(), _model_schema()
    drift = {}
    for table in set(mig) & set(model):
        model_only = model[table] - mig[table]   # a model column no migration creates -> breaks `alembic upgrade`
        mig_only = mig[table] - model[table]      # a migration column the model dropped -> stale schema
        if model_only or mig_only:
            drift[table] = {"model_only": sorted(model_only), "migration_only": sorted(mig_only)}
    assert not drift, f"column drift between models and migrations: {drift}"
