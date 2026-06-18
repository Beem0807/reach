import json
import os
import stat
from pathlib import Path

CONFIG_DIR = Path.home() / ".reach"
CONFIG_FILE = CONFIG_DIR / "config.json"


def load() -> dict:
    if not CONFIG_FILE.exists():
        return {"active_profile": "default", "profiles": {}}
    with CONFIG_FILE.open() as f:
        return json.load(f)


def save(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_FILE.open("w") as f:
        json.dump(data, f, indent=2)
    os.chmod(CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)


def active_profile_name() -> str:
    return load().get("active_profile", "default")


def load_profile(name: str = None) -> dict:
    data = load()
    name = name or data.get("active_profile", "default")
    return data.get("profiles", {}).get(name, {})


def save_profile(profile_data: dict, name: str = None) -> None:
    data = load()
    name = name or data.get("active_profile", "default")
    data.setdefault("profiles", {})[name] = profile_data
    save(data)


def set_active_profile(name: str) -> None:
    data = load()
    if name not in data.get("profiles", {}):
        raise SystemExit(f"[reach] profile '{name}' not found. Run 'reach login --profile {name}' first.")
    data["active_profile"] = name
    save(data)


def delete_profile(name: str) -> None:
    data = load()
    profiles = data.get("profiles", {})
    if name not in profiles:
        raise SystemExit(f"[reach] profile '{name}' not found.")
    if data.get("active_profile") == name:
        raise SystemExit(f"[reach] cannot delete the active profile. Run 'reach profile use <other>' first.")
    del profiles[name]
    save(data)


def rename_profile(old: str, new: str) -> None:
    data = load()
    profiles = data.get("profiles", {})
    if old not in profiles:
        raise SystemExit(f"[reach] profile '{old}' not found.")
    if new in profiles:
        raise SystemExit(f"[reach] profile '{new}' already exists.")
    profiles[new] = profiles.pop(old)
    if data.get("active_profile") == old:
        data["active_profile"] = new
    save(data)


def list_profiles() -> list:
    return list(load().get("profiles", {}).keys())


def require(key: str) -> str:
    cfg = load_profile()
    val = cfg.get(key)
    if not val:
        raise SystemExit(
            f"[reach] missing '{key}' in config. "
            f"Run 'reach login' first, or 'reach use <agent_id>' to set an agent."
        )
    return val


def resolve_agent(name: str) -> str:
    cfg = load_profile()
    aliases = cfg.get("aliases", {})
    return aliases.get(name, name)


def set_alias(alias: str, agent_id: str) -> None:
    cfg = load_profile()
    aliases = cfg.setdefault("aliases", {})
    aliases[alias] = agent_id
    save_profile(cfg)


def remove_alias(alias: str) -> bool:
    cfg = load_profile()
    aliases = cfg.get("aliases", {})
    if alias not in aliases:
        return False
    del aliases[alias]
    save_profile(cfg)
    return True


def list_aliases() -> dict:
    return load_profile().get("aliases", {})
