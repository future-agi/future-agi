"""Replace the demo rows an earlier build seeded, where nobody has changed them.

Every organization gets a seeded RAG demo dataset ("Demo-dataset"). Organizations
seeded by an earlier build hold the earlier rows. This module finds those rows
and rewrites their cells with the rows the seed writes now.

A dataset is the seeded demo dataset only when all of these hold: its source is
"demo", its name is "Demo-dataset", it is not deleted, and it has each column
the seed creates exactly once (matched by name and source; the annotation label
column is optional because some earlier seeds did not create it). Copies made
with duplicate or clone get another source and only text columns, and an
upload creates only text columns, so none of them has this column set.

A row is replaced only when all of these hold: it has exactly one cell in each
seeded column of its dataset and no cell in any other column, those cells hold
byte for byte what an earlier seed wrote for one earlier row (checked against
the digests in demo_row_repair_prior_rows.json, which carry no row text), and
the row is still at the position that earlier row was seeded at. Any other row
is left as it is. A row that already holds the current seeded row is current.

Cells are rewritten in place: no row, column or dataset is created or deleted,
and nothing else the seed made (experiment, optimization, evaluations,
annotation, run prompt) is changed. The image demo dataset is not read.

Every row that is left is counted with its reason: edited (a seeded cell
changed or removed, or a cell added in another column), added after the seed
(the row sits at a position no seed wrote a row at; the product puts a new or
copied row after the last row), or in a demo dataset whose seeded columns
changed (then every row of that dataset counts there).

A real run writes each organization in its own transaction and locks its demo
datasets and the cells it reads until that transaction ends. A dry run opens
no transaction and locks nothing. An error in one organization rolls back that
organization only; it is counted and the run goes on with the next one.
"""

import copy
import hashlib
import json
from collections import Counter, defaultdict
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path

DEMO_DATASET_NAME = "Demo-dataset"
DEMO_DATASET_SOURCE = "demo"
# Row history names this change as made by the system with this origin.
ACTIVITY_ORIGIN = "demo_repair"
PRIOR_ROWS = Path(__file__).resolve().with_name("demo_row_repair_prior_rows.json")

# Each column the seed creates, by its id in the seed files: the name and the
# source of the column the seed creates for it.
TABLE_COLUMNS = {
    "2e25aa95-7fb0-4804-8319-219cb7981c63": ("context", "OTHERS"),
    "cf635e5b-92af-460d-a62b-8b22c64287d9": ("question", "OTHERS"),
    "6ed50ca8-4aeb-48db-a68d-7a570e3a92ac": ("answer", "OTHERS"),
    "4a502202-ad6e-4cea-a295-426cf977dfb4": ("generated answer", "run_prompt"),
    "2ecba19c-20de-4431-840e-74aac636f155": (
        "Optimization-gpt-4o-mini-old-prompt",
        "optimisation",
    ),
    "a882962e-9f50-4e2c-9c58-868471f4903f": (
        "Optimization-gpt-4o-mini-new-prompt",
        "optimisation",
    ),
    "75faf354-d038-409c-a1ba-2810d863c3c8": (
        "Factual Accuracy-Optimization-gpt-4o-mini-new-prompt",
        "optimisation_evaluation",
    ),
    "5114b3d5-21be-4932-b0af-293083ca894e": (
        "Factual Accuracy-Optimization-gpt-4o-mini-old-prompt",
        "optimisation_evaluation",
    ),
    "a8b7e149-2048-4521-98ec-2341b67fd646": ("Answer Similarity", "evaluation"),
    "6753effc-70b0-49f1-97cf-764e4e52f834": (
        "Answer Similarity-reason",
        "evaluation_reason",
    ),
    "ad4b782d-950a-41b1-a030-0b5d08b5b7ad": ("Groundedness", "evaluation"),
    "95693a5a-1a6b-4051-916d-9b757a62ea2c": (
        "Groundedness-reason",
        "evaluation_reason",
    ),
}
# The seed names the annotation label column itself; some earlier seeds did not
# create it, so a dataset may have it or not.
ANNOTATION_COLUMN_ID = "caa28acb-df8b-4702-8878-271a4514ec85"
ANNOTATION_COLUMN = ("Context Relevancy:is_relevant:1", "annotation_label")
EXPERIMENT_COLUMNS = {
    "67a09509-ed10-4dee-bb39-6e62d5e41f5c": (
        "Experiment-prompt1-o1-mini",
        "experiment",
    ),
    "060040e2-611d-4e54-a5c1-c8efec3c77a1": (
        "Answer Similarity-Experiment-prompt1-o1-mini",
        "experiment_evaluation",
    ),
    "8f5c9cec-1979-4bad-91f3-16c12f914dd2": (
        "Answer Similarity-Experiment-prompt1-o1-mini-reason",
        "experiment_evaluation",
    ),
    "8b64215e-d754-4a3e-ac75-7acc4127a10f": (
        "Groundedness-Experiment-prompt1-o1-mini",
        "experiment_evaluation",
    ),
    "0edbd24d-e3b4-4cec-997d-4d2f8ef41acf": (
        "Groundedness-Experiment-prompt1-o1-mini-reason",
        "experiment_evaluation",
    ),
}
REQUIRED_COLUMNS = frozenset(TABLE_COLUMNS) | frozenset(EXPERIMENT_COLUMNS)
SEEDED_COLUMNS = {
    **TABLE_COLUMNS,
    ANNOTATION_COLUMN_ID: ANNOTATION_COLUMN,
    **EXPERIMENT_COLUMNS,
}


def _stored_text(value):
    """What a text cell stores for a seed value: str() of anything not a string."""
    if value is None or isinstance(value, str):
        return value
    return str(value)


def _table_cell(cell):
    value_infos = cell.get("value_infos")
    return [
        _stored_text(cell.get("cell_value")),
        json.dumps(value_infos) if value_infos is not None else None,
        cell.get("feedback_info", {}),
        "pass",
    ]


def _experiment_cell(cell):
    value_infos = copy.deepcopy(cell.get("value_infos"))
    if isinstance(value_infos, dict) and "metadata" in cell:
        value_infos["metadata"] = cell.get("metadata")
    return [
        _stored_text(cell.get("cell_value")),
        json.dumps(value_infos) if value_infos is not None else None,
        {},
        "pass",
    ]


def seeded_cells(table_row, experiment_row, *, with_annotation):
    """The cells the seed writes for one row: seed column id -> stored fields.

    The stored fields are [value, value_infos, feedback_info, status], as the
    seed saves them and as they read back.
    """
    cells = {}
    columns = list(TABLE_COLUMNS) + ([ANNOTATION_COLUMN_ID] if with_annotation else [])
    for column_id in columns:
        if column_id in table_row:
            cells[column_id] = _table_cell(table_row[column_id])
    if experiment_row is not None:
        for column_id in EXPERIMENT_COLUMNS:
            if column_id in experiment_row:
                cells[column_id] = _experiment_cell(experiment_row[column_id])
    return cells


def row_digest(cells):
    """sha256 over the stored fields of every cell of a row, keyed by seed column id."""
    canonical = json.dumps(cells, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def seeded_rows(table_data, experiment_data):
    """(digest, row id, position, cells) for each row, with and without the annotation cell."""
    experiment_rows = {row["row_id"]: row for row in experiment_data["table"]}
    for position, table_row in enumerate(table_data["table"]):
        experiment_row = experiment_rows.get(table_row["row_id"])
        for with_annotation in (True, False):
            cells = seeded_cells(table_row, experiment_row, with_annotation=with_annotation)
            yield row_digest(cells), table_row["row_id"], position, cells


def load_prior_rows(path=PRIOR_ROWS):
    """Digest of an earlier seeded row -> {"row_id", "position"}."""
    return json.loads(Path(path).read_text())["rows"]


def current_rows():
    """Digest -> (row id, position, cells) for the rows the seed writes now."""
    from accounts.base_data import dataset_data, experiment_data

    return {
        digest: (row_id, position, cells)
        for digest, row_id, position, cells in seeded_rows(dataset_data, experiment_data)
    }


def seeded_positions(prior, current):
    """Every position a seed wrote a row at, in the earlier seeds and the current one."""
    return {earlier["position"] for earlier in prior.values()} | {
        position for _row_id, position, _cells in current.values()
    }


@dataclass
class Outcome:
    """One organization's counts."""

    replaced: int = 0
    left_edited: int = 0
    left_added: int = 0
    left_columns_changed: int = 0


@dataclass
class Totals:
    organizations: int = 0
    replaced: int = 0
    left_edited: int = 0
    left_added: int = 0
    left_columns_changed: int = 0
    failed: int = 0
    # Exception class name -> organizations that failed with it.
    failures: Counter = field(default_factory=Counter)


@dataclass
class _Plan:
    replace: list = field(default_factory=list)
    left_edited: int = 0
    left_added: int = 0
    left_columns_changed: int = 0


def demo_dataset_organizations(organization_id=None):
    """Ids of organizations with a live dataset named and sourced as the demo dataset."""
    from model_hub.models.develop_dataset import Dataset

    datasets = Dataset.no_workspace_objects.filter(
        source=DEMO_DATASET_SOURCE, name=DEMO_DATASET_NAME
    )
    if organization_id is not None:
        datasets = datasets.filter(organization_id=organization_id)
    return sorted(set(datasets.values_list("organization_id", flat=True)), key=str)


def _seeded_columns_of(dataset):
    """Live column id -> seed column id, or None when the seeded column set is not intact."""
    from model_hub.models.develop_dataset import Column

    by_name_and_source = {value: key for key, value in SEEDED_COLUMNS.items()}
    found = {}
    for column_id, name, source in Column.no_workspace_objects.filter(
        dataset_id=dataset.id
    ).values_list("id", "name", "source"):
        key = by_name_and_source.get((name, source))
        if key is None:
            continue
        if key in found.values():
            return None
        found[column_id] = key
    if not REQUIRED_COLUMNS <= set(found.values()):
        return None
    return found


def _plan_dataset(dataset, prior, current, positions_seeded, *, lock):
    """The rows of one demo dataset to replace, and the rows left by reason.

    With lock=True the cells are read with SELECT ... FOR UPDATE; otherwise
    they are read without a lock.
    """
    from model_hub.models.develop_dataset import Cell, Row

    plan = _Plan()
    positions = dict(
        Row.no_workspace_objects.filter(dataset_id=dataset.id).values_list("id", "order")
    )
    columns = _seeded_columns_of(dataset)
    if columns is None:
        plan.left_columns_changed = len(positions)
        return plan
    seeded = {}
    for row_id, position in positions.items():
        if position in positions_seeded:
            seeded[row_id] = position
        else:
            plan.left_added += 1
    required = set(columns.values())
    live_cells = Cell.no_workspace_objects.filter(row_id__in=list(seeded)).order_by("id")
    if lock:
        live_cells = live_cells.select_for_update()
    cells_by_row = defaultdict(dict)
    unexpected = set()
    for cell in live_cells:
        key = columns.get(cell.column_id)
        if key is None or key in cells_by_row[cell.row_id]:
            unexpected.add(cell.row_id)
            continue
        cells_by_row[cell.row_id][key] = cell
    for row_id, position in seeded.items():
        cells = cells_by_row.get(row_id, {})
        if row_id in unexpected or set(cells) != required:
            plan.left_edited += 1
            continue
        digest = row_digest(
            {
                key: [cell.value, cell.value_infos, cell.feedback_info, cell.status]
                for key, cell in cells.items()
            }
        )
        earlier = prior.get(digest)
        if earlier is not None and earlier["position"] == position:
            plan.replace.append((cells, earlier["row_id"]))
        elif digest not in current:
            plan.left_edited += 1
    return plan


def _new_cells(current):
    """Seed row id -> the current row's cells, with the annotation cell."""
    rows = {}
    for row_id, _position, cells in current.values():
        if ANNOTATION_COLUMN_ID in cells or row_id not in rows:
            rows[row_id] = cells
    return rows


def repair_organization(organization_id, *, apply, prior=None, current=None):
    """Plan, and with apply=True write, one organization's repair.

    With apply=True it is one transaction: the organization's demo datasets
    and the cells read are locked (SELECT ... FOR UPDATE) until it ends, and an
    error rolls back the whole organization. With apply=False it opens no
    transaction and takes no lock. Returns the organization's Outcome.
    """
    from django.db import transaction
    from django.utils import timezone

    from model_hub.models.develop_dataset import Cell, Dataset
    from model_hub.services.row_activity_writes import row_activity_actor

    prior = load_prior_rows() if prior is None else prior
    current = current_rows() if current is None else current
    positions_seeded = seeded_positions(prior, current)
    outcome = Outcome()
    with transaction.atomic() if apply else nullcontext():
        datasets = Dataset.no_workspace_objects.filter(
            organization_id=organization_id,
            source=DEMO_DATASET_SOURCE,
            name=DEMO_DATASET_NAME,
        ).order_by("id")
        if apply:
            datasets = datasets.select_for_update()
        to_replace = []
        for dataset in list(datasets):
            plan = _plan_dataset(dataset, prior, current, positions_seeded, lock=apply)
            outcome.replaced += len(plan.replace)
            outcome.left_edited += plan.left_edited
            outcome.left_added += plan.left_added
            outcome.left_columns_changed += plan.left_columns_changed
            to_replace.extend(plan.replace)
        if apply and to_replace:
            new_rows = _new_cells(current)
            now = timezone.now()
            updates = []
            for cells, row_id in to_replace:
                fresh = new_rows[row_id]
                for key, cell in cells.items():
                    cell.value, cell.value_infos, cell.feedback_info, cell.status = fresh[key]
                    cell.updated_at = now
                    updates.append(cell)
            with row_activity_actor(None, origin=ACTIVITY_ORIGIN):
                Cell.no_workspace_objects.bulk_update(
                    updates,
                    ["value", "value_infos", "feedback_info", "status", "updated_at"],
                    batch_size=500,
                )
    return outcome


def repair_demo_rows(*, apply, organization_id=None):
    """Repair every organization (or one), each on its own.

    An error in one organization does not stop the run: a real run has already
    rolled that organization back, the error is counted by its exception class
    name (no id, no text), the organization's counts are left out, and the run
    goes on with the next organization.
    """
    prior = load_prior_rows()
    current = current_rows()
    totals = Totals()
    for org_id in demo_dataset_organizations(organization_id):
        try:
            outcome = repair_organization(
                org_id, apply=apply, prior=prior, current=current
            )
        except Exception as error:  # noqa: BLE001 - one organization must not stop the others
            totals.failed += 1
            totals.failures[type(error).__name__] += 1
            continue
        totals.organizations += 1 if outcome.replaced else 0
        totals.replaced += outcome.replaced
        totals.left_edited += outcome.left_edited
        totals.left_added += outcome.left_added
        totals.left_columns_changed += outcome.left_columns_changed
    return totals
