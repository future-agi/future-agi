"""What a Try it answer says about guardrails, read the same way for the
non-streamed test (views/gateway.py test_playground) and the streamed one
(services/playground_stream.py), so the two never disagree."""


def playground_outcome(status_code, body, headers):
    """blocked: a 403 whose error.type is guardrail_error (a block). warned: a
    2xx with x-agentcc-guardrail-triggered: true (a check warned or logged
    and let the request through). The action and the check names come only
    from x-agentcc-guardrail-action and x-agentcc-guardrails-triggered; a
    gateway that does not send them gives no action and no names."""
    lowered = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    error = body.get("error") if isinstance(body, dict) else None
    error_type = error.get("type") if isinstance(error, dict) else None
    triggered = (
        lowered.get("x-agentcc-guardrail-triggered", "").strip().lower() == "true"
    )
    warned = 200 <= status_code < 300 and triggered
    action = lowered.get("x-agentcc-guardrail-action", "").strip().lower()
    names = lowered.get("x-agentcc-guardrails-triggered", "")
    return {
        "blocked": status_code == 403 and error_type == "guardrail_error",
        "warned": warned,
        "guardrail_action": action if warned and action in ("warn", "log") else None,
        "triggered_checks": (
            sorted({n.strip() for n in names.split(",") if n.strip()}) if warned else []
        ),
    }
