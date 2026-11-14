"""
Eval clustering on a worker deployed before the Instant eval tables exist.

Every regular eval task's failing results ask ``is_instant_eval_task``, and
the clustering fetch excludes Instant eval runs; both read
``tracer_instant_eval_run`` (migration 0117). A worker that runs ahead of
``migrate`` must still finish those entries and cluster their failures, and
the failed read must not break a transaction it runs in.
"""

import uuid
from types import SimpleNamespace

import pytest
from django.db import OperationalError, ProgrammingError, connection, transaction

from tracer.models.instant_eval import InstantEvalRun
from tracer.models.observation_span import EvalLogger
from tracer.models.project import Project
from tracer.queries.eval_clustering import (
    get_unclustered_eval_results,
    is_instant_eval_task,
)
from tracer.services.eval_tasks import run_entry


@pytest.fixture
def without_instant_eval_table(db):
    """The database as before migration 0117: the run table is renamed away
    inside the test's transaction, and its rollback puts it back."""
    with connection.cursor() as cur:
        cur.execute(
            "ALTER TABLE tracer_instant_eval_run RENAME TO tracer_instant_eval_run_away"
        )


class _Unreadable:
    """A queryset whose read fails with ``error``."""

    db = "default"

    def __init__(self, error):
        self.error = error

    def exists(self):
        raise self.error


def _programming_error(message, sqlstate):
    """A ProgrammingError as Django raises it: wrapping the driver's error,
    which carries the SQLSTATE."""

    class DriverError(Exception):
        pass

    cause = DriverError(message)
    cause.sqlstate = cause.pgcode = sqlstate
    error = ProgrammingError(message)
    error.__cause__ = cause
    return error


def _failing_eval(cfg):
    """A regular eval task's failing session eval (sessions live in
    ClickHouse; the eval logger keeps only their ids)."""
    return EvalLogger.objects.create(
        trace_session_id=uuid.uuid4(),
        custom_eval_config=cfg,
        target_type="session",
        output_bool=False,
        eval_explanation="the agent never answered the refund question",
        eval_task_id=str(uuid.uuid4()),
    )


@pytest.mark.django_db
def test_a_task_check_without_the_table_answers_no_and_the_transaction_goes_on(
    without_instant_eval_table, project
):
    with transaction.atomic():
        assert is_instant_eval_task(uuid.uuid4()) is False
        # Without a savepoint the failed read would have aborted this
        # transaction, and this query would raise.
        assert Project.objects.filter(id=project.id).exists()


@pytest.mark.django_db
def test_a_regular_tasks_failure_still_starts_clustering_without_the_table(
    without_instant_eval_table, monkeypatch
):
    dispatched = []
    monkeypatch.setattr(
        "tracer.tasks.eval_clustering.dispatch_eval_clustering", dispatched.append
    )
    monkeypatch.setattr(
        "tracer.queries.eval_clustering.is_clusterable_eval_failure", lambda e: True
    )
    entry = SimpleNamespace(eval_task_id=str(uuid.uuid4()))
    run_entry._reseed_eval_clustering(entry, "project-1")
    assert dispatched == ["project-1"]


@pytest.mark.django_db
def test_the_fetch_without_the_table_excludes_nothing_and_the_transaction_goes_on(
    project, custom_eval_config, without_instant_eval_table
):
    failed = _failing_eval(custom_eval_config)
    with transaction.atomic():
        fetched = {
            r.eval_logger_id for r in get_unclustered_eval_results(str(project.id))
        }
        assert fetched == {str(failed.id)}
        assert EvalLogger.objects.filter(id=failed.id).exists()


@pytest.mark.django_db
def test_the_fetch_excludes_nothing_when_the_runs_read_as_missing(
    project, custom_eval_config, monkeypatch
):
    failed = _failing_eval(custom_eval_config)
    monkeypatch.setattr(
        InstantEvalRun.objects,
        "all",
        lambda: _Unreadable(
            _programming_error(
                'relation "tracer_instant_eval_run" does not exist', "42P01"
            )
        ),
    )
    fetched = {r.eval_logger_id for r in get_unclustered_eval_results(str(project.id))}
    assert fetched == {str(failed.id)}


@pytest.mark.django_db
def test_the_fetch_fails_on_any_other_programming_error(project, monkeypatch):
    # An undefined column after a later migration is not "no Instant eval
    # table": clustering Instant eval answers by mistake can't be undone.
    monkeypatch.setattr(
        InstantEvalRun.objects,
        "all",
        lambda: _Unreadable(
            _programming_error('column "unit" does not exist', "42703")
        ),
    )
    with pytest.raises(ProgrammingError):
        get_unclustered_eval_results(str(project.id))


@pytest.mark.django_db
def test_any_failed_task_check_answers_no(monkeypatch):
    monkeypatch.setattr(
        InstantEvalRun.objects,
        "filter",
        lambda **_: _Unreadable(OperationalError("server closed the connection")),
    )
    assert is_instant_eval_task(uuid.uuid4()) is False


@pytest.mark.django_db
def test_the_fetch_fails_on_any_other_database_error(project, monkeypatch):
    # Only a missing table means there is nothing to exclude; clustering an
    # Instant eval run's answers because a read failed can't be undone.
    monkeypatch.setattr(
        InstantEvalRun.objects,
        "all",
        lambda: _Unreadable(OperationalError("server closed the connection")),
    )
    with pytest.raises(OperationalError):
        get_unclustered_eval_results(str(project.id))
