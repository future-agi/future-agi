"""TH-8216 B1 D7: swagger.json declares the API-key pair as one AND requirement.

``APIKeyAuthentication`` needs X-Api-Key and X-Secret-Key together
(``accounts/tests/test_th8216b_api_key_pair.py``). A Swagger 2.0 ``security``
list is an OR of requirement objects, and the keys inside one object are ANDed.

Only operations whose view authenticates with ``APIKeyAuthentication`` must
inherit that requirement. Views that never read API keys (webhooks with
``authentication_classes = []``, Basic-auth alternatives) may later declare a
truthful override; whether they should is auth-owner decision O4.
"""

import copy

import pytest
from drf_yasg import openapi
from drf_yasg.generators import OpenAPISchemaGenerator

from accounts.authentication import APIKeyAuthentication
from tfc.tests.openapi_parity import load_swagger

KEY_PAIR = {"X-Api-Key": [], "X-Secret-Key": []}
WEBHOOK_OPERATION = ("/agentcc/webhook/logs/", "post")
API_KEY_OPERATION = ("/agent-playground/graphs/", "get")


@pytest.fixture(scope="module")
def authenticators():
    """``(path, method) -> authentication_classes`` for every routed operation,
    enumerated by drf-yasg exactly as ``generate_swagger`` does."""
    generator = OpenAPISchemaGenerator(openapi.Info(title="", default_version=""))
    result = {}
    for path, (view_cls, methods) in generator.get_endpoints(None).items():
        for method, callback in methods:
            initkwargs = getattr(callback, "initkwargs", {})
            result[(path, method.lower())] = tuple(
                initkwargs.get(
                    "authentication_classes", view_cls.authentication_classes
                )
            )
    return result


def _uses_api_keys(authenticators, path, method):
    # Operations not routed under the test settings (EE/cloud) fail closed.
    classes = authenticators.get((path, method), (APIKeyAuthentication,))
    return any(issubclass(cls, APIKeyAuthentication) for cls in classes)


def _api_key_overrides(swagger, authenticators):
    return [
        f"{method.upper()} {path}"
        for path, item in swagger["paths"].items()
        for method, op in item.items()
        if method != "parameters"
        and "security" in op
        and _uses_api_keys(authenticators, path, method)
    ]


def test_security_declares_the_key_pair_as_one_requirement():
    swagger = load_swagger()

    assert swagger["security"] == [KEY_PAIR]
    assert swagger["securityDefinitions"] == {
        "X-Api-Key": {"type": "apiKey", "in": "header", "name": "X-Api-Key"},
        "X-Secret-Key": {"type": "apiKey", "in": "header", "name": "X-Secret-Key"},
    }


def test_no_api_key_operation_overrides_the_global_requirement(authenticators):
    assert _api_key_overrides(load_swagger(), authenticators) == []


def test_override_guard_only_covers_api_key_operations(authenticators):
    """The guard resolves real views: a webhook override is allowed (O4), an
    override on an API-key operation is not."""
    assert not _uses_api_keys(authenticators, *WEBHOOK_OPERATION)
    assert _uses_api_keys(authenticators, *API_KEY_OPERATION)

    swagger = copy.deepcopy(load_swagger())
    webhook_path, webhook_method = WEBHOOK_OPERATION
    swagger["paths"][webhook_path][webhook_method]["security"] = []
    assert _api_key_overrides(swagger, authenticators) == []

    key_path, key_method = API_KEY_OPERATION
    swagger["paths"][key_path][key_method]["security"] = []
    assert _api_key_overrides(swagger, authenticators) == [
        f"{key_method.upper()} {key_path}"
    ]
