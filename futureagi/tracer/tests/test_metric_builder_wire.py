"""Consume payloads produced by F05's JS builder in the cloud qualification run."""

import json
import os
from pathlib import Path

import pytest

from tracer.serializers.derived_metric import DerivedMetricWriteSerializer
from tracer.services.derived_metrics.spec import normalize_spec


@pytest.mark.parametrize("index", range(16))
def test_actual_sentence_builder_wire(index):
    # The committed fixture is the actual cloud builder output and is also
    # pinned by Vitest. Qualification prefers the freshly generated payloads.
    fixture = Path(__file__).resolve().parents[3] / (
        "frontend/src/sections/derived-metrics/__tests__/fixtures/builder-wire.json"
    )
    path = Path(os.environ.get("F05_WIRE_OUT", str(fixture)))
    wires = json.loads(path.read_text())
    serializer = DerivedMetricWriteSerializer(data=wires[index])
    assert serializer.is_valid(), serializer.errors
    definition = normalize_spec(serializer.validated_data)
    assert definition["kind"] == wires[index]["kind"]
    assert definition["level"] == wires[index]["level"]
    for actual, sent in zip(definition["inputs"], wires[index]["inputs"], strict=True):
        for key, value in sent.items():
            # DRF normalizes UUID strings to UUID objects; compare its JSON wire
            # representation, not Python's internal type, without dropping keys.
            actual_value = str(actual[key]) if key == "config_id" else actual[key]
            assert actual_value == value
    assert (
        definition["field_spec"]["conditions"]
        == wires[index]["field_spec"]["conditions"]
    )
