"""XP-06 WT-21b and the writer's one lock order (webhooks DESIGN.md section 14.3).

Real Postgres, threads, each with its own connection and transaction.

- **20 parallel moves of Production.** Before the head table, two concurrent moves locked different version rows and
  both committed, leaving the label on two versions. Through the one writer they serialize on the label's head row:
  exactly one version holds it, and the 20 events form a chain (each ``from`` is the previous ``to``) with revisions
  n+1..n+20.
- **No deadlock between the writer's entry points** (``model_hub.services.prompt_locks``). Each case forces the
  interleaving that deadlocked before the lock order: the first writer holds its
  version row(s) and stops just before the heads; the second starts and must be seen waiting on a lock; only then does
  the first go on. Both orders of each pair run. Before the fix, a move locked the head first and then waited on the
  version for its foreign-key check, so Postgres aborted one side with "deadlock detected".
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from django.db import connection

from model_hub.models.run_prompt import PromptVersion
from model_hub.services import prompt_label as writer
from model_hub.tests.prompt_webhook_helpers import (  # noqa: F401 - fixture
    holders,
    label_events,
    make_template,
    make_version,
    system_labels,
    webhook_endpoint,
)

MOVES = 20
HOLD_S = 0.05


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("webhook_endpoint")
def test_twenty_parallel_moves_leave_one_holder_and_a_revision_chain(
    organization, workspace, user, monkeypatch
):
    t = make_template(organization, workspace, user=user)
    versions = [make_version(t, n) for n in range(1, MOVES + 2)]
    P = system_labels()["Production"]
    writer.move_label(versions[0], P)
    start_rev = label_events(t, "Production")[-1]["revision"]

    # Each writer holds its lock a little, so the 20 transactions really overlap and must wait on each other.
    real_apply = writer._apply

    def slow_apply(*args, **kwargs):
        time.sleep(HOLD_S)
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(writer, "_apply", slow_apply)
    gate = threading.Barrier(MOVES)
    errors: list[BaseException] = []

    def move(v):
        try:
            gate.wait()
            writer.move_label(v, P)
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)
        finally:
            connection.close()

    threads = [threading.Thread(target=move, args=(v,)) for v in versions[1:]]
    began = time.monotonic()
    for th in threads:
        th.start()
    for th in threads:
        th.join(60)
    elapsed = time.monotonic() - began
    assert errors == []
    assert elapsed >= MOVES * HOLD_S  # serialized on the head row, not run side by side

    chain = sorted(label_events(t, "Production"), key=lambda d: d["revision"])
    assert len(chain) == MOVES + 1
    assert [d["revision"] for d in chain] == list(
        range(start_rev, start_rev + MOVES + 1)
    )
    for prev, cur in zip(chain, chain[1:], strict=False):
        assert cur["action"] == "moved"
        assert cur["from_version"]["id"] == prev["to_version"]["id"]
    # Every target got the label once, and exactly one version holds it at the end: the last event's target.
    targets = [d["to_version"]["version"] for d in chain[1:]]
    assert sorted(targets) == sorted(v.template_version for v in versions[1:])
    assert holders(t, P) == [chain[-1]["to_version"]["version"]]


# ---- the lock order: each pair, both ways ---------------------------------------------------------------------------


def _backend_pid() -> int:
    with connection.cursor() as c:
        c.execute("SELECT pg_backend_pid()")
        return c.fetchone()[0]


def _waits_on_a_lock(pid: int) -> bool:
    with connection.cursor() as c:
        c.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = %s AND NOT granted)",
            [pid],
        )
        return c.fetchone()[0]


def _contend(monkeypatch, first, second) -> tuple[dict, bool]:
    """Run ``first`` until it holds its rows and is about to lock the heads; then run ``second``, and let ``first``
    go on once ``second`` waits on a lock (10 s at most). Returns each thread's error and whether ``second`` waited."""
    first_holds = threading.Event()
    second_pid: list[int] = []
    second_waited: list[bool] = []
    real = writer._lock_heads

    def lock_heads(*args, **kwargs):
        if threading.current_thread().name == "first" and not first_holds.is_set():
            first_holds.set()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if second_pid and _waits_on_a_lock(second_pid[0]):
                    second_waited.append(True)
                    break
                time.sleep(0.02)
        return real(*args, **kwargs)

    monkeypatch.setattr(writer, "_lock_heads", lock_heads)
    errors: dict[str, BaseException] = {}

    def thread(name, fn):
        def go():
            try:
                if name == "second":
                    assert first_holds.wait(10), (
                        "the first writer never reached the heads"
                    )
                    second_pid.append(_backend_pid())
                fn()
            except BaseException as e:  # noqa: BLE001 - asserted by the caller
                errors[name] = e
            finally:
                connection.close()

        return threading.Thread(target=go, name=name)

    threads = [thread("first", first), thread("second", second)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(60)
    assert not any(th.is_alive() for th in threads)
    return errors, bool(second_waited)


def _revisions(t, name):
    return [(d["action"], d["cause"], d["revision"]) for d in label_events(t, name)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("webhook_endpoint")
@pytest.mark.parametrize("assign_multiple_first", [True, False])
def test_assign_multiple_and_assign_by_id_on_one_version_do_not_deadlock(
    organization, workspace, user, monkeypatch, assign_multiple_first
):
    """The reachable case: ``assign-multiple-labels`` and ``assign-label-by-id`` (``move_label``) on the same version
    at the same time. Before the fix one request failed with a 400 carrying Postgres' deadlock text."""
    t = make_template(organization, workspace, user=user)
    v1, v2 = make_version(t, 1), make_version(t, 2)
    P = system_labels()["Production"]
    writer.move_label(v1, P)

    def assign_multiple():
        writer.assign_labels_to_version(v2.id, [str(P.id)], user, workspace)

    def assign_by_id():
        writer.move_label(PromptVersion._base_manager.get(pk=v2.pk), P)

    pair = (assign_multiple, assign_by_id)
    errors, waited = _contend(
        monkeypatch, *(pair if assign_multiple_first else pair[::-1])
    )
    assert errors == {}
    assert (
        waited
    )  # the two really contended: the second waited on the first's version row
    assert holders(t, P) == ["v2"]
    # One move v1 -> v2; whichever ran second found the label already there and published nothing.
    assert _revisions(t, "Production") == [
        ("assigned", "label_change", 1),
        ("moved", "label_change", 2),
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("webhook_endpoint")
@pytest.mark.parametrize("delete_first", [True, False])
def test_soft_deleting_a_version_and_moving_a_label_onto_it_do_not_deadlock(
    organization, workspace, user, monkeypatch, delete_first
):
    """The latent case: ``PromptVersion.delete()`` (``version_saving`` -> ``release_deleted_versions``) against
    ``move_label`` onto that version. Delete first: the move then finds the version deleted and is refused cleanly
    (no label on a deleted version). Move first: the delete then releases the label it put there."""
    t = make_template(organization, workspace, user=user)
    v1, v2 = make_version(t, 1), make_version(t, 2)
    labels = system_labels()
    P, S = labels["Production"], labels["Staging"]
    writer.move_label(v1, P)
    writer.move_label(v2, S)

    def delete():
        PromptVersion._base_manager.get(pk=v2.pk).delete()

    def move():
        writer.move_label(PromptVersion._base_manager.get(pk=v2.pk), P)

    if delete_first:
        errors, waited = _contend(monkeypatch, delete, move)
        assert set(errors) == {"second"}
        assert isinstance(errors["second"], writer.DeletedTarget), errors
        assert holders(t, P) == ["v1"]
        assert _revisions(t, "Production") == [("assigned", "label_change", 1)]
    else:
        errors, waited = _contend(monkeypatch, move, delete)
        assert errors == {}
        assert holders(t, P) == []
        assert _revisions(t, "Production") == [
            ("assigned", "label_change", 1),
            ("moved", "label_change", 2),
            ("removed", "version_deleted", 3),
        ]
    assert waited
    assert PromptVersion._base_manager.get(pk=v2.pk).deleted
    assert holders(t, S) == []
    assert _revisions(t, "Staging") == [
        ("assigned", "label_change", 1),
        ("removed", "version_deleted", 2),
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("webhook_endpoint")
@pytest.mark.parametrize("folder_first", [True, False])
def test_deleting_a_folder_and_moving_a_label_in_it_do_not_deadlock(
    organization, workspace, user, monkeypatch, folder_first
):
    """A folder delete locks its prompts and all their versions before it takes the heads, then soft-deletes the rows
    in bulk; a move inside the folder at the same time either lands first (and is released) or is refused."""
    from model_hub.models.prompt_folders import PromptFolder
    from model_hub.views.prompt_folder import PromptFolderViewSet

    folder = PromptFolder.objects.create(
        name="lock-order-folder", organization=organization, workspace=workspace
    )
    t = make_template(organization, workspace, name="in-folder", user=user)
    t.prompt_folder = folder
    t.save(update_fields=["prompt_folder"])
    v1, v2 = make_version(t, 1), make_version(t, 2)
    P = system_labels()["Production"]
    writer.move_label(v1, P)

    def delete_folder():
        view = PromptFolderViewSet()
        view.request = SimpleNamespace(user=user)
        view.perform_destroy(PromptFolder.objects.get(pk=folder.pk))

    def move():
        writer.move_label(PromptVersion._base_manager.get(pk=v2.pk), P)

    if folder_first:
        errors, waited = _contend(monkeypatch, delete_folder, move)
        assert set(errors) == {"second"}
        assert isinstance(errors["second"], writer.DeletedTarget), errors
        assert _revisions(t, "Production") == [
            ("assigned", "label_change", 1),
            ("removed", "version_deleted", 2),
        ]
    else:
        errors, waited = _contend(monkeypatch, move, delete_folder)
        assert errors == {}
        assert _revisions(t, "Production") == [
            ("assigned", "label_change", 1),
            ("moved", "label_change", 2),
            ("removed", "version_deleted", 3),
        ]
    assert waited
    assert not PromptVersion._base_manager.filter(
        original_template=t, deleted=False
    ).exists()
    assert not PromptVersion.labels.through.objects.filter(
        promptversion__original_template=t
    ).exists()
