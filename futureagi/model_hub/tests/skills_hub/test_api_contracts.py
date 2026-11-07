"""Validate actual JSON responses against the generated Skills response schemas."""

import json
import re
from functools import cache
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from model_hub.tests.skills_hub import test_api_endpoints as fixtures

BASE = fixtures.BASE
archive = fixtures.archive
client = fixtures.client
credentials = fixtures.credentials
pytestmark = pytest.mark.django_db


@cache
def swagger_contract():
    path = Path(__file__).resolve().parents[4] / "api_contracts/openapi/swagger.json"
    return json.loads(path.read_text())


def json_schema(value):
    """Convert Swagger 2 nullability to JSON Schema for the standard validator."""
    if isinstance(value, list):
        return [json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("x-json-value"):
        return {}
    if "x-oneOf" in value:
        return {"oneOf": json_schema(value["x-oneOf"])}
    result = {
        key: json_schema(item) for key, item in value.items() if key != "x-nullable"
    }
    return {"anyOf": [result, {"type": "null"}]} if value.get("x-nullable") else result


def test_download_contracts_declare_zip_media_type():
    for path in (BASE + "download/", BASE + "{skill_id}/versions/{number}/download/"):
        operation = swagger_contract()["paths"][path]["get"]
        assert "application/zip" in operation["produces"]
        assert operation["responses"]["200"]["schema"]["type"] == "file"


def assert_contract(response):
    request = response.wsgi_request
    contract = swagger_contract()
    path = next(
        route
        for route in contract["paths"]
        if route.startswith(BASE)
        and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", route), request.path)
    )
    operation = contract["paths"][path][request.method.lower()]
    schema = operation["responses"][str(response.status_code)]["schema"]
    validator = Draft7Validator(
        json_schema({**schema, "definitions": contract["definitions"]})
    )
    validator.validate(json.loads(response.content))
    return response.json().get("result")


def test_actual_success_and_error_payloads_match_schema(client, make_skill_files):
    created = assert_contract(
        client.post(
            BASE,
            {
                "name": "help-desk",
                "description": "Use when a support request needs clear and verifiable instructions.",
            },
            format="json",
        )
    )
    assert created["latest"] is None
    path = BASE + created["id"] + "/"
    assert_contract(client.get(BASE))
    assert_contract(client.get(BASE + "templates/"))
    assert_contract(client.get(path + "draft/"))
    assert_contract(
        client.post(BASE + "checks/", {"files": make_skill_files()}, format="json")
    )
    assert_contract(
        client.post(path + "draft/publish/", {"labels": ["production"]}, format="json")
    )
    for route, query in [
        (path, {}),
        (path + "versions/", {}),
        (path + "versions/1/", {}),
        (path + "labels/", {}),
        (path + "labels/history/", {}),
        (path + "compare/", {"from": 1, "to": 1}),
        (path + "usage/", {}),
        (path + "versions/1/files/", {"path": "SKILL.md"}),
        (BASE + "resolve/", {"name": "help-desk"}),
    ]:
        assert_contract(client.get(route, query))
    assert_contract(client.get(path + "draft/"))
    assert_contract(client.get(BASE, {"page_size": 101}))
    assert_contract(
        client.post(
            BASE, {"name": "help-desk", "description": "Duplicate"}, format="json"
        )
    )
    assert_contract(
        client.post(
            path + "versions/",
            {"files": make_skill_files(), "base_version": None},
            format="json",
        )
    )
    assert_contract(client.post(BASE + "publish/", {"files": []}, format="json"))
    preview = assert_contract(
        client.post(
            BASE + "import/preview/",
            {"files": archive(make_skill_files())},
            format="multipart",
        )
    )
    assert_contract(
        client.post(
            BASE + "import/",
            {
                "files": archive(make_skill_files()),
                "selected": json.dumps([preview["bundles"][0]["key"]]),
            },
            format="multipart",
        )
    )


@pytest.mark.parametrize(
    "method,suffix,limit",
    [
        ("post", "versions/", 2000),
        ("post", "versions/1/restore/", 2000),
        ("post", "draft/publish/", 2000),
        ("put", "draft/", 2000),
        ("put", "labels/staging/", 500),
    ],
)
def test_oversized_notes_keep_the_service_error_code(
    client, hub_skill, make_skill_files, method, suffix, limit
):
    payload = {"note": "n" * (limit + 1)}
    if suffix == "versions/":
        payload["files"] = make_skill_files()
    elif suffix == "draft/":
        payload.update(files=make_skill_files(), base_version=None, revision=1)
    elif suffix.startswith("labels/"):
        payload["version"] = 1
    response = getattr(client, method)(
        BASE + str(hub_skill.pk) + "/" + suffix, payload, format="json"
    )
    assert response.status_code == 413
    assert response.data["code"] == "payload_too_large"
    assert_contract(response)
