"""Configuration and secrets (adapted from safety-index `sie.config`).

Precedence (highest first): CLI flags > environment (IB__SECTION__KEY=value) > config/defaults.yaml.
Secrets are read from the env files listed under `secrets.env_files`; an already-set environment
variable is never overwritten. Nothing in the code base hard-codes models, paths or keys.
"""
from __future__ import annotations

import copy
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(os.environ.get("IB_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = ROOT / "config"
_ENV_PREFIX = "IB__"


def _coerce(value: str) -> Any:
    try:
        return yaml.safe_load(value)
    except yaml.YAMLError:
        return value


def _apply_env_overrides(cfg: dict) -> dict:
    for key, value in os.environ.items():
        if not key.startswith(_ENV_PREFIX):
            continue
        path = [p.lower() for p in key[len(_ENV_PREFIX):].split("__") if p]
        node = cfg
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = _coerce(value)
    return cfg


def load_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    return yaml.safe_load(path.read_text()) or {} if path.exists() else {}


@lru_cache(maxsize=1)
def settings() -> dict:
    """Merged global settings (defaults.yaml + IB__ env overrides)."""
    return _apply_env_overrides(copy.deepcopy(load_yaml("defaults.yaml")))


def get(dotted: str, default: Any = None) -> Any:
    node: Any = settings()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def path(name: str) -> Path:
    p = Path(os.path.expanduser(str(get(f"paths.{name}"))))
    return p if p.is_absolute() else ROOT / p


_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_env_file(p: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in p.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        k, v = m.groups()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k] = v
    return out


_secrets_loaded = False


def load_secrets() -> list[str]:
    """Load secrets into os.environ (never overriding). Returns the files actually read."""
    global _secrets_loaded
    read = []
    for var, f in (get("secrets.key_files", {}) or {}).items():
        p = Path(f) if Path(f).is_absolute() else ROOT / f
        if p.is_file() and p.read_text().strip():
            os.environ[var] = p.read_text().strip()
            read.append(str(p))
    for f in get("secrets.env_files", []) or []:
        p = Path(f) if Path(f).is_absolute() else ROOT / f
        if not p.is_file():
            continue
        for k, v in parse_env_file(p).items():
            os.environ.setdefault(k, v)
        read.append(str(p))
    _secrets_loaded = True
    return read


def setup_runtime_env() -> None:
    """Point every cache at the shared /data cache so workers and the dev box share downloads."""
    load_secrets()
    cache = path("cache")
    os.environ.setdefault("HF_HOME", str(cache / "hf"))
    os.environ.setdefault("UV_CACHE_DIR", str(cache / "uv"))
    tools = get("orchestration.tools_path")
    if tools:  # keep uv-managed Pythons on the shared filesystem so workers can use env venvs
        os.environ.setdefault("UV_PYTHON_INSTALL_DIR", str(Path(tools) / "python"))
    os.environ.setdefault("IB_DATA_DIR", str(path("data")))
    # tokenizers/nltk/etc. write under XDG dirs; keep them on the shared cache (workers, sandboxed agents)
    os.environ.setdefault("XDG_CACHE_HOME", str(cache / "xdg-cache"))
    os.environ.setdefault("XDG_DATA_HOME", str(cache / "xdg-data"))
    os.environ.setdefault("INSPECT_LOG_DIR", str(path("logs")))
    # HF libraries read HF_TOKEN; keep both spellings in sync.
    if "HF_TOKEN" in os.environ:
        os.environ.setdefault("HUGGING_FACE_HUB_TOKEN", os.environ["HF_TOKEN"])


def expand(value: str) -> str:
    """Expand ${VAR} from the environment; raise if a referenced variable is missing."""
    def sub(m: re.Match) -> str:
        var = m.group(1)
        if var not in os.environ:
            raise KeyError(f"environment variable {var} is required (referenced in config)")
        return os.environ[var]
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", sub, value)


def data_dir(instance_id: str) -> Path:
    """Canonical directory for an instance's large assets (shared cache on /data, never committed)."""
    d = Path(os.environ.get("IB_DATA_DIR", path("data"))) / instance_id
    d.mkdir(parents=True, exist_ok=True)
    return d
