"""Pin the 708J checkout-redaction and MCP launch-gate middleware order."""

import ast
from pathlib import Path


def test_checkout_and_mcp_gate_follow_schema_guard_and_precede_logging_auth_and_redirects():
    """The startup schema guard answers first; checkout then wraps MCP refusals and
    every request-logging/authentication middleware, and the MCP gate hides
    routes before authentication, body validation, redirects or session I/O."""
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
    guard = "tfc.middleware.schema_readiness.SchemaReadinessMiddleware"
    checkout = "tfc.logging.checkout.CheckoutLoggingMiddleware"
    mcp = "agentcc.mcp_features.MCPSubfeatureMiddleware"
    assert mcp in middleware, "MCPSubfeatureMiddleware must be installed"
    assert (
        middleware.count(guard)
        == middleware.count(checkout)
        == middleware.count(mcp)
        == 1
    )

    # Nothing of ours runs before a not-ready refusal; the guard's own test pins
    # what comes before it.
    assert middleware.index(checkout) == middleware.index(guard) + 1, (
        "Checkout directly after the schema guard"
    )
    assert middleware.index(mcp) == middleware.index(checkout) + 1, (
        "Checkout wraps MCP refusals"
    )

    # Request logging goes through CallbackRequestMiddleware, which drops the query
    # on /saml2_auth/ (OAuth code, state, error); the stock django_structlog
    # RequestMiddleware it subclasses would log it, so it is never installed itself.
    request_logging = "tfc.logging.middleware.CallbackRequestMiddleware"
    assert middleware.count(request_logging) == 1, "One request-logging middleware"
    assert "django_structlog.middlewares.RequestMiddleware" not in middleware, (
        "The stock RequestMiddleware would log OAuth callback queries"
    )

    # Checkout scopes redaction outside all request-logging/authentication middleware.
    for later in (
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "tfc.telemetry.middleware.OTelContextMiddleware",
        request_logging,
        "tfc.middleware.auth_monitoring.AuthMonitoringMiddleware",
        "tfc.utils.audit.AuditMiddleware",
        "agentcc.middleware_ee_agent_key.EEAgentKeyScopeMiddleware",
        "accounts.authentication.AuthMonitoringMiddleware",
    ):
        assert middleware.index(checkout) < middleware.index(later), (
            f"Checkout must precede {later}"
        )

    # The MCP gate hides routes before authentication, body validation, redirects or I/O:
    # sessions (I/O), CommonMiddleware (slash redirects), CSRF (body) and authentication.
    for later in (
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.common.CommonMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
    ):
        assert middleware.index(mcp) < middleware.index(later), (
            f"MCP gate must precede {later}"
        )
