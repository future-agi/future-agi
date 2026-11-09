"""Wire proof bindings, distinct from API-adjacent admission checks.

The source shape binds the installed-stack behavioral regression to each route;
statement adjacency alone is deliberately insufficient to classify a wire send.
"""

import ast

PREFIX = "ee.agenthub.traceerroragent."
HTTP_PROOF = "WitnessWire.test_httpx_wire_refusal_matrix"
BEDROCK_PROOF = "WitnessWire.test_bedrock_wire_refusal_matrix"
REDIRECT_PROOF = "WitnessWire.test_no_route_follows_307"
HEALTHY_PROOF = "WitnessWire.test_real_configs_healthy_and_original_bytes"


def wire_proof(module, owner, call, sources):
    body = ast.unparse(ast.parse(sources[module]))
    transport = ast.unparse(ast.parse(sources["tracer.services.background_transport"]))
    if "return {'event_hooks': {'request': [_sync_httpx_request]}}" not in transport:
        return None
    if module == PREFIX + "background_llm":
        proofs = [
            wire_proof(
                PREFIX + "background_provider",
                "direct_completion",
                "client.post",
                sources,
            ),
            wire_proof(
                PREFIX + "background_provider",
                "direct_completion",
                "client.invoke_model",
                sources,
            ),
            wire_proof(
                PREFIX + "background_gateway",
                "managed_completion",
                "client.post",
                sources,
            ),
            wire_proof(
                PREFIX + "background_gateway",
                "sdk_completion",
                "completions.with_raw_response.create",
                sources,
            ),
        ]
        return "; ".join(proofs) if all(proofs) else None
    if call in {"client.invoke_model", "boto3.client"}:
        helper = ast.unparse(ast.parse(sources.get(PREFIX + "background_wire", "")))
        checks = (
            "pools = _guarded_pool_classes()",
            "class HTTP(pools['http'].ConnectionCls, AWSHTTPConnection)",
            "class HTTPS(pools['https'].ConnectionCls, AWSHTTPSConnection)",
            "session._manager.pool_classes_by_scheme = private",
            "session._pool_classes_by_scheme = private",
            "manager.pool_classes_by_scheme = private",
            "client.meta.events.register('before-send.bedrock-runtime.InvokeModel', admit)",
            "def admit(**kwargs):\n        before_send()",
        )
        if "guard_bedrock(client)" in body and all(s in helper for s in checks):
            return BEDROCK_PROOF + "; " + REDIRECT_PROOF + "; " + HEALTHY_PROOF
        return None
    functions = [
        n
        for n in ast.walk(ast.parse(body))
        if isinstance(n, ast.FunctionDef) and n.name == owner
    ]
    if not functions:
        return None
    own = ast.unparse(functions[0])
    if not all(
        s in own
        for s in ("httpx.Client(", "follow_redirects=False", "**sync_client_options()")
    ):
        return None
    if owner == "sdk_completion" and not all(
        s in own
        for s in (
            "client.with_options(max_retries=0, http_client=http)",
            "getattr(scoped, '_client', None) is not http",
            "getattr(scoped, 'max_retries', None) != 0",
        )
    ):
        return None
    return HTTP_PROOF + "; " + REDIRECT_PROOF + "; " + HEALTHY_PROOF
