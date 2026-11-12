"""Whole-agentcc AST custody discovery; never imports or executes target modules.

Every production decrypt site is classified, including non-connector custody.
Tests are discovered too, then excluded explicitly from the runtime allowlist.
Counts are intentional: an extra branch in an existing symbol is a new site.
This census is not a control-flow/dominance proof; runtime row/secret spies are
required alongside the typed-boundary assertions.
"""
import ast
from collections import Counter


# (module, qualified symbol): (number of decrypts, classification/rationale)
CLASSIFIED = {
    ("services/connector_oauth_clients.py", "_org_secret"): (1, "typed identity guard"),
    ("services/mcp_credentials.py", "_decrypted"): (1, "typed OAuth identity guard"),
    ("services/mcp_revocation.py", "revoke"): (1, "typed identity guard"),
    ("services/mcp_upstream_oauth.py", "_exchange_code"): (1, "typed identity guard"),
    ("services/mcp_upstream_tokens.py", "_refresh"): (1, "typed identity guard"),
    ("services/mcp_upstream_tokens.py", "_served"): (1, "protected; exact row guarded by locked public selection; private direct-call P3 remains"),
    ("services/mcp_vendor_credentials.py", "vendor_client"): (1, "Microsoft partner provenance refusal before decrypt"),
    ("services/mcp_vendor_credentials.py", "_grant"): (2, "Composio sessions after vendor_client"),
    ("services/mcp_vendor_connections.py", "start"): (1, "Composio session after vendor_client"),
    ("services/mcp_vendor_connections.py", "_revoke_one"): (2, "Composio sessions after vendor_client"),
    ("services/mcp_vendor_pipedream.py", "Pipedream.bearer"): (1, "partner bearer; producer vendor_client; non-Microsoft alias collision P3"),
    ("mcp_oauth/keys.py", "sign"): (1, "downstream JWT signing key, not connector OAuth"),
    ("services/managed_upstream_keys.py", "_decrypted_key"): (1, "managed model-provider API key, not connector OAuth"),
    ("services/siem_signing.py", "headers"): (3, "SIEM sink authentication, not connector OAuth"),
}


class _Calls(ast.NodeVisitor):
    def __init__(self, aliases):
        self.aliases = aliases
        self.scope = []
        self.sites = []

    def _definition(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _definition
    visit_AsyncFunctionDef = _definition
    visit_ClassDef = _definition

    def visit_Call(self, node):
        callee = node.func
        if (isinstance(callee, ast.Name) and callee.id in self.aliases
                or isinstance(callee, ast.Attribute) and callee.attr == "decrypt_token"):
            self.sites.append((".".join(self.scope) or "<module>", node.lineno))
        self.generic_visit(node)


def discover(root):
    """Enumerate call sites in all Python modules, including nested packages."""
    sites = []
    for path in sorted(root.rglob("*.py")):
        # Do not follow external trees via links while inspecting source.
        if path.is_symlink() or any((root / p).is_symlink() for p in path.relative_to(root).parents):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = {"decrypt_token"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                aliases.update(item.asname or item.name for item in node.names
                               if item.name == "decrypt_token")
        calls = _Calls(aliases)
        calls.visit(tree)
        sites.extend((path.relative_to(root).as_posix(), symbol, line)
                     for symbol, line in calls.sites)
    return sites


def unclassified(sites):
    """Unknown runtime symbols and unexpected counts cannot silently pass."""
    runtime = [site for site in sites if not site[0].startswith("tests/")]
    counts = Counter((path, symbol) for path, symbol, _ in runtime)
    return [site for site in runtime
            if counts[site[:2]] != CLASSIFIED.get(site[:2], (0, "unknown"))[0]]
