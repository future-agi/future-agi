"""A workspace can retain safe snapshot results while its inventory changes."""

from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace

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
from tracer.services.clickhouse.v2.property_catalog.reader import (
    PropertyCatalogBootstrapPending,
)
from tracer.services.clickhouse.v2.property_catalog.value_reader import (
    PropertyCatalogValuePending,
)
from tracer.tests import test_unified_property_catalog_reader as definitions
from tracer.tests import test_unified_property_catalog_value_reader as values


@pytest.mark.parametrize("covered", [(), (definitions.OTHER_PROJECT_ID,)])
def test_workspace_refresh_never_widens_to_new_or_removed_projects(settings, covered):
    settings.SECRET_KEY = "workspace-refresh-unit-only"
    executor = definitions.FakeExecutor(
        [
            [definitions._activation_row(covered_project_ids=covered)],
            [definitions._conflict_row()],
            [],
        ]
    )
    page = definitions.PropertyCatalogReader(
        executor, catalog_database="property_catalog_dev_test"
    ).read_page(
        scope=definitions._scope(workspace_scope=True),
        query=definitions.QUERY,
        page_size=50,
    )
    params = executor.calls[1]["params"]
    assert params["catalog_project_ids"] == ()
    assert params["catalog_include_workspace_default"] == 1
    assert params["catalog_include_all_projects"] == 0
    assert page.activation_fingerprint == definitions.ACTIVATION_SHA


@pytest.mark.parametrize("covered", [(), (values.OTHER_PROJECT_ID,)])
def test_workspace_value_snapshot_with_no_visible_project_never_queries_values(
    settings, covered
):
    settings.SECRET_KEY = "workspace-refresh-unit-only"
    executor = values.FakeExecutor(
        [[values._activation_row(covered_project_ids=covered)]]
    )
    page = values._read(
        values._reader(executor), scope=values._scope(workspace_scope=True)
    )
    assert not page.values and not page.has_more and page.next_cursor is None
    assert page.activation_fingerprint == values.ACTIVATION_SHA
    assert len(executor.calls) == 1


@pytest.mark.parametrize("field", ["principal_id", "auth_type"])
@pytest.mark.parametrize("family", ["definitions", "values"])
def test_incomplete_workspace_snapshot_requires_authenticated_scope(
    settings, family, field
):
    settings.SECRET_KEY = "workspace-refresh-unit-only"
    subject = definitions if family == "definitions" else values
    scope = subject._scope(workspace_scope=True)
    scope[field] = ""
    executor = subject.FakeExecutor([[subject._activation_row(covered_project_ids=())]])
    if family == "values":
        # Value scope validation rejects an unauthenticated request before any
        # database access, which is stronger than the coverage rejection below.
        with pytest.raises(
            ValueError, match="authenticated principal scope is required"
        ):
            subject._read(subject._reader(executor), scope=scope)
        assert executor.calls == []
        return
    with pytest.raises(definitions.PropertyCatalogUnavailable) as error:
        subject.PropertyCatalogReader(
            executor, catalog_database="property_catalog_dev_test"
        ).read_page(
            scope=scope,
            query=subject.QUERY,
            page_size=50,
        )
    assert error.value.reason == "activation_scope_incomplete"
    assert len(executor.calls) == 1


@pytest.mark.parametrize("family", ["definitions", "values"])
def test_workspace_refresh_does_not_hide_corrupt_activation(settings, family):
    settings.SECRET_KEY = "workspace-refresh-unit-only"
    subject = definitions if family == "definitions" else values
    executor = subject.FakeExecutor(
        [[subject._activation_row(covered_project_ids=(), latest_state_variants=2)]]
    )
    with pytest.raises(definitions.PropertyCatalogUnavailable) as error:
        if family == "definitions":
            subject.PropertyCatalogReader(
                executor, catalog_database="property_catalog_dev_test"
            ).read_page(
                scope=subject._scope(workspace_scope=True),
                query=subject.QUERY,
                page_size=50,
            )
        else:
            subject._read(
                subject._reader(executor), scope=subject._scope(workspace_scope=True)
            )
    assert error.value.reason == "activation_conflict"
    assert len(executor.calls) == 1


@pytest.mark.parametrize("family", ["definitions", "values"])
@pytest.mark.parametrize(
    "selection", ["none", "legacy", "follow", "activate", "rollback"]
)
@pytest.mark.parametrize("continued", [False, True])
def test_project_coverage_pending_is_follow_first_page_only(
    settings, family, selection, continued
):
    settings.SECRET_KEY = "workspace-refresh-unit-only"
    subject = definitions if family == "definitions" else values
    row = subject._activation_row(covered_project_ids=())
    executor = subject.FakeExecutor([[row]])
    target = ActivationControlTarget(
        organization_id=subject.ORG_ID,
        workspace_id=subject.WORKSPACE_ID,
        catalog_epoch=row["catalog_epoch"],
        catalog_revision=row["catalog_revision"],
        projection_version=row["projection_version"],
        build_token=row["build_token"],
        activation_sha256=row["activation_sha256"],
    )
    selector = None
    control_executor = None
    if selection == "legacy":
        selector = SimpleNamespace(select_target=lambda **kwargs: target)
    elif selection != "none":
        event = ActivationControlEvent.create(
            control_sequence=1,
            request_id="00000000-0000-0000-0000-000000000100",
            action=ActivationControlAction(selection),
            target=target,
            previous_control_sha256="0" * 64,
            controlled_at=datetime(2026, 9, 5, tzinfo=UTC),
        )
        results = [[event.as_row()]]
        if selection == "follow":
            results.append(
                [{**asdict(target), "activation_sequence": 1, "latest_variants": 1}]
            )
        control_executor = subject.FakeExecutor(results)
        selector = ClickHouseActivationControlSelector(
            control_executor,
            database="property_catalog_dev_test",
            deployment="dev",
            managed=True,
        )
    scope = subject._scope()
    query = subject.QUERY if family == "definitions" else subject._query()
    kwargs = {"scope": scope, "query": query, "page_size": 2}
    if continued:
        cursor_kwargs = {
            **kwargs,
            "catalog_epoch": row["catalog_epoch"],
            "catalog_revision": row["catalog_revision"],
            "activation_fingerprint": row["activation_sha256"],
        }
        if family == "definitions":
            token = encode_property_catalog_cursor(
                **cursor_kwargs,
                order=(1, 1, "traces", "plan", "plan", "custom_attribute:plan"),
            )
        else:
            token = values.encode_property_catalog_value_cursor(
                **cursor_kwargs,
                window_start=values.WINDOW_START,
                window_end=values.WINDOW_END,
                order=(1, "0" * 64),
            )
        kwargs["cursor_token"] = token
    elif family == "values":
        kwargs.update(window_start=values.WINDOW_START, window_end=values.WINDOW_END)
    reader_class = (
        subject.PropertyCatalogReader
        if family == "definitions"
        else subject.PropertyCatalogValueReader
    )
    pending_class = (
        PropertyCatalogBootstrapPending
        if family == "definitions"
        else PropertyCatalogValuePending
    )
    reader = reader_class(
        executor,
        catalog_database="property_catalog_dev_test",
        activation_selector=selector,
    )
    with pytest.raises(definitions.PropertyCatalogUnavailable) as error:
        reader.read_page(**kwargs)
    should_wait = selection == "follow" and not continued
    assert isinstance(error.value, pending_class) is should_wait
    assert error.value.reason == (
        "activation_scope_pending" if should_wait else "activation_scope_incomplete"
    )
    assert len(executor.calls) == 1  # No raw fallback or uncovered property query.
    if control_executor is not None:
        assert len(control_executor.calls) == (2 if selection == "follow" else 1)
