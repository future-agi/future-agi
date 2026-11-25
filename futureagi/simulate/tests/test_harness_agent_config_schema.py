"""The agent `config` block must accept the booleans the UI sends.

The preflight and build forms post ``config: {"inbound": bool,
"target_speaks_first": bool}``. The frontend validates request bodies against
the generated OpenAPI schema, and an untyped ``DictField`` is rendered there
as a map of strings -- which made the validator refuse those booleans before
the request ever left the browser. The field is a ``JSONField`` so the schema
says "object" and nothing more; these tests keep it that way.
"""

import pytest
from rest_framework import serializers

from simulate.serializers.harness_job import HarnessAgentSerializer


def test_agent_config_field_is_a_plain_json_object_in_the_schema():
    field = HarnessAgentSerializer().fields["config"]
    assert isinstance(field, serializers.JSONField)
    assert not isinstance(field, serializers.DictField)


def test_agent_config_accepts_the_booleans_the_forms_send():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "vapi",
            "config": {"inbound": False, "target_speaks_first": True},
        }
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["config"] == {
        "inbound": False,
        "target_speaks_first": True,
    }


@pytest.mark.parametrize("bad", ["not-an-object", ["inbound"], 3])
def test_agent_config_still_has_to_be_an_object(bad):
    serializer = HarnessAgentSerializer(data={"connector": "vapi", "config": bad})
    assert not serializer.is_valid()
    assert "config" in serializer.errors


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        (900, True),
        ("900", True),
        (300.5, True),
        (3300, True),
        (0, False),
        (-5, False),
        (3301, False),
        ("slow", False),
        (True, False),
    ],
)
def test_a_chat_agent_s_reply_deadline_is_a_number_of_seconds_within_the_build_s_budget(
    value, valid
):
    serializer = HarnessAgentSerializer(
        data={"connector": "auto", "config": {"chat_call_timeout_seconds": value}}
    )
    assert serializer.is_valid() is valid, serializer.errors
    if not valid:
        assert "chat_call_timeout_seconds" in str(serializer.errors["config"])


def test_a_chat_agent_with_no_reply_deadline_keeps_the_default():
    serializer = HarnessAgentSerializer(data={"connector": "auto", "config": {}})
    assert serializer.is_valid(), serializer.errors
    assert "chat_call_timeout_seconds" not in serializer.validated_data["config"]
