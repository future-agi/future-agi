"""Synthetic, offline runner/grader fixtures; no external models or GitHub."""

import json
from uuid import uuid4

import pytest

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import file_sha256, manifest_digest
from model_hub.services.skills_hub.v11.grading import (
    file_coverage,
    grade_deterministic,
    pointer_value,
    reduce_results,
    strict_equal,
    validate_judge_output,
    validate_schema,
)
from model_hub.services.skills_hub.v11.inventory import (
    bind_findings,
    build_inventory,
    validate_inventory_review,
)
from model_hub.services.skills_hub.v11.runner import (
    build_task_messages,
    process_tool,
    read_snapshot_chunk,
    variant_bundles,
)


def check(kind, config):
    return {"id": str(uuid4()), "kind": kind, "critical": False, "config": config}


def case(checks=None):
    return {
        "id": str(uuid4()),
        "title": "Boundary request",
        "source_group_id": "synthetic-policy",
        "prompt": "Return the approved refund amount.",
        "fixtures": [],
        "expected_trigger": True,
        "checks": checks or [],
        "reviewed": True,
    }


def bundle(skill_id=None, root="root instructions", extra=None):
    files = [{"path": "SKILL.md", "content": root}, *(extra or [])]
    return {
        "skill_id": skill_id or str(uuid4()),
        "version_id": str(uuid4()),
        "version": 1,
        "draft_revision": None,
        "digest": manifest_digest(files),
        "execution_manifest_digest": manifest_digest(files),
        "name": "policy",
        "description": "Use for refund decisions.",
        "files": [
            {
                **item,
                "sha256": file_sha256(item["content"]),
                "size_bytes": len(item["content"].encode()),
            }
            for item in files
        ],
    }


def receipt(path="policy.md", start=0, end=None, total=5, size=5):
    return {
        "path": path,
        "offset": start,
        "next_offset": end,
        "total_chars": total,
        "outcome": "delivered",
        "delivered_bytes": size,
    }


@pytest.mark.parametrize(
    "root,offset,maximum,text,next_offset",
    [
        ("aé界z", 0, 3, "aé", 2),
        ("aé界z", 2, 3, "界", 3),
        ("aé界z", 3, 4, "z", None),
        ("", 0, 2, "", None),
    ],
)
def test_chunk_character_offsets(root, offset, maximum, text, next_offset):
    item = bundle(root=root)
    result = read_snapshot_chunk(
        snapshot={"opaque": item},
        skill_handle="opaque",
        path="SKILL.md",
        offset=offset,
        max_bytes=maximum,
    )
    assert result["text"] == text
    assert result["next_offset"] == next_offset
    assert result["sha256"] == file_sha256(root)


@pytest.mark.parametrize(
    "offset,maximum", [(True, 20), (-1, 20), (0, 0), (0, 49153), (100, 20), (0, 1)]
)
def test_invalid_chunks_rejected(offset, maximum):
    with pytest.raises(SkillsHubError):
        read_snapshot_chunk(
            snapshot={"x": bundle(root="界")},
            skill_handle="x",
            path="SKILL.md",
            offset=offset,
            max_bytes=maximum,
        )


@pytest.mark.parametrize(
    "receipts,expected",
    [
        ([receipt(start=3)], (True, False)),
        ([receipt(end=3), receipt(start=2)], (True, True)),
        ([receipt(end=2), receipt(start=3)], (True, False)),
        ([receipt(total=0, size=0)], (True, True)),
        ([], (False, False)),
    ],
)
def test_interval_union_not_eof(receipts, expected):
    assert file_coverage(receipts, "policy.md") == expected


def test_no_target_has_no_access_and_competitors_remain():
    target, rival = bundle(), bundle()
    snapshot = {
        "candidate": target,
        "production": {**target, "description": "Older description"},
        "competitors": [rival],
        "plan_fingerprint": "stable",
    }
    item = case(
        [check("contains", {"text": "HIDDEN_EXPECTATION", "case_sensitive": True})]
    )
    messages, catalog = build_task_messages(
        snapshot=snapshot, case=item, variant="without_target"
    )
    assert (
        len(catalog) == 1
        and next(iter(catalog.values()))["skill_id"] == rival["skill_id"]
    )
    assert "HIDDEN_EXPECTATION" not in json.dumps(messages)
    assert item["id"] not in json.dumps(messages)
    assert "expected_trigger" not in json.dumps(messages)
    candidate = variant_bundles(snapshot=snapshot, variant="candidate")
    production = variant_bundles(snapshot=snapshot, variant="production")
    assert list(candidate) == list(production)
    snapshot["competitors"] = [target]
    with pytest.raises(SkillsHubError, match="competitor"):
        variant_bundles(snapshot=snapshot, variant="without_target")


def test_tools_require_activation_and_exact_paths():
    item = bundle(extra=[{"path": "empty.txt", "content": ""}])
    active = set()
    read = {
        "id": "t1",
        "function": {
            "name": "read_skill_file",
            "arguments": json.dumps(
                {"skill_id": "x", "path": "empty.txt", "offset": 0, "max_bytes": 100}
            ),
        },
    }
    output, record, _ = process_tool(
        call=read,
        call_id="c",
        bundles={"x": item},
        activated=active,
        target_id=item["skill_id"],
        remaining_bytes=100,
    )
    assert (
        output["error"]["code"] == "root_not_loaded" and record["outcome"] == "rejected"
    )
    load = {
        "id": "t2",
        "function": {"name": "load_skill", "arguments": '{"skill_id":"x"}'},
    }
    output, _, requested = process_tool(
        call=load,
        call_id="c",
        bundles={"x": item},
        activated=active,
        target_id=item["skill_id"],
        remaining_bytes=100,
    )
    assert requested and active == {"x"} and output["text"] == "root instructions"
    output, record, _ = process_tool(
        call=read,
        call_id="c",
        bundles={"x": item},
        activated=active,
        target_id=item["skill_id"],
        remaining_bytes=100,
    )
    assert output["eof"] and record["delivered_bytes"] == 0
    read["function"]["arguments"] = (
        '{"skill_id":"x","path":"../secret","offset":0,"max_bytes":100}'
    )
    output, record, _ = process_tool(
        call=read,
        call_id="c",
        bundles={"x": item},
        activated=active,
        target_id=item["skill_id"],
        remaining_bytes=100,
    )
    assert output["error"]["code"] == "resource_not_found" and record["path"] == ""


def test_mandatory_equal_refund_json_no_manufactured_uplift():
    item = case(
        [
            check("json_equals", {"pointer": "/refund", "value": 20}),
            check("file_delivered", {"path": "policy.md", "complete": True}),
        ]
    )
    candidate = grade_deterministic(
        case=item, answer='{"refund":20}', receipts=[receipt()], variant="candidate"
    )
    without = grade_deterministic(
        case=item, answer='{"refund":20}', receipts=[], variant="without_target"
    )
    assert candidate[0]["verdict"] == without[0]["verdict"] == "pass"
    assert (
        candidate[1]["verdict"] == "pass" and without[1]["verdict"] == "not_applicable"
    )
    rows = [_attempt(item, "candidate"), _attempt(item, "without_target")]
    result = reduce_results(
        cases=[item], attempts=rows, baselines=["without_target"], repetitions=1
    )
    assert result["answer_comparisons"][0]["delta_percentage_points"] == 0
    assert result["decision"]["improved_cases"] == 0


def _attempt(item, variant, passed=True, repeat=0):
    return {
        "id": str(uuid4()),
        "case_id": item["id"],
        "variant": variant,
        "repeat_index": repeat,
        "status": "completed",
        "answer": "answer",
        "answer_verdict": "pass" if passed else "fail",
        "delivery_verdict": "not_applicable" if variant == "without_target" else "pass",
        "routing_verdict": "not_applicable" if variant == "without_target" else "pass",
        "target_loaded": variant != "without_target",
    }


def test_mixed_two_baselines_is_regression_and_missing_is_unmatched():
    item = case([check("json_valid", {})])
    attempts = [
        _attempt(item, variant, passed, repeat)
        for variant, outcomes in [
            ("candidate", [True, False, True]),
            ("production", [True, True, True]),
            ("without_target", [False, False, False]),
        ]
        for repeat, passed in enumerate(outcomes)
    ]
    result = reduce_results(
        cases=[item],
        attempts=attempts,
        baselines=["production", "without_target"],
        repetitions=3,
    )
    assert result["case_results"][0]["change"] == "mixed"
    assert result["decision"]["regressed_cases"] == 1
    assert result["case_results"][0]["variants"][0]["inconsistent"]
    incomplete = reduce_results(
        cases=[item],
        attempts=attempts[:-1],
        baselines=["production", "without_target"],
        repetitions=3,
    )
    assert incomplete["case_results"][0]["change"] == "unmatched"
    diagnostic = reduce_results(
        cases=[item],
        attempts=attempts,
        baselines=["production", "without_target"],
        repetitions=3,
        diagnostic=True,
    )
    assert diagnostic["decision"]["state"] == "diagnostic"
    assert all(
        row["delta_percentage_points"] is None
        for row in diagnostic["answer_comparisons"]
    )


@pytest.mark.parametrize(
    "left,right,equal",
    [
        (True, 1, False),
        (1, 1.0, False),
        ({"x": [True]}, {"x": [1]}, False),
        (None, None, True),
    ],
)
def test_type_strict_json(left, right, equal):
    assert strict_equal(left, right) is equal


def test_pointer_and_full_json_only():
    assert pointer_value({"a/b": {"~": [None]}}, "/a~1b/~0/0") is None
    item = case([check("json_valid", {})])
    for answer in ("```json\n{}\n```", "{} trailing", "NaN", '{"x":1,"x":2}'):
        assert (
            grade_deterministic(
                case=item, answer=answer, receipts=[], variant="candidate"
            )[0]["verdict"]
            == "fail"
        )
    with pytest.raises(ValueError):
        pointer_value({}, "/~2")


def test_schema_rejects_external_ref_and_bounds_local_evaluation():
    from model_hub.services.skills_hub.v11.grading import bounded_schema_matches

    with pytest.raises(SkillsHubError):
        validate_schema({"$ref": "https://example.invalid/schema"})
    schema = {"$defs": {"amount": {"type": "integer"}}, "$ref": "#/$defs/amount"}
    validate_schema(schema)
    assert bounded_schema_matches(schema, 20) is True
    assert (
        bounded_schema_matches(
            {"type": "string", "pattern": "(a+)+$"}, "a" * 100000 + "!"
        )
        is None
    )


def test_judge_false_quote_ids_and_injection_are_unverifiable():
    item = case([check("semantic", {"requirement": "Use only supplied facts."})])
    row = {
        "check_id": item["checks"][0]["id"],
        "verdict": "pass",
        "evidence": "invented quote",
        "reason": None,
    }
    assert (
        validate_judge_output(
            text=json.dumps({"results": [row]}), case=item, answer="A real answer"
        )[0]["verdict"]
        == "unverifiable"
    )
    row["evidence"] = "real answer"
    assert (
        validate_judge_output(
            text=json.dumps({"results": [row]}), case=item, answer="A real answer"
        )[0]["verdict"]
        == "pass"
    )
    assert (
        validate_judge_output(
            text=json.dumps({"results": [row, row]}), case=item, answer="A real answer"
        )[0]["verdict"]
        == "unverifiable"
    )
    row["check_id"] = str(uuid4())
    assert (
        validate_judge_output(
            text=json.dumps({"results": [row]}), case=item, answer="A real answer"
        )[0]["verdict"]
        == "unverifiable"
    )


def test_inventory_filename_review_and_overlap_cannot_be_overridden():
    item = case(
        [
            check(
                "contains",
                {
                    "text": "This expected response is longer than thirty two characters",
                    "case_sensitive": True,
                },
            )
        ]
    )
    package = bundle(
        extra=[{"path": "tests/reference.md", "content": "Ordinary factual reference."}]
    )
    plan = build_inventory(bundles=[package], cases=[item])
    flagged = plan["inventory"][0]["files"][1]
    review = {
        "fingerprint": "p",
        "reviewed_all_files": True,
        "no_known_answer_keys": True,
        "classifications": [
            {
                "skill_id": package["skill_id"],
                "path": flagged["path"],
                "sha256": flagged["sha256"],
                "classification": "reference_material",
                "reason": "Real policy, not answers.",
            }
        ],
        "false_positive_secret_findings": [],
    }
    accepted, reasons = validate_inventory_review(
        plan_fingerprint="p",
        inventory=plan["inventory"],
        findings=[],
        review=review,
        purpose="regression",
    )
    assert not reasons and accepted["classifications"]
    contaminated = bundle(
        extra=[{"path": "reference.md", "content": item["checks"][0]["config"]["text"]}]
    )
    plan = build_inventory(bundles=[contaminated], cases=[item])
    review["classifications"] = []
    with pytest.raises(SkillsHubError) as caught:
        validate_inventory_review(
            plan_fingerprint="p",
            inventory=plan["inventory"],
            findings=[],
            review=review,
            purpose="regression",
        )
    assert caught.value.code == "evaluation_material_present"


def test_secret_findings_include_case_and_fixture_and_reject_fabrication():
    item = case()
    # Synthetic private-key marker exercises the detector, not a real credential.
    marker = "-----BEGIN PRIVATE KEY-----\nSYNTHETIC NONKEY\n-----END PRIVATE KEY-----"
    item["prompt"] = marker
    item["fixtures"] = [{"id": str(uuid4()), "name": "synthetic", "text": marker}]
    plan = build_inventory(bundles=[], cases=[item])
    findings = bind_findings(plan["outbound_findings"], "plan-one")
    assert {row["location"]["kind"] for row in findings} == {"case_prompt", "fixture"}
    assert marker not in json.dumps(findings)
    assert findings != bind_findings(plan["outbound_findings"], "plan-two")
    review = {
        "fingerprint": "plan-one",
        "reviewed_all_files": True,
        "no_known_answer_keys": True,
        "classifications": [],
        "false_positive_secret_findings": [],
    }
    with pytest.raises(SkillsHubError) as caught:
        validate_inventory_review(
            plan_fingerprint="plan-one",
            inventory=[],
            findings=findings,
            review=review,
            purpose="regression",
        )
    assert caught.value.code == "test_input_unsafe"
    review["false_positive_secret_findings"] = [
        {"finding_id": "fabricated", "reason": "Not valid"}
    ]
    with pytest.raises(SkillsHubError) as caught:
        validate_inventory_review(
            plan_fingerprint="plan-one",
            inventory=[],
            findings=findings,
            review=review,
            purpose="regression",
        )
    assert caught.value.code == "invalid_inventory_review"
