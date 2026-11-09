"""Finite structural binding events and Python lexical destinations (test only).

This is deliberately separate from the frozen send-alias resolver. All Name
binding forms enter one handler; expression scope is assigned before propagation.
"""

import ast
from collections import defaultdict

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
BINDING_NODE_TYPES = frozenset(
    "Assign AnnAssign AugAssign NamedExpr For AsyncFor comprehension With AsyncWith "
    "ExceptHandler MatchAs MatchStar MatchMapping FunctionDef AsyncFunctionDef "
    "ClassDef Lambda Import ImportFrom Delete arg TypeAlias TypeVar ParamSpec TypeVarTuple".split()
)


class BindingForms:
    """One event table: (syntax, target, value); deletions are explicit kills.

    Symbols are collected independently of candidate evidence, so an untainted
    parameter/local shadows an enclosing candidate. Globals/nonlocals resolve to
    their actual destination. Comprehension walruses bind the containing scope.
    """

    def __init__(self, tree, parents):
        self.tree, self.parents = tree, parents
        self.scopes, self.outer = {}, {}
        self.symbols = defaultdict(set)
        self.globals, self.nonlocals = defaultdict(set), defaultdict(set)
        self.events, self.kills, self.imports = [], [], set()
        self.classes = {}
        self.map_scopes(tree, tree)
        for node in ast.walk(tree):
            scope = self.scopes[node]
            if isinstance(node, ast.Global):
                self.globals[scope].update(node.names)
            elif isinstance(node, ast.Nonlocal):
                self.nonlocals[scope].update(node.names)
            self.bind_node(node)
        self.classes = {
            (self.destination(scope, name, True), name): cls
            for (scope, name), cls in self.classes.items()
        }

    def map_scopes(self, node, scope):
        self.scopes[node] = scope
        if isinstance(node, (*FUNCTIONS, ast.ClassDef)):
            self.outer[node] = scope
            for field, value in ast.iter_fields(node):
                children = value if isinstance(value, list) else [value]
                for child in children:
                    if isinstance(child, ast.AST):
                        # Defaults/decorators/bases evaluate outside the body.
                        inner = (
                            node if field in {"body", "args", "type_params"} else scope
                        )
                        self.map_scopes(child, inner)
            return
        if isinstance(node, ast.arguments):
            for child in ast.iter_child_nodes(node):
                self.map_scopes(
                    child, scope if isinstance(child, ast.arg) else self.outer[scope]
                )
            return
        if isinstance(node, ast.arg):
            for child in ast.iter_child_nodes(node):
                self.map_scopes(child, self.outer[scope])
            return
        if isinstance(node, COMPREHENSIONS):
            self.outer[node] = scope
            for index, generator in enumerate(node.generators):
                self.scopes[generator] = node
                self.map_scopes(generator.iter, scope if index == 0 else node)
                self.map_scopes(generator.target, node)
                for condition in generator.ifs:
                    self.map_scopes(condition, node)
            for field in ("elt", "key", "value"):
                if hasattr(node, field):
                    self.map_scopes(getattr(node, field), node)
            return
        for child in ast.iter_child_nodes(node):
            self.map_scopes(child, scope)

    def name(self, name, node, scope):
        target = ast.copy_location(ast.Name(id=name, ctx=ast.Store()), node)
        self.scopes[target] = scope
        return target

    def add(self, node, target, value, pairwise=True):
        if isinstance(target, (ast.Tuple, ast.List)):
            exact = (
                pairwise
                and isinstance(value, (ast.Tuple, ast.List))
                and len(target.elts) == len(value.elts)
            )
            for index, child in enumerate(target.elts):
                self.add(node, child, value.elts[index] if exact else value, pairwise)
        elif isinstance(target, ast.Starred):
            self.add(node, target.value, value, pairwise)
        else:
            if isinstance(target, ast.Name):
                self.symbols[self.scopes[target]].add(target.id)
            self.events.append((node, target, value))

    def bind_node(self, node):
        """The exhaustive binding-form handler; None values declare without flow."""
        scope = self.scopes[node]
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(node, ast.NamedExpr):
                    destination = scope
                    while isinstance(destination, COMPREHENSIONS):
                        destination = self.outer[destination]
                    self.scopes[target] = destination
                self.add(
                    node,
                    target,
                    node if isinstance(node, ast.AugAssign) else node.value,
                )
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            # The iterable is a collection of rows, not the unpacked row itself.
            self.add(node, node.target, node.iter, pairwise=False)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    self.add(node, item.optional_vars, item.context_expr)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            # The caught exception may be a candidate raised by the protected
            # body, even when its catch type is a generic Exception.
            protected = self.parents[node]
            raised = [
                part.exc
                for stmt in protected.body
                for part in ast.walk(stmt)
                if isinstance(part, ast.Raise) and part.exc is not None
            ]
            value = ast.Tuple(
                elts=([node.type] if node.type else []) + raised, ctx=ast.Load()
            )
            target = self.name(node.name, node, scope)
            self.add(node, target, value)
            self.kills.append((target, node, (node.end_lineno, 10**9)))
        elif isinstance(node, (ast.MatchAs, ast.MatchStar, ast.MatchMapping)):
            name = node.rest if isinstance(node, ast.MatchMapping) else node.name
            if name:
                match = self.parents[node]
                while not isinstance(match, ast.Match):
                    match = self.parents[match]
                self.add(node, self.name(name, node, scope), match.subject)
        elif isinstance(node, (*FUNCTIONS, ast.ClassDef)):
            if not isinstance(node, ast.Lambda):
                target = self.name(node.name, node, scope)
                self.add(node, target, None)
                if isinstance(node, ast.ClassDef):
                    self.classes[scope, node.name] = node
            if isinstance(node, FUNCTIONS):
                positional = node.args.posonlyargs + node.args.args
                pairs = list(
                    zip(
                        positional[len(positional) - len(node.args.defaults) :],
                        node.args.defaults,
                        strict=True,
                    )
                )
                pairs += list(
                    zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True)
                )
                for parameter, default in pairs:
                    self.add(node, self.name(parameter.arg, parameter, node), default)
        elif isinstance(node, ast.arg):
            self.add(node, self.name(node.arg, node, scope), None)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = alias.asname or (
                    alias.name.split(".")[0]
                    if isinstance(node, ast.Import)
                    else alias.name
                )
                canonical = (
                    alias.name
                    if isinstance(node, ast.Import)
                    else (node.module or "") + "." + alias.name
                )
                value = ast.copy_location(ast.Constant(value=canonical), node)
                self.imports.add(value)
                self.add(node, self.name(name, node, scope), value)
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                for part in ast.walk(target):
                    if isinstance(part, (ast.Name, ast.Attribute)) and isinstance(
                        part.ctx, ast.Del
                    ):
                        self.kills.append((part, node, self.position(node)))
                        if isinstance(part, ast.Name):
                            self.symbols[scope].add(part.id)
        elif isinstance(node, ast.TypeAlias):
            self.add(node, node.name, node.value)
        elif isinstance(node, (ast.TypeVar, ast.ParamSpec, ast.TypeVarTuple)):
            self.add(
                node, self.name(node.name, node, scope), getattr(node, "bound", None)
            )

    def destination(self, scope, name, store=False):
        if name in self.globals[scope]:
            return self.tree
        if name in self.nonlocals[scope]:
            outer = self.outer.get(scope)
            while outer is not None:
                if isinstance(outer, FUNCTIONS) and name in self.symbols[outer]:
                    return self.destination(outer, name, True)
                outer = self.outer.get(outer)
            return self.tree  # malformed synthetic input must still fail closed
        if store or name in self.symbols[scope] or scope is self.tree:
            return scope
        outer = self.outer[scope]
        # Class namespaces are not closure cells for nested functions/classes.
        while isinstance(outer, ast.ClassDef):
            outer = self.outer[outer]
        return self.destination(outer, name)

    def key(self, target, store=False):
        scope = self.scopes[target]
        if isinstance(target, ast.Name):
            return self.destination(scope, target.id, store), target.id
        if isinstance(target, ast.Attribute):
            base = target
            suffix = []
            while isinstance(base, ast.Attribute):
                suffix.insert(0, base.attr)
                base = base.value
            if isinstance(base, ast.Name):
                destination = self.destination(scope, base.id)
                parts = [base.id, *suffix]
                while len(parts) > 1 and (destination, parts[0]) in self.classes:
                    destination = self.classes[destination, parts.pop(0)]
                return destination, ".".join(parts)
        return None

    def local(self, target):
        scope = self.scopes[target]
        return (
            isinstance(scope, (*FUNCTIONS, *COMPREHENSIONS))
            and target.id not in self.globals[scope]
            and target.id not in self.nonlocals[scope]
            and self.destination(scope, target.id, True) is scope
        )

    @staticmethod
    def position(node):
        return getattr(node, "lineno", 0), getattr(node, "col_offset", 0)

    def killed(self, key, origin, read):
        syntax, bound_target = origin
        for target, deletion, position in self.kills:
            if (
                self.key(target, True) != key
                or self.scopes[read] is not self.scopes[target]
            ):
                continue
            if self.scopes[bound_target] is not self.scopes[target]:
                continue  # no execution-order claim across function calls
            if not (self.position(bound_target) < position < self.position(read)):
                continue
            # Only same-block deletes kill. A conditional delete cannot erase
            # evidence read outside its branch. Except names are auto-cleared
            # after their handler, including assignments inside that handler.
            if isinstance(deletion, ast.ExceptHandler):
                if syntax is deletion or syntax in set(ast.walk(deletion)):
                    return True
            else:
                parent = self.parents.get(deletion)
                for field in vars(parent).values():
                    if isinstance(field, list) and deletion in field:
                        if any(read in set(ast.walk(stmt)) for stmt in field):
                            return True
        return False
