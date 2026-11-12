"""Normalize once, then propagate finite identities.

Direct calls keep existing store analysis. Non-call loads and literal getattr
acquisition are structural flags. Only this scanner uses the identity closure;
the frozen alias resolvers and other OR components are not changed.
"""

import ast
import copy
import runpy
from collections import defaultdict, deque
from pathlib import Path

BindingForms = runpy.run_path(str(Path(__file__).with_name("falcon_send_bindings.py")))[
    "BindingForms"
]


StackSafeText = runpy.run_path(
    str(Path(__file__).with_name("falcon_send_scan_safety.py"))
)["StackSafeText"]


class NormalizeWalruses:
    """Iterative private copy, postorder replacements and ordinary side stores.

    No scope/alias table is read before normalization. Side stores share their
    normalized RHS and are scoped later against the resulting value tree. The
    original-node map and original callee membership are diagnostic metadata only.
    """

    def __init__(self, tree):
        self.originals, self.replacements = {}, {}
        self.bindings, shadows = [], []
        self.callees = set()
        pending = [(tree, False)]
        while pending:
            original, ready = pending.pop()
            if not ready:
                pending.append((original, True))
                pending.extend(
                    (child, False) for child in ast.iter_child_nodes(original)
                )
                continue
            node = copy.copy(original)
            for field, value in ast.iter_fields(original):
                if isinstance(value, ast.AST):
                    setattr(node, field, self.replacements[value])
                elif isinstance(value, list):
                    setattr(
                        node,
                        field,
                        [
                            self.replacements[item]
                            if isinstance(item, ast.AST)
                            else item
                            for item in value
                        ],
                    )
            self.originals[node] = original
            if isinstance(original, ast.Call):
                self.callees.add(original.func)
            if isinstance(node, ast.NamedExpr):
                value = ast.copy_location(copy.copy(node.value), node)
                self.originals[value] = self.originals[node.value]
                shadows.append((node.value, value))
                self.bindings.append(
                    ast.copy_location(
                        ast.Assign(targets=[node.target], value=value), node
                    )
                )
                node = value
            self.replacements[original] = node
        self.tree = self.replacements[tree]
        # Nested walruses replace the same value root more than once. Flatten
        # those links once, in reverse postorder, rather than chase depth per use.
        surviving = {}
        for old, new in reversed(shadows):
            surviving[old] = surviving.get(new, new)
        self.replacements = {
            old: surviving.get(new, new) for old, new in self.replacements.items()
        }
        for binding in self.bindings:
            binding.value = surviving.get(binding.value, binding.value)


class NormalizedBindingForms(BindingForms):
    """Scanner-local stack-safe scope walk; frozen binding rules stay untouched."""

    def map_scopes(self, node, scope):
        functions = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        comprehensions = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
        pending = [(node, scope)]
        while pending:
            node, scope = pending.pop()
            self.scopes[node] = scope
            if isinstance(node, (*functions, ast.ClassDef)):
                self.outer[node] = scope
                for field, value in ast.iter_fields(node):
                    inner = node if field in {"body", "args", "type_params"} else scope
                    children = value if isinstance(value, list) else [value]
                    pending.extend(
                        (child, inner)
                        for child in children
                        if isinstance(child, ast.AST)
                    )
            elif isinstance(node, ast.arguments):
                pending.extend(
                    (child, scope if isinstance(child, ast.arg) else self.outer[scope])
                    for child in ast.iter_child_nodes(node)
                )
            elif isinstance(node, ast.arg):
                pending.extend(
                    (child, self.outer[scope]) for child in ast.iter_child_nodes(node)
                )
            elif isinstance(node, comprehensions):
                self.outer[node] = scope
                for index, generator in enumerate(node.generators):
                    self.scopes[generator] = node
                    pending.append((generator.iter, scope if index == 0 else node))
                    pending.append((generator.target, node))
                    pending.extend((condition, node) for condition in generator.ifs)
                pending.extend(
                    (getattr(node, field), node)
                    for field in ("elt", "key", "value")
                    if hasattr(node, field)
                )
            else:
                pending.extend((child, scope) for child in ast.iter_child_nodes(node))


SETTERS = {
    "setattr",
    "delattr",
    "builtins.setattr",
    "builtins.delattr",
    "object.__setattr__",
    "type.__setattr__",
    "builtins.object.__setattr__",
    "builtins.type.__setattr__",
}
REFLECTED_SETTERS = {"setattr", "delattr", "__setattr__"}
GETTERS = {"getattr", "builtins.getattr"}
# Prefix identities allow imported/aliased builtins, object and type without
# expanding arbitrary dotted names. This is a constant-size label domain.
IDENTITIES = (
    SETTERS
    | GETTERS
    | {"builtins", "object", "type", "builtins.object", "builtins.type"}
)


class BuiltinSetterLoads:
    def __init__(self, tree, aliases):
        self.normalized = NormalizeWalruses(tree)
        self.tree = self.normalized.tree
        renderer = self.renderer = StackSafeText()
        parents = {
            child: node
            for node in ast.walk(self.tree)
            for child in ast.iter_child_nodes(node)
        }
        self.forms = NormalizedBindingForms(self.tree, parents)

        # Only the resolver's type/configuration is reused, never its original
        # parents, scopes, bindings, cache or pre-normalization prefix evidence.
        class NormalizedAliases(type(aliases)):
            def bind(self, target, value, scope):
                # Same finite ordinary bindings, but no recursive target renderer
                # or recursive tuple walk in this scanner-private table.
                pending = [(target, value)]
                while pending:
                    target, value = pending.pop()
                    if isinstance(target, (ast.Tuple, ast.List)):
                        exact = isinstance(value, (ast.Tuple, ast.List)) and len(
                            target.elts
                        ) == len(value.elts)
                        pending.extend(
                            reversed(
                                [
                                    (child, value.elts[i] if exact else value)
                                    for i, child in enumerate(target.elts)
                                ]
                            )
                        )
                    elif isinstance(target, (ast.Name, ast.Attribute)):
                        if isinstance(value, (ast.Tuple, ast.List)):
                            pending.extend(
                                (target, item) for item in reversed(value.elts)
                            )
                        elif isinstance(value, (ast.Name, ast.Attribute, ast.Call)):
                            self.bindings[scope, renderer.text(target)].append(
                                (value, scope)
                            )

            def close_composed_paths(self, tree):
                # The scanner closes source-present paths below. Calling the
                # legacy recursive expander on newly visible walrus bindings
                # would reintroduce exponential tuple/Name diamonds.
                pass

        self.aliases = NormalizedAliases(self.tree, parents, aliases.transports)
        comprehensions = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
        for binding in self.normalized.bindings:
            target = binding.targets[0]
            scope = self.forms.scopes[binding.value]
            while isinstance(scope, comprehensions):
                scope = self.forms.outer[scope]
            self.forms.scopes[target] = scope
            self.forms.symbols[scope].add(target.id)
        self.walrus_bindings = []
        for binding in self.normalized.bindings:
            target, value = binding.targets[0], binding.value
            scope = self.forms.destination(self.forms.scopes[target], target.id, True)
            self.forms.scopes[target] = scope
            self.forms.add(binding, target, value)
            self.walrus_bindings.append((self.normalized.originals[scope], binding))
            # LocalAliases.bind also handles tuple/list values. Preserve the RHS
            # evaluation scope (not the destination of a comprehension walrus).
            entries = self.aliases.bindings[scope, target.id]
            start = len(entries)
            self.aliases.bind(target, value, scope)
            entries[start:] = [
                (expr, self.forms.scopes[value]) for expr, _ in entries[start:]
            ]
        # Composed dotted paths must see the normalized ordinary side stores too.
        self._close_paths()
        self.identities = defaultdict(set)
        self.keys = {}
        self._close_identities()

    def _close_paths(self):
        """Finite may-alias congruence for source-present dotted prefixes.

        Copying a holder may share its fields in either direction. Merge those
        reference classes, then merge equally named fields of aliased holders.
        Only attribute bindings inherit that union; scalar builtin identities
        below remain directional (a rebound lookup never rewrites getattr).
        No unbounded paths, recursive alias expansion or edge-subset states.
        """
        aliases = self.aliases
        direct = {key: tuple(values) for key, values in aliases.bindings.items()}
        roots, rank, pending, paths, calls = {}, {}, deque(), {}, set()

        def intern(key):
            if key not in roots:
                roots[key] = key
                rank[key] = 0
                pending.append(key)
            return key

        def root(key):
            while roots[key] != key:
                roots[key] = roots[roots[key]]
                key = roots[key]
            return key

        def merge(left, right):
            left, right = root(left), root(right)
            if left == right:
                return False
            if rank[left] < rank[right]:
                left, right = right, left
            roots[right] = left
            rank[left] += rank[left] == rank[right]
            return True

        attributes = set()
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.Name, ast.Attribute)):
                key = intern((aliases.scope(node), self.renderer.text(node)))
                if isinstance(node, ast.Attribute):
                    attributes.add(key)
        for key in direct:
            intern(key)
        while pending:
            key = pending.popleft()
            scope, name = key
            if name.endswith("()"):
                paths[key] = (intern((scope, name[:-2])), "()")
            elif "." in name:
                prefix, attr = name.rsplit(".", 1)
                paths[key] = (intern((scope, prefix)), attr)
            for outer in aliases.enclosing(scope):
                for value, origin in direct.get((outer, name), ()):
                    if isinstance(value, (ast.Name, ast.Attribute)):
                        merge(key, intern((origin, self.renderer.text(value))))
                    elif isinstance(value, str):
                        merge(key, intern((origin, value)))
                    elif isinstance(value, ast.Call):
                        func = self.renderer.text(value.func)
                        calls.add(
                            (key, intern((origin, func)), intern((origin, func + "()")))
                        )
        changed = True
        while changed:
            changed, fields = False, {}
            # Retain precisely the legacy constructor family, not arbitrary
            # factory-return inference. Callee aliases share the finite classes.
            modules = {root(key) for key in roots if key[1] in aliases.transports}
            constructors = {
                root(key)
                for key in roots
                if key[1].split(".")[0] in aliases.transports
                and (
                    key[1].rsplit(".", 1)[-1][:1].isupper()
                    or key[1].rsplit(".", 1)[-1] in {"client", "get_client"}
                )
            }
            constructors.update(
                root(key)
                for key, (prefix, attr) in paths.items()
                if root(prefix) in modules
                and (attr[:1].isupper() or attr in {"client", "get_client"})
            )
            for target, func, value in calls:
                if root(func) in constructors:
                    changed |= merge(target, value)
            for key, (prefix, attr) in paths.items():
                signature = (root(prefix), attr)
                previous = fields.setdefault(signature, key)
                changed |= merge(key, previous)
        evidence = defaultdict(set)
        for key, values in direct.items():
            evidence[root(key)].update(values)
        for key in attributes:
            extra = evidence[root(key)] - set(aliases.bindings[key])
            aliases.bindings[key].extend(extra)

    def _close_identities(self):
        # All evidence, including composed prefixes, comes from the normalized
        # tree. Identity propagation never expands arbitrary loaded Name values.
        bindings = defaultdict(list)
        for key, values in self.aliases.bindings.items():
            bindings[key].extend(values)

        def bind(target, value):
            if isinstance(target, (ast.Tuple, ast.List)):
                exact = isinstance(value, (ast.Tuple, ast.List)) and len(
                    target.elts
                ) == len(value.elts)
                for index, child in enumerate(target.elts):
                    bind(child, value.elts[index] if exact else value)
            elif isinstance(value, (ast.Tuple, ast.List)):
                for item in value.elts:
                    bind(target, item)
            elif isinstance(target, (ast.Name, ast.Attribute)) and isinstance(
                value, (ast.Name, ast.Attribute)
            ):
                key = self.forms.key(target, True)
                if key is not None:
                    bindings[key].append((value, self.forms.scopes[value]))

        # The normalized side stores are ordinary events in this same table.
        for _, target, value in self.forms.events:
            bind(target, value)

        pending = deque()
        known = set()
        dependents = defaultdict(set)
        seeds = deque()
        ancestors = {}

        def intern(scope, name):
            key = (scope, name)
            if key not in known:
                known.add(key)
                pending.append(key)
                if name in IDENTITIES:
                    seeds.append((key, name))
            return key

        for node in ast.walk(self.tree):
            if isinstance(node, (ast.Name, ast.Attribute)):
                name = self.renderer.text(node)
                key = intern(self.aliases.scope(node), name)
                self.keys[node] = key
                # Keep legacy lexical lookup AND the PEP 572 evaluation scope.
                # In particular comprehension values read their own loop locals,
                # while their walrus destinations live in the containing scope.
                actual = intern(self.forms.scopes[node], name)
                if actual != key:
                    dependents[actual].add((key, ""))

        while pending:
            key = pending.popleft()
            scope, name = key
            if "." in name:
                prefix, attr = name.rsplit(".", 1)
                dependents[intern(scope, prefix)].add((key, "." + attr))
            if scope not in ancestors:
                ancestors[scope] = tuple(self.aliases.enclosing(scope))
            for outer in ancestors[scope]:
                for value, origin in bindings.get((outer, name), ()):
                    if isinstance(value, str):
                        if value in IDENTITIES:
                            seeds.append((key, value))
                    elif isinstance(value, (ast.Name, ast.Attribute)):
                        source = intern(origin, self.renderer.text(value))
                        dependents[source].add((key, ""))
                    # Calls cannot produce builtin identities in the frozen
                    # resolver; arbitrary call-return inference stays excluded.

        # Each (scope/path, identity) is processed once, including cycles and
        # reverse-order aliases. At most the constant identity domain traverses
        # each direct edge: no edge-subset enumeration or arbitrary-name growth.
        while seeds:
            key, identity = seeds.popleft()
            if identity in self.identities[key]:
                continue
            self.identities[key].add(identity)
            for target, suffix in dependents[key]:
                candidate = identity + suffix
                if candidate in IDENTITIES:
                    seeds.append((target, candidate))

    def names(self, node):
        # Compatibility for callers holding original source nodes. All identity
        # computation itself sees only the normalized, walrus-free expressions.
        node = self.normalized.replacements.get(node, node)
        return self.identities.get(self.keys.get(node), set())

    def violations(self):
        for node in ast.walk(self.tree):
            original = self.normalized.originals[node]
            if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(
                node.ctx, ast.Load
            ):
                if original in self.normalized.callees:
                    # Exempt only the actual callee node, not its arguments,
                    # walrus value, conditional branches or container elements.
                    continue
                if self.names(node) & SETTERS:
                    yield (
                        original,
                        "builtin-setter-load " + self.renderer.diagnostic(original),
                    )
            elif (
                isinstance(node, ast.Call)
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                and node.args[1].value in REFLECTED_SETTERS
                and self.names(node.func) & GETTERS
            ):
                # Deliberately receiver-independent: an obj may be builtins,
                # object, type or an alias. Do not infer its runtime identity.
                yield (
                    original,
                    "builtin-setter-load " + self.renderer.diagnostic(original),
                )
