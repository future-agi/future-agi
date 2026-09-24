from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tracer.services.clickhouse.v2.property_catalog.value_cursor import (
    PropertyCatalogValueCursorError,
    decode_property_catalog_value_cursor,
    encode_property_catalog_value_cursor,
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
}
QUERY = {
    "property_id": "custom_attribute:customer.plan",
    "source": "traces",
    "attribute_type": "string",
    "search": "Straße",
}
WINDOW_START = datetime(2026, 8, 1, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 14, 12, 0, 0, 123456, tzinfo=UTC)
ACTIVATION_SHA = "a" * 64
ORDER = (1, "b" * 64)


def _token(**overrides):
    values = {
        "scope": SCOPE,
        "query": QUERY,
        "page_size": 10,
        "catalog_epoch": 3,
        "catalog_revision": 17,
        "activation_fingerprint": ACTIVATION_SHA,
        "window_start": WINDOW_START,
        "window_end": WINDOW_END,
        "order": ORDER,
    }
    values.update(overrides)
    return encode_property_catalog_value_cursor(**values)


def test_value_cursor_round_trips_snapshot_window_and_keyset(settings):
    settings.SECRET_KEY = "property-value-cursor-secret"

    cursor = decode_property_catalog_value_cursor(
        _token(), scope=SCOPE, query=QUERY, page_size=10
    )

    assert cursor.catalog_epoch == 3
    assert cursor.catalog_revision == 17
    assert cursor.activation_fingerprint == ACTIVATION_SHA
    assert cursor.window_start == WINDOW_START
    assert cursor.window_end == WINDOW_END
    assert cursor.order == ORDER


def test_value_cursor_canonicalizes_project_order_and_search(settings):
    settings.SECRET_KEY = "property-value-cursor-secret"

    cursor = decode_property_catalog_value_cursor(
        _token(),
        scope={**SCOPE, "project_ids": list(reversed(SCOPE["project_ids"]))},
        query={**QUERY, "search": "STRASSE"},
        page_size=10,
    )

    assert cursor.order == ORDER


@pytest.mark.parametrize(
    ("scope", "query", "page_size"),
    [
        ({**SCOPE, "workspace_id": "other"}, QUERY, 10),
        (SCOPE, {**QUERY, "property_id": "custom_attribute:other"}, 10),
        (SCOPE, {**QUERY, "attribute_type": "number"}, 10),
        (SCOPE, {**QUERY, "search": "other"}, 10),
        (SCOPE, QUERY, 20),
    ],
)
def test_value_cursor_rejects_scope_property_type_search_and_page_replay(
    settings, scope, query, page_size
):
    settings.SECRET_KEY = "property-value-cursor-secret"

    with pytest.raises(PropertyCatalogValueCursorError) as exc_info:
        decode_property_catalog_value_cursor(
            _token(), scope=scope, query=query, page_size=page_size
        )

    assert exc_info.value.code == "cursor_mismatch"


def test_value_cursor_rejects_tampering(settings):
    settings.SECRET_KEY = "property-value-cursor-secret"
    token = _token()
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")

    with pytest.raises(PropertyCatalogValueCursorError) as exc_info:
        decode_property_catalog_value_cursor(
            tampered, scope=SCOPE, query=QUERY, page_size=10
        )

    assert exc_info.value.code == "invalid_cursor"


@pytest.mark.parametrize(
    "overrides",
    [
        {"page_size": 0},
        {"catalog_epoch": 0},
        {"catalog_revision": 0},
        {"activation_fingerprint": "not-a-sha"},
        {"window_end": WINDOW_START},
        {"order": (0, "a" * 64)},
        {"order": (1, "not-a-sha")},
    ],
)
def test_value_cursor_refuses_invalid_issuer_state(settings, overrides):
    settings.SECRET_KEY = "property-value-cursor-secret"

    with pytest.raises((ValueError, PropertyCatalogValueCursorError)):
        _token(**overrides)
