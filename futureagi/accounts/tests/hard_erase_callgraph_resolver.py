"""Lexical, conservative alias/import resolution for the issuance source gate."""

import ast

WRITE_METHODS = {
    "save",
    "create",
    "update",
    "get_or_create",
    "update_or_create",
    "select_for_update",
    "add",
    "set",
    "delete",
    "execute",
    "executemany",
}

CALLBACK_DISPATCH = {
    "concurrent.futures.ThreadPoolExecutor.submit",
    "asgiref.sync.sync_to_async",
    "asgiref.sync.async_to_sync",
    "channels.db.database_sync_to_async",
    "django.db.transaction.on_commit",
}


def scope_nodes(node):
    """Do not merge nested function execution into its parent's execution order."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(
            child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            yield child
        else:
            yield from scope_nodes(child)


def imports_for(node, module, *, package=False):
    aliases = {}
    for child in scope_nodes(node):
        if isinstance(child, ast.ImportFrom):
            base = child.module or ""
            if child.level:
                parts = module.split(".") if package else module.split(".")[:-1]
                if child.level > len(parts):
                    raise ValueError("unresolved relative issuance import")
                base = ".".join(
                    parts[: len(parts) - child.level + 1] + ([base] if base else [])
                )
            for alias in child.names:
                aliases.setdefault(alias.asname or alias.name, set()).add(
                    base + "." + alias.name
                )
        elif isinstance(child, ast.Import):
            for alias in child.names:
                aliases.setdefault(alias.asname or alias.name.split(".")[0], set()).add(
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
    return aliases


def expand(name, exports, seen=frozenset()):
    parts = name.split(".")
    for i in range(len(parts) - 1, 0, -1):
        module, symbol = ".".join(parts[:i]), parts[i]
        if symbol in exports.get(module, {}) and (module, symbol) not in seen:
            suffix = "." + ".".join(parts[i + 1 :]) if i + 1 < len(parts) else ""
            return {
                target
                for alias in exports[module][symbol]
                for target in expand(alias + suffix, exports, seen | {(module, symbol)})
            }
    return {name}


def resolve(node, aliases, module, owner, exports):
    if isinstance(node, ast.Lambda):
        return {node.issuance_scope_key}
    if isinstance(node, ast.Attribute) and ast.unparse(node) in aliases:
        names = aliases[ast.unparse(node)]
    elif isinstance(node, ast.Name):
        names = aliases.get(node.id, {module + "." + node.id})
    elif isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id == "self" and owner:
            class_owner = (
                owner.split(".<locals>")[0].rsplit(".", 1)[0]
                if ".<locals>" in owner
                else owner
            )
            names = {module + "." + class_owner + "." + node.attr}
        else:
            names = {
                name + "." + node.attr
                for name in resolve(node.value, aliases, module, owner, exports)
            }
        if not names and (node.attr in WRITE_METHODS or node.attr.startswith("bulk_")):
            # A query/cursor factory need not resolve to know that its bound
            # save/update/lock/execute method carries a conservative write effect.
            names = {"<write>." + node.attr}
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) == 3
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "_original_func"
    ):
        # Celery compatibility shim resolves to the same callable, never a
        # dynamic method name. A None default adds no callable effect; otherwise
        # both source/default references must be identical.
        left = resolve(node.args[0], aliases, module, owner, exports)
        right = resolve(node.args[2], aliases, module, owner, exports)
        absent = isinstance(node.args[2], ast.Constant) and node.args[2].value is None
        return left if absent or left == right else set()
    else:
        return set()
    return {target for name in names for target in expand(name, exports)}


def bindings(function, module, owner, inherited, exports, nested, classes=frozenset()):
    aliases = {name: set(values) for name, values in inherited.items()}
    if not isinstance(function, ast.Module):
        for name, values in imports_for(function, module).items():
            aliases[name] = set(values)
    aliases.update({name: {target} for name, target in nested.items()})
    if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        positional = [*function.args.posonlyargs, *function.args.args]
        defaults = (
            list(
                zip(
                    positional[-len(function.args.defaults) :],
                    function.args.defaults,
                    strict=True,
                )
            )
            if function.args.defaults
            else []
        )
        defaults.extend(
            zip(function.args.kwonlyargs, function.args.kw_defaults, strict=True)
        )
        for argument, default in defaults:
            if default is not None:
                aliases.setdefault(argument.arg, set()).update(
                    resolve(default, aliases, module, owner, exports)
                )
    for node in scope_nodes(function):
        if isinstance(node, ast.With):
            for item in node.items:
                if isinstance(item.optional_vars, ast.Name) and isinstance(
                    item.context_expr, ast.Call
                ):
                    targets = resolve(
                        item.context_expr.func, aliases, module, owner, exports
                    )
                    if targets == {"concurrent.futures.ThreadPoolExecutor"}:
                        aliases[item.optional_vars.id] = targets
    # Union all possible local aliases, including assignments in branches. A
    # reassignment cannot make a potentially issuing callable disappear.
    assignments = [
        n for n in scope_nodes(function) if isinstance(n, (ast.Assign, ast.AnnAssign))
    ]
    for _ in range(len(assignments) + 1):
        changed = False
        for assignment in assignments:
            value = assignment.value
            if isinstance(value, ast.Call):
                names = (
                    resolve(value.func, aliases, module, owner, exports) & classes
                ) | resolve(value, aliases, module, owner, exports)
            elif isinstance(value, (ast.Name, ast.Attribute, ast.Lambda)):
                names = resolve(value, aliases, module, owner, exports)
            else:
                continue
            targets = (
                assignment.targets
                if isinstance(assignment, ast.Assign)
                else [assignment.target]
            )
            for target in targets:
                if not isinstance(target, (ast.Name, ast.Attribute)):
                    continue
                spelling = ast.unparse(target)
                # Attribute aliases track callable bindings, not inferred object
                # property types across methods. Keep constructor inference for
                # local instances (the pre-existing same-module method rule).
                bound = (
                    names - classes
                    if isinstance(target, ast.Attribute) and isinstance(value, ast.Call)
                    else names
                )
                before = set(aliases.get(spelling, ()))
                aliases.setdefault(spelling, set()).update(bound)
                changed |= before != aliases[spelling]
        if not changed:
            break
    return aliases


def unresolved_references(
    key,
    function,
    aliases,
    module,
    owner,
    exports,
    reached,
    calls,
    classes=frozenset(),
    *,
    resolved_dispatch_arguments=frozenset(),
):
    nodes = list(scope_nodes(function))
    parents = {
        id(child): parent for parent in nodes for child in ast.iter_child_nodes(parent)
    }
    annotation_nodes = {
        id(child)
        for node in nodes
        for annotation in (
            [node.annotation]
            if isinstance(node, (ast.arg, ast.AnnAssign))
            else [node.returns]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            else []
        )
        if annotation is not None
        for child in ast.walk(annotation)
    }
    resolved_calls = {id(node.func) for node, target in calls if target in reached}
    for node, target in calls:
        if (
            target in reached
            and node.args
            and resolve(node.func, aliases, module, owner, exports) & CALLBACK_DISPATCH
        ):
            resolved_calls.add(id(node.args[0]))
    for node in nodes:
        if not isinstance(node, (ast.Name, ast.Attribute)) or not isinstance(
            node.ctx, ast.Load
        ):
            continue
        if id(node) in annotation_nodes:
            continue
        parent = parents.get(id(node))
        # Classify only the full attribute expression, not each module prefix.
        if isinstance(parent, ast.Attribute) and parent.value is node:
            continue
        targets = resolve(node, aliases, module, owner, exports)
        capable = any(
            target in reached
            or any(r.startswith(target + ".") for r in reached)
            or target.rsplit(".", 1)[-1] in {"issue_membership", "save_new_person"}
            for target in targets
        )
        if not capable:
            continue
        if (
            isinstance(parent, ast.Call)
            and resolve(parent, aliases, module, owner, exports) == targets
        ):
            continue
        ancestor = parent
        while ancestor is not None and not isinstance(ancestor, ast.Call):
            ancestor = parents.get(id(ancestor))
        if isinstance(ancestor, ast.Call) and resolve(
            ancestor.func, aliases, module, owner, exports
        ) == {"tfc.temporal.common.registry.register_for_queues"}:
            # Registration is not invocation; registered activities are roots.
            continue
        if id(node) in resolved_calls or id(node) in resolved_dispatch_arguments:
            # Dispatch arguments qualify only with exact source-bound capture and
            # registration edges; the original issuance method remains reachable.
            continue
        # Constructors are resolved to __init__; instance method calls have
        # their own edges. Constructing an object does not invoke every method.
        if isinstance(parent, ast.Call) and parent.func is node and targets <= classes:
            continue
        if isinstance(parent, ast.Compare) and all(
            isinstance(op, (ast.Is, ast.IsNot)) for op in parent.ops
        ):
            continue
        if isinstance(parent, (ast.BoolOp, ast.If)) and targets <= classes:
            continue
        if isinstance(parent, ast.Call) and resolve(
            parent.func, aliases, module, owner, exports
        ) in (
            {"inspect.signature"},
            {"tfc.utils.parse_errors.parse_serialized_errors"},
        ):
            continue
        if isinstance(parent, (ast.Assign, ast.AnnAssign)) and parent.value is node:
            assigned = (
                parent.targets if isinstance(parent, ast.Assign) else [parent.target]
            )
            if all(
                isinstance(target, (ast.Name, ast.Attribute)) for target in assigned
            ):
                continue
        raise ValueError(
            f"unresolved issuance reference in {key}:{node.lineno}: {ast.unparse(node)}"
        )
