"""Frontend examples must remain exact outputs of the current DRF contract."""

import json
from pathlib import Path

import pytest

from model_hub.tests.skills_hub.r3_l07.export_wire_fixtures import samples

FIXTURES = (
    Path(__file__).resolve().parents[5]
    / "frontend/src/api/skills/__tests__/r3_l07/wire-fixtures.json"
)


@pytest.mark.parametrize("name", sorted(samples()))
def test_frontend_fixture_is_current_serializer_output(name):
    serializer, input_value = samples()[name]
    instance = serializer(data=input_value)
    assert instance.is_valid(), instance.errors
    stored = json.loads(FIXTURES.read_text())[name]
    assert stored == instance.data


@pytest.mark.parametrize(
    "name",
    [
        "CredentialInput",
        "DraftStart",
        "DraftSave",
        "HeadAcknowledgment",
        "ContextPublish",
        "DependencyPreview",
        "DependencyApply",
        "HumanAnnotation",
        "RetainedPlanAcceptance",
        "CheckpointRestore",
        "SpendPolicyInput",
        "ReceiptInput",
        "SessionInput",
        "QuarantineInput",
    ],
)
def test_frozen_mutations_reject_untrusted_extra_fields(name):
    serializer, value = samples()[name]
    instance = serializer(
        data={**value, "organization_id": "00000000-0000-4000-8000-000000000099"}
    )
    assert not instance.is_valid(), f"{name} accepted an invented scope field"
