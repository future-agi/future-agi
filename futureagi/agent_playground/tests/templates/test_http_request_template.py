"""Tests for the HTTP Request template definition."""

import jsonschema
import pytest

from agent_playground.templates import get_all_templates
from agent_playground.templates.http_request import HTTP_REQUEST_TEMPLATE

REQUIRED_FIELDS = {
    "name",
    "display_name",
    "description",
    "icon",
    "categories",
    "input_definition",
    "output_definition",
    "input_mode",
    "output_mode",
    "config_schema",
}


@pytest.mark.unit
class TestHttpRequestDefinitionStructure:
    def test_has_all_required_fields(self):
        assert set(HTTP_REQUEST_TEMPLATE.keys()) == REQUIRED_FIELDS

    def test_registered_in_registry(self):
        templates = get_all_templates()
        assert "http_request" in templates
        assert templates["http_request"] is HTTP_REQUEST_TEMPLATE

    def test_input_mode_is_dynamic(self):
        assert HTTP_REQUEST_TEMPLATE["input_mode"] == "dynamic"

    def test_output_mode_is_strict(self):
        assert HTTP_REQUEST_TEMPLATE["output_mode"] == "strict"

    def test_input_definition_empty_for_dynamic(self):
        assert HTTP_REQUEST_TEMPLATE["input_definition"] == []

    def test_output_definition_has_response_port(self):
        keys = [p["key"] for p in HTTP_REQUEST_TEMPLATE["output_definition"]]
        assert "response" in keys

    def test_config_schema_is_valid_json_schema(self):
        jsonschema.Draft7Validator.check_schema(
            HTTP_REQUEST_TEMPLATE["config_schema"]
        )


@pytest.mark.unit
class TestHttpRequestConfigSchema:
    def _validate(self, config):
        jsonschema.validate(
            instance=config, schema=HTTP_REQUEST_TEMPLATE["config_schema"]
        )

    def test_minimal_valid_config(self):
        self._validate({"method": "GET", "url": "https://api.example.com"})

    def test_full_valid_config(self):
        self._validate(
            {
                "method": "POST",
                "url": "https://api.example.com/users",
                "headers": {"X-Api-Key": "abc"},
                "body": {"name": "Ada"},
                "auth": {"type": "bearer", "token": "t"},
                "timeout": 10,
                "retries": 2,
            }
        )

    def test_all_methods_allowed(self):
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            self._validate({"method": method, "url": "https://x.example.com"})

    def test_missing_method_rejected(self):
        with pytest.raises(jsonschema.ValidationError):
            self._validate({"url": "https://api.example.com"})

    def test_missing_url_rejected(self):
        with pytest.raises(jsonschema.ValidationError):
            self._validate({"method": "GET"})

    def test_invalid_method_rejected(self):
        with pytest.raises(jsonschema.ValidationError):
            self._validate({"method": "OPTIONS", "url": "https://x.example.com"})

    def test_empty_url_rejected(self):
        with pytest.raises(jsonschema.ValidationError):
            self._validate({"method": "GET", "url": ""})

    def test_timeout_bounds_enforced(self):
        with pytest.raises(jsonschema.ValidationError):
            self._validate(
                {"method": "GET", "url": "https://x.example.com", "timeout": 0}
            )
        with pytest.raises(jsonschema.ValidationError):
            self._validate(
                {"method": "GET", "url": "https://x.example.com", "timeout": 301}
            )

    def test_retries_bounds_enforced(self):
        with pytest.raises(jsonschema.ValidationError):
            self._validate(
                {"method": "GET", "url": "https://x.example.com", "retries": 6}
            )

    def test_unknown_config_key_rejected(self):
        with pytest.raises(jsonschema.ValidationError):
            self._validate(
                {"method": "GET", "url": "https://x.example.com", "evil": 1}
            )
