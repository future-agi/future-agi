"""XP-06: the one prompt label writer (webhooks DESIGN.md section 14.3; WT-21c).

Real Postgres, real rows. Every event asserted here is the row ``webhooks.publish()`` wrote inside the writer's
transaction; ``data`` is the stored thin, names-off body data.
"""

from __future__ import annotations

import importlib
import types

import pytest
from django.db import connection

from model_hub.models.prompt_label import PromptLabelHead
from model_hub.models.run_prompt import PromptVersion
from model_hub.services import prompt_label as writer
from model_hub.tests.prompt_webhook_helpers import (  # noqa: F401 - fixture
    LABEL_EVENT,
    VERSION_EVENT,
    events,
    head,
    holders,
    label_events,
    make_template,
    make_version,
    system_labels,
    webhook_endpoint,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("webhook_endpoint")]


@pytest.fixture
def prompt(organization, workspace, user):
    t = make_template(organization, workspace, user=user)
    vs = [make_version(t, n, message=f"m{n}") for n in (1, 2, 3)]
    return types.SimpleNamespace(
        t=t, v1=vs[0], v2=vs[1], v3=vs[2], labels=system_labels()
    )


def _summary(d):
    return (
        d["label"]["name"],
        d["action"],
        d["cause"],
        d["from_version"] and d["from_version"]["version"],
        d["to_version"] and d["to_version"]["version"],
        d["revision"],
    )


def test_set_replace_emits_replaced_for_each_dropped_label_and_nothing_when_repeated(
    prompt,
):
    """WT-21c: assigning Production to a version that held Staging removes Staging with cause replaced; the same
    request again changes nothing and publishes nothing (the no-op diff, O5)."""
    P, S = prompt.labels["Production"], prompt.labels["Staging"]
    writer.assign_labels(prompt.v2, [S])
    before = len(events(LABEL_EVENT))
    writer.assign_labels(prompt.v2, [P])
    new = [_summary(d) for d in label_events(prompt.t)][before:]
    assert sorted(new) == sorted(
        [
            ("Production", "assigned", "label_change", None, "v2", 1),
            ("Staging", "removed", "replaced", "v2", None, 2),
        ]
    )
    assert holders(prompt.t, P) == ["v2"] and holders(prompt.t, S) == []
    count = len(events(LABEL_EVENT))
    writer.assign_labels(prompt.v2, [P])
    assert len(events(LABEL_EVENT)) == count
    assert head(prompt.t, P).revision == 1


def test_a_move_is_one_moved_event_with_the_next_revision(prompt):
    P = prompt.labels["Production"]
    writer.move_label(prompt.v1, P)
    writer.move_label(prompt.v3, P)
    assert [_summary(d) for d in label_events(prompt.t)] == [
        ("Production", "assigned", "label_change", None, "v1", 1),
        ("Production", "moved", "label_change", "v1", "v3", 2),
    ]
    moved = label_events(prompt.t)[-1]
    assert moved["to_version"]["commit_message"] == "m3"
    assert "commit_message" not in moved["from_version"]
    assert holders(prompt.t, P) == ["v3"]
    h = head(prompt.t, P)
    assert (h.version_id, h.revision) == (prompt.v3.id, 2)


def test_moving_a_label_onto_the_version_that_holds_it_publishes_nothing(prompt):
    P = prompt.labels["Production"]
    writer.move_label(prompt.v1, P)
    n = len(events(LABEL_EVENT))
    assert writer.move_label(prompt.v1, P) == []
    assert len(events(LABEL_EVENT)) == n


def test_remove_publishes_removed_and_removing_an_absent_label_publishes_nothing(
    prompt,
):
    P = prompt.labels["Production"]
    writer.move_label(prompt.v1, P)
    assert writer.remove_label(prompt.v2, P) == []  # not on v2
    writer.remove_label(prompt.v1, P)
    assert [_summary(d) for d in label_events(prompt.t)][-1] == (
        "Production",
        "removed",
        "label_change",
        "v1",
        None,
        2,
    )
    assert holders(prompt.t, P) == []
    assert head(prompt.t, P).version_id is None


def test_the_model_methods_delegate_to_the_writer(prompt):
    P = prompt.labels["Production"]
    prompt.v1.add_label(P)
    prompt.v2.add_label(P)  # moves it: a label is on one version of a prompt at most
    prompt.v2.remove_label(P)
    assert [_summary(d)[:3] for d in label_events(prompt.t)] == [
        ("Production", "assigned", "label_change"),
        ("Production", "moved", "label_change"),
        ("Production", "removed", "label_change"),
    ]


def test_soft_deleting_a_version_releases_its_labels_as_version_deleted(prompt):
    P, S = prompt.labels["Production"], prompt.labels["Staging"]
    writer.assign_labels(prompt.v2, [P, S])
    prompt.v2.delete()  # BaseModel soft delete: save() with deleted=True
    released = [
        _summary(d) for d in label_events(prompt.t) if d["cause"] == "version_deleted"
    ]
    assert sorted(released) == [
        ("Production", "removed", "version_deleted", "v2", None, 2),
        ("Staging", "removed", "version_deleted", "v2", None, 2),
    ]
    assert holders(prompt.t, P) == [] and holders(prompt.t, S) == []
    prompt.v2.save()  # saving the deleted version again releases nothing more
    assert (
        len([d for d in label_events(prompt.t) if d["cause"] == "version_deleted"]) == 2
    )


def test_soft_deleting_a_prompt_releases_every_label_once(prompt):
    P, S = prompt.labels["Production"], prompt.labels["Staging"]
    writer.move_label(prompt.v1, P)
    writer.move_label(prompt.v3, S)
    prompt.t.delete()
    deleted = [d for d in label_events(prompt.t) if d["cause"] == "version_deleted"]
    assert sorted(
        (d["label"]["name"], d["from_version"]["version"]) for d in deleted
    ) == [
        ("Production", "v1"),
        ("Staging", "v3"),
    ]
    assert all(d["action"] == "removed" and d["to_version"] is None for d in deleted)


def test_removing_a_label_that_was_never_on_the_prompt_creates_no_head(prompt):
    """The remove route with any visible label id used to insert a head (template, label, NULL, 0)."""
    P = prompt.labels["Production"]
    assert writer.remove_label(prompt.v1, P) == []
    assert not PromptLabelHead.objects.filter(template=prompt.t, label=P).exists()
    assert label_events(prompt.t) == []


def test_a_label_is_never_put_on_a_deleted_version_or_a_version_of_a_deleted_prompt(
    prompt,
):
    """Checked under the writer's locks, so a delete racing the move either lands first (and the move is refused)
    or releases what the move wrote (test_prompt_label_concurrency)."""
    P = prompt.labels["Production"]
    prompt.v2.delete()
    with pytest.raises(writer.DeletedTarget):
        writer.move_label(prompt.v2, P)
    with pytest.raises(writer.DeletedTarget):
        writer.assign_labels(prompt.v2, [P])
    assert writer.assign_labels(prompt.v2, []) == []  # emptying it is a no-op
    prompt.t.delete()  # the prompt only: its versions are soft-deleted by the caller
    with pytest.raises(writer.DeletedTarget):
        writer.move_label(prompt.v1, P)
    assert holders(prompt.t, P) == [] and label_events(prompt.t) == []
    assert not PromptLabelHead.objects.filter(template=prompt.t, label=P).exists()


def test_events_suppressed_writes_the_change_but_publishes_nothing(prompt):
    P = prompt.labels["Production"]
    with writer.events_suppressed("test"):
        writer.move_label(prompt.v1, P)
        make_version(prompt.t, 9, message="seeded")
    assert holders(prompt.t, P) == ["v1"]
    assert head(prompt.t, P).revision == 1
    assert label_events(prompt.t) == []
    assert not [
        e for e in events(VERSION_EVENT) if e.data["version"]["version"] == "v9"
    ]


def test_the_writer_repairs_a_label_the_old_race_left_on_two_versions(prompt):
    """O1: before the head table, two concurrent moves could leave a label on two versions. The backfill points the
    head at one of them; the next change through the writer leaves exactly one."""
    P = prompt.labels["Production"]
    through = PromptVersion.labels.through
    through.objects.create(promptversion_id=prompt.v1.id, promptlabel_id=P.id)
    through.objects.create(promptversion_id=prompt.v2.id, promptlabel_id=P.id)
    assert holders(prompt.t, P) == ["v1", "v2"]
    writer.move_label(prompt.v3, P)
    assert holders(prompt.t, P) == ["v3"]
    d = label_events(prompt.t)[-1]
    assert d["action"] == "moved" and d["to_version"]["version"] == "v3"


def test_the_migration_backfill_points_each_head_at_the_newest_holder(prompt, capsys):
    """Migration 0123's backfill, run on real rows: one head per (prompt, label), at the most recently updated live
    version; a duplicate is reported and no attachment changes."""
    mig = importlib.import_module("model_hub.migrations.0123_promptlabelhead")
    P, S = prompt.labels["Production"], prompt.labels["Staging"]
    through = PromptVersion.labels.through
    through.objects.create(promptversion_id=prompt.v1.id, promptlabel_id=P.id)
    through.objects.create(promptversion_id=prompt.v2.id, promptlabel_id=P.id)
    through.objects.create(promptversion_id=prompt.v3.id, promptlabel_id=S.id)
    prompt.v1.save(
        update_fields=["updated_at"]
    )  # v1 is now the most recently updated holder of Production
    PromptLabelHead.objects.all().delete()
    mig.backfill(None, types.SimpleNamespace(connection=connection))
    assert head(prompt.t, P).version_id == prompt.v1.id
    assert head(prompt.t, S).version_id == prompt.v3.id
    assert {h.revision for h in PromptLabelHead.objects.all()} == {0}
    assert holders(prompt.t, P) == ["v1", "v2"]  # unchanged until the next change
    assert (
        "1 (prompt, label) pairs sit on more than one live version"
        in capsys.readouterr().out
    )


def test_a_head_left_stale_by_an_old_pod_names_the_real_holder(prompt):
    """During a rolling deploy, a pod on code from before migration 0123 moves or removes a label in the
    attachment table without moving its head. The writer reads the real holder under its lock, so the next change
    names it as from_version (not the head's stale version), and the revisions stay a chain."""
    P = prompt.labels["Production"]
    through = PromptVersion.labels.through

    def old_pod_moves(frm, to):
        through.objects.filter(promptversion_id=frm.id, promptlabel_id=P.id).delete()
        if to is not None:
            through.objects.create(promptversion_id=to.id, promptlabel_id=P.id)

    writer.move_label(prompt.v1, P)  # head: v1, revision 1
    old_pod_moves(prompt.v1, prompt.v2)
    writer.move_label(prompt.v3, P)
    assert _summary(label_events(prompt.t)[-1]) == (
        "Production",
        "moved",
        "label_change",
        "v2",
        "v3",
        2,
    )
    assert holders(prompt.t, P) == ["v3"]

    # Removed by an old pod: the next assignment is from no version.
    old_pod_moves(prompt.v3, None)
    writer.move_label(prompt.v1, P)
    assert _summary(label_events(prompt.t)[-1]) == (
        "Production",
        "assigned",
        "label_change",
        None,
        "v1",
        3,
    )

    # Already where the writer is asked to put it: nothing changes.
    old_pod_moves(prompt.v1, prompt.v2)
    n = len(events(LABEL_EVENT))
    assert writer.move_label(prompt.v2, P) == []
    assert len(events(LABEL_EVENT)) == n
    h = head(prompt.t, P)
    # The head is repaired, and no revision is spent on it.
    assert (h.version_id, h.revision) == (prompt.v2.id, 3)
    writer.remove_label(prompt.v2, P)  # the remove route sees the real holder too
    assert _summary(label_events(prompt.t)[-1]) == (
        "Production",
        "removed",
        "label_change",
        "v2",
        None,
        4,
    )
    assert holders(prompt.t, P) == []


def _row_locks(queries) -> list[str]:
    return [
        q["sql"]
        for q in queries
        if "FOR NO KEY UPDATE" in q["sql"] or "FOR UPDATE" in q["sql"]
    ]


def test_a_full_save_of_a_version_loaded_committed_takes_no_row_lock(prompt):
    """The run paths full-save committed versions repeatedly. Such a save can neither publish (one event
    per version, D-X3) nor release a label, so it takes no row lock. A draft loaded and committed by a full save
    still locks its row and publishes once; a version loaded committed and then soft-deleted still releases its
    labels; after a refresh the next save locks again."""
    from django.test.utils import CaptureQueriesContext

    v1 = PromptVersion._base_manager.get(pk=prompt.v1.pk)
    v1.commit_message = "edited by a run"
    with CaptureQueriesContext(connection) as q:
        v1.save()
    assert _row_locks(q.captured_queries) == []
    v1.refresh_from_db()
    with CaptureQueriesContext(connection) as q:
        v1.save()
    assert len(_row_locks(q.captured_queries)) == 1

    draft = make_version(prompt.t, 7, draft=True)
    n = len(events(VERSION_EVENT))
    loaded = PromptVersion._base_manager.get(pk=draft.pk)
    loaded.is_draft = False
    with CaptureQueriesContext(connection) as q:
        loaded.save()
    assert len(_row_locks(q.captured_queries)) == 1
    assert [e.data["version"]["version"] for e in events(VERSION_EVENT)[n:]] == ["v7"]
    loaded.save()  # loaded as a draft: locks, and publishes nothing more
    assert len(events(VERSION_EVENT)) == n + 1

    P = prompt.labels["Production"]
    writer.move_label(prompt.v2, P)
    v2 = PromptVersion._base_manager.get(pk=prompt.v2.pk)
    v2.deleted = True
    v2.save()
    assert holders(prompt.t, P) == []
    assert label_events(prompt.t)[-1]["cause"] == "version_deleted"
