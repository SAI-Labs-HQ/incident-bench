"""Small GraphQL executor: parses the query documents the gh CLI sends and resolves them against Python objects.

Objects are dicts (field -> value), callables (args -> value) or Node instances with a `field(name, args)` method.
Unknown fields resolve to null, which gh decodes as zero values.
"""
from __future__ import annotations

import json
import re

TOKEN = re.compile(r'''
    (?P<ws>[\s,﻿]+|\#[^\n]*)
  | (?P<block>"""(?:\\"""|[^"]|"(?!""))*""")
  | (?P<str>"(?:\\.|[^"\\\n])*")
  | (?P<num>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<spread>\.\.\.)
  | (?P<name>[_A-Za-z][_0-9A-Za-z]*)
  | (?P<punct>[!$&()\[\]{}:=@|])
''', re.X)


class GraphQLError(Exception):
    pass


def tokenize(src: str):
    pos, out = 0, []
    while pos < len(src):
        m = TOKEN.match(src, pos)
        if not m:
            raise GraphQLError(f"Syntax Error: Unexpected character {src[pos]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        out.append((kind, m.group(0)))
    out.append(("eof", ""))
    return out


class Parser:
    def __init__(self, src: str):
        self.t = tokenize(src)
        self.i = 0

    def peek(self, value=None):
        k, v = self.t[self.i]
        return v == value if value is not None else (k, v)

    def take(self, value=None):
        k, v = self.t[self.i]
        if k == "eof":
            raise GraphQLError(f"Syntax Error: Expected {value or 'Name'}, found <EOF>")
        if value is not None and v != value:
            raise GraphQLError(f"Syntax Error: Expected {value}, found {v or '<EOF>'}")
        self.i += 1
        return k, v

    def document(self):
        ops, frags = [], {}
        while self.peek()[0] != "eof":
            if self.peek("{"):
                ops.append({"type": "query", "name": None, "vars": {}, "sel": self.selection_set()})
                continue
            _, word = self.take()
            if word == "fragment":
                _, name = self.take()
                self.take("on")
                _, typ = self.take()
                self.directives()
                frags[name] = {"on": typ, "sel": self.selection_set()}
            elif word in ("query", "mutation", "subscription"):
                name = None
                if self.peek()[0] == "name":
                    name = self.take()[1]
                defaults = {}
                if self.peek("("):
                    self.take("(")
                    while not self.peek(")"):
                        self.take("$")
                        _, var = self.take()
                        self.take(":")
                        self.type_ref()
                        if self.peek("="):
                            self.take("=")
                            defaults[var] = self.value(const=True)
                        self.directives()
                    self.take(")")
                self.directives()
                ops.append({"type": word, "name": name, "vars": defaults, "sel": self.selection_set()})
            else:
                raise GraphQLError(f"Syntax Error: Unexpected Name \"{word}\"")
        return ops, frags

    def type_ref(self):
        if self.peek("["):
            self.take("[")
            self.type_ref()
            self.take("]")
        else:
            self.take()
        if self.peek("!"):
            self.take("!")

    def directives(self):
        out = []
        while self.peek("@"):
            self.take("@")
            _, name = self.take()
            out.append((name, self.arguments()))
        return out

    def arguments(self):
        args = {}
        if self.peek("("):
            self.take("(")
            while not self.peek(")"):
                _, name = self.take()
                self.take(":")
                args[name] = self.value()
            self.take(")")
        return args

    def value(self, const=False):
        k, v = self.take()
        if v == "$" and not const:
            return ("var", self.take()[1])
        if k == "num":
            return float(v) if any(c in v for c in ".eE") else int(v)
        if k == "str":
            return json.loads(v)
        if k == "block":
            return v[3:-3].replace('\\"""', '"""')
        if v == "[":
            items = []
            while not self.peek("]"):
                items.append(self.value(const))
            self.take("]")
            return items
        if v == "{":
            obj = {}
            while not self.peek("}"):
                _, name = self.take()
                self.take(":")
                obj[name] = self.value(const)
            self.take("}")
            return obj
        if v in ("true", "false"):
            return v == "true"
        if v == "null":
            return None
        if k == "name":
            return ("enum", v)
        raise GraphQLError(f"Syntax Error: Unexpected {v}")

    def selection_set(self):
        self.take("{")
        sels = []
        while not self.peek("}"):
            if self.peek("..."):
                self.take("...")
                if self.peek("on"):
                    self.take("on")
                    _, typ = self.take()
                    dirs = self.directives()
                    sels.append({"inline": typ, "dirs": dirs, "sel": self.selection_set()})
                elif self.peek("{") or self.peek("@"):
                    dirs = self.directives()
                    sels.append({"inline": None, "dirs": dirs, "sel": self.selection_set()})
                else:
                    _, name = self.take()
                    sels.append({"spread": name, "dirs": self.directives()})
                continue
            _, name = self.take()
            alias = name
            if self.peek(":"):
                self.take(":")
                _, name = self.take()
            args = self.arguments()
            dirs = self.directives()
            sub = self.selection_set() if self.peek("{") else None
            sels.append({"name": name, "alias": alias, "args": args, "dirs": dirs, "sel": sub})
        self.take("}")
        return sels


def _resolve_value(v, variables):
    if isinstance(v, tuple):
        if v[0] == "var":
            return variables.get(v[1])
        return v[1]  # enum
    if isinstance(v, list):
        return [_resolve_value(x, variables) for x in v]
    if isinstance(v, dict):
        return {k: _resolve_value(x, variables) for k, x in v.items()}
    return v


class Node:
    """Base for typed objects. Subclasses define `typename` and methods named f_<field>(args)."""
    typename = "Node"

    def field(self, name, args):
        fn = getattr(self, "f_" + name, None)
        if fn is not None:
            return fn(args)
        return getattr(self, "data", {}).get(name)


def typename_of(obj):
    if isinstance(obj, Node):
        return obj.typename
    if isinstance(obj, dict):
        return obj.get("__typename")
    return None


def _skip(dirs, variables):
    for name, args in dirs or []:
        cond = _resolve_value(args.get("if"), variables)
        if name == "skip" and cond:
            return True
        if name == "include" and not cond:
            return True
    return False


def _get(obj, name, args):
    if isinstance(obj, Node):
        return obj.field(name, args)
    if isinstance(obj, dict):
        v = obj.get(name)
        return v(args) if callable(v) else v
    return None


def execute_selection(obj, sels, frags, variables, parent_type=None):
    if obj is None:
        return None
    out = {}
    tname = typename_of(obj) or parent_type
    for s in sels:
        if _skip(s.get("dirs"), variables):
            continue
        if "spread" in s or "inline" in s:
            frag = frags.get(s["spread"]) if "spread" in s else {"on": s["inline"], "sel": s["sel"]}
            if frag is None:
                raise GraphQLError(f"Unknown fragment \"{s.get('spread')}\".")
            on = frag["on"]
            if on is None or on == tname or on in getattr(obj, "interfaces", ()) or on in ("Node", "Actor", "Comment",
                                                                                          "Labelable", "Assignable",
                                                                                          "UniformResourceLocatable"):
                for k, v in execute_selection(obj, frag["sel"], frags, variables, tname).items():
                    if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                        out[k] = {**out[k], **v}
                    else:
                        out[k] = v
            continue
        name = s["name"]
        if name == "__typename":
            out[s["alias"]] = tname
            continue
        args = {k: _resolve_value(v, variables) for k, v in s["args"].items()}
        val = _get(obj, name, args)
        out[s["alias"]] = _shape(val, s["sel"], frags, variables)
    return out


def _shape(val, sel, frags, variables):
    if sel is None:
        if isinstance(val, Node):
            return None
        return val
    if isinstance(val, list):
        return [_shape(v, sel, frags, variables) for v in val]
    if val is None:
        return None
    return execute_selection(val, sel, frags, variables)


def execute(query: str, variables: dict | None, root_query, root_mutation, operation_name: str | None = None):
    try:
        ops, frags = Parser(query).document()
    except GraphQLError as ex:
        return {"errors": [{"message": str(ex)}]}
    if not ops:
        return {"errors": [{"message": "Must provide an operation."}]}
    op = ops[0]
    if operation_name:
        op = next((o for o in ops if o["name"] == operation_name), op)
    variables = {**op["vars"], **(variables or {})}
    root = root_mutation if op["type"] == "mutation" else root_query
    try:
        data = execute_selection(root, op["sel"], frags, variables, "Mutation" if op["type"] == "mutation" else "Query")
    except GraphQLError as ex:
        return {"data": None, "errors": [{"message": str(ex)}]}
    errors = getattr(root, "errors", None)
    if errors:
        out = {"data": data, "errors": list(errors)}
        errors.clear()
        return out
    return {"data": data}
