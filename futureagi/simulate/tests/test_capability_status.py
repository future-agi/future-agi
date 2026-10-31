"""Capability status: decided at read time from stored evidence and the current versions."""

from simulate.services.capability_status import capability_statuses

_CATALOGUE = [
    {"id": "start_health", "label": "Start and health", "depends_on": ["source"], "fix_type": "startup"},
    {"id": "one_turn", "label": "One turn", "depends_on": ["start_health"], "fix_type": "message_mapping"},
    {"id": "tool_routing", "label": "Tool routing", "depends_on": ["environment"], "fix_type": "tool_routing"},
    {"id": "world_reset", "label": "World reset", "depends_on": ["environment"], "fix_type": "evidence"},
    {"id": "streaming", "label": "Streaming", "depends_on": ["one_turn"], "fix_type": "message_mapping"},
]
_INPUTS = {"source_digest": "s1", "bundle_digest": "b1"}


def _evidence(**findings):
    return {
        "inputs": dict(_INPUTS),
        "capabilities": {key: {"finding": value, "checks": []} for key, value in findings.items()},
    }


def _statuses(evidence, current=_INPUTS):
    return {row["id"]: row["status"] for row in capability_statuses(_CATALOGUE, evidence, current)}


def test_each_finding_reads_as_its_status_and_unchecked_ones_are_untested():
    assert _statuses(
        _evidence(start_health="passed", one_turn="passed", tool_routing="passed_with_limits", world_reset="failed")
    ) == {
        "start_health": "supported",
        "one_turn": "supported",
        "tool_routing": "partial",
        "world_reset": "blocked",
        "streaming": "untested",
    }


def test_no_evidence_is_untested_never_supported():
    assert set(_statuses(None).values()) == {"untested"}


def test_changed_inputs_make_every_capability_stale_with_what_changed():
    rows = capability_statuses(
        _CATALOGUE, _evidence(start_health="passed"), {"source_digest": "s2", "bundle_digest": "b1"}
    )
    assert {row["status"] for row in rows} == {"stale"}
    assert rows[0]["stale_because"] == "source_digest"


def test_a_capability_behind_a_closed_gate_is_untested_and_names_the_gate():
    rows = capability_statuses(_CATALOGUE, _evidence(start_health="failed", one_turn="passed"), _INPUTS)
    one_turn = next(row for row in rows if row["id"] == "one_turn")
    assert (one_turn["status"], one_turn["blocked_by"]) == ("untested", "start_health")


def test_affected_counts_come_from_what_each_scenario_needs():
    from simulate.services.capability_evidence import affected_scenarios

    rows = capability_statuses(
        _CATALOGUE, _evidence(start_health="passed", one_turn="passed", tool_routing="failed", world_reset="passed"), _INPUTS
    )
    requirements = {
        "refund": {"capabilities": ["start_health", "one_turn", "tool_routing"]},
        "greeting": {"capabilities": ["start_health", "one_turn"]},
        "stream": {"capabilities": ["start_health", "streaming"]},
    }
    counts = affected_scenarios(rows, requirements)
    by_id = {row["id"]: (row["needed_by"], row["affected"]) for row in rows}
    assert counts == {"requested": 3, "eligible": 1, "blocked": 1, "untested": 1, "requirements_recorded": True}
    assert by_id["tool_routing"] == (1, 1)
    assert by_id["start_health"] == (3, 0)
    assert by_id["world_reset"] == (0, 0)


def test_a_partial_capability_holds_only_for_scenarios_that_avoid_what_failed():
    from simulate.services.capability_evidence import affected_scenarios

    def rows(checks):
        evidence = _evidence(start_health="passed", one_turn="passed")
        evidence["capabilities"]["tool_routing"] = {"finding": "passed_with_limits", "checks": checks}
        return capability_statuses(_CATALOGUE, evidence, _INPUTS)

    def needs(*tools):
        return {"capabilities": ["start_health", "one_turn", "tool_routing"], "tools": {"required": list(tools)}}

    requirements = {"refund": needs("lookup", "refund"), "lookup": needs("lookup")}
    named = rows([{"id": "tool_contract", "result": "1/2", "failed": ["refund"]}, {"id": "action_behavior", "result": "2/2"}])
    assert affected_scenarios(named, requirements) == {
        "requested": 2, "eligible": 1, "blocked": 1, "untested": 0, "requirements_recorded": True
    }
    assert next(row for row in named if row["id"] == "tool_routing")["affected"] == 1

    unnamed = rows([{"id": "tool_contract", "result": "1/2"}])
    assert affected_scenarios(unnamed, requirements)["untested"] == 2
    assert affected_scenarios(unnamed, {})["requirements_recorded"] is False


def test_a_production_only_refund_tool_blocks_only_the_cases_that_need_it_with_the_reason_and_fix():
    """The done-when, from a fixture evidence row until TH-8872's production-only detection lands."""
    from simulate.services.capability_evidence import affected_scenarios

    evidence = _evidence(start_health="passed", one_turn="passed", world_reset="passed")
    evidence["capabilities"]["tool_routing"] = {
        "finding": "failed",
        "reason_code": "production_only",
        "reason": "refund reaches a fixed production URL",
        "fix": "Point refund at the world's tool service, or mark it unavailable",
        "checks": [{"target": "refund", "result": "failed"}],
    }
    rows = capability_statuses(_CATALOGUE, evidence, _INPUTS)
    routing = next(row for row in rows if row["id"] == "tool_routing")
    assert (routing["status"], routing["reason_code"], routing["fix"]) == (
        "blocked", "production_only", "Point refund at the world's tool service, or mark it unavailable")
    assert next(row for row in rows if row["id"] == "world_reset")["reason"] is None

    needs = {"capabilities": ["start_health", "one_turn", "tool_routing"]}
    requirements = {
        **{f"refund_{n}": {**needs, "tools": {"required": ["refund", "lookup"]}} for n in range(4)},
        **{f"status_{n}": {**needs, "tools": {"required": ["lookup"]}} for n in range(6)},
    }
    counts = affected_scenarios(rows, requirements)
    assert (counts["blocked"], counts["eligible"]) == (4, 6)
    assert (routing["needed_by"], routing["affected"]) == (10, 4)
    assert routing["affected_scenarios"] == ["refund_0", "refund_1", "refund_2", "refund_3"]

    # After the tool is redirected and re-checked, nothing is affected.
    evidence["capabilities"]["tool_routing"] = {"finding": "passed", "checks": []}
    again = capability_statuses(_CATALOGUE, evidence, _INPUTS)
    assert affected_scenarios(again, requirements)["blocked"] == 0


def test_an_unreachable_tool_in_the_current_contract_blocks_routing_over_older_or_stale_evidence():
    from simulate.services.capability_evidence import structural_findings

    contract = {"tool_entrypoints": [
        {"tool": "refund", "mode": "unreachable"},
        {"tool": "lookup", "mode": "service", "service": "tools-api"},
        {"tool": "notify", "mode": "service", "service": ""},
    ]}
    structural = structural_findings(contract)
    assert structural["tool_routing"]["checks"] == [
        {"target": "notify", "result": "failed"}, {"target": "refund", "result": "failed"}]
    evidence = _evidence(start_health="passed", one_turn="passed", tool_routing="passed", world_reset="passed")
    for inputs in (_INPUTS, {**_INPUTS, "source_digest": "s2"}):
        routing = next(row for row in capability_statuses(_CATALOGUE, evidence, inputs, structural)
                       if row["id"] == "tool_routing")
        assert (routing["status"], routing["reason_code"]) == ("blocked", "tool_unreachable")
        assert "notify, refund" in routing["reason"]
    assert structural_findings({"tool_entrypoints": [{"tool": "lookup", "mode": "import"}]}) == {}
    assert structural_findings(None) == {}
