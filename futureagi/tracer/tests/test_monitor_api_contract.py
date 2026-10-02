import json
from pathlib import Path


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _swagger():
    with (_repo_root() / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def _operation(path, method):
    return _swagger()["paths"][path][method.lower()]


def _body_ref(operation):
    body = next(
        parameter
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "body"
    )
    return body["schema"]["$ref"].rsplit("/", 1)[-1]


def _response_ref(operation, status_code="200"):
    schema = operation["responses"][status_code]["schema"]
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    return schema["type"]


def test_monitor_duplicate_has_explicit_contract():
    operation = _operation("/tracer/user-alerts/duplicate/", "POST")

    assert _body_ref(operation) == "UserAlertMonitorDuplicate"
    assert _response_ref(operation) == "UserAlertMonitorDuplicateResponse"


def test_monitor_metric_options_has_explicit_contract():
    operation = _operation("/tracer/user-alerts/metric-options/", "GET")

    assert _response_ref(operation) == "UserAlertMonitorMetricOptionsResponse"
