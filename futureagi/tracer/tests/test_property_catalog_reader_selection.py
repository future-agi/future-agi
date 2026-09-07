"""Reader pending means proven automatic advancement, not a pinned target."""

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog import activation_control as control
from tracer.tests.test_property_catalog_follow_selection import (
    Executor as FollowExecutor,
)
from tracer.tests.test_property_catalog_follow_selection import (
    event,
    row,
)
from tracer.tests.test_property_catalog_initial_reader_publication import (
    HISTORY,
    initial_event,
    manual_event,
)
from tracer.tests.test_property_catalog_managed_reader_admission import Executor
from tracer.tests.test_property_catalog_reader_activation import SCOPE, target


@pytest.mark.parametrize("deployment", ["dev", "prod"])
@pytest.mark.parametrize("action", ["follow", "activate", "rollback"])
def test_selection_binds_advancement_to_the_same_control_head(deployment, action):
    executor = FollowExecutor(
        event(control.ActivationControlAction(action)), [row(target()), row(target(2))]
    )
    selection = control.select_reader_activation(
        control.ClickHouseActivationControlSelector(
            executor,
            database="property_catalog"
            if deployment == "prod"
            else "property_catalog_dev_oss",
            deployment=deployment,
        ),
        scope=asdict(SCOPE),
        timeout_ms=1000,
    )
    assert selection.follows_latest is (action == "follow")
    assert selection.target == target(2 if action == "follow" else 1)
    assert selection.follow_anchor == (target() if action == "follow" else None)
    assert len(executor.calls) == (2 if action == "follow" else 1)


def production_selector(executor, monkeypatch):
    # A separately provisioned database is an infrastructure choice, not an
    # application revision. Every proof query must use this production binding.
    database = "isolated_catalog_test"
    monkeypatch.setenv("PROPERTY_CATALOG_PRODUCTION_DATABASE", database)
    return control.activation_control_selector_for_deployment(
        executor, database=database, deployment="prod", managed=False
    )


def test_production_initial_follow_is_pending_without_oss_mode(monkeypatch):
    prepared = initial_event(database="isolated_catalog_test").as_row()
    executor = Executor([[prepared], [], [], [prepared]])
    selector = production_selector(executor, monkeypatch)
    with pytest.raises(control.ActivationControlBootstrapPending):
        selector.select_for_read(scope=asdict(SCOPE), timeout_ms=1000)
    assert len(executor.calls) == 4
    assert all("`isolated_catalog_test`." in call[0] for call in executor.calls)


@pytest.mark.parametrize("history", [HISTORY, [{"bad": 1}], RuntimeError("offline")])
def test_production_incomplete_or_invalid_history_is_not_bootstrap(
    monkeypatch, history
):
    prepared = initial_event(database="isolated_catalog_test").as_row()
    executor = Executor([[prepared], [], history, [prepared], []])
    selector = production_selector(executor, monkeypatch)
    with pytest.raises(control.ActivationControlUnavailable) as error:
        selector.select_for_read(scope=asdict(SCOPE), timeout_ms=1000)
    assert not isinstance(error.value, control.ActivationControlBootstrapPending)


@pytest.mark.parametrize("action", ["disable", "rollback"])
def test_production_operator_head_wins_during_initial_publication(monkeypatch, action):
    prepared = initial_event(database="isolated_catalog_test")
    changed = manual_event(prepared, action)
    executor = Executor(
        [[prepared.as_row()], [], [], [prepared.as_row(), changed.as_row()]]
    )
    selector = production_selector(executor, monkeypatch)
    if action == "disable":
        with pytest.raises(control.ActivationControlUnavailable) as error:
            selector.select_for_read(scope=asdict(SCOPE), timeout_ms=1000)
        assert error.value.reason == "control_disabled"
    else:
        selection = selector.select_for_read(scope=asdict(SCOPE), timeout_ms=1000)
        assert selection.target == target() and selection.follows_latest is False


def test_production_local_absence_without_control_is_not_auto_enable(monkeypatch):
    executor = Executor([[]])
    with pytest.raises(control.ActivationControlUnavailable) as error:
        production_selector(executor, monkeypatch).select_for_read(
            scope=asdict(SCOPE), timeout_ms=1000
        )
    assert error.value.reason == "control_missing"
    assert not isinstance(error.value, control.ActivationControlBootstrapPending)


def test_legacy_target_only_selector_does_not_imply_follow():
    selection = control.select_reader_activation(
        SimpleNamespace(select_target=lambda **_: target()),
        scope=asdict(SCOPE),
        timeout_ms=1000,
    )
    assert selection == control.ReaderActivationSelection(target(), False)


def test_invalid_extended_selection_does_not_fall_back_to_target():
    selector = SimpleNamespace(
        select_for_read=lambda **_: {"target": target(), "follows_latest": True},
        select_target=lambda **_: pytest.fail("must not ignore corrupt selection"),
    )
    with pytest.raises(control.ActivationControlUnavailable) as error:
        control.select_reader_activation(selector, scope=asdict(SCOPE), timeout_ms=1000)
    assert error.value.reason == "control_selection_invalid"


@pytest.mark.parametrize(
    "epoch,revision,allowed",
    [
        (7, 1, True),
        (7, 2, True),
        (7, 3, False),
        (7, 4, False),
        (7, 0, False),
        (8, 2, False),
        (7, True, False),
        (7, 1.0, False),
    ],
)
def test_follow_cursor_history_is_bounded_by_the_validated_anchor(
    epoch, revision, allowed
):
    selection = control.ReaderActivationSelection(target(3), True, target())
    assert selection.allows_previous_revision(epoch=epoch, revision=revision) is allowed


@pytest.mark.parametrize("follow", [False, True])
def test_selection_without_anchor_never_authorizes_older_cursors(follow):
    selection = control.ReaderActivationSelection(target(3), follow)
    assert not selection.allows_previous_revision(epoch=7, revision=2)


@pytest.mark.parametrize(
    "anchor,follow",
    [
        (target(), False),
        (target(4), True),
        (target(epoch=8), True),
        (target(projection=4), True),
    ],
)
def test_follow_anchor_must_match_control_scope_format_and_order(anchor, follow):
    with pytest.raises(ValueError, match="same FOLLOW"):
        control.ReaderActivationSelection(target(3), follow, anchor)
