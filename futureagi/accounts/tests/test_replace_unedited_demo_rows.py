"""The replace_unedited_demo_rows command and the seeded demo rows it rewrites.

An organization seeded by an earlier build holds the earlier demo rows. The
command rewrites each such row that still holds exactly what was seeded, so it
holds what a new organization gets now. It leaves a row anyone changed, a row
added after the seed (a copy included) and every dataset that is not the
seeded demo dataset, and it prints how many rows it left for each reason. A
second run changes nothing. With --dry-run it prints the counts, locks no row
and writes nothing in any table. An error in one organization rolls back that organization
only; the command counts it, goes on with the others and exits with an error.

The earlier rows come from fixtures/demo_dataset_earlier_rows.json: three rows
of an earlier seed file, copied whole, each seeded at its own position among
the current rows.
"""

import copy
import hashlib
import json
import re
import uuid
from contextlib import ExitStack, contextmanager
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EARLIER_ROWS = FIXTURES / "demo_dataset_earlier_rows.json"
PRIOR_ROW_HASHES = FIXTURES / "demo_dataset_prior_row_hashes.json"
COMMAND = "replace_unedited_demo_rows"
# Evaluation templates the demo dataset links its columns to. An install loads
# them with the system evaluations; the test database starts without them.
DEMO_EVAL_IDS = (101, 47, 66, 100)
QUESTION = ("question", "OTHERS")
ANNOTATION_SOURCE = "annotation_label"
# The seed writes 50 demo rows, at positions 0 to 49. A row added later (by
# hand, by an upload or as a copy) goes after the last row.
SEEDED_ROWS = 50
# A clause that takes row locks (FOR UPDATE, FOR NO KEY UPDATE, FOR SHARE, FOR
# KEY SHARE), and the leading verbs of statements that write rows.
ROW_LOCK = re.compile(r"\bFOR\s+(?:NO\s+KEY\s+UPDATE|UPDATE|KEY\s+SHARE|SHARE)\b", re.I)
WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE")


def earlier_rows():
    return json.loads(EARLIER_ROWS.read_text())["rows"]


EARLIER_POSITIONS = [row["position"] for row in earlier_rows()]
EDITED_POSITION = EARLIER_POSITIONS[1]


def counts(
    *,
    touched=0,
    replaced=0,
    edited=0,
    added=0,
    columns_changed=0,
    failed=0,
    dry_run=False,
):
    """The lines the command prints, in order."""
    replaced_label = "rows that would be replaced" if dry_run else "rows replaced"
    return [
        f"organizations touched: {touched}",
        f"{replaced_label}: {replaced}",
        f"rows left because edited: {edited}",
        f"rows left because added after the seed: {added}",
        f"rows left because the dataset's seeded columns changed: {columns_changed}",
        f"organizations failed: {failed}",
    ]


def file_row_hash(row):
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def earlier_seed_files():
    """The current seed files with the earlier rows at their positions."""
    from accounts.base_data import dataset_data, experiment_data

    table = copy.deepcopy(dataset_data)
    experiment = copy.deepcopy(experiment_data)
    earlier = {row["position"]: row for row in earlier_rows()}
    experiment_by_id = {row["row_id"]: row for row in experiment["table"]}
    table_rows, experiment_rows = [], []
    for position in range(SEEDED_ROWS):
        if position in earlier:
            table_rows.append(copy.deepcopy(earlier[position]["table"]))
            experiment_rows.append(copy.deepcopy(earlier[position]["experiment"]))
        else:
            row = table["table"][position]
            table_rows.append(row)
            experiment_rows.append(experiment_by_id[row["row_id"]])
    table["table"] = table_rows
    experiment["table"] = experiment_rows
    return table, experiment


class RefusedLabel:
    """An annotation label input the seed's serializer refuses, as some earlier seeds saw."""

    errors = {"name": ["refused"]}

    def __init__(self, *args, **kwargs):
        pass

    def is_valid(self, *args, **kwargs):
        return False


def seed_organization(*, earlier=True, annotation=True):
    """A new organization seeded the way the background seed does it."""
    from accounts.models.organization import Organization
    from accounts.models.user import User
    from accounts.user_onboard import upload_demo_dataset
    from model_hub.models.develop_dataset import Dataset
    from model_hub.models.evals_metric import EvalTemplate
    from tfc.constants.roles import OrganizationRoles

    for eval_id in DEMO_EVAL_IDS:
        EvalTemplate.no_workspace_objects.get_or_create(
            eval_id=eval_id,
            defaults={"name": f"eval-{eval_id}", "config": {"config": {}}},
        )
    organization = Organization.objects.create(name=f"Seeded {uuid.uuid4().hex[:8]}")
    user = User.objects.create_user(
        email=f"seeded-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="SafePassword123!",
        name="Seeded Member",
        organization=organization,
        organization_role=OrganizationRoles.ADMIN,
    )
    with ExitStack() as stack:
        if earlier:
            table, experiment = earlier_seed_files()
            stack.enter_context(patch("accounts.user_onboard.dataset_data", table))
            stack.enter_context(
                patch("accounts.user_onboard.experiment_data", experiment)
            )
        if not annotation:
            stack.enter_context(
                patch("accounts.user_onboard.AnnotationsLabelsSerializer", RefusedLabel)
            )
        upload_demo_dataset(str(organization.id), str(user.id))
    return Dataset.no_workspace_objects.get(
        organization_id=organization.id, name="Demo-dataset"
    )


def two_seeded_organizations():
    """Two organizations seeded with the earlier rows, in the order the command visits them."""
    return sorted(
        (seed_organization(), seed_organization()),
        key=lambda dataset: str(dataset.organization_id),
    )


def cells_by_position(dataset):
    """Row position -> {(column name, column source): stored cell fields}."""
    from model_hub.models.develop_dataset import Cell, Column, Row

    columns = {
        column.id: (column.name, column.source)
        for column in Column.no_workspace_objects.filter(dataset_id=dataset.id)
    }
    positions = dict(
        Row.no_workspace_objects.filter(dataset_id=dataset.id).values_list("id", "order")
    )
    found = {}
    for cell in Cell.no_workspace_objects.filter(row_id__in=list(positions)):
        found.setdefault(positions[cell.row_id], {})[columns[cell.column_id]] = (
            cell.value,
            cell.value_infos,
            cell.feedback_info,
            cell.status,
        )
    return found


def snapshot(dataset):
    """Everything the command could write in a dataset, deleted records included."""
    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

    return {
        "dataset": list(
            Dataset.all_objects.filter(id=dataset.id).values_list(
                "name", "source", "deleted", "column_order", "updated_at"
            )
        ),
        "columns": sorted(
            Column.all_objects.filter(dataset_id=dataset.id).values_list(
                "id", "name", "source", "deleted", "updated_at"
            )
        ),
        "rows": sorted(
            Row.all_objects.filter(dataset_id=dataset.id).values_list(
                "id", "order", "deleted", "updated_at"
            )
        ),
        "cells": sorted(
            (
                str(cell.id),
                str(cell.row_id),
                str(cell.column_id),
                cell.value,
                json.dumps(cell.value_infos, sort_keys=True),
                json.dumps(cell.feedback_info, sort_keys=True),
                cell.status,
                cell.deleted,
                cell.updated_at.isoformat(),
            )
            for cell in Cell.all_objects.filter(row__dataset_id=dataset.id)
        ),
    }


def edit_question(dataset, position):
    """A member changes the question of the row at this position."""
    from model_hub.models.develop_dataset import Cell, Column, Row

    row = Row.no_workspace_objects.get(dataset_id=dataset.id, order=position)
    column = Column.no_workspace_objects.get(
        dataset_id=dataset.id, name=QUESTION[0], source=QUESTION[1]
    )
    Cell.no_workspace_objects.filter(row=row, column=column).update(
        value="Which colour modes does the lamp have?"
    )


def preview(value):
    """What row history keeps of a cell value: its first 2048 characters."""
    return None if value is None else value[:2048]


def run(*args):
    out, err = StringIO(), StringIO()
    call_command(COMMAND, *args, stdout=out, stderr=err)
    assert err.getvalue() == ""
    return out.getvalue().splitlines()


def run_failing(*args):
    """Run the command when it must end with an error: (printed lines, the error)."""
    out, err = StringIO(), StringIO()
    with pytest.raises(CommandError) as raised:
        call_command(COMMAND, *args, stdout=out, stderr=err)
    return out.getvalue().splitlines(), raised.value


def run_and_capture(*args):
    """Run the command: (printed lines, every SQL statement it sent)."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as queries:
        lines = run(*args)
    statements = [query["sql"] for query in queries.captured_queries]
    assert statements
    return lines, statements


def row_locks(statements):
    """Each statement that locks rows, as '<table> <lock clause>'."""
    found = []
    for sql in statements:
        lock = ROW_LOCK.search(sql)
        if lock:
            table = re.search(r"\bFROM\s+(\S+)", sql)
            found.append(f"{table.group(1) if table else sql[:80]} {lock.group(0).upper()}")
    return found


def writes(statements):
    """Each statement that inserts, updates or deletes rows, as '<verb> <table>'."""
    found = []
    for sql in statements:
        words = sql.lstrip(" (\n").split()
        verb = words[0].upper() if words else ""
        if verb in WRITE_VERBS:
            target = [word for word in words[1:4] if word.upper() not in ("INTO", "FROM")]
            found.append(f"{verb} {target[0] if target else ''}".strip())
    return found


def table_row_counts():
    """Table name -> number of rows, for every table and partitioned table in the database."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT c.relname FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind IN ('r', 'p') "
            "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
            "AND pg_catalog.pg_table_is_visible(c.oid) ORDER BY c.relname"
        )
        tables = [name for (name,) in cursor.fetchall()]
        found = {}
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {connection.ops.quote_name(table)}")
            found[table] = cursor.fetchone()[0]
    return found


def changed_counts(before, after):
    """Table name -> (rows before, rows after), for each table whose count changed."""
    return {
        table: (before.get(table), after.get(table))
        for table in sorted(set(before) | set(after))
        if before.get(table) != after.get(table)
    }


@contextmanager
def first_write_fails():
    """The first organization's cells are written, then the database connection fails."""
    from django.db import DatabaseError

    from model_hub.services import row_activity_writes

    real = row_activity_writes.row_activity_actor
    uses = []

    @contextmanager
    def actor(user, *, origin="write"):
        uses.append(origin)
        with real(user, origin=origin):
            yield
        if len(uses) == 1:
            raise DatabaseError("server closed the connection unexpectedly")

    with patch.object(row_activity_writes, "row_activity_actor", actor):
        yield


@contextmanager
def reading_fails_for(organization_id):
    """Reading this organization's demo dataset fails."""
    from django.db import DatabaseError

    from accounts.services import demo_row_repair

    real = demo_row_repair._plan_dataset

    def plan(dataset, *args, **kwargs):
        if dataset.organization_id == organization_id:
            raise DatabaseError("canceling statement due to statement timeout")
        return real(dataset, *args, **kwargs)

    with patch.object(demo_row_repair, "_plan_dataset", plan):
        yield


def test_the_fixture_rows_are_rows_of_an_earlier_demo_file():
    from accounts.base_data import dataset_data, experiment_data

    assert len(dataset_data["table"]) == len(experiment_data["table"]) == SEEDED_ROWS
    prior = json.loads(PRIOR_ROW_HASHES.read_text())["files"]
    earlier_table = {d for digests in prior["table_data.json"].values() for d in digests}
    earlier_experiment = {
        d for digests in prior["experiment.json"].values() for d in digests
    }
    rows = earlier_rows()
    assert len(rows) == 3
    for row in rows:
        assert file_row_hash(row["table"]) in earlier_table
        assert file_row_hash(row["experiment"]) in earlier_experiment
        assert row["table"]["row_id"] == row["experiment"]["row_id"]
        assert dataset_data["table"][row["position"]]["row_id"] == row["table"]["row_id"]
        assert file_row_hash(row["table"]) != file_row_hash(
            dataset_data["table"][row["position"]]
        )


@pytest.mark.django_db
def test_unedited_earlier_rows_are_replaced_and_an_edited_row_is_left():
    dataset = seed_organization()
    edit_question(dataset, EDITED_POSITION)
    before = cells_by_position(dataset)
    new_organization = cells_by_position(seed_organization(earlier=False))
    shape = snapshot(dataset)

    assert run() == counts(touched=1, replaced=2, edited=1)

    after = cells_by_position(dataset)
    for position in range(SEEDED_ROWS):
        if position in EARLIER_POSITIONS and position != EDITED_POSITION:
            # Every cell now holds what a new organization gets for this row.
            assert before[position] != new_organization[position]
            assert after[position] == new_organization[position], position
        else:
            assert after[position] == before[position], position
    assert before[EDITED_POSITION][QUESTION][0] == "Which colour modes does the lamp have?"
    # Cells are rewritten in place: no dataset, column, row or cell is added or removed.
    now = snapshot(dataset)
    assert now["dataset"][0][:4] == shape["dataset"][0][:4]
    assert now["columns"] == shape["columns"]
    assert now["rows"] == shape["rows"]
    assert [cell[:3] for cell in now["cells"]] == [cell[:3] for cell in shape["cells"]]


@pytest.mark.django_db
def test_a_second_run_changes_nothing():
    dataset = seed_organization()
    edit_question(dataset, EDITED_POSITION)
    assert run()[1] == "rows replaced: 2"
    after_first = snapshot(dataset)

    assert run() == counts(edited=1)
    assert snapshot(dataset) == after_first


@pytest.mark.django_db
def test_dry_run_prints_only_the_counts_and_changes_nothing():
    dataset = seed_organization()
    edit_question(dataset, EDITED_POSITION)
    before = snapshot(dataset)

    assert run("--dry-run") == [
        "organizations touched: 1",
        "rows that would be replaced: 2",
        "rows left because edited: 1",
        "rows left because added after the seed: 0",
        "rows left because the dataset's seeded columns changed: 0",
        "organizations failed: 0",
    ]
    assert snapshot(dataset) == before


@pytest.mark.django_db
def test_a_dry_run_locks_no_row_and_writes_nothing():
    dataset = seed_organization()
    edit_question(dataset, EDITED_POSITION)
    before = table_row_counts()
    assert before["model_hub_cell"] > 0
    assert "model_hub_rowactivity" in before

    lines, statements = run_and_capture("--dry-run")

    after = table_row_counts()
    # No statement takes a row lock, none inserts, updates or deletes, and
    # every table holds as many rows as before.
    assert (row_locks(statements), writes(statements), changed_counts(before, after)) == (
        [],
        [],
        {},
    )
    assert lines == counts(touched=1, replaced=2, edited=1, dry_run=True)


@pytest.mark.django_db
def test_a_real_run_locks_and_writes_what_a_dry_run_does_not():
    dataset = seed_organization()
    edit_question(dataset, EDITED_POSITION)
    before = table_row_counts()

    _lines, statements = run_and_capture()

    after = table_row_counts()
    # The same checks see the locks and the writes of a real run: the demo
    # dataset and its cells are locked, cells are updated, and row history
    # gains one row per cell whose value changed.
    locks = [sql for sql in statements if ROW_LOCK.search(sql)]
    assert any('"model_hub_dataset"' in sql for sql in locks)
    assert any('"model_hub_cell"' in sql for sql in locks)
    assert 'UPDATE "model_hub_cell"' in writes(statements)
    rows_before, rows_after = changed_counts(before, after)["model_hub_rowactivity"]
    assert rows_after > rows_before


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change, columns_changed",
    [
        ("source", 0),
        ("name", 0),
        ("column", SEEDED_ROWS),
    ],
    ids=["another_source", "another_name", "a_seeded_column_renamed"],
)
def test_a_dataset_that_is_not_the_intact_demo_dataset_is_untouched(
    change, columns_changed
):
    from model_hub.models.develop_dataset import Column, Dataset

    dataset = seed_organization()
    if change == "source":
        Dataset.no_workspace_objects.filter(id=dataset.id).update(source="build")
    elif change == "name":
        Dataset.no_workspace_objects.filter(id=dataset.id).update(name="Demo-dataset copy")
    else:
        Column.no_workspace_objects.filter(
            dataset_id=dataset.id, name="generated answer"
        ).update(name="answer draft")
    before = snapshot(dataset)

    # The rows look exactly like seeded earlier rows, but the dataset is not
    # the intact seeded demo dataset, so none of them is replaced. A demo
    # dataset with a seeded column renamed has every row counted on its own line.
    assert run() == counts(columns_changed=columns_changed)
    assert snapshot(dataset) == before


@pytest.mark.django_db
def test_a_demo_dataset_seeded_without_the_annotation_column_is_repaired():
    from model_hub.models.develop_dataset import Column

    dataset = seed_organization(annotation=False)
    assert not Column.no_workspace_objects.filter(
        dataset_id=dataset.id, source=ANNOTATION_SOURCE
    ).exists()
    new_organization = cells_by_position(
        seed_organization(earlier=False, annotation=False)
    )

    assert run() == counts(touched=1, replaced=3)
    after = cells_by_position(dataset)
    for position in EARLIER_POSITIONS:
        assert after[position] == new_organization[position], position


@pytest.mark.django_db
def test_a_copy_of_an_earlier_row_is_left():
    from model_hub.models.develop_dataset import Cell, Row

    dataset = seed_organization(annotation=False)
    # A member copies an earlier row into the dataset: the copy holds the same
    # cells as the seeded row but sits after the last seeded row.
    source = Row.no_workspace_objects.get(dataset_id=dataset.id, order=EARLIER_POSITIONS[0])
    copy_row = Row.no_workspace_objects.create(dataset=dataset, order=SEEDED_ROWS)
    Cell.no_workspace_objects.bulk_create(
        Cell(
            dataset=dataset,
            column_id=cell.column_id,
            row=copy_row,
            value=cell.value,
            value_infos=cell.value_infos,
            feedback_info=cell.feedback_info,
            status=cell.status,
        )
        for cell in Cell.no_workspace_objects.filter(row=source)
    )
    copied = cells_by_position(dataset)[SEEDED_ROWS]
    assert copied == cells_by_position(dataset)[EARLIER_POSITIONS[0]]

    assert run() == counts(touched=1, replaced=3, added=1)
    assert cells_by_position(dataset)[SEEDED_ROWS] == copied


@pytest.mark.django_db
def test_only_the_named_organization_is_repaired():
    named = seed_organization()
    other = seed_organization()
    other_before = snapshot(other)

    assert run("--organization", str(named.organization_id)) == counts(
        touched=1, replaced=3
    )
    assert snapshot(other) == other_before
    with pytest.raises(CommandError):
        run("--organization", "not-an-id")


@pytest.mark.django_db
def test_an_organization_that_fails_is_rolled_back_and_the_next_is_still_repaired():
    from model_hub.models.row_activity import RowActivity

    first, second = two_seeded_organizations()
    first_before = snapshot(first)
    new_organization = cells_by_position(seed_organization(earlier=False))

    with first_write_fails():
        lines, error = run_failing()

    assert lines == counts(touched=1, replaced=3, failed=1)
    assert error.returncode != 0
    assert str(first.organization_id) not in str(error)
    # The first organization's cells were written, then rolled back with its history.
    assert snapshot(first) == first_before
    assert not RowActivity.objects.filter(
        row__dataset_id=first.id, origin="demo_repair"
    ).exists()
    after = cells_by_position(second)
    for position in EARLIER_POSITIONS:
        assert after[position] == new_organization[position], position


@pytest.mark.django_db
def test_a_dry_run_counts_the_other_organizations_when_one_fails():
    first, second = two_seeded_organizations()
    before = [snapshot(first), snapshot(second)]

    with reading_fails_for(first.organization_id):
        lines, error = run_failing("--dry-run")

    assert lines == counts(touched=1, replaced=3, failed=1, dry_run=True)
    assert error.returncode != 0
    assert str(first.organization_id) not in str(error)
    assert [snapshot(first), snapshot(second)] == before


@pytest.mark.django_db
def test_each_replaced_cell_whose_value_changed_records_one_system_edit():
    from model_hub.models.develop_dataset import Cell
    from model_hub.models.row_activity import RowActivity

    dataset = seed_organization()

    def stored():
        return {
            cell.id: (cell.value, cell.value_infos, cell.feedback_info, cell.status)
            for cell in Cell.no_workspace_objects.filter(
                row__dataset_id=dataset.id, row__order__in=EARLIER_POSITIONS
            )
        }

    before = stored()
    call_command(COMMAND, stdout=StringIO(), stderr=StringIO())
    after = stored()

    rewritten = {cell_id for cell_id in before if after[cell_id] != before[cell_id]}
    value_changed = {
        cell_id for cell_id in before if after[cell_id][0] != before[cell_id][0]
    }
    # Some rewritten cells keep their value and change only value_infos or
    # feedback_info. Row history records an edit only for a changed value.
    assert value_changed
    assert value_changed < rewritten
    events = list(
        RowActivity.objects.filter(row__dataset_id=dataset.id, origin="demo_repair")
    )
    assert sorted(event.cell_id for event in events) == sorted(value_changed)
    for event in events:
        assert (event.subject, event.action, event.actor, event.actor_id) == (
            "cell",
            "edit",
            "System",
            None,
        )
        assert event.before_value == preview(before[event.cell_id][0])
        assert event.after_value == preview(after[event.cell_id][0])
