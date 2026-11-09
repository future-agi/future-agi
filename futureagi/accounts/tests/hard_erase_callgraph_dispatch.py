"""Exact Falcon dispatcher binding for the offline issuance census.

This is not a purity exemption: the registered closure and captured original
method both stay reachable. A change to the installer invalidates this summary.
All other issuance references retain the resolver's fail-closed handling.
"""

import ast
import runpy
from pathlib import Path

fingerprint = runpy.run_path(
    str(Path(__file__).with_name("hard_erase_ast_fingerprint.py"))
)["fingerprint"]

INSTALL = "ee.agenthub.traceerroragent.background_payload_wire.install_falcon_dispatch"
DISPATCH = INSTALL + ".<locals>.dispatch"
CALLER = "tracer.tasks.error_analysis._falcon_analyze_trace_in_scope"
# Bind the reviewed instance setup too: a new pre-install method replacement,
# alias escape or helper call must not be summarized as the original dispatcher.
CALLER_AST = "9e1e515bdf993d95ab76f89b58cfd9de430071a3e7c614607e05bf52f6776bd2"
AGENT = "ee.falcon_ai.agent.AgentLoop"
ORIGINAL = AGENT + "._execute_tool"
# background_payload_wire.py:160-185: capture original, then register dispatch.
INSTALL_AST = "4f85c577114a4819ec8e11a40bafcf448d27dcefe15bd4d955eb4f8e971f2479"
MODULE = INSTALL.rsplit(".", 1)[0]
# Also binds _ACTIVE's stdlib ContextVar construction: .set is not a row write.
MODULE_AST = "fe6444f87e69ea4d0d2d77ee2a11afe458aeffe2791c004bb70555cfaedaf935"


def bind(functions, environments, exports, resolver, trees):
    """Return conservative registration edges and the exactly bound argument."""
    if INSTALL not in functions:
        return {}, set()
    function = functions[INSTALL][1]
    if fingerprint(function) != INSTALL_AST or fingerprint(trees[MODULE]) != MODULE_AST:
        raise ValueError("unresolved issuance dispatcher construct in " + INSTALL)
    if not {DISPATCH, CALLER, ORIGINAL} <= functions.keys():
        raise ValueError("unresolved issuance dispatcher endpoints in " + INSTALL)
    _, caller, module, owner = functions[CALLER]
    if fingerprint(caller) != CALLER_AST:
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
