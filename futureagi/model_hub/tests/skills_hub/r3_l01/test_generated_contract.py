"""The generated transport must describe conditional responses and lease headers."""

import json
from pathlib import Path

from model_hub.views.skills_context_foundation import ConsumptionManifestView


def test_manifest_view_declares_conditional_response():
    assert 304 in ConsumptionManifestView.get._swagger_auto_schema["responses"]


def test_generated_foundation_manifest_contract_matches_runtime():
    root = Path(__file__).resolve().parents[5]
    document = json.loads((root / "api_contracts/openapi/swagger.json").read_text())
    for prefix in ("consumption", "contexts"):
        route = (
            f"/model-hub/skills/{prefix}/{{resource_id}}/versions/{{number}}/manifest/"
        )
        operation = document["paths"][route]["get"]
        assert "304" in operation["responses"]
        assert "schema" not in operation["responses"]["304"]
        header = next(
            item
            for item in operation["parameters"]
            if item["name"] == "X-Context-Resolution"
        )
        assert header["in"] == "header" and header["required"] is True
        assert header["format"] == "uuid"
