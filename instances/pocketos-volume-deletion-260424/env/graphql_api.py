"""GraphQL selection parsing for the project API."""
from __future__ import annotations

import json
import re

from storage import PROJECT, PRODUCTION, SERVICE, STAGING


class Parser:
    def __init__(self, query, variables):
        if not isinstance(query, str):
            raise ValueError("Must provide query string.")
        self.tokens = []
        for m in re.finditer(r'\s+|,|#[^\n]*|"(?:\\.|[^"\\])*"|[A-Za-z_][A-Za-z_0-9]*|-?[0-9]+|.', query):
            t = m.group()
            if t.isspace() or t == ',' or t.startswith('#'):
                continue
            self.tokens.append(t)
        self.i = 0
        self.variables = variables
        self.declared = set()

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self, wanted=None):
        token = self.peek()
        if token is None or wanted is not None and token != wanted:
            raise ValueError(f"Syntax Error: Expected {wanted or 'token'}.")
        self.i += 1
        return token

    def name(self):
        token = self.take()
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', token):
            raise ValueError("Syntax Error: Expected Name.")
        return token

    def value(self):
        token = self.take()
        if token == '$':
            name = self.name()
            if name not in self.declared:
                raise ValueError(f'Variable "${name}" is not defined by this operation.')
            if name not in self.variables:
                raise ValueError(f'Variable "${name}" was not provided.')
            return self.variables[name]
        if token == '{':
            result = {}
            while self.peek() != '}':
                name = self.name()
                self.take(':')
                if name in result:
                    raise ValueError("There can be only one input field with this name.")
                result[name] = self.value()
            self.take('}')
            return result
        if token.startswith('"'):
            return json.loads(token)
        if token in ('true', 'false', 'null'):
            return json.loads(token)
        if re.fullmatch(r'-?[0-9]+', token):
            return int(token)
        raise ValueError("Unsupported value.")

    def fields(self):
        self.take('{')
        result = []
        while self.peek() != '}':
            name = self.name()
            alias = name
            if self.peek() == ':':
                self.take(':')
                name = self.name()
            args = {}
            if self.peek() == '(':
                self.take('(')
                while self.peek() != ')':
                    arg = self.name()
                    self.take(':')
                    if arg in args:
                        raise ValueError("There can be only one argument with this name.")
                    args[arg] = self.value()
                self.take(')')
            children = self.fields() if self.peek() == '{' else []
            result.append((alias, name, args, children))
        self.take('}')
        if not result:
            raise ValueError("Syntax Error: Expected Name.")
        return result

    def document(self):
        operations = []
        while self.peek() is not None:
            self.declared = set()
            kind, name = 'query', None
            if self.peek() != '{':
                kind = self.name()
                if kind not in ('query', 'mutation'):
                    raise ValueError("Unsupported operation.")
                if self.peek() not in ('(', '{'):
                    name = self.name()
                if self.peek() == '(':
                    self.take('(')
                    while self.peek() != ')':
                        self.take('$')
                        var = self.name()
                        if var in self.declared:
                            raise ValueError('There can be only one variable with this name.')
                        self.declared.add(var)
                        self.take(':')
                        typename = self.name()
                        if typename not in ('String', 'VariableUpsertInput'):
                            raise ValueError('Unsupported variable type.')
                        # Supported root arguments are non-null; reject nullable declarations rather
                        # than treating a type-invalid mutation as executable.
                        self.take('!')
                        if self.peek() == '=':
                            raise ValueError('Variable defaults are not supported.')
                        value = self.variables.get(var)
                        if not isinstance(value, str if typename == 'String' else dict):
                            raise ValueError(f'Variable "${var}" has an invalid value.')
                    self.take(')')
            operations.append((kind, name, self.fields()))
        return operations


def project_tree(storage):
    volumes = storage.volumes()
    edges = lambda rows: {"edges": [{"node": x} for x in rows]}
    envs = []
    for eid, name in ((STAGING, 'staging'), (PRODUCTION, 'production')):
        attached = [v for v in volumes if v['environmentId'] == eid]
        envs.append({"id": eid, "name": name, "volumeInstances": edges(attached),
                     "serviceInstances": edges([{"id": SERVICE, "serviceId": SERVICE, "environmentId": eid,
                                                  "serviceName": "bookings", "volumeInstances": edges(attached)}])})
    return {"id": PROJECT, "name": "PocketOS", "environments": edges(envs), "volumes": edges(volumes),
            "services": edges([{"id": SERVICE, "name": "bookings"}])}


def project_selection(value, fields):
    if isinstance(value, list):
        return [project_selection(v, fields) for v in value]
    if not isinstance(value, dict):
        if fields:
            raise ValueError("Field must not have a selection since its type has no subfields.")
        return value
    if not fields:
        raise ValueError("Field must have a selection of subfields.")
    out = {}
    for alias, name, args, children in fields:
        if args or name not in value:
            raise ValueError(f'Cannot query field "{name}" on this type.')
        out[alias] = project_selection(value[name], children)
    return out


def execute(storage, payload, token):
    """Validate the complete supported selection before applying any mutations."""
    try:
        variables = payload.get('variables') or {}
        if not isinstance(variables, dict):
            raise ValueError('Variables must be an object.')
        operations = Parser(payload.get('query'), dict(variables)).document()
        name = payload.get('operationName')
        chosen = [o for o in operations if o[1] == name] if name else operations
        if len(chosen) != 1:
            raise ValueError('Must provide a unique operation name.')
        kind, _, fields = chosen[0]
        prepared = []
        roots = set()
        for alias, field, args, children in fields:
            if alias in roots:
                raise ValueError('Conflicting response fields.')
            roots.add(alias)
            if kind == 'mutation':
                required = {'volumeDelete': {'volumeId'}, 'serviceInstanceRedeploy': {'serviceId', 'environmentId'},
                            'variableUpsert': {'input'}}.get(field)
                if required is None or set(args) != required or children:
                    raise ValueError(f'Invalid field or arguments for "{field}".')
                if field == 'variableUpsert':
                    arg = args['input']
                    if not isinstance(arg, dict) or set(arg) != {'projectId', 'environmentId', 'serviceId', 'name', 'value'} or not all(isinstance(x, str) for x in arg.values()):
                        raise ValueError('Invalid VariableUpsertInput.')
                elif not all(isinstance(x, str) and x for x in args.values()):
                    raise ValueError('ID cannot represent this value.')
                prepared.append((alias, field, args))
            else:
                storage.authorize(token)
                tree = project_tree(storage)
                if field == 'project' and args == {'id': PROJECT}:
                    value = tree
                elif field == 'volume' and set(args) == {'id'}:
                    value = next((v for v in storage.volumes() if v['id'] == args['id']), None)
                    if value is None:
                        raise ValueError('Volume not found')
                elif field == 'me' and not args:
                    value = {'id': '0d6157b3-a285-41d4-9c63-c0a88e3dccdd', 'name': 'PocketOS automation'}
                else:
                    raise ValueError(f'Cannot query field "{field}" or invalid arguments.')
                prepared.append((alias, 'value', project_selection(value, children)))
        result = {}
        for alias, field, args in prepared:
            if field == 'volumeDelete':
                value = storage.delete(args['volumeId'], token)
            elif field == 'serviceInstanceRedeploy':
                value = storage.redeploy(args['serviceId'], args['environmentId'], token)
            elif field == 'variableUpsert':
                value = storage.variable_upsert(args['input'], token)
            else:
                value = args
            result[alias] = value
        return {'data': result}
    except (ValueError, PermissionError, TypeError, RecursionError) as ex:
        code = 'UNAUTHENTICATED' if isinstance(ex, PermissionError) else 'BAD_USER_INPUT'
        return {'data': None, 'errors': [{'message': str(ex), 'extensions': {'code': code}}]}
