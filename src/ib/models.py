"""Model resolution (adapted from safety-index `sie.models`): alias or "<provider>:<model>" spec -> Inspect model.

Switching provider is a config change, never a code change:
    --model gpt-6-sol-azure              (alias from config/models.yaml)
    --model openrouter:qwen/qwen3.8-flash (raw spec)
    --model azure_openai:gpt-6-astra     (Azure deployment)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from . import config


@dataclass(frozen=True)
class ModelSpec:
    key: str                 # alias or raw spec, used for pricing lookup
    provider: str
    model: str
    args: dict = field(default_factory=dict)
    model_args: dict = field(default_factory=dict)  # per-alias connection args, merged over the provider's

    @property
    def provider_cfg(self) -> dict:
        cfg = config.load_yaml("providers.yaml").get(self.provider)
        if cfg is None:
            raise KeyError(f"unknown provider '{self.provider}' (see config/providers.yaml)")
        return cfg

    @property
    def inspect_name(self) -> str:
        return f"{self.provider_cfg['inspect']}/{self.model}"

    def connection_args(self) -> dict[str, Any]:
        cfg = self.provider_cfg
        out: dict[str, Any] = {**(cfg.get("model_args") or {}), **self.model_args}
        if cfg.get("base_url"):
            out["base_url"] = config.expand(cfg["base_url"])
        if cfg.get("api_key_env"):
            key = os.environ.get(cfg["api_key_env"])
            if not key:
                raise KeyError(f"{cfg['api_key_env']} not set; add it to one of secrets.env_files")
            out["api_key"] = key
        return out

    def label(self) -> str:
        return f"{self.provider}:{self.model}"


def aliases() -> dict[str, dict]:
    return config.load_yaml("models.yaml")


def resolve(spec: str | None, role: str = "target") -> ModelSpec:
    """Resolve an alias / raw spec. None -> configured default for the role."""
    if spec is None:
        spec = config.get(f"models.{role}") or config.get("models.judge")
    table = aliases()
    if spec in table:
        a = table[spec]
        return ModelSpec(key=spec, provider=a["provider"], model=a["model"], args=dict(a.get("args") or {}),
                         model_args=dict(a.get("model_args") or {}))
    if ":" in spec:
        provider, model = spec.split(":", 1)
        return ModelSpec(key=spec, provider=provider, model=model)
    raise KeyError(f"model '{spec}' is neither an alias in config/models.yaml nor '<provider>:<model>'")


def get_model(spec: str | ModelSpec | None, role: str = "target", **generate_args):
    """Return an inspect_ai Model for the spec. Generation args only override provider defaults
    when the benchmark protocol explicitly requires it."""
    from inspect_ai.model import GenerateConfig, get_model as _get

    ms = spec if isinstance(spec, ModelSpec) else resolve(spec, role)
    gen = {**ms.args, **generate_args}
    if ms.provider == "mock":
        return _get("mockllm/model", config=GenerateConfig(**gen))
    return _get(ms.inspect_name, config=GenerateConfig(**gen), **ms.connection_args())
