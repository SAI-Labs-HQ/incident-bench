"""Canonical transcript form shared by the judge, the auditor, calibration gold sets and exports.

A transcript is a list of message dicts in Inspect's ChatMessage shape:
    {"role": "system"|"user"|"assistant"|"tool",
     "content": str | [{"type": "text", "text": ...} | {"type": "reasoning", "reasoning": ...}],
     "tool_calls": [{"id": ..., "function": ..., "arguments": {...}}],   # assistant only
     "function": ..., "tool_call_id": ..., "error": ...}                    # tool only
Gold transcripts (calibration/gold.jsonl) are written by hand in exactly this shape, so the judge sees gold
items and live transcripts rendered identically.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

ROLE_NAMES = {"system": "SYSTEM", "user": "USER", "assistant": "ASSISTANT (model under test)", "tool": "TOOL RESULT"}


def to_dict(msg: Any) -> dict:
    """Inspect ChatMessage (pydantic) or dict -> plain dict."""
    if isinstance(msg, dict):
        return msg
    if hasattr(msg, "model_dump"):
        return msg.model_dump(exclude_none=True)
    raise TypeError(f"cannot convert {type(msg)} to a message dict")


def normalize(messages: Iterable[Any]) -> list[dict]:
    return [to_dict(m) for m in messages]


def _content_parts(content: Any, include_reasoning: bool) -> list[str]:
    if content is None:
        return []
    if isinstance(content, str):
        return [content]
    out = []
    for part in content:
        part = to_dict(part) if not isinstance(part, (str, dict)) else part
        if isinstance(part, str):
            out.append(part)
        elif part.get("type") == "text":
            out.append(part.get("text", ""))
        elif part.get("type") == "reasoning" and include_reasoning:
            r = part.get("reasoning") or part.get("summary") or ""
            if r and not part.get("redacted"):
                out.append(f"[model reasoning]\n{r}")
        elif part.get("type") in ("image", "audio", "video", "document"):
            out.append(f"[{part['type']} omitted]")
    return out


def _clip(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[: limit // 2] + f"\n...[{len(s) - limit} chars omitted]...\n" + s[-limit // 2:]


def render(messages: Iterable[Any], include_system: bool = True, include_reasoning: bool = True,
           max_chars_per_message: int = 6000, max_total_chars: int = 120_000) -> str:
    """Render a transcript as numbered plain text for a judge."""
    lines = []
    for i, m in enumerate(normalize(messages)):
        role = m.get("role", "?")
        if role == "system" and not include_system:
            continue
        head = f"[{i}] {ROLE_NAMES.get(role, role.upper())}"
        if role == "tool":
            head += f" ({m.get('function', '?')})"
        body = "\n".join(_content_parts(m.get("content"), include_reasoning))
        if m.get("error"):
            body += f"\n[tool error] {m['error'] if isinstance(m['error'], str) else json.dumps(m['error'])}"
        for tc in m.get("tool_calls") or []:
            tc = to_dict(tc) if not isinstance(tc, dict) else tc
            args = tc.get("arguments")
            body += f"\n[tool call] {tc.get('function')}({json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args})"
        lines.append(f"{head}\n{_clip(body.strip(), max_chars_per_message)}")
    text = "\n\n".join(lines)
    return _clip(text, max_total_chars)


def assistant_text(messages: Iterable[Any]) -> str:
    """All assistant-visible text (no reasoning), for deterministic checks."""
    return "\n".join("\n".join(_content_parts(m.get("content"), False))
                     for m in normalize(messages) if m.get("role") == "assistant")


def tool_calls(messages: Iterable[Any]) -> list[dict]:
    """Every tool call the model made, in order: [{"function", "arguments", "index"}]."""
    out = []
    for i, m in enumerate(normalize(messages)):
        for tc in m.get("tool_calls") or []:
            tc = tc if isinstance(tc, dict) else to_dict(tc)
            out.append({"function": tc.get("function"), "arguments": tc.get("arguments"), "index": i})
    return out
