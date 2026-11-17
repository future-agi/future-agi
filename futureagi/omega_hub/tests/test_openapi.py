"""openapi.yaml documents every Hub route and method, and nothing that does not exist."""

import re
from pathlib import Path

import pytest
import yaml
from django.urls import get_resolver

OPENAPI = Path(__file__).resolve().parents[1] / "openapi.yaml"
METHODS = ("get", "post", "patch", "delete")


def _routes():
    def walk(resolver, prefix=""):
        for pattern in resolver.url_patterns:
            if hasattr(pattern, "url_patterns"):
                yield from walk(pattern, prefix + str(pattern.pattern))
            else:
                yield prefix + str(pattern.pattern), pattern.callback
    for route, callback in walk(get_resolver()):
        if route.startswith("omega-hub/"):
            path = "/" + re.sub(r"<(?:\w+:)?(\w+)>", lambda m: "{" + _camel(m.group(1)) + "}", route)
            methods = {m for m in METHODS if hasattr(callback.cls, m)}
            yield path, methods


def _camel(name):
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


@pytest.fixture(scope="module")
def spec():
    return yaml.safe_load(OPENAPI.read_text())


def test_every_route_and_method_is_documented(spec):
    documented = spec["paths"]
    routes = dict(_routes())
    assert routes, "no omega-hub routes resolved"
    for path, methods in routes.items():
        assert path in documented, path
        assert set(documented[path]) == methods, (path, set(documented[path]), methods)
    assert set(documented) == set(routes)


def test_no_conversation_paths(spec):
    for path in spec["paths"]:
        assert not re.search(r"comment|discussion|question|reply|thread", path), path


def test_no_operation_accepts_the_org_api_key_pair(spec):
    """An org key pair is not a person (SPEC §9.3); the Hub refuses it wherever an identity is needed."""
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            for requirement in operation.get("security", []):
                assert not {"apiKey", "secretKey"} & set(requirement), (method, path, requirement)
