from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from simulate.serializers.harness_environment import (
    HarnessEnvironmentContractSerializer,
    HarnessEnvironmentDetailSerializer,
    HarnessEnvironmentDetailV2Serializer,
    HarnessEnvironmentSubGoalSerializer,
    HarnessEnvironmentWorldSerializer,
)
from simulate.serializers.harness_job import HarnessStageOutputSerializer
from simulate.services.harness_environment import (
    _catalogue_index,
    _contract_section,
    _environment_evidence,
    _provenance,
    _world_section,
    environment_detail,
)
from simulate.services.harness_environment_evidence import (
    EnvironmentEvidence,
    _secret_safe,
    environment_detail_v2,
)
from simulate.services.hosted_harness_gateway import (
    _authoring_archive_command,
    authoring_stage_outputs,
    authoring_stage_outputs_from_archive,
)


def _fingerprinted(document):
    encoded = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return {**document, "fingerprint": "sha256:" + hashlib.sha256(encoded).hexdigest()}


def _source_model():
    return _fingerprinted(
        {
            "schema_version": "futureagi.source-model.v2",
            "engine": "sqlite",
            "tables": [
                {
                    "name": "public.orders",
                    "columns": [
                        {"name": "id", "native_type": "", "nullable": False},
                        {
                            "name": "status",
                            "native_type": "TEXT",
                            "nullable": True,
                            "enum_values": ["open", "closed"],
                        },
                        {
                            "name": "customer_id",
                            "native_type": "INTEGER",
                            "nullable": False,
                        },
                    ],
                    "primary_key": ["id"],
                    "foreign_keys": [
                        {
                            "columns": ["customer_id"],
                            "referenced_table": "customers",
                            "referenced_columns": ["id"],
                        },
                        {
                            "columns": ["id", "status"],
                            "referenced_table": "history",
                            "referenced_columns": ["order_id", "status"],
                        },
                    ],
                }
            ],
        }
    )


def _certification():
    return _fingerprinted(
        {
            "schema_version": "futureagi.tool-certification.v1",
            "tools": [
                {
                    "tool": "lookup_order",
                    "availability": "runtime_only",
                    "probe_mode": "runtime_only",
                    "reason": "Observed during the call only",
                }
            ],
        }
    )


def _evidence(contract=None, **overrides):
    values = {
        "contract": contract or {"agent": "support", "modality": "chat"},
        "environment": None,
        "scenarios": None,
        "sub_goals": None,
        "stores": None,
        "source_model": None,
        "tool_certification": None,
    }
    return EnvironmentEvidence(**{**values, **overrides})


def _serialized_contract(evidence, connector="http", seed=42, mode=None):
    job = SimpleNamespace(
        payload={"agent": {"connector": connector, "mode": mode}, "seed": seed}
    )
    with patch(
        "simulate.services.harness_environment._provenance",
        return_value={
            "source": {},
            "built_by": None,
            "attempt": None,
            "snapshot": None,
            "digests": {},
            "authored_at": {},
        },
    ):
        section = _contract_section(job, evidence, evidence.schema_tables())
    return HarnessEnvironmentContractSerializer(section).data


@pytest.mark.parametrize("archived", [False, True])
def test_source_artifacts_reach_real_serializers_with_measured_rows(archived):
    prompt = "You handle order support.\nKeep the original whitespace.\n"
    origin = {
        "kind": "file",
        "path": "agent/prompt.txt",
        "line": None,
        "sha256": hashlib.sha256(prompt.encode()).hexdigest(),
    }
    contract = {
        "agent": "support",
        "modality": "voice",
        "base_environment": {"orders": [{"id": "order-1"}]},
        "refusal_signature": "Order not found",
        "domain": {"id": "support", "label": "Order Support"},
        "one_liner": "Find an order",
        "system_prompt": prompt,
        "system_prompt_origin": origin,
        "hard_constraints": ["Verify identity"],
        "hard_constraint_details": [
            {
                "text": "Verify identity",
                "origin": origin,
                "enforced_by": ["prompt"],
                "sub_goal": "identity_checked",
            }
        ],
        "real_use_cases": ["Find an order"],
        "real_use_case_details": [
            {"text": "Find an order", "short": "Order lookup", "sub_goal": "invented"}
        ],
        "amendments": ["tool removed - charge_card: no runnable seam"],
        "amendment_details": [
            {
                "subject": "charge_card",
                "kind": "unreachable_tool",
                "reason": "no runnable seam",
            }
        ],
        "actors": [{"name": "support", "role": "assistant", "origin": origin}],
        "runtime_dependencies": [{"name": "orders", "used_by": ["lookup_order"]}],
        "data_store": {
            "configured_by": "DATABASE_URL",
            "location": "external_db",
            "configuration": {"source": "environment", "ref": "DATABASE_URL"},
        },
        "data_schema": {"orders": {"id": ""}},
        "data_schema_columns": {"orders": [{"name": "wrong", "type": "TEXT"}]},
        "tools": [
            {
                "name": "lookup_order",
                "effect": "Reads one order",
                "effect_kind": "read",
                "effect_evidence": {
                    "origin": origin,
                    "excerpt": "SELECT * FROM orders",
                },
            }
        ],
    }
    build = {
        "stores": [
            {
                "capability": "sql",
                "engine": "postgres",
                "strategy": "template_database",
                "row_counts": {
                    "orders": 0,
                    "_alk_tool_trace": 4,
                    "harness_seed_sentinel": 1,
                    "_business": 2,
                },
            }
        ]
    }
    kwargs = {
        "source_model": _source_model(),
        "tool_certification": _certification(),
        "build_output": build,
        "sub_goals": {
            "sub_goals": [{"name": "identity_checked", "what": "Identity is checked"}]
        },
    }
    if archived:
        documents = {
            "contract.json": contract,
            "build.json": build,
            "sub_goals.json": kwargs["sub_goals"],
            "generic-harness/source-model.json": kwargs["source_model"],
            "generic-harness/tool-certification.json": kwargs["tool_certification"],
        }
        outputs = authoring_stage_outputs_from_archive(_archive(documents))
    else:
        outputs = authoring_stage_outputs(contract, None, None, **kwargs)
    by_kind = {
        output["kind"]: output["data"]
        for output in HarnessStageOutputSerializer(outputs, many=True).data
    }
    evidence = _evidence(**by_kind)
    actual = _serialized_contract(evidence, connector="livekit", seed=0)
    assert actual["system_prompt"] == prompt
    assert actual["system_prompt_origin"] == origin
    assert actual["domain"] == {"id": "support", "label": "Order Support"}
    assert actual["one_liner"] == "Find an order"
    assert actual["base_environment"] == {"orders": [{"id": "order-1"}]}
    assert actual["refusal_signature"] == "Order not found"
    assert actual["hard_constraints"] == ["Verify identity"]
    assert actual["hard_constraint_details"][0]["sub_goal"] == "identity_checked"
    assert "sub_goal" not in actual["real_use_case_details"][0]
    assert actual["real_use_cases"] == ["Find an order"]
    assert actual["amendments"] == [
        {"subject": "tool removed - charge_card", "note": "no runnable seam"}
    ]
    assert actual["amendment_details"] == contract["amendment_details"]
    assert actual["actors"] == contract["actors"]
    assert actual["runtime_dependencies"][0]["used_by"] == ["lookup_order"]
    assert actual["tools"][0]["effect_kind"] == "read"
    assert actual["data_store"]["configured_by"] == "DATABASE_URL"
    assert actual["data_schema"] == {"orders": {"id": ""}}
    columns = actual["data_schema_columns"]["public.orders"]
    assert columns == [
        {
            "name": "id",
            "type": "",
            "enum": [],
            "pk": True,
            "nullable": False,
            "references": None,
        },
        {
            "name": "status",
            "type": "TEXT",
            "enum": ["open", "closed"],
            "pk": False,
            "nullable": True,
            "references": None,
        },
        {
            "name": "customer_id",
            "type": "INTEGER",
            "enum": [],
            "pk": False,
            "nullable": False,
            "references": "customers.id",
        },
    ]
    assert actual["data_schema_tables"][0]["foreign_keys"][1]["columns"] == [
        "id",
        "status",
    ]
    assert actual["tool_certification"] == _certification()["tools"]
    assert actual["end_conditions"]["seed"] == {
        "value": 0,
        "controls": ["scenario_world_rng"],
    }
    assert actual["end_conditions"]["terminate_reasons"] == [
        "assistant-ended-call",
        "customer-ended-call",
    ]
    assert actual["end_conditions"]["truncate_reasons"] == [
        "cancelled",
        "max-duration-reached",
        "no-answer",
        "silence-timed-out",
    ]
    assert "stop_on_constraint_violation" not in actual["end_conditions"]
    world = HarnessEnvironmentWorldSerializer(
        _world_section(None, [], evidence.store_tables(evidence.schema_tables()))
    ).data
    store = world["stores"][0]
    assert store["total_rows"] == 7
    tables = {table["name"]: table for table in store["tables"]}
    assert tables["orders"] == {
        "name": "orders",
        "rows": 0,
        "columns": columns,
        "internal": False,
    }
    assert tables["_alk_tool_trace"]["internal"] is True
    assert tables["harness_seed_sentinel"]["internal"] is True
    assert tables["_business"]["internal"] is False
    assert tables["_business"]["columns"] is None
    assert "customers" not in tables


@pytest.mark.parametrize("connector", ["vapi", "retell", "retell_chat"])
def test_provider_fields_keep_exact_schema_and_scrub_callback_url(connector):
    parameters = {
        "type": "object",
        "properties": {
            "api_key": {"type": "string"},
            "order": {"type": "string", "pattern": "^[A-Z]+$"},
        },
        "required": ["order"],
        "additionalProperties": False,
    }
    contract = {
        "agent": "provider",
        "modality": "voice",
        "tool_entrypoints": [
            {
                "tool": "lookup_order",
                "mode": "provider",
                "implementation_origin": "provider_custom_function",
                "origin": {"kind": "tool_list", "field": "model.tools[0]"},
                "provider_function": {
                    "url": "https://user:password@example.test/tool?signature=secret#token",
                    "parameters": parameters,
                },
            },
            {
                "tool": "end_call",
                "mode": "provider",
                "implementation_origin": "provider_builtin",
            },
        ],
    }
    outputs = authoring_stage_outputs(contract, None, None)
    actual = _serialized_contract(_evidence(outputs[0]["data"]), connector=connector)
    tool = actual["tool_entrypoints"][0]
    assert tool["provider_function"] == {
        "url": "https://example.test/tool",
        "parameters": parameters,
    }
    assert tool["origin"]["field"] == "model.tools[0]"
    assert "source" not in tool
    assert "provider_function" not in actual["tool_entrypoints"][1]


def test_old_contract_keeps_legacy_values_and_absent_evidence():
    contract = {
        "agent": "old",
        "modality": "chat",
        "hard_constraints": ["Be kind"],
        "real_use_cases": ["Help"],
        "data_schema": {"orders": {"id": "INT"}},
        "data_store": {"configured_by": "DATABASE_URL"},
        "amendments": ["A note without a reason"],
    }
    actual = _serialized_contract(_evidence(contract), seed=None)
    assert actual["hard_constraints"] == ["Be kind"]
    assert actual["real_use_cases"] == ["Help"]
    assert actual["data_store"] == {"configured_by": "DATABASE_URL"}
    assert actual["data_schema"] == {"orders": {"id": "INT"}}
    assert actual["data_schema_columns"] is None
    assert actual["actors"] is None
    assert actual["amendment_details"] is None
    assert actual["amendments"] == [{"subject": "", "note": "A note without a reason"}]
    assert "tool_certification" not in actual
    assert "data_schema_tables" not in actual
    assert "seed" not in actual["end_conditions"]


def test_sub_goal_runtime_metadata_reaches_contract_and_scenarios():
    catalogue = {
        "sub_goals": [
            {
                "name": "identity_checked",
                "what": "Identity was checked",
                "judged": "The agent verified identity",
                "output": "pass_fail",
                "overlay": "authentication",
            }
        ]
    }
    evidence = _evidence(sub_goals=catalogue)
    actual = _serialized_contract(evidence)
    assert actual["sub_goals"] == [
        {
            "name": "identity_checked",
            "what": "Identity was checked",
            "kind": "judge",
            "claim": "The agent verified identity",
            "check": "",
            "output": "pass_fail",
            "overlay": "authentication",
        }
    ]
    scenario_sub_goal = _catalogue_index(catalogue)["identity_checked"]
    assert HarnessEnvironmentSubGoalSerializer(scenario_sub_goal).data["output"] == (
        "pass_fail"
    )
    assert HarnessEnvironmentSubGoalSerializer(scenario_sub_goal).data["overlay"] == (
        "authentication"
    )


def test_bundle_manifest_fields_are_available_to_world_and_provenance():
    output = authoring_stage_outputs(
        None,
        {"services": [{"name": "tools-api"}]},
        None,
        bundle={
            "managed": True,
            "processes": [{"name": "worker"}],
            "capabilities": ["tools"],
            "services": [{"name": "bundle-service"}],
        },
    )[0]["data"]
    assert output["services"] == [{"name": "tools-api"}]
    assert output["managed"] is True
    assert output["processes"] == [{"name": "worker"}]
    assert output["capabilities"] == ["tools"]
    assert output["bundle_manifest"]["managed"] is True
    assert _world_section(output, [], [])["runtime"]["managed"] is True
    job = SimpleNamespace(
        payload={},
        attempts=SimpleNamespace(
            order_by=lambda *_args: SimpleNamespace(first=lambda: None)
        ),
        bundle_digest=None,
    )
    assert _provenance(job, output)["built_by"] == "alk"


def test_empty_secret_reference_stays_empty_while_secret_value_is_redacted():
    assert _secret_safe({"password_from": ""}) == {"password_from": ""}
    assert _secret_safe({"password_from": "DATABASE_PASSWORD"}) == {
        "password_from": "[redacted]"
    }


@pytest.mark.parametrize(
    "prompt,digest",
    [
        ("Call https://user:secret@example.test/endpoint to find the order", None),
        ("Read https://example.test/help?signature=secret before answering", None),
        ("Use API_KEY=secret-value to authenticate", None),
        ("Help the caller", "a" * 64),
    ],
)
def test_scrubbed_or_mismatched_prompts_never_claim_verbatim_provenance(prompt, digest):
    origin = {
        "kind": "file",
        "path": "prompt.txt",
        "sha256": digest or hashlib.sha256(prompt.encode()).hexdigest(),
    }
    output = authoring_stage_outputs(
        {
            "system_prompt": prompt,
            "system_prompt_origin": origin,
            "system_prompt_excerpt": "Help the caller",
        },
        None,
        None,
    )[0]["data"]
    assert "system_prompt" not in output
    assert "system_prompt_origin" not in output
    assert output["system_prompt_excerpt"] == "Help the caller"


@pytest.mark.parametrize(
    "url,expected_url",
    [
        (
            "https://schemas.example.test/params?signature=secret#token",
            "https://schemas.example.test/params",
        ),
        (
            "https://user:password@schemas.example.test/params?signature=secret#token",
            "https://schemas.example.test/params",
        ),
        (
            "https://schemas.example.test/params?version=2",
            "https://schemas.example.test/params?version=2",
        ),
        (
            "https://user:password@schemas.example.test/params?version=2&signature=secret&v=1.3&token=hidden#token",
            "https://schemas.example.test/params?version=2&v=1.3",
        ),
        (
            "https://schemas.example.test/params?version=secret-token",
            "https://schemas.example.test/params",
        ),
    ],
)
def test_schema_secret_defaults_are_redacted_without_losing_property_types(
    url, expected_url
):
    parameters = {
        "type": "object",
        "$ref": url,
        "$defs": {"local": {"$ref": "#/$defs/order"}},
        "properties": {"api_key": {"type": "string", "default": "real-secret"}},
    }
    output = authoring_stage_outputs(
        {
            "tool_entrypoints": [{"provider_function": {"parameters": parameters}}],
            "parameters": {"api_key": "another-secret"},
        },
        None,
        None,
    )[0]["data"]
    assert output["parameters"]["api_key"] == "[redacted]"
    assert output["tool_entrypoints"][0]["provider_function"]["parameters"] == {
        "type": "object",
        "$ref": expected_url,
        "$defs": {"local": {"$ref": "#/$defs/order"}},
        "properties": {"api_key": {"type": "string", "default": "[redacted]"}},
    }


def test_embedded_signed_urls_are_scrubbed_in_nested_evidence():
    output = authoring_stage_outputs(
        {
            "notes": "Read (https://user:password@example.test/help?signature=secret#token), then https://other.test/docs?version=2.",
            "tools": [
                {
                    "effect_evidence": {
                        "excerpt": "Call `HTTPS://example.test/action?signature=secret` before returning."
                    }
                }
            ],
            "tool_entrypoints": [
                {
                    "source": {
                        "code": "requests.get('https://x.test/a?signature=secret')"
                    }
                }
            ],
        },
        None,
        None,
    )[0]["data"]
    assert (
        output["notes"]
        == "Read (https://example.test/help), then https://other.test/docs."
    )
    assert (
        output["tools"][0]["effect_evidence"]["excerpt"]
        == "Call `https://example.test/action` before returning."
    )
    assert (
        output["tool_entrypoints"][0]["source"]["code"]
        == "requests.get('https://x.test/a')"
    )
    assert "secret" not in json.dumps(output)
    assert "password" not in json.dumps(output)


@pytest.mark.parametrize("digest", [None, "", "a" * 64])
def test_unverified_full_prompt_is_hidden_in_new_and_stored_artifacts(digest):
    contract = {
        "system_prompt": "Help the caller",
        "system_prompt_origin": {"kind": "file", "path": "prompt.txt"},
        "system_prompt_excerpt": "Help the caller",
    }
    if digest is not None:
        contract["system_prompt_origin"]["sha256"] = digest
    output = authoring_stage_outputs(contract, None, None)[0]["data"]
    assert "system_prompt" not in output
    assert "system_prompt_origin" not in output
    actual = _serialized_contract(_evidence(contract))
    assert actual["system_prompt"] is None
    assert actual["system_prompt_origin"] is None
    assert actual["system_prompt_excerpt"] == "Help the caller"


def test_ambiguous_table_and_store_identities_do_not_acquire_columns():
    source = _source_model()
    second = deepcopy(source["tables"][0])
    second["name"] = "archive.orders"
    source["tables"].append(second)
    store = {
        "capability": "sql",
        "tables": [{"name": "orders", "rows": 9}, {"name": "public.orders", "rows": 3}],
    }
    evidence = _evidence(source_model=source, stores=[store])
    tables = evidence.store_tables(evidence.schema_tables())[0]["tables"]
    assert tables[0]["columns"] is None
    assert tables[1]["columns"][0]["name"] == "id"
    evidence = _evidence(
        source_model=source, stores=[store, {**store, "capability": "other"}]
    )
    assert all(
        table["columns"] is None
        for one in evidence.store_tables(evidence.schema_tables())
        for table in one["tables"]
    )


def _archive(documents):
    body = io.BytesIO()
    with tarfile.open(fileobj=body, mode="w:gz") as archive:
        for name, document in documents.items():
            data = json.dumps(document).encode()
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return body.getvalue()


def test_archive_ignores_wrong_paths_invalid_fingerprints_and_oversized_evidence():
    certificate = _certification()
    certificate["tools"][0]["availability"] = "certified"
    source = _source_model()
    source["padding"] = "x" * (8 * 1024 * 1024)
    outputs = authoring_stage_outputs_from_archive(
        _archive(
            {
                "contract.json": {"agent": "old"},
                "generic-harness/tool-certification.json": certificate,
                "source/tool-certification.json": _certification(),
                "generic-harness/source-model.json": source,
            }
        )
    )
    assert [output["kind"] for output in outputs] == ["contract"]


def test_detail_evidence_prefers_normalized_artifacts_in_one_query():
    job = SimpleNamespace(
        stage_outputs=[
            {"kind": "contract", "data": {"agent": "live"}},
            {"kind": "stores", "data": [{"capability": "live"}]},
        ]
    )
    with patch(
        "simulate.services.harness_environment.HostedHarnessStageOutput"
    ) as model:
        query = model.no_workspace_objects.filter.return_value
        query.values_list.return_value = [
            ("contract", {"agent": "verified"}),
            ("stores", [{"capability": "verified"}]),
        ]
        evidence = _environment_evidence(job)
    assert evidence.contract == {"agent": "verified"}
    assert evidence.stores == [{"capability": "verified"}]
    model.no_workspace_objects.filter.assert_called_once()


@pytest.mark.parametrize("built", [False, True])
def test_archive_command_includes_measured_build_when_present(tmp_path, built):
    authoring = tmp_path / "authoring"
    artifacts = tmp_path / "artifacts"
    authoring.mkdir()
    artifacts.mkdir()
    (authoring / "contract.json").write_text('{"agent":"source"}')
    if built:
        (artifacts / "build.json").write_text('{"stores":[]}')
    destination = tmp_path / "authoring.tar.gz"
    command = _authoring_archive_command(str(destination)).replace(
        "/work/", str(tmp_path) + "/"
    )
    subprocess.run(["sh", "-c", command], check=True)
    with tarfile.open(destination) as archive:
        names = archive.getnames()
    assert "./contract.json" in names
    assert ("build.json" in names) is built


@pytest.mark.django_db
def test_full_detail_serializes_stored_evidence_without_provider_or_storage_reads(
    user, workspace
):
    from simulate.models import HostedHarnessStageOutput
    from simulate.services.hosted_harness import create_hosted_job
    from simulate.tests.test_harness_provider import _v1_payload

    job, _ = create_hosted_job(
        user.organization,
        _v1_payload(),
        idempotency_key="th8038-source-evidence",
        workspace=workspace,
    )
    job.stage_outputs = authoring_stage_outputs(
        {"agent": "live", "modality": "chat"}, None, None
    )
    job.save(update_fields=["stage_outputs", "updated_at"])
    for output in authoring_stage_outputs(
        {"agent": "verified", "modality": "chat"},
        None,
        None,
        source_model=_source_model(),
        tool_certification=_certification(),
    ):
        HostedHarnessStageOutput.no_workspace_objects.create(
            job=job,
            kind=output["kind"],
            data=output["data"],
        )
    with (
        patch(
            "simulate.services.hosted_harness_gateway.get_storage_client",
            side_effect=AssertionError("storage read"),
        ),
        patch(
            "simulate.services.hosted_harness_gateway.get_sandbox_provider",
            side_effect=AssertionError("provider read"),
        ),
    ):
        actual = HarnessEnvironmentDetailSerializer(environment_detail(job)).data
    assert actual["contract"]["agent"] == "verified"
    assert actual["contract"]["data_schema_columns"]["public.orders"][0]["pk"] is True
    assert actual["contract"]["tool_certification"] == _certification()["tools"]


def _detail_job(user, workspace, contract, *, source_model=None, sub_goals=None):
    from simulate.services.hosted_harness import create_hosted_job
    from simulate.tests.test_harness_provider import _v1_payload

    job, _ = create_hosted_job(
        user.organization,
        _v1_payload(),
        idempotency_key="th8038-versioned-detail",
        workspace=workspace,
    )
    job.stage_outputs = authoring_stage_outputs(
        contract, None, None, source_model=source_model, sub_goals=sub_goals
    )
    job.save(update_fields=["stage_outputs", "updated_at"])
    return job


@pytest.mark.django_db
@pytest.mark.parametrize("normalized", [False, True])
def test_historical_list_description_is_scrubbed_without_rewriting_artifacts(
    user, workspace, normalized
):
    from simulate.models import HostedHarnessStageOutput

    contract = {
        "agent": "old",
        "one_liner": "Read https://user:password@example.test/help?signature=secret#token before answering.",
    }
    job = _detail_job(user, workspace, None)
    if normalized:
        artifact = HostedHarnessStageOutput.no_workspace_objects.create(
            job=job, kind="contract", data=deepcopy(contract)
        )
    else:
        job.stage_outputs = [{"kind": "contract", "data": deepcopy(contract)}]
        job.save(update_fields=["stage_outputs", "updated_at"])
    client = APIClient()
    client.force_authenticate(user=user)
    with (
        patch(
            "simulate.services.hosted_harness_gateway.get_storage_client",
            side_effect=AssertionError("storage read"),
        ),
        patch(
            "simulate.services.hosted_harness_gateway.get_sandbox_provider",
            side_effect=AssertionError("provider read"),
        ),
    ):
        response = client.get(
            "/simulate/api/harness-environments/", HTTP_X_WORKSPACE_ID=str(workspace.id)
        )
    assert response.status_code == 200
    row = next(row for row in response.json()["results"] if row["id"] == str(job.id))
    assert row["description"] == "Read https://example.test/help before answering."
    if normalized:
        artifact.refresh_from_db()
        assert artifact.data == contract
    else:
        job.refresh_from_db()
        assert job.stage_outputs[0]["data"] == contract


@pytest.mark.django_db
@pytest.mark.parametrize("normalized", [False, True])
@pytest.mark.parametrize("version", [1, 2])
def test_historical_contract_secrets_are_hidden_without_rewriting_artifacts(
    user, workspace, normalized, version
):
    from simulate.models import HostedHarnessStageOutput

    prompt = "Read https://example.test/help?signature=secret before answering."
    origin = {"kind": "file", "path": "agent.py", "line": 1}
    contract = {
        "agent": "old",
        "modality": "chat",
        "system_prompt": prompt,
        "system_prompt_origin": {
            **origin,
            "sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        },
        "system_prompt_excerpt": "Help the caller",
        "notes": "Read https://example.test/help?signature=secret before answering.",
        "tools": [
            {
                "name": "lookup",
                "effect": "Reads an order",
                "effect_kind": "read",
                "effect_evidence": {
                    "origin": origin,
                    "excerpt": "Read https://example.test/help?signature=secret",
                },
            }
        ],
        "tool_entrypoints": [
            {
                "tool": "lookup",
                "mode": "import",
                "implementation_origin": "source",
                "source": {
                    "path": "agent.py",
                    "line_start": 1,
                    "line_end": 1,
                    "code": "requests.get('https://x.test/a?signature=secret')",
                },
            }
        ],
        "tool_entrypoint_details": [
            {
                "tool": "provider_tool",
                "mode": "provider",
                "implementation_origin": "provider_custom_function",
                "provider_function": {
                    "url": "https://callback.test/tool?signature=secret",
                    "parameters": {
                        "$ref": "https://schemas.test/tool?version=2&signature=secret"
                    },
                },
            }
        ],
    }
    job = _detail_job(user, workspace, None)
    if normalized:
        artifact = HostedHarnessStageOutput.no_workspace_objects.create(
            job=job, kind="contract", data=deepcopy(contract)
        )
    else:
        job.stage_outputs = [{"kind": "contract", "data": deepcopy(contract)}]
        job.save(update_fields=["stage_outputs", "updated_at"])
    client = APIClient()
    client.force_authenticate(user=user)
    with (
        patch(
            "simulate.services.hosted_harness_gateway.get_storage_client",
            side_effect=AssertionError("storage read"),
        ),
        patch(
            "simulate.services.hosted_harness_gateway.get_sandbox_provider",
            side_effect=AssertionError("provider read"),
        ),
    ):
        response = client.get(
            f"/simulate/api/harness-environments/{job.id}/",
            {"contract_version": version},
            HTTP_X_WORKSPACE_ID=str(workspace.id),
        )
    assert response.status_code == 200
    actual = response.json()["contract"]
    assert actual["system_prompt"] is None
    assert actual["system_prompt_origin"] is None
    assert actual["system_prompt_excerpt"] == "Help the caller"
    assert actual["notes"] == "Read https://example.test/help before answering."
    assert (
        actual["tools"][0]["effect_evidence"]["excerpt"]
        == "Read https://example.test/help"
    )
    assert (
        actual["tool_entrypoints"][0]["source"]["code"]
        == "requests.get('https://x.test/a')"
    )
    if version == 2:
        assert actual["tool_entrypoints"][1]["provider_function"] == {
            "url": "https://callback.test/tool",
            "parameters": {"$ref": "https://schemas.test/tool?version=2"},
        }
    assert "secret" not in json.dumps(actual)
    if normalized:
        artifact.refresh_from_db()
        assert artifact.data == contract
    else:
        job.refresh_from_db()
        assert job.stage_outputs[0]["data"] == contract


@pytest.mark.django_db
def test_detail_v2_projects_rich_shapes_without_changing_v1_or_persistence(
    user, workspace
):
    origin = {"kind": "file", "path": "agent.py", "line": 12}
    contract = {
        "agent": "support",
        "modality": "chat",
        "hard_constraints": ["Verify identity"],
        "hard_constraint_details": [
            {
                "text": "Verify identity",
                "origin": origin,
                "enforced_by": ["prompt"],
                "sub_goal": "verified",
            }
        ],
        "real_use_cases": ["Find an order"],
        "real_use_case_details": [
            {"text": "Find an order", "short": "Order lookup", "sub_goal": "missing"}
        ],
        "data_schema": {"orders": {"id": "INT"}},
        "data_store": {
            "kind": "postgres",
            "configured_by": "DATABASE_URL",
            "configuration": {"source": "environment", "ref": "DATABASE_URL"},
            "location": "external_db",
        },
        "amendments": ["charge_card: no runnable seam"],
        "amendment_details": [
            {
                "subject": "charge_card",
                "kind": "unreachable_tool",
                "reason": "no runnable seam",
            }
        ],
        "tools": [
            {
                "name": "lookup_order",
                "effect": "Reads one order",
                "effect_kind": "read",
                "effect_evidence": {
                    "origin": origin,
                    "excerpt": "SELECT * FROM orders",
                },
            },
            {"name": "unknown", "effect": "Unclassified operation"},
        ],
        "tool_entrypoints": [
            {
                "tool": "lookup_order",
                "mode": "import",
                "implementation_origin": "source",
                "origin": origin,
            }
        ],
    }
    job = _detail_job(
        user,
        workspace,
        contract,
        source_model=_source_model(),
        sub_goals={"sub_goals": [{"name": "verified", "what": "Identity was checked"}]},
    )
    stored_payload, stored_outputs = deepcopy(job.payload), deepcopy(job.stage_outputs)
    client = APIClient()
    client.force_authenticate(user=user)
    url = f"/simulate/api/harness-environments/{job.id}/"
    with (
        patch(
            "simulate.services.hosted_harness_gateway.get_storage_client",
            side_effect=AssertionError("storage read"),
        ),
        patch(
            "simulate.services.hosted_harness_gateway.get_sandbox_provider",
            side_effect=AssertionError("provider read"),
        ),
    ):
        omitted = client.get(url, HTTP_X_WORKSPACE_ID=str(workspace.id))
        legacy = client.get(
            url, {"contract_version": 1}, HTTP_X_WORKSPACE_ID=str(workspace.id)
        )
        rich = client.get(
            url, {"contract_version": 2}, HTTP_X_WORKSPACE_ID=str(workspace.id)
        )
    assert omitted.status_code == legacy.status_code == rich.status_code == 200
    assert omitted.content == legacy.content
    old = legacy.json()
    assert "contract_version" not in old
    assert old["contract"]["hard_constraints"] == ["Verify identity"]
    assert old["contract"]["real_use_cases"] == ["Find an order"]
    assert old["contract"]["data_store"]["configured_by"] == "DATABASE_URL"
    assert old["contract"]["tools"][0]["effect"] == "Reads one order"
    assert old["contract"]["tool_entrypoints"][0]["origin"] == origin
    legacy_serializer = HarnessEnvironmentDetailSerializer(data=old)
    assert legacy_serializer.is_valid(), legacy_serializer.errors
    data = rich.json()
    assert data["contract_version"] == 2
    assert HarnessEnvironmentDetailV2Serializer(data).data == data
    actual = data["contract"]
    assert actual["hard_constraints"][0]["text"] == "Verify identity"
    assert actual["hard_constraints"][0]["sub_goal"] == "verified"
    assert actual["hard_constraints"][0]["enforced_by"] == ["prompt"]
    assert actual["real_use_cases"][0]["short"] == "Order lookup"
    assert "sub_goal" not in actual["real_use_cases"][0]
    assert actual["data_schema"]["public.orders"][0]["type"] == ""
    assert actual["data_schema"]["public.orders"][1]["enum"] == ["open", "closed"]
    assert actual["data_schema_legacy"] == {"orders": {"id": "INT"}}
    assert actual["data_store"]["configured_by"]["source"] == "environment"
    assert actual["data_store"]["configured_by"]["ref"] == "DATABASE_URL"
    assert actual["data_store"]["configured_by_legacy"] == "DATABASE_URL"
    assert actual["amendments"] == contract["amendment_details"]
    assert actual["amendments_legacy"] == old["contract"]["amendments"]
    assert actual["tools"][0]["effect"] == "read"
    assert actual["tools"][0]["effect_legacy"] == "Reads one order"
    assert actual["tools"][0]["effect_evidence"]["excerpt"] == "SELECT * FROM orders"
    assert actual["tools"][0]["effect_evidence"]["origin"]["path"] == "agent.py"
    assert actual["tools"][1]["effect"] is None
    assert actual["tool_entrypoints"][0]["origin"] == "source"
    assert actual["tool_entrypoints"][0]["origin_evidence"]["path"] == "agent.py"
    job.refresh_from_db()
    assert job.payload == stored_payload
    assert job.stage_outputs == stored_outputs


@pytest.mark.django_db
@pytest.mark.parametrize(
    "legacy_mode", [None, "unreachable", "import", "unreachable_source"]
)
def test_provider_entrypoint_companion_only_joins_v2(user, workspace, legacy_mode):
    legacy_entries = (
        []
        if legacy_mode is None
        else [
            {
                "tool": "lookup_order",
                "mode": legacy_mode,
                "implementation_origin": "source" if legacy_mode == "import" else None,
            }
        ]
    )
    if legacy_mode == "unreachable_source":
        legacy_entries[0].update(
            {
                "mode": "unreachable",
                "source": {
                    "path": "agent.py",
                    "line_start": 1,
                    "line_end": 1,
                    "code": "def lookup_order(): pass",
                },
            }
        )
    contract = {
        "agent": "provider",
        "modality": "voice",
        "tool_entrypoints": legacy_entries,
        "tool_entrypoint_details": [
            {
                "tool": "lookup_order",
                "mode": "provider",
                "implementation_origin": "provider_custom_function",
                "origin": {"kind": "tool_list", "field": "model.tools[0]"},
                "provider_function": {
                    "url": "https://callback.example.test/tool?signature=secret",
                    "parameters": {
                        "$ref": "https://schema.example.test/order?signature=secret"
                    },
                },
            }
        ],
    }
    job = _detail_job(user, workspace, contract)
    client = APIClient()
    client.force_authenticate(user=user)
    url = f"/simulate/api/harness-environments/{job.id}/"
    legacy = client.get(url, HTTP_X_WORKSPACE_ID=str(workspace.id))
    rich = client.get(
        url, {"contract_version": 2}, HTTP_X_WORKSPACE_ID=str(workspace.id)
    )
    assert legacy.status_code == rich.status_code == 200
    assert legacy.json()["contract"]["tool_entrypoints"] == legacy_entries
    assert "tool_entrypoint_details" not in legacy.json()["contract"]
    entries = rich.json()["contract"]["tool_entrypoints"]
    assert len(entries) == 1
    if legacy_mode == "unreachable_source":
        assert entries[0]["source"]["path"] == "agent.py"
        assert entries[0]["origin"] is None
    elif legacy_mode == "import":
        assert entries[0]["origin"] == "source"
    else:
        assert entries[0]["origin"] == "provider_custom_function"
        assert entries[0]["provider_function"] == {
            "url": "https://callback.example.test/tool",
            "parameters": {"$ref": "https://schema.example.test/order"},
        }
    job.refresh_from_db()
    assert job.stage_outputs[0]["data"]["tool_entrypoints"] == legacy_entries
    assert bool(job.stage_outputs[0]["data"]["tool_entrypoints"]) == bool(
        legacy_entries
    )


@pytest.mark.django_db
@pytest.mark.parametrize("version", ["0", "3", "two", "2.0", ""])
def test_detail_rejects_invalid_contract_version(user, workspace, version):
    job = _detail_job(user, workspace, {"agent": "old"})
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get(
        f"/simulate/api/harness-environments/{job.id}/",
        {"contract_version": version},
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    assert response.status_code == 400


@pytest.mark.django_db
@pytest.mark.parametrize("authored", [False, True])
def test_detail_v2_keeps_missing_evidence_unknown(user, workspace, authored):
    contract = (
        {
            "agent": "old",
            "hard_constraints": ["Be kind"],
            "real_use_cases": ["Help"],
            "data_schema": {"orders": {"id": "INT"}},
            "data_store": {"configured_by": "DATABASE_URL"},
            "amendments": ["A note without a reason"],
            "tool_entrypoints": [{"tool": "old", "mode": "import"}],
        }
        if authored
        else None
    )
    job = _detail_job(user, workspace, contract)
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get(
        f"/simulate/api/harness-environments/{job.id}/",
        {"contract_version": 2},
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["contract_version"] == 2
    if not authored:
        assert data["contract"] is None
        return
    actual = data["contract"]
    assert actual["hard_constraints"][0]["text"] == "Be kind"
    assert actual["real_use_cases"][0]["text"] == "Help"
    assert actual["data_schema"] is None
    assert actual["data_schema_legacy"] == {"orders": {"id": "INT"}}
    assert actual["data_store"]["configured_by"] is None
    assert actual["data_store"]["configured_by_legacy"] == "DATABASE_URL"
    assert actual["amendments"] == [
        {"subject": "", "kind": None, "reason": "A note without a reason"}
    ]
    assert actual["tool_entrypoints"][0]["origin"] is None


def test_v2_does_not_assign_ambiguous_item_evidence_or_mutate_source():
    detail = {
        "contract": {
            "hard_constraints": ["Be kind"],
            "hard_constraint_details": [
                {"text": "Be kind", "sub_goal": "one"},
                {"text": "Be kind", "sub_goal": "two"},
            ],
            "amendments": [],
        }
    }
    before = deepcopy(detail)
    actual = environment_detail_v2(detail, [])
    assert actual["contract"]["hard_constraints"] == [{"text": "Be kind"}]
    assert detail == before


def test_v2_keeps_unmatched_legacy_and_rich_amendments():
    detail = {
        "contract": {
            "amendments": [
                {"subject": "charge_card", "note": "No runnable seam"},
                {"subject": "", "note": "Another legacy note"},
            ],
            "amendment_details": [
                {
                    "subject": "charge_card",
                    "kind": "unreachable_tool",
                    "reason": "No runnable seam",
                },
                {
                    "subject": "orders",
                    "kind": "contract_change",
                    "reason": "Source table name corrected",
                },
            ],
        }
    }
    actual = environment_detail_v2(detail, [])["contract"]
    assert actual["amendments"] == [
        {
            "subject": "charge_card",
            "kind": "unreachable_tool",
            "reason": "No runnable seam",
        },
        {
            "subject": "orders",
            "kind": "contract_change",
            "reason": "Source table name corrected",
        },
        {"subject": "", "kind": None, "reason": "Another legacy note"},
    ]
    assert actual["amendments_legacy"] == detail["contract"]["amendments"]
