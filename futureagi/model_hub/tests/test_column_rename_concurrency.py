"""Real PostgreSQL lock compatibility for concurrent column rename writers."""

import json
import threading
import time
from copy import deepcopy

import pytest
from django.db import connection, connections, transaction
from rest_framework.test import APIClient

from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.run_prompt import RunPrompter
from model_hub.tests.test_column_rename_derived_api import (
    _client,
    _rename,
    _seed,
)
from model_hub.views.develop_dataset import UpdateColumnNameView
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(name="owner_client")
def authenticated_client(user, organization, workspace):
    return _client(user, organization, workspace)


@pytest.fixture(name="seeded")
def committed_rows(organization, workspace):
    return _seed(organization, workspace)


@pytest.fixture
def workers():
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL row locks are required")
    assert connection.get_autocommit() and not connection.in_atomic_block
    running = []

    def start(operation):
        result = {}
        started = threading.Event()
        done = threading.Event()

        def run():
            clear_workspace_context()
            try:
                assert connection.get_autocommit()
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '15s'")
                    cursor.execute("SET lock_timeout = '12s'")
                    cursor.execute(
                        "SELECT pg_backend_pid(), current_setting('deadlock_timeout'), "
                        "current_setting('lock_timeout'), current_setting('statement_timeout')"
                    )
                    result["pid"], *timeouts = cursor.fetchone()
                    result["timeouts"] = timeouts
                started.set()
                result["value"] = operation()
            except Exception as exc:
                result["error"] = repr(exc)
                cause = exc.__cause__ or exc
                result["sqlstate"] = getattr(cause, "sqlstate", None) or getattr(
                    cause, "pgcode", None
                )
            finally:
                started.set()
                connections.close_all()
                clear_workspace_context()
                done.set()

        thread = threading.Thread(target=run, daemon=True)
        running.append((thread, result))
        thread.start()
        assert started.wait(10), result
        assert "pid" in result, result
        return thread, result, done

    yield start
    for thread, result in running:
        thread.join(20)
        assert not thread.is_alive(), result


def _snapshot(*pids):
    # This is the main thread's separate autocommit connection, never a worker's
    # open transaction: pg_stat_activity snapshots must not be cached between polls.
    assert connection.get_autocommit() and not connection.in_atomic_block
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        assert cursor.fetchone()[0] not in pids
        cursor.execute(
            "SELECT pid, wait_event_type, query, pg_blocking_pids(pid) "
            "FROM pg_stat_activity WHERE pid = ANY(%s) ORDER BY pid",
            [list(pids)],
        )
        activity = [
            {"pid": pid, "wait": wait, "query": query, "blockers": blockers}
            for pid, wait, query, blockers in cursor.fetchall()
        ]
        cursor.execute(
            "SELECT pid, locktype, relation::regclass::text, mode, granted "
            "FROM pg_locks WHERE pid = ANY(%s) ORDER BY pid, locktype, mode",
            [list(pids)],
        )
        locks = cursor.fetchall()
    return {"activity": activity, "locks": locks}


def _wait_for(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for the expected PostgreSQL lock state")


def _blocked(waiter, blocker, table):
    snapshot = _snapshot(waiter, blocker)
    if any(
        row["pid"] == waiter
        and row["wait"] == "Lock"
        and blocker in row["blockers"]
        and table in row["query"]
        for row in snapshot["activity"]
    ):
        return snapshot
    return None


def _finish(worker):
    thread, result, _ = worker
    thread.join(20)
    assert not thread.is_alive(), result
    assert "error" not in result, result
    return result["value"]


def _pause_after_dataset_lock(monkeypatch):
    locked = threading.Event()
    resume = threading.Event()
    original = UpdateColumnNameView._run_prompter_for_update

    def pause(self, dataset, column):
        if not locked.is_set():
            locked.set()
            assert resume.wait(15), "Rename was not released"
        return original(self, dataset, column)

    monkeypatch.setattr(UpdateColumnNameView, "_run_prompter_for_update", pause)
    return locked, resume


def _assert_renamed(column, prompter, name="B"):
    column.refresh_from_db()
    prompter.refresh_from_db()
    assert column.name == name
    assert prompter.run_prompt_config["derived_variables"]["full_variables"] == [
        f"{name}.user.name",
        f"{name}.score",
        "Another.score",
    ]


def test_rename_allows_concurrent_autocommit_cell_insert(
    owner_client, seeded, workers, monkeypatch
):
    dataset, column, prompter, _ = seeded
    row = Row.no_workspace_objects.create(dataset=dataset, order=1)
    dataset_locked, resume = _pause_after_dataset_lock(monkeypatch)
    rename = workers(lambda: _rename(owner_client, dataset, column).status_code)
    observations = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT c.conname, c.confrelid::regclass::text, "
                "c.condeferrable, c.condeferred, t.tgname FROM pg_constraint c "
                "JOIN pg_trigger t ON t.tgconstraint = c.oid "
                "WHERE c.conrelid = 'model_hub_cell'::regclass AND c.contype = 'f' "
                "AND t.tgrelid = c.conrelid AND t.tgtype & 4 <> 0 ORDER BY t.tgname"
            )
            observations["insert_fk_triggers"] = cursor.fetchall()
        assert dataset_locked.wait(10), rename[1]

        def insert_cell():
            assert connection.get_autocommit() and not connection.in_atomic_block
            return str(
                Cell.objects.create(
                    dataset=dataset, column=column, row=row, value="concurrent value"
                ).id
            )

        insert = workers(insert_cell)
        rename_pid, insert_pid = rename[1]["pid"], insert[1]["pid"]
        assert rename_pid != insert_pid
        # With NO KEY UPDATE this finishes before the rename resumes. FOR UPDATE
        # makes INSERT's deferred FK check wait on Dataset while holding Column.
        observations["insert_before_resume"] = _wait_for(
            lambda: (
                _blocked(insert_pid, rename_pid, "model_hub_cell")
                or ("finished" if insert[2].is_set() else None)
            )
        )
        resume.set()

        def cycle_or_completion():
            snapshot = _snapshot(rename_pid, insert_pid)
            blockers = {r["pid"]: r["blockers"] for r in snapshot["activity"]}
            if insert_pid in blockers.get(
                rename_pid, []
            ) and rename_pid in blockers.get(insert_pid, []):
                return snapshot
            if rename[2].is_set() and insert[2].is_set():
                return "finished"
            return None

        observations["after_resume"] = _wait_for(cycle_or_completion)
        # Join both before asserting so a deadlock victim is captured whichever
        # transaction PostgreSQL chooses, and no writer escapes test teardown.
        rename[0].join(20)
        insert[0].join(20)
        print("RENAME_INSERT_LOCKS " + json.dumps(observations, default=str))
        print("RENAME_INSERT_RESULTS " + json.dumps([rename[1], insert[1]]))
        assert _finish(rename) == 200
        cell_id = _finish(insert)
        assert observations["insert_before_resume"] == "finished", observations
        _assert_renamed(column, prompter)
        cell = Cell.no_workspace_objects.get(id=cell_id)
        assert (cell.dataset_id, cell.column_id, cell.row_id, cell.value) == (
            dataset.id,
            column.id,
            row.id,
            "concurrent value",
        )
    finally:
        resume.set()


def test_cell_insert_does_not_wait_for_renamed_column_commit(
    owner_client, seeded, workers, monkeypatch
):
    dataset, column, prompter, _ = seeded
    row = Row.no_workspace_objects.create(dataset=dataset, order=1)
    written = threading.Event()
    resume = threading.Event()
    propagate = UpdateColumnNameView._update_derived_variables_for_renamed_column

    def pause_after_write(self, dataset, column, old_name, new_name):
        assert connection.in_atomic_block
        assert Column.no_workspace_objects.get(id=column.id).name == "B"
        written.set()
        assert resume.wait(15), "Rename was not released after writing the column"
        return propagate(self, dataset, column, old_name, new_name)

    monkeypatch.setattr(
        UpdateColumnNameView,
        "_update_derived_variables_for_renamed_column",
        pause_after_write,
    )
    rename = workers(lambda: _rename(owner_client, dataset, column).status_code)
    try:
        assert written.wait(10), rename[1]

        def insert_cell():
            assert connection.get_autocommit() and not connection.in_atomic_block
            return str(
                Cell.objects.create(
                    dataset=dataset, column=column, row=row, value="during rename"
                ).id
            )

        insert = workers(insert_cell)
        assert insert[1]["pid"] != rename[1]["pid"]
        assert insert[2].wait(8), _snapshot(rename[1]["pid"], insert[1]["pid"])
        cell_id = _finish(insert)
        assert not rename[2].is_set(), rename[1]
        assert Cell.no_workspace_objects.get(id=cell_id).value == "during rename"
        print("RENAME_AFTER_WRITE_RESULTS " + json.dumps([rename[1], insert[1]]))
        resume.set()
        assert _finish(rename) == 200
        _assert_renamed(column, prompter)
        assert Cell.no_workspace_objects.get(id=cell_id).column_id == column.id
    finally:
        resume.set()


def test_editor_then_rename_uses_prompter_before_column(owner_client, seeded, workers):
    dataset, column, prompter, _ = seeded
    editor_locked = threading.Event()
    resume_editor = threading.Event()

    def edit():
        with transaction.atomic():
            locked = RunPrompter.no_workspace_objects.select_for_update().get(
                id=prompter.id
            )
            editor_locked.set()
            assert resume_editor.wait(15), "Editor was not released"
            Column.no_workspace_objects.select_for_update().get(id=column.id)
            config = deepcopy(locked.run_prompt_config)
            config["concurrent_edit"] = "preserved"
            locked.run_prompt_config = config
            locked.save(update_fields=["run_prompt_config"])
        return "committed"

    editor = workers(edit)
    try:
        assert editor_locked.wait(10), editor[1]
        rename = workers(lambda: _rename(owner_client, dataset, column).status_code)
        snapshot = _wait_for(
            lambda: _blocked(
                rename[1]["pid"], editor[1]["pid"], "model_hub_runprompter"
            )
        )
        print("EDITOR_RENAME_LOCKS " + json.dumps(snapshot))
        resume_editor.set()
        assert _finish(editor) == "committed"
        assert _finish(rename) == 200
        _assert_renamed(column, prompter)
        assert prompter.run_prompt_config["concurrent_edit"] == "preserved"
    finally:
        resume_editor.set()


def test_concurrent_renames_serialize_on_dataset(
    owner_client, seeded, workers, monkeypatch
):
    dataset, column, prompter, _ = seeded
    second_client = APIClient()
    second_client.credentials(**owner_client._credentials)
    dataset_locked, resume = _pause_after_dataset_lock(monkeypatch)
    first = workers(lambda: _rename(owner_client, dataset, column).status_code)
    try:
        assert dataset_locked.wait(10), first[1]
        second = workers(
            lambda: (
                second_client.put(
                    f"/model-hub/develops/{dataset.id}/update_column_name/{column.id}/",
                    {"new_column_name": "C"},
                    format="json",
                ).status_code
            )
        )
        snapshot = _wait_for(
            lambda: _blocked(second[1]["pid"], first[1]["pid"], "model_hub_dataset")
        )
        assert not second[2].is_set(), second[1]
        print("RENAME_RENAME_LOCKS " + json.dumps(snapshot))
        resume.set()
        assert _finish(first) == 200
        assert _finish(second) == 200
        _assert_renamed(column, prompter, "C")
    finally:
        resume.set()


def test_editor_holding_prompter_can_lock_column_while_rename_waits(
    owner_client, seeded, workers
):
    # The rename must wait for the editor's prompter lock before it locks the
    # column, so the editor can still take the column without waiting. Lock
    # state is read from the main thread's separate autocommit connection: a
    # transaction that is still open sees a cached pg_stat_activity snapshot.
    dataset, column, prompter, _ = seeded
    prompter_locked = threading.Event()
    rename_waiting = threading.Event()
    column_locked = threading.Event()

    def edit():
        with transaction.atomic():
            locked = RunPrompter.no_workspace_objects.select_for_update().get(
                id=prompter.id
            )
            prompter_locked.set()
            assert rename_waiting.wait(15), "Rename never waited on the prompter"
            # NOWAIT fails at once if the waiting rename already held the column.
            Column.no_workspace_objects.select_for_update(nowait=True).get(id=column.id)
            column_locked.set()
            config = deepcopy(locked.run_prompt_config)
            config["concurrent_edit"] = "preserved"
            locked.run_prompt_config = config
            locked.save(update_fields=["run_prompt_config"])
        return "committed"

    editor = workers(edit)
    try:
        assert prompter_locked.wait(10), editor[1]
        rename = workers(lambda: _rename(owner_client, dataset, column).status_code)
        editor_pid, rename_pid = editor[1]["pid"], rename[1]["pid"]
        assert editor_pid != rename_pid
        snapshot = _wait_for(
            lambda: _blocked(rename_pid, editor_pid, "model_hub_runprompter")
        )
        held = [lock[1:] for lock in snapshot["locks"] if lock[0] == rename_pid]
        # Waiting for the editor's row lock: the prompter tuple lock is held
        # and the editor's transaction is awaited; no column row lock yet.
        assert ("tuple", "model_hub_runprompter") in {
            (kind, table) for kind, table, _, granted in held if granted
        }, held
        assert any(
            kind == "transactionid" and not granted for kind, _, _, granted in held
        ), held
        assert not any(
            table == "model_hub_column" and mode == "RowShareLock"
            for _, table, mode, _ in held
        ), held
        assert not rename[2].is_set(), rename[1]
        rename_waiting.set()
        assert _finish(editor) == "committed"
        assert column_locked.is_set()
        assert _finish(rename) == 200
        print("EDITOR_COLUMN_WHILE_RENAME_WAITS " + json.dumps(snapshot))
        _assert_renamed(column, prompter)
        assert prompter.run_prompt_config["concurrent_edit"] == "preserved"
        read = owner_client.get(
            f"/model-hub/develops/{dataset.id}/get-dataset-table/",
            {"page_size": 10, "current_page_index": 0},
        )
        assert read.status_code == 200, read.content
        names = {
            str(item["id"]): item["name"]
            for item in read.json()["result"]["column_config"]
        }
        assert names[str(column.id)] == "B", names
    finally:
        rename_waiting.set()
