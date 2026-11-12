"""Frozen, conservative local-alias language for the offline transport census.

Not whole-program Python type inference: a may-alias is enough to census a send,
never enough to grant admission. Reassignment cannot erase an earlier candidate.
"""

import ast
from collections import defaultdict

CENSUS_CONTRACT = (
    "Source/import closure from the six component roots plus the actual background "
    "tool allowlist; existing explicit dependency boundaries and exact counted "
    "exceptions remain. Enumerate listed transport verbs, SDK chains, imported "
    "module/from aliases, and transport constructors. Follow ordinary local "
    "Assign (including chained targets), AnnAssign, transitive Name/Attribute "
    "copies, recursive tuple/list unpacking (including locally named tuples/lists "
    "by conservative element union), dotted attribute-prefix "
    "chains, and with/async-with targets (including multiple items and tuple/list "
    "targets). Seeds are imported transports/constructors and client, session, "
    "connection, conn, http, transport attributes/parameters (including leading "
    "underscores). Alias evidence is scope-aware and monotonic across rebinding; "
    "nested scopes cannot erase parent evidence. Bound-method copies and dynamic "
    "getattr calls are candidates, not admissions. No arbitrary interprocedural "
    "factory-return inference, indexed-container flow, starred unpacking, "
    "reflection, descriptors or runtime monkeypatch proof; forms outside this "
    "listed contract with no production instance are backlog."
    " Structural transport-store rule: independently of alias/send inference, "
    "fail closed on Assign, AugAssign, AnnAssign, recursive tuple/list unpack, "
    "with/async-with targets and setattr stores whose value is or may be an "
    "imported transport module/name, transport constructor, any expression "
    "reading a client/session/connection/conn/http/transport attribute (leading "
    "underscores included), or a local already holding one. Only plain local "
    "Names and direct self.<name> attributes are allowed; container stores and "
    "deeper or aliased-holder attributes require exact function-level counted "
    "exceptions. Non-literal setattr/getattr names on these values also require "
    "exact exceptions. Send admission cannot waive this structural rule."
)
CLIENT_PARTS = {"client", "session", "connection", "conn", "http", "transport"}
SCOPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


class LocalAliases:
    """Resolve dotted prefixes over finite binding edges without a depth cutoff.

    Multiple assignments retain all candidates. Only lexical ancestors are
    searched, never unrelated functions that happen to reuse the same local.
    Composed paths first acquire binding evidence to a monotone fixed point.
    Only paths present in the AST and existing binding values enter that closure;
    a visited binding edge then stops expansion cycles, including x = x.transport.
    """

    def __init__(self, tree, parents, transport_modules):
        self.parents = parents
        self.transports = transport_modules
        self.bindings = defaultdict(list)
        self.cache = {}
        for node in ast.walk(tree):
            scope = self.scope(node)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.bindings[
                        scope, alias.asname or alias.name.split(".")[0]
                    ].append(
                        (
                            alias.name if alias.asname else alias.name.split(".")[0],
                            scope,
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    self.bindings[scope, alias.asname or alias.name].append(
                        ((node.module or "") + "." + alias.name, scope)
                    )
            elif (
                isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
            ):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    self.bind(target, node.value, scope)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        self.bind(item.optional_vars, item.context_expr, scope)
        self.close_composed_paths(tree)

    def close_composed_paths(self, tree):
        # Every Attribute contains its own AST prefix, so these finite paths
        # cover intermediate lookups too. Do not recursively intern generated
        # spellings: x = x.transport would create infinitely many of them.
        paths = dict.fromkeys(
            (self.scope(node), ast.unparse(node))
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        )
        evidence = {key: set(values) for key, values in self.bindings.items()}
        changed = True
        while changed:
            changed = False
            for scope, name in paths:
                parts = name.split(".")
                acquired = set()
                for count in range(1, len(parts)):
                    prefix = ".".join(parts[:count])
                    suffix = "." + ".".join(parts[count:])
                    for candidate in self.expand(prefix, scope, set()):
                        # other -> holder is not enough: lookup holder.pipe
                        # after composing the suffix, retaining the binding's
                        # original scope rather than borrowing sibling evidence.
                        for outer in self.enclosing(scope):
                            acquired.update(
                                self.bindings.get((outer, candidate + suffix), ())
                            )
                key = (scope, name)
                new = acquired - evidence.get(key, set())
                if new:
                    self.bindings[key].extend(new)
                    evidence.setdefault(key, set()).update(new)
                    changed = True
        # The table only grows, over AST paths x original (value, scope) pairs.
        # No hop cutoff, rebinding erasure, or unbounded generated path domain.

    def scope(self, node):
        # Definition nodes are bindings in the outer scope; expressions in their
        # bodies naturally select the function/class itself through parents.
        while node in self.parents:
            node = self.parents[node]
            if isinstance(node, SCOPES):
                return node
        return node

    def enclosing(self, scope):
        yield scope
        while scope in self.parents:
            scope = self.scope(scope)
            yield scope

    def bind(self, target, value, scope):
        if isinstance(target, (ast.Tuple, ast.List)):
            if isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(
                value.elts
            ):
                for child, item in zip(target.elts, value.elts, strict=True):
                    self.bind(child, item, scope)
            else:
                # Unknown with-target tuple shape: conservatively taint all
                # elements from its manager, rather than silently losing them.
                for child in target.elts:
                    self.bind(child, value, scope)
        elif isinstance(target, (ast.Name, ast.Attribute)):
            if isinstance(value, (ast.Tuple, ast.List)):
                # A locally named tuple may later be unpacked. Union its element
                # capabilities conservatively; no indexed-container inference.
                for item in value.elts:
                    self.bind(target, item, scope)
            elif isinstance(value, (ast.Name, ast.Attribute, ast.Call)):
                self.bindings[scope, ast.unparse(target)].append((value, scope))

    def expand(self, name, scope, seen):
        results = {name}
        parts = name.split(".")
        for count in range(1, len(parts) + 1):
            prefix = ".".join(parts[:count])
            suffix = "." + ".".join(parts[count:]) if count < len(parts) else ""
            for outer in self.enclosing(scope):
                key = (outer, prefix)
                if key in seen:
                    continue
                for value, origin in self.bindings.get(key, ()):
                    if isinstance(value, str):
                        resolved = {value}
                    elif isinstance(value, ast.Call):
                        # Only known transport constructors confer a capability.
                        resolved = {
                            candidate + "()"
                            for candidate in self.expand(
                                ast.unparse(value.func), origin, seen | {key}
                            )
                            if candidate.split(".")[0] in self.transports
                            and (
                                candidate.rsplit(".", 1)[-1][:1].isupper()
                                or candidate.rsplit(".", 1)[-1]
                                in {"client", "get_client"}
                            )
                        }
                    else:
                        resolved = self.expand(ast.unparse(value), origin, seen | {key})
                    results.update(candidate + suffix for candidate in resolved)
        return results

    def names(self, node):
        if node not in self.cache:
            self.cache[node] = self.expand(ast.unparse(node), self.scope(node), set())
        return self.cache[node]

    def resolve(self, node):
        names = self.names(node)
        # Imported canonical names preserve the existing admission proofs. Raw
        # local spellings still remain available for conservative classification.
        imported = sorted(
            name
            for name in names
            if name.split(".")[0] in self.transports
            or name.startswith(("tracer.", "ee."))
        )
        return imported[0] if imported else ast.unparse(node)

    def transport_receiver(self, node):
        return any(
            name.split(".")[0] in self.transports
            or any(part.lstrip("_") in CLIENT_PARTS for part in name.split("."))
            for name in self.names(node)
        )


class TransportStores:
    """Structural fail-closed check, not a second holder-alias resolver.

    Propagate only a boolean may-value to finite lexical local Names. Inspect
    all store targets directly, including ones the send resolver cannot model.
    No reverse holder equivalence, generated paths, or new send admissions.
    """

    def __init__(self, tree, aliases):
        self.aliases = aliases
        self.locals = set()
        self.stores, self.reflections = [], []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                if node.value is not None:
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    for target in targets:
                        # AugAssign reads its old target as well as its RHS.
                        value = node if isinstance(node, ast.AugAssign) else node.value
                        self.add_store(node, target, value)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        self.add_store(node, item.optional_vars, item.context_expr)
            elif isinstance(node, ast.Call):
                names = aliases.names(node.func)
                if names & {"setattr", "builtins.setattr"} and len(node.args) >= 3:
                    self.stores.append((node, node, node.args[2]))
                if names & {
                    "getattr",
                    "setattr",
                    "builtins.getattr",
                    "builtins.setattr",
                }:
                    self.reflections.append(node)
        # A finite monotone bit per local binding; rebinding never removes it.
        changed = True
        while changed:
            changed = False
            for _, target, value in self.stores:
                if isinstance(target, ast.Name) and self.may_value(value):
                    key = (aliases.scope(target), target.id)
                    if key not in self.locals:
                        self.locals.add(key)
                        changed = True

    def add_store(self, node, target, value):
        if isinstance(target, (ast.Tuple, ast.List)):
            if isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(
                value.elts
            ):
                for child, item in zip(target.elts, value.elts, strict=True):
                    self.add_store(node, child, item)
            else:
                for child in target.elts:
                    self.add_store(node, child, value)
        elif isinstance(target, ast.Starred):
            self.add_store(node, target.value, value)
        else:
            self.stores.append((node, target, value))

    def may_value(self, value):
        # Expression reads are deliberately conservative: conditional values,
        # containers and wrappers cannot launder a capability out of the rule.
        for part in ast.walk(value):
            if not isinstance(part, (ast.Name, ast.Attribute)):
                continue
            if self.aliases.transport_receiver(part):
                return True
            if isinstance(part, ast.Name) and any(
                (scope, part.id) in self.locals
                for scope in self.aliases.enclosing(self.aliases.scope(part))
            ):
                return True
        return False

    @staticmethod
    def literal_name(node):
        return isinstance(node, ast.Constant) and isinstance(node.value, str)

    def allowed(self, target):
        if isinstance(target, ast.Name):
            return True
        if isinstance(target, ast.Attribute):
            return isinstance(target.value, ast.Name) and target.value.id == "self"
        if isinstance(target, ast.Call):
            return (
                isinstance(target.args[0], ast.Name)
                and target.args[0].id == "self"
                and self.literal_name(target.args[1])
            )
        return False

    def violations(self):
        flagged = set()
        for node, target, value in self.stores:
            if self.may_value(value) and not self.allowed(target):
                flagged.add(node)
                yield (
                    node,
                    "transport-store "
                    + ast.unparse(target)
                    + " <- "
                    + ast.unparse(value),
                )
        for node in self.reflections:
            if (
                node not in flagged
                and len(node.args) >= 2
                and not self.literal_name(node.args[1])
                and any(self.may_value(arg) for arg in (node.args[0], *node.args[2:]))
            ):
                yield node, "dynamic-attribute " + ast.unparse(node)
