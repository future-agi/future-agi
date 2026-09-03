from __future__ import annotations

import pytest

from tracer.services.clickhouse.v2.property_catalog.cursor import (
    PropertyCatalogCursorError,
    decode_property_catalog_cursor,
    encode_property_catalog_cursor,
)

SCOPE = {
    "principal_id": "user-1",
    "auth_type": "Token",
    "auth_id": "token-1",
    "organization_id": "11111111-1111-1111-1111-111111111111",
    "workspace_id": "22222222-2222-2222-2222-222222222222",
    "project_ids": [
        "44444444-4444-4444-4444-444444444444",
        "33333333-3333-3333-3333-333333333333",
    ],
    "agent_definition_id": "",
    "dataset_id": "",
}
QUERY = {
    "category": "custom_attribute",
    "source": "traces",
    "property_kind": "custom_attribute",
    "per_eval_config": False,
    "search": "Straße",
}
ORDER = (3, 1, "traces", "strasse", "Straße", "custom_attribute:Straße")
FINGERPRINT = "a" * 64


def _token(**overrides):
    values = {
        "scope": SCOPE,
        "query": QUERY,
        "page_size": 50,
        "catalog_epoch": 7,
        "catalog_revision": 42,
        "activation_fingerprint": FINGERPRINT,
        "order": ORDER,
    }
    values.update(overrides)
    return encode_property_catalog_cursor(**values)


def test_property_cursor_round_trips_immutable_revision(settings):
    settings.SECRET_KEY = "test-property-catalog-secret"

    cursor = decode_property_catalog_cursor(
        _token(), scope=SCOPE, query=QUERY, page_size=50
    )

    assert cursor.catalog_epoch == 7
    assert cursor.catalog_revision == 42
    assert cursor.activation_fingerprint == FINGERPRINT
    assert cursor.order == ORDER


def test_property_cursor_canonicalizes_project_order_and_unicode_search(settings):
    settings.SECRET_KEY = "test-property-catalog-secret"
    token = _token()
    reordered_scope = {
        **SCOPE,
        "project_ids": list(reversed(SCOPE["project_ids"])),
    }

    cursor = decode_property_catalog_cursor(
        token,
        scope=reordered_scope,
        query={**QUERY, "search": "STRASSE"},
        page_size=50,
    )

    assert cursor.order == ORDER


@pytest.mark.parametrize(
    ("scope", "query", "page_size"),
    [
        ({**SCOPE, "workspace_id": "other"}, QUERY, 50),
        (SCOPE, {**QUERY, "source": "sessions"}, 50),
        (SCOPE, {**QUERY, "role": "metric"}, 50),
        (SCOPE, QUERY, 25),
    ],
)
def test_property_cursor_rejects_scope_filter_and_page_size_replay(
    settings, scope, query, page_size
):
    settings.SECRET_KEY = "test-property-catalog-secret"

    with pytest.raises(PropertyCatalogCursorError) as exc_info:
        decode_property_catalog_cursor(
            _token(), scope=scope, query=query, page_size=page_size
        )

    assert exc_info.value.code == "cursor_mismatch"


def test_property_cursor_rejects_tampering(settings):
    settings.SECRET_KEY = "test-property-catalog-secret"
    token = _token()
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")

    with pytest.raises(PropertyCatalogCursorError) as exc_info:
        decode_property_catalog_cursor(tampered, scope=SCOPE, query=QUERY, page_size=50)

    assert exc_info.value.code == "invalid_cursor"


@pytest.mark.parametrize(
    "overrides",
    [
        {"page_size": 0},
        {"catalog_epoch": 0},
        {"catalog_revision": 0},
        {"activation_fingerprint": "not-a-sha"},
        {"order": (1, 2, "too", "short")},
    ],
)
def test_property_cursor_refuses_invalid_issuer_state(settings, overrides):
    settings.SECRET_KEY = "test-property-catalog-secret"

    with pytest.raises(ValueError):
        _token(**overrides)
