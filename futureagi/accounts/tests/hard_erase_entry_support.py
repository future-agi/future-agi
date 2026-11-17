"""Offline execution of actual entry planners, with database dependencies doubled."""

import ast
from pathlib import Path
from types import SimpleNamespace

SERVICES = Path(__file__).resolve().parents[1] / "services"


def entry_namespace(lifecycle, *, existing=None, live=None):
    class Imports(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            if node.module.startswith(("accounts.", "django.", "ai_tools.")):
                return ast.Pass()
            return node

    def exists(model, value, using):
        if value is not None and live is not None and value not in live:
            lifecycle["_refuse"]("erased identity")

    namespace = {
        "identity_keys": lifecycle["identity_keys"],
        "lifecycle_lock": lifecycle["lifecycle_lock"],
        "_refuse": lifecycle["_refuse"],
        "_exists": exists,
        "User": SimpleNamespace(),
        "Organization": SimpleNamespace(),
        "resolve_org": lambda request: request.organization,
    }
    path = SERVICES / "hard_erase_issuance_entry.py"
    tree = ast.fix_missing_locations(Imports().visit(ast.parse(path.read_text())))
    exec(compile(tree, str(path), "exec"), namespace)
    namespace["_target_exists"] = lambda value, using: live is None or value in live
    namespace["_email_people"] = lambda emails, using: {
        email: pk for email, pk in (existing or {}).items() if email in emails
    }
    return namespace
