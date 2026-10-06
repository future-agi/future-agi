"""Response-parity helpers: validate real handler output against swagger.json.

Used by the TH-8216 contract-truth tests. ``validate_response`` converts the
checked-in Swagger 2.0 schema for one operation/status into JSON Schema and
validates a captured body with ``jsonschema``. The conversion mirrors the
frontend runtime mapper (``frontend/src/api/contracts/openapi-contract.js``)
for the repo's extensions (``x-nullable``, ``x-json-value``,
``x-string-or-object``, ``x-string-or-array``) and is stricter in one way: an
object schema that declares ``properties`` but no ``additionalProperties``
rejects undeclared keys, so a response that emits fields the contract does
not mention fails parity instead of passing silently. Because of that rule
``allOf`` is unsupported and raises ``NotImplementedError``.

Captures: ``assert_capture`` sanitises a body (UUIDs, timestamps, emails) and
compares it with the checked-in fixture. Regenerate fixtures with::

    TH8216_REGENERATE_CAPTURES=1 bin/test --no-services <test paths>
"""

import copy
import json
import os
import re
from pathlib import Path

from jsonschema import Draft7Validator, FormatChecker

REGENERATE_ENV = "TH8216_REGENERATE_CAPTURES"
_IGNORED_KEYS = {"title", "description", "example", "readOnly", "default"}
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$"
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def repo_root():
    return Path(__file__).resolve().parents[3]


def load_swagger():
    with (repo_root() / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def operation(swagger, path, method):
    return swagger["paths"][path][method.lower()]


def response_schema(swagger, path, method, status_code):
    """Return the declared schema for one status, falling back like the FE."""
    responses = operation(swagger, path, method)["responses"]
    declared = responses.get(str(status_code))
    if declared is None and not 200 <= int(status_code) < 300:
        declared = responses.get("default")
    if declared is None or "schema" not in declared:
        return None
    return declared["schema"]


class _Converter:
    def __init__(self, swagger, strict_objects):
        self.definitions = swagger.get("definitions", {})
        self.strict_objects = strict_objects
        self.converted = {}

    def ref(self, name):
        if name not in self.converted:
            self.converted[name] = {}
            self.converted[name] = self.convert(self.definitions[name])
        return {"$ref": f"#/definitions/{name}"}

    def convert(self, schema):
        if not isinstance(schema, dict):
            return {}
        nullable = bool(schema.get("x-nullable"))
        if schema.get("x-json-value"):
            return {}
        if schema.get("x-string-or-object"):
            object_schema = {
                k: v for k, v in schema.items() if k != "x-string-or-object"
            }
            object_schema.pop("x-nullable", None)
            object_schema["type"] = "object"
            return self._nullable(
                {"anyOf": [{"type": "string"}, self.convert(object_schema)]},
                nullable,
            )
        if schema.get("x-string-or-array"):
            return self._nullable(
                {"anyOf": [{"type": "string"}, {"type": "array"}]}, nullable
            )
        if "$ref" in schema:
            return self._nullable(self.ref(schema["$ref"].rsplit("/", 1)[-1]), nullable)

        out = {}
        for key, value in schema.items():
            if key in _IGNORED_KEYS or key.startswith("x-"):
                continue
            if key == "properties":
                out["properties"] = {k: self.convert(v) for k, v in value.items()}
            elif key == "items":
                out["items"] = self.convert(value)
            elif key == "additionalProperties":
                out[key] = value if isinstance(value, bool) else self.convert(value)
            elif key == "allOf":
                # Each closed (additionalProperties: false) branch would reject
                # the other branches' keys, so a composed object could never
                # validate. swagger.json has no allOf today; refuse rather
                # than silently mis-validate.
                raise NotImplementedError(
                    "openapi_parity does not support allOf with closed objects"
                )
            elif key == "format" and value != "uuid":
                continue
            elif key == "type" and value == "file":
                continue
            else:
                out[key] = copy.deepcopy(value)
        if (
            self.strict_objects
            and "properties" in out
            and "additionalProperties" not in out
        ):
            out["additionalProperties"] = False
        return self._nullable(out, nullable)

    @staticmethod
    def _nullable(schema, nullable):
        if not nullable:
            return schema
        if "enum" in schema:
            schema = dict(schema, enum=[*schema["enum"], None])
        if isinstance(schema.get("type"), str) and set(schema) <= {
            "type",
            "format",
            "minLength",
            "maxLength",
            "minimum",
            "maximum",
            "enum",
        }:
            return dict(schema, type=[schema["type"], "null"])
        return {"anyOf": [schema, {"type": "null"}]}


def to_json_schema(swagger, schema, *, strict_objects=True):
    converter = _Converter(swagger, strict_objects)
    root = converter.convert(schema)
    root["definitions"] = converter.converted
    return root


def validation_errors(swagger, schema, body, *, strict_objects=True):
    validator = Draft7Validator(
        to_json_schema(swagger, schema, strict_objects=strict_objects),
        format_checker=FormatChecker(formats=("uuid",)),
    )
    return sorted(
        f"{'/'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message}"
        for error in validator.iter_errors(body)
    )


def assert_response_matches_contract(swagger, path, method, response):
    schema = response_schema(swagger, path, method, response.status_code)
    assert schema is not None, (
        f"{method} {path} declares no schema for status {response.status_code}"
    )
    errors = validation_errors(swagger, schema, response.json())
    assert not errors, (
        f"{method} {path} {response.status_code} does not match swagger.json:\n"
        + "\n".join(errors[:25])
    )


class Sanitizer:
    """Deterministically replace identifiers so fixtures carry no real data."""

    def __init__(self):
        self.uuids = {}

    def _uuid(self, value):
        key = value.lower()
        if key not in self.uuids:
            self.uuids[key] = f"00000000-0000-4000-8000-{len(self.uuids) + 1:012d}"
        return self.uuids[key]

    def __call__(self, value):
        if isinstance(value, dict):
            return {k: self(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self(v) for v in value]
        if isinstance(value, str):
            if _UUID_RE.match(value):
                return self._uuid(value)
            if _DATETIME_RE.match(value):
                return "2026-01-01T00:00:00Z"
            if _EMAIL_RE.match(value):
                return "user@example.com"
            if value.startswith("http://testserver/"):
                return _URL_UUID_RE.sub(lambda m: self._uuid(m.group(0)), value)
        return value


def capture_record(
    operation_id, method, path, response, *, request=None, note="", normalize=None
):
    """Build a fixture record; ``normalize`` can fix unordered collections."""
    body = response.json()
    if normalize is not None:
        body = normalize(body)
    return {
        "label": "captured",
        "operation": operation_id,
        "method": method.upper(),
        "path": path,
        "request": request or {},
        "note": note,
        "status": response.status_code,
        "body": Sanitizer()(body),
    }


def assert_capture(fixture_path, record):
    """Compare a sanitised capture with its fixture, or rewrite it on demand."""
    fixture_path = Path(fixture_path)
    rendered = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if os.environ.get(REGENERATE_ENV) == "1":
        fixture_path.parent.mkdir(parents=True, exist_ok=True)
        fixture_path.write_text(rendered)
        return
    assert fixture_path.exists(), (
        f"missing capture {fixture_path.name}; run with {REGENERATE_ENV}=1"
    )
    stored = json.loads(fixture_path.read_text())
    assert stored == json.loads(rendered), (
        f"{fixture_path.name} drifted from the live handler output; rerun with "
        f"{REGENERATE_ENV}=1 and review the diff"
    )
