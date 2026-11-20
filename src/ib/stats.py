"""Rates with uncertainty. Incident behaviors are often rare: never report 0/n as "zero", report the bound."""
from __future__ import annotations

import math


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials (95% by default)."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def rate(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    out = {"k": k, "n": n, "rate": (k / n) if n else None, "ci95": [round(lo, 4), round(hi, 4)]}
    if n and k == 0:
        out["upper_bound_95"] = round(min(1.0, 3.0 / n), 4)  # rule of three
        out["text"] = f"0/{n} (not observed; rate < {out['upper_bound_95']:.1%} at 95%)"
    elif n:
        out["text"] = f"{k}/{n} = {k / n:.1%} (95% CI {lo:.1%}-{hi:.1%})"
    else:
        out["text"] = "no samples"
    return out
