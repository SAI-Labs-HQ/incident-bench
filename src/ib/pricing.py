"""Official per-token prices (adapted from safety-index `sie.pricing`).

- OpenRouter: live from https://openrouter.ai/api/v1/models (cached per day under <cache>/prices/).
- Azure / direct APIs: config/prices/table.yaml (USD per 1M tokens, each entry cites its source).
- mock / local: free.
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.request
from dataclasses import dataclass

from . import config
from .models import ModelSpec

OPENROUTER_MODELS = "https://openrouter.ai/api/v1/models"


@dataclass
class Price:
    """USD per token."""
    input: float
    output: float
    cached_input: float | None = None
    cache_write: float | None = None
    source: str = ""

    def cost(self, usage) -> float:
        inp = usage.input_tokens or 0
        out = usage.output_tokens or 0          # Inspect: output includes reasoning tokens
        cr = usage.input_tokens_cache_read or 0  # Inspect: input_tokens excludes cache read/write
        cw = usage.input_tokens_cache_write or 0
        return (inp * self.input + out * self.output
                + cr * (self.cached_input if self.cached_input is not None else self.input)
                + cw * (self.cache_write if self.cache_write is not None else self.input))


def _openrouter_table(refresh: bool = False) -> dict:
    d = config.path("cache") / "prices"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"openrouter-{dt.date.today().isoformat()}.json"
    if refresh or not f.exists():
        req = urllib.request.Request(OPENROUTER_MODELS, headers={"User-Agent": "ib-pricing/0.1"})
        data = json.load(urllib.request.urlopen(req, timeout=60))["data"]
        f.write_text(json.dumps({m["id"]: m["pricing"] for m in data}))
    return json.loads(f.read_text())


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def price_for(ms: ModelSpec) -> Price:
    mode = ms.provider_cfg.get("pricing", "table")
    if mode == "free":
        return Price(0.0, 0.0, source="free")
    if mode == "openrouter":
        p = _openrouter_table().get(ms.model)
        if p is None:
            raise KeyError(f"OpenRouter has no price for '{ms.model}'")
        return Price(_f(p.get("prompt")) or 0.0, _f(p.get("completion")) or 0.0,
                     _f(p.get("input_cache_read")), _f(p.get("input_cache_write")),
                     source=f"{OPENROUTER_MODELS} ({dt.date.today()})")
    table = config.load_yaml("prices/table.yaml")
    e = table.get(ms.key) or table.get(ms.label())
    if e is None:
        raise KeyError(f"no price for '{ms.key}' in config/prices/table.yaml (add one with its source)")
    m = 1e-6
    return Price(e["input"] * m, e["output"] * m,
                 e.get("cached_input") and e["cached_input"] * m,
                 e.get("cache_write") and e["cache_write"] * m,
                 source=f"{e.get('source', '?')} as of {e.get('as_of', '?')}")


def refresh() -> int:
    return len(_openrouter_table(refresh=True))
