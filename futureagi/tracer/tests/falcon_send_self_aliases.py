"""Storing bare self is forbidden, before any alias can escape.

This is a structural store rule, not reference-path inference. Evidence ranges
only over source-present binding keys and store origins; no generated suffixes,
used-edge subsets, recursive prefix expansion, or interprocedural call results.
The frozen transport checkers remain independently OR-ed with this rule.
"""

import ast
from collections import defaultdict


class BareSelfStores:
    def __init__(self, tree, aliases, forms):
        self.forms = forms
        self.evidence = defaultdict(set)
        self.stores = list(forms.events)
        self.extra = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and len(node.args) >= 3:
                if aliases.names(node.func) & {"setattr", "builtins.setattr"}:
                    target = node
                    name = node.args[1]
                    if isinstance(name, ast.Constant) and isinstance(name.value, str):
                        target = ast.copy_location(
                            ast.Attribute(
                                value=node.args[0], attr=name.value, ctx=ast.Store()
                            ),
                            node,
                        )
                        forms.scopes[target] = forms.scopes[node]
                    self.stores.append((node, target, node.args[2]))
            if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom)):
                self.extra.append((node, node.value, ast.unparse(node)))
            elif isinstance(node, ast.Lambda):
                self.extra.append(
                    (node.body, node.body, "return " + ast.unparse(node.body))
                )
            elif isinstance(node, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
                if not isinstance(getattr(node, "ctx", None), (ast.Store, ast.Del)):
                    self.extra.append((node, node, "collection " + ast.unparse(node)))
            elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
                self.extra.append((node, node.elt, "collection " + ast.unparse(node)))
            elif isinstance(node, ast.DictComp):
                for value in (node.key, node.value):
                    self.extra.append((node, value, "collection " + ast.unparse(node)))

        # Monotone finite bit closure, not enumeration of reference paths.
        # Plain/local/direct-self stores are intentionally NOT exemptions here.
        changed = True
        while changed:
            changed = False
            for node, target, value in self.stores:
                key = forms.key(target, True)
                origin = node, target
                if (
                    key is not None
                    and origin not in self.evidence[key]
                    and self.contains(value)
                ):
                    self.evidence[key].add(origin)
                    changed = True

    def contains(self, value):
        """Attribute bases and ordinary call arguments are not stored values.

        A literal container nested in a call is checked independently. Calls do
        not transfer their arguments into the return value. In particular,
        ``consume(self)`` and ``saved = self.field`` remain allowed. Name/attribute
        values already holding self are checked at their lexical destination.
        """
        if value is None:
            return False
        if isinstance(value, ast.Name) and value.id == "self":
            return True
        if isinstance(value, (ast.Name, ast.Attribute)):
            return any(
                not self.forms.killed(key, origin, value)
                for key in self.forms.read_keys(value)
                for origin in self.evidence.get(key, ())
            )
        if isinstance(value, (ast.Call, ast.Lambda, ast.Subscript)):
            return False
        if isinstance(value, ast.IfExp):
            return self.contains(value.body) or self.contains(value.orelse)
        if isinstance(value, ast.NamedExpr):
            return self.contains(value.value)
        # BindingForms supplies synthetic tuples for exception/iterable unions.
        # Only result-bearing fields matter; tests/annotations are not stores.
        if isinstance(value, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            return self.contains(value.elt)
        if isinstance(value, ast.DictComp):
            return self.contains(value.key) or self.contains(value.value)
        return any(self.contains(child) for child in ast.iter_child_nodes(value))

    def violations(self):
        seen = set()
        for node, target, value in self.stores:
            if not self.contains(value):
                continue
            # Defaults live in the defining scope but belong to the parameter's
            # function in the census. Anchor the original expression, not a
            # synthetic Name lacking parents or the outer definition node.
            site = (
                value
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                else node
            )
            if isinstance(node, ast.comprehension):
                site = target
            text = (
                "bare-self-store " + ast.unparse(target) + " <- " + ast.unparse(value)
            )
            seen.add((site, text))
            yield site, text
        for site, value, label in self.extra:
            flag = site, "bare-self-store " + label
            if flag not in seen and self.contains(value):
                seen.add(flag)
                yield flag
