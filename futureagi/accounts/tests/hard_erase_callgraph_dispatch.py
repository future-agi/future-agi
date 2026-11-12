"""Exact Falcon dispatcher binding for the offline issuance census.

This is not a purity exemption: the registered closure and captured original
method both stay reachable. A change to the installer invalidates this summary.
All other issuance references retain the resolver's fail-closed handling.
"""

import ast
from hashlib import sha256

INSTALL = "ee.agenthub.traceerroragent.background_payload_wire.install_falcon_dispatch"
DISPATCH = INSTALL + ".<locals>.dispatch"
CALLER = "tracer.tasks.error_analysis._falcon_analyze_trace_in_scope"
# Bind the reviewed instance setup too: a new pre-install method replacement,
# alias escape or helper call must not be summarized as the original dispatcher.
CALLER_AST = "95826d6da0bb105b5438644425781baf248ba1015e56f9274f0eeb6aa4dd5561"
AGENT = "ee.falcon_ai.agent.AgentLoop"
ORIGINAL = AGENT + "._execute_tool"
INSTALL_AST = "9cef9018ace838b64c1011c30e354468188bc0daddbe8ac7270bb81b13b4ec4d"
MODULE = INSTALL.rsplit(".", 1)[0]
# Also binds _ACTIVE's stdlib ContextVar construction: .set is not a row write.
MODULE_AST = "084e37970940bac37107352f5316d5aa1bf8907e6c0b1d3bb3c5a6835c50eb1e"


def bind(functions, environments, exports, resolver, trees):
    """Return conservative registration edges and the exactly bound argument."""
    if INSTALL not in functions:
        return {}, set()
    function = functions[INSTALL][1]
    fingerprint = sha256(
        ast.dump(function, include_attributes=False).encode()
    ).hexdigest()
    module_fingerprint = sha256(
        ast.dump(trees[MODULE], include_attributes=False).encode()
    ).hexdigest()
    if fingerprint != INSTALL_AST or module_fingerprint != MODULE_AST:
        raise ValueError("unresolved issuance dispatcher construct in " + INSTALL)
    if not {DISPATCH, CALLER, ORIGINAL} <= functions.keys():
        raise ValueError("unresolved issuance dispatcher endpoints in " + INSTALL)
    _, caller, module, owner = functions[CALLER]
    if (
        sha256(ast.dump(caller, include_attributes=False).encode()).hexdigest()
        != CALLER_AST
    ):
        raise ValueError("unresolved issuance dispatcher caller in " + CALLER)
    bound = set()
    for node in resolver["scope_nodes"](caller):
        if (
            isinstance(node, ast.Call)
            and resolver["resolve"](
                node.func, environments[CALLER], module, owner, exports
            )
            == {INSTALL}
            and len(node.args) == 2
            and not node.keywords
            and resolver["resolve"](
                node.args[0], environments[CALLER], module, owner, exports
            )
            == {AGENT}
        ):
            bound.add(id(node.args[0]))
    if not bound:
        raise ValueError("unresolved issuance dispatcher instance in " + CALLER)
    # The pinned installer captures the original BEFORE replacing the attribute.
    # Do not infer that a reassignment removes the original issuance-capable edge.
    for key in (INSTALL, DISPATCH):
        environments[key]["agent"] = {AGENT}
        environments[key]["original"] = {ORIGINAL}
    for node in resolver["scope_nodes"](functions[DISPATCH][1]):
        if (
            isinstance(node, ast.Call)
            and ast.unparse(node) == "_ACTIVE.set((witness, call_id))"
        ):
            # Exact AST-bound ContextVar bookkeeping only. Never remove the
            # captured original(tool, decoded) call or any issuance edge.
            node.issuance_non_row_effect = True
    registration = function.body[-1]
    call = ast.copy_location(
        ast.Call(func=registration.value, args=[], keywords=[]), registration
    )
    return {INSTALL: [(call, DISPATCH)]}, bound
