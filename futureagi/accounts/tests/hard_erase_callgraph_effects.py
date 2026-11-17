"""Conservative write/row-lock effects for the issuance completeness gate.

No flow-sensitive alias inference: assignments union effects, and same-module
helpers propagate effects to a fixed point. Unsupported effect-bearing references
fail the gate rather than silently removing a write from dominance checking.
"""

import ast
import runpy
from pathlib import Path

CLASSIFICATIONS = runpy.run_path(
    str(Path(__file__).with_name("hard_erase_callgraph_classifications.py"))
)


def propagate(
    functions, edges, reached, environments, exports, resolver, writes, names
):
    scope_nodes, resolve = resolver["scope_nodes"], resolver["resolve"]
    # Qualify the issuance component, not unrelated application functions. Follow
    # references as well as calls so an unresolved callback cannot hide a helper.
    relevant, pending, nodes_by_key = set(reached), list(reached), {}
    while pending:
        key = pending.pop()
        _, function, module, owner = functions[key]
        nodes = nodes_by_key.setdefault(key, list(scope_nodes(function)))
        targets = {target for _, target in edges[key]}
        for node in nodes:
            if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(
                node.ctx, ast.Load
            ):
                targets.update(resolve(node, environments[key], module, owner, exports))
        new = {
            target
            for target in targets
            if target in functions and functions[target][2] == module
        } - relevant
        relevant.update(new)
        pending.extend(new)
    references, direct, memory = {}, {}, {}
    for key in relevant:
        _, function, module, owner = functions[key]
        nodes = nodes_by_key[key]
        CLASSIFICATIONS["qualify"](key, function, functions, nodes)
        parents = {
            id(child): node for node in nodes for child in ast.iter_child_nodes(node)
        }
        memory[key] = {
            target.id
            for node in nodes
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
            if isinstance(node.value, (ast.Set, ast.SetComp, ast.Dict, ast.DictComp))
            or (
                isinstance(node.value, ast.Call)
                and ast.unparse(node.value.func) in {"set", "dict"}
            )
        }
        direct[key] = list(writes(function))
        references[key] = []
        for node in nodes:
            if not isinstance(node, (ast.Name, ast.Attribute)) or not isinstance(
                node.ctx, ast.Load
            ):
                continue
            if getattr(parents.get(id(node)), "issuance_non_row_effect", False):
                continue
            spelling = ast.unparse(node)
            targets = resolve(node, environments[key], module, owner, exports)
            # The same explicit non-row operations as writes(); no inferred purity.
            if spelling in {
                "set",
                "dict",
                "bindings.update",
                "desired_ws_ids.add",
                "cache.set",
                "data.pop",
                "extra_fields.setdefault",
            }:
                continue
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in memory[key]
            ):
                continue
            if any(
                target.rsplit(".", 1)[-1] in names
                or target.rsplit(".", 1)[-1].startswith("bulk_")
                for target in targets
            ):
                references[key].append(node)
        parents = {
            id(child): node for node in nodes for child in ast.iter_child_nodes(node)
        }
        for node in references[key]:
            parent = parents.get(id(node))
            if isinstance(parent, ast.Call) and parent.func is node:
                direct[key].append(parent)

    # Propagate even references not called directly: a helper with unresolved
    # callback dispatch must remain effect-capable and be checked if reachable.
    capable = {key for key in relevant if direct[key] or references[key]}
    while True:
        new = {
            key
            for key in relevant
            if any(
                target in capable
                and functions[target][2] == functions[key][2]
                and not getattr(node, "issuance_non_row_effect", False)
                for node, target in edges[key]
            )
        } - capable
        if not new:
            break
        capable.update(new)
    for key in relevant:
        _, function, module, _ = functions[key]
        function.issuance_effect_calls = [
            *direct[key],
            *(
                node
                for node, target in edges[key]
                if target in capable
                and functions[target][2] == module
                and not getattr(node, "issuance_non_row_effect", False)
            ),
        ]

    unresolved = []
    for key in sorted(relevant):
        _, function, module, owner = functions[key]
        nodes = nodes_by_key[key]
        parents = {
            id(child): node for node in nodes for child in ast.iter_child_nodes(node)
        }
        resolved = {id(node.func) for node, target in edges[key] if target in capable}
        for node, target in edges[key]:
            if (
                target in capable
                and node.args
                and resolve(node.func, environments[key], module, owner, exports)
                & resolver["CALLBACK_DISPATCH"]
            ):
                resolved.add(id(node.args[0]))
        for node in nodes:
            if not isinstance(node, (ast.Name, ast.Attribute)) or not isinstance(
                node.ctx, ast.Load
            ):
                continue
            parent = parents.get(id(node))
            if isinstance(parent, ast.Attribute) and parent.value is node:
                continue
            targets = resolve(node, environments[key], module, owner, exports)
            is_direct = any(node is ref for ref in references[key])
            if not is_direct and not targets & capable:
                continue
            if id(node) in resolved:
                continue
            if isinstance(parent, ast.Call):
                if resolve(parent.func, environments[key], module, owner, exports) == {
                    "inspect.signature"
                }:
                    continue  # Signature inspection never invokes the callable.
                if (
                    isinstance(parent.func, ast.Name)
                    and parent.func.id == "callable"
                    and "callable" not in environments[key]
                    and "callable" not in function.issuance_parameters
                    and module + ".callable" not in functions
                ):
                    continue  # Builtin callable() inspects, rather than invokes.
                if (
                    resolve(parent, environments[key], module, owner, exports)
                    == targets
                ):
                    continue  # The resolver's exact _original_func/default shim.
            ancestor = parent
            while ancestor is not None and not isinstance(ancestor, ast.Call):
                ancestor = parents.get(id(ancestor))
            if isinstance(ancestor, ast.Call) and resolve(
                ancestor.func, environments[key], module, owner, exports
            ) == {"tfc.temporal.common.registry.register_for_queues"}:
                continue  # Registration, not execution; activities remain roots.
            if isinstance(parent, ast.Call) and parent.func is node and is_direct:
                continue
            if isinstance(parent, (ast.Assign, ast.AnnAssign)) and parent.value is node:
                assigned = (
                    parent.targets
                    if isinstance(parent, ast.Assign)
                    else [parent.target]
                )
                if all(
                    isinstance(target, (ast.Name, ast.Attribute)) for target in assigned
                ):
                    continue
            unresolved.append(
                f"unresolved write reference in {key}:{node.lineno}: {ast.unparse(node)}"
            )
    if unresolved:
        raise ValueError("\n".join(unresolved))
