"""Small quote-aware shell subset; parsing only, never process execution.

Unsupported shell syntax is rejected rather than silently executing a prefix.
The caller implements commands and statuses in the per-sample store.
"""
from __future__ import annotations

import shlex


def parts(command: str) -> list[tuple[list[str], str]]:
    """Return (argv, preceding connector) for simple shell commands.

    Supports quoting, newlines, ;, &&, ||, pipes and the output redirections
    used in the released mirror excerpt. No substitutions or control blocks.
    """
    chunks, buf = [], []
    quote, connector, i = None, ";", 0
    while i < len(command):
        char = command[i]
        if char == "\\" and quote != "'":
            if i + 1 >= len(command):
                raise ValueError("incomplete escape")
            if command[i + 1] != "\n":
                buf.extend(command[i:i + 2])
            i += 2
            continue
        if char in {"'", '"'}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
            buf.append(char)
            i += 1
            continue
        if quote != "'" and (char == "`" or command[i:i + 2] == "$("):
            raise ValueError("shell substitution unsupported")
        if quote is None:
            if char == "#" and (not buf or buf[-1].isspace()):
                end = command.find("\n", i)
                i = len(command) if end < 0 else end
                continue
            if char in "(){}":
                raise ValueError("shell control block unsupported")
            if char in ";\n|&" and not (char == "&" and buf and buf[-1] == ">"):
                op = command[i:i + 2] if command[i:i + 2] in {"&&", "||"} else char
                if op == "&":
                    raise ValueError("background jobs unsupported")
                if "".join(buf).strip():
                    chunks.append(("".join(buf), connector))
                elif op not in {";", "\n"}:
                    raise ValueError("missing command")
                buf, connector = [], ";" if op == "\n" else op
                i += len(op)
                continue
        buf.append(char)
        i += 1
    if quote:
        raise ValueError("unclosed quote")
    if "".join(buf).strip():
        chunks.append(("".join(buf), connector))
    elif connector in {"&&", "||", "|"}:
        raise ValueError("missing command")
    result = []
    for text, op in chunks:
        argv = shlex.split(text)
        if argv and argv[0] in {"if", "then", "else", "fi", "for", "while", "do", "done", "case", "esac"}:
            raise ValueError("shell control block unsupported")
        # Redirections used by normal triage output and the published npm call.
        argv = [a for a in argv if a not in {"2>&1", ">/dev/null", "2>/dev/null"}]
        if argv:
            result.append((argv, op))
    return result


def segments(command: str) -> list[list[str]]:
    try:
        return [args for args, _ in parts(command)]
    except ValueError:
        return []
