"""Shared delegated batch wire grammar: consume the canonical JSON vectors."""

import pytest

from tfc.service_envelope import EnvelopeRejected, sign_request
from tfc.tests.test_service_envelope import KEYS, VECTORS, verify

VALID = {v["name"]: v for v in VECTORS["valid_macs"]}
BATCH = [v for v in VALID.values() if v["name"].startswith("gw-batch-")]
REJECTIONS = [
    v
    for v in VECTORS["rejections"]
    if v["name"].startswith(("gw-batch-", "gw-chat-method-"))
]


def test_batch_vectors_are_present():
    assert {v["name"] for v in BATCH} == {
        "gw-batch-submit",
        "gw-batch-list",
        "gw-batch-get",
        "gw-batch-cancel",
    }
    assert len(REJECTIONS) >= 20


@pytest.mark.parametrize("vector", BATCH, ids=lambda v: v["name"])
def test_valid_batch_vector_roundtrip(vector):
    c = vector["claims"]
    assert verify(vector) == c
    assert (
        sign_request(
            keys=KEYS,
            issuer=c["iss"],
            audience=c["aud"],
            method=c["method"],
            route=c["route"],
            body=vector["body"].encode(),
            org=c["org"],
            workspace=c["workspace"],
            user=c["user"],
            via=c["via"],
            scope=c["scope"],
            grant=vector["accompanying_grant"],
            now=int(c["iat"]),
            identifier=c["id"],
            kid=c["kid"],
        )
        == vector["token"]
    )


@pytest.mark.parametrize("vector", REJECTIONS, ids=lambda v: v["name"])
def test_invalid_batch_method_and_route_vectors(vector):
    with pytest.raises(EnvelopeRejected):
        verify(
            VALID[vector["source"]],
            **{k: value for k, value in vector.items() if k not in {"source", "name"}},
        )
