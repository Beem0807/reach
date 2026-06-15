import json
import os
import stat
from pathlib import Path

CONFIG_DIR = Path.home() / ".reach"
CONFIG_FILE = CONFIG_DIR / "config.json"


def load() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    with CONFIG_FILE.open() as f:
        return json.load(f)


def save(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_FILE.open("w") as f:
        json.dump(data, f, indent=2)
    os.chmod(CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)


def require(key: str) -> str:
    cfg = load()
    val = cfg.get(key)
    if not val:
        raise SystemExit(
            f"[reach] missing '{key}' in config. "
            f"Run 'reach login' first, or 'reach use <agent_id>' to set an agent."
        )
    return val


def resolve_agent(name: str) -> str:
    """Resolve an alias or agent ID to a real agent ID."""
    cfg = load()
    aliases = cfg.get("aliases", {})
    return aliases.get(name, name)


def set_alias(alias: str, agent_id: str) -> None:
    cfg = load()
    aliases = cfg.setdefault("aliases", {})
    aliases[alias] = agent_id
    save(cfg)


def remove_alias(alias: str) -> bool:
    cfg = load()
    aliases = cfg.get("aliases", {})
    if alias not in aliases:
        return False
    del aliases[alias]
    save(cfg)
    return True


def list_aliases() -> dict:
    return load().get("aliases", {})
