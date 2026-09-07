"""Managed selection must not consume one control event per completed batch."""

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ActivationControlAction,
    ActivationControlEvent,
    ActivationControlUnavailable,
    ClickHouseActivationControlSelector,
    qualified_activation_sql,
)
from tracer.tests.test_property_catalog_reader_activation import (
    AT,
    SCOPE,
    Client,
    service,
    target,
)


def row(item):
    return {
        **asdict(item),
        "activation_sequence": item.catalog_revision,
        "latest_variants": 1,
    }


def test_thousands_of_qualified_revisions_do_not_append_control_events(tmp_path):
    client = Client()
    first = service(client, tmp_path).reconcile(SCOPE)
    client.targets = [target(index) for index in range(1, 10001)]
    result = service(client, tmp_path).reconcile(SCOPE)
    assert result.selected_target == target(10000)
    assert result.event is None
    assert len(client.events) == len(client.attempts) == 1
    assert first.event.action is ActivationControlAction.FOLLOW


class Executor:
    def __init__(self, event, qualified):
        self.event, self.qualified, self.calls = event, qualified, []

    def execute(self, sql, params, **kwargs):
        self.calls.append((sql, params, kwargs))
        if "catalog_control_result_limit" in params:
            return SimpleNamespace(data=[self.event.as_row()])
        assert params["catalog_follow_anchor"] == 1
        assert params["catalog_follow_epoch"] == 7
        assert kwargs["settings"]["max_result_rows"] == 4
        return SimpleNamespace(data=self.qualified)


def event(action=ActivationControlAction.FOLLOW):
    return ActivationControlEvent.create(
        control_sequence=1,
        request_id="00000000-0000-0000-0000-000000000100",
        action=action,
        target=target(),
        previous_control_sha256="0" * 64,
        controlled_at=AT,
    )


@pytest.mark.parametrize(
    "deployment,database",
    [
        ("prod", "property_catalog"),
        ("dev", "property_catalog_dev_oss"),
    ],
)
def test_reader_follows_only_new_qualified_builds_in_the_same_installation(
    deployment, database
):
    executor = Executor(event(), [row(target()), row(target(9999)), row(target(10000))])
    selector = ClickHouseActivationControlSelector(
        executor, database=database, deployment=deployment
    )
    assert selector.select_target(scope=asdict(SCOPE), timeout_ms=1000) == target(10000)
    assert len(executor.calls) == 2
    assert executor.calls[-1][0] == qualified_activation_sql(
        database, deployment=deployment, follow=True
    )


@pytest.mark.parametrize(
    "qualified",
    [
        [],
        [row(target(2))],
        [row(target()), row(target(2, projection=4))],
        [row(target()), {**row(target(2)), "latest_variants": 2}],
        [row(target()), row(target(2)), row(target(3)), row(target(4))],
    ],
)
def test_missing_anchor_conflict_or_incompatible_format_cannot_follow(qualified):
    selector = ClickHouseActivationControlSelector(
        Executor(event(), qualified), database="property_catalog"
    )
    with pytest.raises(ActivationControlUnavailable):
        selector.select_target(scope=asdict(SCOPE), timeout_ms=1000)


@pytest.mark.parametrize(
    "action",
    [
        ActivationControlAction.ACTIVATE,
        ActivationControlAction.ROLLBACK,
        ActivationControlAction.DISABLE,
    ],
)
def test_explicit_control_heads_never_implicitly_follow(action):
    executor = Executor(event(action), [row(target(2))])
    selector = ClickHouseActivationControlSelector(
        executor, database="property_catalog"
    )
    if action is ActivationControlAction.DISABLE:
        with pytest.raises(ActivationControlUnavailable, match="unavailable"):
            selector.select_target(scope=asdict(SCOPE), timeout_ms=1000)
    else:
        assert selector.select_target(scope=asdict(SCOPE), timeout_ms=1000) == target()
    assert len(executor.calls) == 1


def test_follow_sql_returns_anchor_and_two_newest_without_a_lifetime_row_cap():
    sql = qualified_activation_sql("property_catalog", follow=True)
    assert "AND catalog_epoch = %(catalog_follow_epoch)s" in sql
    assert "catalog_revision = %(catalog_follow_anchor)s" in sql
    assert "ORDER BY activation_sequence DESC" in sql
    assert "LIMIT 2" in sql and sql.rstrip().endswith("LIMIT 4")
