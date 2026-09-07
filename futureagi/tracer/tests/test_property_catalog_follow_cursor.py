"""Automatic publication must not move a signed pagination snapshot mid-list."""

from dataclasses import asdict
from datetime import UTC, datetime

import pytest

from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ActivationControlAction,
    ActivationControlEvent,
    ActivationControlTarget,
    ClickHouseActivationControlSelector,
)
from tracer.services.clickhouse.v2.property_catalog.cursor import (
    encode_property_catalog_cursor,
)
from tracer.tests import test_unified_property_catalog_reader as definitions
from tracer.tests import test_unified_property_catalog_value_reader as values


def target(row):
    return ActivationControlTarget(
        organization_id=definitions.ORG_ID,
        workspace_id=definitions.WORKSPACE_ID,
        **{
            key: row[key]
            for key in (
                "catalog_epoch",
                "catalog_revision",
                "projection_version",
                "build_token",
                "activation_sha256",
            )
        },
    )


def make_read(
    family,
    *,
    continued=True,
    action="follow",
    anchor_revision=16,
    latest_revision=18,
    latest_epoch=3,
    projection=1,
    returned=None,
):
    subject = definitions if family == "definitions" else values
    original = subject._activation_row()
    anchor = target(subject._activation_row(catalog_revision=anchor_revision))
    latest = target(
        subject._activation_row(
            catalog_epoch=latest_epoch,
            catalog_revision=latest_revision,
            projection_version=projection,
            activation_sha256="c" * 64
            if latest_revision != 17
            else subject.ACTIVATION_SHA,
        )
    )
    event = ActivationControlEvent.create(
        control_sequence=1,
        request_id="00000000-0000-0000-0000-000000000100",
        action=ActivationControlAction(action),
        target=anchor if action == "follow" else latest,
        previous_control_sha256="0" * 64,
        controlled_at=datetime(2026, 9, 5, tzinfo=UTC),
    )
    control_rows = [[event.as_row()]]
    if action == "follow":
        control_rows.append(
            [
                {
                    **asdict(item),
                    "activation_sequence": item.catalog_revision,
                    "latest_variants": 1,
                }
                for item in (anchor, latest)
            ]
        )
    selector = ClickHouseActivationControlSelector(
        subject.FakeExecutor(control_rows),
        database="property_catalog_dev_test",
        deployment="dev",
        managed=True,
    )
    selected = (
        original
        if continued
        else subject._activation_row(
            catalog_revision=latest_revision, activation_sha256=latest.activation_sha256
        )
    )
    responses = [[selected] if returned is None else returned]
    responses += (
        [[subject._conflict_row()], []]
        if family == "definitions"
        else [
            [subject._definition_row(attribute_types=("string",))],
            [{"value_conflicts": 0}],
            [],
        ]
    )
    executor = subject.FakeExecutor(responses)
    reader_class = (
        subject.PropertyCatalogReader
        if family == "definitions"
        else subject.PropertyCatalogValueReader
    )
    reader = reader_class(
        executor,
        catalog_database="property_catalog_dev_test",
        activation_selector=selector,
    )
    kwargs = {
        "scope": subject._scope(),
        "query": subject.QUERY if family == "definitions" else subject._query(),
        "page_size": 2,
    }
    if continued:
        cursor_args = {
            **kwargs,
            "catalog_epoch": original["catalog_epoch"],
            "catalog_revision": original["catalog_revision"],
            "activation_fingerprint": original["activation_sha256"],
        }
        if family == "definitions":
            token = encode_property_catalog_cursor(
                **cursor_args,
                order=(1, 1, "traces", "plan", "plan", "custom_attribute:plan"),
            )
        else:
            token = subject.encode_property_catalog_value_cursor(
                **cursor_args,
                window_start=values.WINDOW_START,
                window_end=values.WINDOW_END,
                order=(1, "0" * 64),
            )
        kwargs["cursor_token"] = token
    elif family == "values":
        kwargs.update(window_start=values.WINDOW_START, window_end=values.WINDOW_END)
    return reader, executor, kwargs


@pytest.mark.parametrize("family", ["definitions", "values"])
@pytest.mark.parametrize("continued", [False, True])
def test_follow_advances_new_lists_but_preserves_signed_cursor(
    settings, family, continued
):
    settings.SECRET_KEY = "follow-cursor-unit-only"
    reader, executor, kwargs = make_read(family, continued=continued)
    page = reader.read_page(**kwargs)
    expected_revision = 17 if continued else 18
    assert page.catalog_revision == expected_revision
    assert executor.calls[0]["params"]["catalog_revision"] == expected_revision
    assert executor.calls[1]["params"]["catalog_revision"] == expected_revision


@pytest.mark.parametrize("family", ["definitions", "values"])
def test_actual_first_page_cursor_survives_follow_publication(settings, family):
    settings.SECRET_KEY = "follow-cursor-unit-only"
    reader, executor, kwargs = make_read(family, continued=False, latest_revision=17)
    if family == "definitions":
        rows = [
            definitions._property_row("customer." + name, 1) for name in ("a", "b", "c")
        ]
    else:
        rows = sorted(
            [values._value_row(name) for name in ("a", "b", "c")],
            key=lambda row: (row["attribute_type_rank"], row["value_fingerprint"]),
        )
    executor.responses[-1] = rows
    first = reader.read_page(**kwargs)
    assert first.has_more and first.next_cursor
    second_reader, second_executor, second_kwargs = make_read(family)
    second_executor.responses[-1] = rows[-1:]
    second_kwargs["cursor_token"] = first.next_cursor
    second = second_reader.read_page(**second_kwargs)
    assert second.catalog_revision == first.catalog_revision == 17
    assert second.activation_fingerprint == first.activation_fingerprint
    assert not second.has_more and second.next_cursor is None
    if family == "definitions":
        assert [item["name"] for item in first.metrics + second.metrics] == [
            "customer.a",
            "customer.b",
            "customer.c",
        ]
    else:
        assert [item.value_fingerprint for item in first.values + second.values] == [
            row["value_fingerprint"] for row in rows
        ]


@pytest.mark.parametrize("family", ["definitions", "values"])
@pytest.mark.parametrize(
    "selection",
    [
        {"action": "activate"},
        {"action": "rollback"},
        {"action": "disable"},
        {"anchor_revision": 18, "latest_revision": 19},
        {"latest_revision": 16},
        {"latest_epoch": 4},
        {"projection": 2},
    ],
)
def test_cursor_does_not_override_current_control_authority(
    settings, family, selection
):
    settings.SECRET_KEY = "follow-cursor-unit-only"
    reader, executor, kwargs = make_read(family, **selection)
    with pytest.raises(definitions.PropertyCatalogUnavailable):
        reader.read_page(**kwargs)
    assert len(executor.calls) <= 1


@pytest.mark.parametrize("family", ["definitions", "values"])
@pytest.mark.parametrize(
    "corrupt",
    [
        None,
        {"status": "disabled"},
        {"qualified_at": None},
        {"latest_state_variants": 2},
        {"latest_reservation_variants": 2},
        {"projection_version": 2},
        {"catalog_epoch": 4},
        {"catalog_revision": 16},
        {"activation_sha256": "d" * 64},
    ],
)
def test_pinned_cursor_still_requires_its_exact_qualified_snapshot(
    settings, family, corrupt
):
    settings.SECRET_KEY = "follow-cursor-unit-only"
    subject = definitions if family == "definitions" else values
    returned = [] if corrupt is None else [subject._activation_row(**corrupt)]
    reader, executor, kwargs = make_read(family, returned=returned)
    with pytest.raises(definitions.PropertyCatalogUnavailable):
        reader.read_page(**kwargs)
    assert len(executor.calls) == 1
