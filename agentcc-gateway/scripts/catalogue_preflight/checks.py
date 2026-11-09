"""Pure receipt assertions. No imports of product, network, or launcher code."""
import hashlib
import json
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def credential_fingerprint(value):
    return hashlib.sha256(("Bearer " + value).encode()).hexdigest()


def evaluate(case, status, body, before, after, first, last, credential):
    """Keep product expectations identical in green AND assertion-red runs."""
    checks = {}
    delta = last["probe_calls"] - first["probe_calls"]
    lookups = last["resolver_calls"] - first["resolver_calls"]
    queries = last["dns_queries"] - first["dns_queries"]
    seen = last["tools_list"][len(first["tools_list"]):]
    tls = last["tls"][len(first["tls"]):]
    hellos = last["client_hello"][len(first["client_hello"]):]
    checks["tls_handshake_reached"] = (bool(hellos) and all(name == last["name"] for name in hellos)) if case == "positive" or case.startswith("tls-") else not hellos
    checks["registration_unchanged"] = last["seal"] == first["seal"] and bool(last["seal"])
    checks["dns_fixture_valid"] = last["dns_failures"] == 0
    checks["real_probe_route"] = delta == (0 if case in ("member", "localhost") else 1)
    checks["resolver_and_stub_reached"] = (lookups > 0 and queries == lookups) if delta else (lookups == queries == 0)
    checks["synthetic_physical_not_reached"] = last["synthetic_physical_attempts"] == 0
    if case == "positive":
        result = body.get("result", {})
        baseline = result.get("baseline", {})
        expected = credential_fingerprint(credential)
        checks["actual_201"] = status == 201
        tools_hash = baseline.get("tools_hash")
        checks["five_tools"] = baseline.get("tool_count") == 5 and isinstance(tools_hash, str) and re.fullmatch(r"[0-9a-f]{64}", tools_hash) is not None
        checks["real_mock_credential"] = any(row.get("auth", {}).get("sha256") == expected and row.get("method") == "tools/list" and not row.get("rejected") for row in seen)
        checks["original_tls_identity"] = bool(tls) and all(row["sni"] == last["name"] for row in tls)
        checks["stored_and_synced"] = result.get("gateway_synced") is True and result.get("credential", {}).get("connected") is True and result.get("mcp_enabled") is True
        checks["foreign_state_unchanged"] = before["foreign"] == after["foreign"]
        checks["own_state_changed"] = before["own"] != after["own"]
    else:
        expected_status, code, cause = 502, "connector_tool_listing_failed", "egress_refused"
        if case.startswith("tls-"):
            cause = "unreachable"
        if case == "member":
            expected_status, code, cause = 403, None, None
        if case == "localhost":
            expected_status, code, cause = 400, "url_variable_invalid", None
        checks["product_refusal"] = status == expected_status and (code is None or body.get("code") == code) and (cause is None or cause in body.get("message", ""))
        checks["no_write"] = before == after
        checks["no_mock_tools_list"] = not seen
        checks["no_successful_tls_request"] = not tls
    checks["no_credential_in_response"] = credential not in json.dumps(body)
    return [name for name, passed in checks.items() if not passed]


def assertion_red(green, red, restored):
    """A fixture error/skip/compiler failure is not the demanded assertion-red."""
    assert green["failures"] == restored["failures"] == []
    assert green["case"] == red["case"] == restored["case"] == "synthetic-metadata"
    assert green["intent"] == red["intent"] == restored["intent"]
    assert "product_refusal" in red["failures"]
    assert set(red["failures"]) <= {"product_refusal", "no_write", "no_mock_tools_list", "no_successful_tls_request", "synthetic_physical_not_reached", "tls_handshake_reached"}
    assert red["status"] == 201
    assert any(row.get("auth", {}).get("sha256") == red["credential_sha256"] and row.get("method") == "tools/list" and not row.get("rejected") for row in red["last"]["tools_list"])
    assert red["last"]["synthetic_physical_attempts"] > 0
    assert red["last"]["tools_list"]
    assert red["last"]["resolver_calls"] > 0
    assert all(row["sni"] == "metadata.google.internal" for row in red["last"]["tls"])
    assert red["last"]["tls"]
    assert red["last"]["client_hello"] and all(name == "metadata.google.internal" for name in red["last"]["client_hello"])
    assert green["last"]["synthetic_physical_attempts"] == restored["last"]["synthetic_physical_attempts"] == 0
    for receipt in (green, red, restored):
        assert receipt["ordinary_nonloopback_attempts"] == 0
        assert receipt["native_reconciliation"]["accepted"] is True
        assert receipt["native_reconciliation"]["dropped_events"] == 0
    return {"assertion_red": True, "physical_routing": "synthetic owned loopback only", "productAccepted": False}
