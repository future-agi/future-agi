"""Version-independent, fail-closed AST bindings for reviewed source summaries.

Python 3.13+ omits empty lists from ast.dump; 3.12 added empty type_params.
Neither changes the meaning of these non-generic functions. Keep all other
fields (including unknown future fields), scalar types and sequence ordering.
Never hash a freshly read function as its own approval: callers use fixed pins.
"""

import ast
import json
from hashlib import sha256


def fingerprint(node):
    def encode(value):
        if isinstance(value, ast.AST):
            return [
                type(value).__name__,
                [
                    [name, encode(child)]
                    for name, child in sorted(ast.iter_fields(value))
                    if not (
                        name == "type_params"
                        and isinstance(value, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                        and child == []
                    )
                ],
            ]
        if isinstance(value, list):
            return ["list", [encode(child) for child in value]]
        return [type(value).__name__, repr(value)]

    return sha256(json.dumps(encode(node), separators=(",", ":")).encode()).hexdigest()
