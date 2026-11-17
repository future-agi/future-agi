"""Conservative source call graph of explicit identity issuance (no Django import).

Resolve imported aliases, module calls, same-class calls, user-manager dispatch,
and the known signup-serializer/invitation polymorphic entry. Inventory all
transitive callers across futureagi, not only accounts. Dynamic framework entry
methods are roots; their transaction boundaries are reviewed below.
"""

import ast
import runpy
from pathlib import Path

source_files = runpy.run_path(
    str(Path(__file__).with_name("hard_erase_source_files.py"))
)["source_files"]

RESOLVER = runpy.run_path(
    str(Path(__file__).with_name("hard_erase_callgraph_resolver.py"))
)
EFFECTS = runpy.run_path(
    str(Path(__file__).with_name("hard_erase_callgraph_effects.py"))
)
DISPATCH = runpy.run_path(
    str(Path(__file__).with_name("hard_erase_callgraph_dispatch.py"))
)
scope_nodes = RESOLVER["scope_nodes"]
ROOT = Path(__file__).resolve().parents[3]
ROOTS = {
    "accounts.services.identity_issuance.issue_membership",
    "accounts.services.identity_issuance.save_new_person",
}
LOCKS = {"issuance_entry", "lifecycle_lock"}
DECORATORS = {"request_issuance_entry", "tool_issuance_entry"}
# These are nested helpers, never independent request/service entries. Every
# incoming call must be dominated by an entry scope, recursively.
NESTED = {
    "accounts.services.member_role_service._apply_org_level_change",
    "accounts.services.member_role_service._apply_workspace_access",
    "accounts.services.member_role_service._apply_ws_level_change",
    "accounts.services.member_role_service._promote_to_workspace_admin_everywhere",
    "accounts.views.rbac_views.InviteCreateAPIView._dual_write_legacy",
    "accounts.views.workspace_management.ManageTeamView._add_user_to_workspace",
}
# Separate committed preparation, not writes in the identity transaction. These
# orchestrators must remain non-atomic; their issuance child owns the entry set.
SEPARATE_TRANSACTIONS = {
    "tracer.tasks.error_analysis._falcon_analyze_trace_in_scope": "Conversation is created in autocommit before the asynchronous agent/tool transaction; no enclosing atomic block.",
    "accounts.aws_marketplace_utils.process_aws_onboarding": "Entitlement setup precedes signup in autocommit, with no enclosing atomic block.",
    "tfc.management.commands.bootstrap_install.Command.handle": "Schema/seeds finish before first_admin; bootstrap is not an atomic identity transaction.",
}
WRITE_NAMES = {
    "save",
    "create",
    "update",
    "get_or_create",
    "update_or_create",
    "bulk_create",
    "bulk_update",
    "select_for_update",
    "add",
    "set",
    "delete",
    "execute",
    "executemany",
}


def graph(files=None):
    # Fixtures inject source paths, never graph edges or a fake Git process.
    files = source_files(ROOT) if files is None else files
    functions, imports, tool_classes, trees = {}, {}, set(), {}
    parents, classes = {}, set()
    for file in sorted(set(files)):
        if not file.startswith("futureagi/") or not file.endswith(".py"):
            continue
        if (
            any(
                part in file.lower()
                for part in (
                    "/tests/",
                    "/migrations/",
                    "settings",
                    "secret",
                    ".env",
                    "values",
                )
            )
            or Path(file).name == "conftest.py"
        ):
            continue
        tree = ast.parse((ROOT / file).read_text())
        module = file.removeprefix("futureagi/").removesuffix(".py").replace("/", ".")
        package = module.endswith(".__init__")
        if package:
            module = module.removesuffix(".__init__")
        imports[module] = RESOLVER["imports_for"](tree, module, package=package)
        trees[module] = tree
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and any(
                "ai_tools.base.BaseTool"
                in RESOLVER["resolve"](base, imports[module], module, "", {})
                for base in node.bases
            ):
                tool_classes.add(module + "." + node.name)

        def visit(nodes, owner="", parent=None, module=module, file=file):
            for node in nodes:
                if isinstance(node, ast.ClassDef):
                    classes.add(
                        module + "." + (owner + "." if owner else "") + node.name
                    )
                    visit(node.body, (owner + "." if owner else "") + node.name, parent)
                elif isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
                ):
                    if isinstance(node, ast.Lambda):
                        node.name = f"<lambda_{node.lineno}_{node.col_offset}>"
                        node.decorator_list = []
                    key = module + "." + (owner + "." if owner else "") + node.name
                    node.issuance_scope_key = key
                    functions[key] = (file, node, module, owner)
                    parents[key] = parent
                    visit(
                        [node.args, node.body]
                        if isinstance(node, ast.Lambda)
                        else [node.args, *node.decorator_list, *node.body],
                        (owner + "." if owner else "") + node.name + ".<locals>",
                        key,
                    )
                else:
                    # Definitions may be conditional; collect them conservatively.
                    visit(list(ast.iter_child_nodes(node)), owner, parent)

        visit(tree.body)
    exports = imports
    environments = {}
    children = {}
    for child, parent in parents.items():
        children.setdefault(parent, {})[functions[child][1].name] = child
    module_environments = {
        module: RESOLVER["bindings"](
            tree, module, "", imports[module], exports, {}, classes
        )
        for module, tree in trees.items()
    }
    # Module-local callable aliases are also exports for relative/re-export imports.
    exports = module_environments
    for key, (_, function, module, owner) in functions.items():
        parent = parents[key]
        function.issuance_parameters = {
            node.arg for node in scope_nodes(function.args) if isinstance(node, ast.arg)
        } | (functions[parent][1].issuance_parameters if parent else set())
        inherited = environments[parent] if parent else module_environments[module]
        nested = children.get(key, {})
        environments[key] = RESOLVER["bindings"](
            function, module, owner, inherited, exports, nested, classes
        )
    registration_edges, bound_arguments = DISPATCH["bind"](
        functions, environments, exports, RESOLVER, trees
    )
    edges = {}
    for key, (_, function, module, owner) in functions.items():
        calls = list(registration_edges.get(key, ()))
        for node in scope_nodes(function):
            if isinstance(node, ast.Lambda) and node is not function:
                # Conservatively account at definition as well as invocation or
                # callback dispatch. The lambda's body is its own execution scope.
                call = ast.copy_location(
                    ast.Call(func=node, args=[], keywords=[]), node
                )
                calls.append((call, node.issuance_scope_key))
            if not isinstance(node, ast.Call):
                continue
            spelling = ast.unparse(node.func)
            candidates = RESOLVER["resolve"](
                node.func, environments[key], module, owner, exports
            )
            if spelling.endswith((".create_user", ".create_superuser")):
                candidates = {"accounts.models.user.CustomUserManager.create_user"}
            if spelling.endswith(".accept") and not spelling.startswith("self."):
                candidates = {
                    "accounts.models.organization_invite.OrganizationInvite.accept"
                }
            if spelling == "serializer.save" and "UserSignupSerializer" in ast.unparse(
                function
            ):
                candidates = {"accounts.serializers.user.UserSignupSerializer.create"}
            if spelling == "tool.run":
                candidates = {"ai_tools.base.BaseTool.run"}
            if "ai_tools.base.BaseTool.execute" in candidates:
                candidates.update(cls + ".execute" for cls in tool_classes)
            if candidates == {"concurrent.futures.ThreadPoolExecutor.submit"}:
                node.issuance_separate_thread = True
            if candidates & RESOLVER["CALLBACK_DISPATCH"] and node.args:
                candidates.update(
                    RESOLVER["resolve"](
                        node.args[0], environments[key], module, owner, exports
                    )
                )
            candidates.update(
                candidate + ".__init__" for candidate in candidates & classes
            )
            calls.extend(
                (node, candidate) for candidate in candidates if candidate in functions
            )
        edges[key] = calls
    reached = set(ROOTS)
    while True:
        new = {
            key
            for key, calls in edges.items()
            if any(target in reached for _, target in calls)
        } - reached
        if not new:
            break
        reached.update(new)
    # Any unsupported use of an issuance-capable reference fails the census;
    # callbacks/containers/dynamic dispatch cannot silently drop a caller.
    unresolved = []
    for key, (_, function, module, owner) in functions.items():
        try:
            RESOLVER["unresolved_references"](
                key,
                function,
                environments[key],
                module,
                owner,
                exports,
                reached,
                edges[key],
                classes,
                resolved_dispatch_arguments=bound_arguments,
            )
        except ValueError as exc:
            unresolved.append(str(exc))
    if unresolved:
        raise ValueError("\n".join(unresolved))
    EFFECTS["propagate"](
        functions, edges, reached, environments, exports, RESOLVER, writes, WRITE_NAMES
    )
    return functions, edges, reached


def entry_scopes(function):
    return [
        node
        for node in scope_nodes(function)
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and ast.unparse(item.context_expr.func) in LOCKS
            for item in node.items
        )
    ]


def decorated(function):
    return any(ast.unparse(node) in DECORATORS for node in function.decorator_list)


def dominated(node, scopes):
    return any(
        scope.body[0].lineno <= node.lineno <= scope.end_lineno for scope in scopes
    )


def writes(function):
    yield from getattr(function, "issuance_effect_calls", ())
    memory_objects = {
        target.id
        for node in scope_nodes(function)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        if isinstance(node.value, (ast.Set, ast.SetComp, ast.Dict, ast.DictComp))
        or (
            isinstance(node.value, ast.Call)
            and ast.unparse(node.value.func) in {"set", "dict"}
        )
    }
    for node in scope_nodes(function):
        if not isinstance(node, ast.Call) or getattr(
            node, "issuance_non_row_effect", False
        ):
            continue
        spelling = ast.unparse(node.func)
        if spelling in {"set", "dict"} or (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in memory_objects
        ):
            continue
        if spelling.split(".")[-1] in WRITE_NAMES and spelling not in {
            "bindings.update",
            "desired_ws_ids.add",
            "cache.set",
            "data.pop",
            "extra_fields.setdefault",
        }:
            yield node


def violations(functions, edges, reached):
    # Keep all transitive callers in the census, but a real ThreadPoolExecutor
    # cannot inherit the submitting thread's Django transaction/connection.
    # Its worker remains checked from ROOTS; only the submit edge is separated.
    edges = {
        key: [
            (node, target)
            for node, target in calls
            if not getattr(node, "issuance_separate_thread", False)
        ]
        for key, calls in edges.items()
    }
    same_transaction = set(ROOTS)
    while True:
        new = {
            key
            for key in reached
            if any(target in same_transaction for _, target in edges[key])
        } - same_transaction
        if not new:
            break
        same_transaction.update(new)
    reached = same_transaction
    failures = []
    incoming = {
        key: [
            (caller, node)
            for caller in reached
            for node, target in edges[caller]
            if target == key
        ]
        for key in reached
    }

    def covered(key, trail=frozenset()):
        if key in trail:
            return False
        parents = incoming[key]
        return bool(parents) and all(
            decorated(functions[parent][1])
            or dominated(node, entry_scopes(functions[parent][1]))
            or (parent in NESTED and covered(parent, trail | {key}))
            for parent, node in parents
        )

    for key in sorted(reached):
        function = functions[key][1]
        if decorated(function):
            continue
        scopes = entry_scopes(function)
        calls = [(node, target) for node, target in edges[key] if target in reached]
        if key in NESTED:
            if not covered(key):
                failures.append((key, "nested helper has an unguarded incoming caller"))
            continue
        if key in SEPARATE_TRANSACTIONS:
            if "transaction.atomic" in ast.unparse(function):
                failures.append(
                    (key, "preparation and issuance now share an atomic transaction")
                )
            continue
        if key in ROOTS or scopes:
            # No row write, row lock or transitive issuance may precede or escape
            # the entry scope. A new pre-write in an already-known entry fails.
            for node in [*writes(function), *(node for node, _ in calls)]:
                if not dominated(node, scopes):
                    failures.append((key, f"line {node.lineno} escapes entry scope"))
        elif calls:
            # Pure delegates may validate/read, then invoke a locked child. No
            # write can precede any issuance call, including a later batch item.
            last_call = max(node.lineno for node, _ in calls)
            call_nodes = {id(node) for node, _ in calls}
            for node in writes(function):
                if node.lineno <= last_call and id(node) not in call_nodes:
                    failures.append(
                        (key, f"line {node.lineno} writes before delegated issuance")
                    )
    return failures
