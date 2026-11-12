"""Pin the 708J checkout-redaction and MCP launch-gate middleware order."""

import ast
from pathlib import Path


def test_checkout_outermost_and_mcp_subfeature_before_security():
    """Checkout wraps MCP refusals, and the MCP gate precedes security."""
    source = Path(__file__).resolve().parents[1] / "settings" / "settings.py"
    module = ast.parse(source.read_text(encoding="utf-8"))
    assignments = [
        node
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "MIDDLEWARE"
            for target in node.targets
        )
    ]
    assert len(assignments) == 1, "One authoritative initial MIDDLEWARE list"
    middleware = ast.literal_eval(assignments[0].value)
    checkout = "tfc.logging.checkout.CheckoutLoggingMiddleware"
    mcp = "agentcc.mcp_features.MCPSubfeatureMiddleware"
    security = "django.middleware.security.SecurityMiddleware"
    assert middleware[0] == checkout, "Checkout must be outermost"
    assert mcp in middleware, "MCPSubfeatureMiddleware must be installed"
    assert middleware[1] == mcp, "MCPSubfeatureMiddleware must be second"
    assert middleware.index(mcp) < middleware.index(security)
    assert middleware.count(checkout) == middleware.count(mcp) == 1
