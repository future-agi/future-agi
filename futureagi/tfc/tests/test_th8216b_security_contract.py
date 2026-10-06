"""TH-8216 B1 D7: swagger.json declares the API-key pair as one AND requirement.

``APIKeyAuthentication`` needs X-Api-Key and X-Secret-Key together
(``accounts/tests/test_th8216b_api_key_pair.py``). A Swagger 2.0 ``security``
list is an OR of requirement objects, and the keys inside one object are ANDed.
"""

from tfc.tests.openapi_parity import load_swagger

KEY_PAIR = {"X-Api-Key": [], "X-Secret-Key": []}


def test_security_declares_the_key_pair_as_one_requirement():
    swagger = load_swagger()

    assert swagger["security"] == [KEY_PAIR]
    assert swagger["securityDefinitions"] == {
        "X-Api-Key": {"type": "apiKey", "in": "header", "name": "X-Api-Key"},
        "X-Secret-Key": {"type": "apiKey", "in": "header", "name": "X-Secret-Key"},
    }


def test_no_operation_overrides_the_global_requirement():
    swagger = load_swagger()

    overrides = [
        f"{method.upper()} {path}"
        for path, item in swagger["paths"].items()
        for method, op in item.items()
        if method != "parameters" and "security" in op
    ]

    assert overrides == []
