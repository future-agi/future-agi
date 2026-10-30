"""The record of every reader of the span tables, decided one by one.

The traces of a simulated run live in a hidden project that the project
managers leave out. A reader that takes its projects from a manager therefore
never sees them, and the few readers that open the hidden project do it on
purpose, by an id the caller was allowed to read. This file keeps that true as
the code grows: every function or SQL template that can read a span is filed
here under the category whose claim it makes, with a reason, and the claim is
held to the code wherever the code can show it. A reader nobody filed fails the
first case below, so a new reader cannot ship until someone has decided.

A reader is the source of a function (the functions and classes nested in it
count on their own), or of a module-level template, that matches
``READS_A_TABLE`` or builds the hourly rollup source. A file is never filed as
a whole: one file mixes readers of several kinds.

The scan only reads source files. The one case that counts Postgres rows is the
only one that opens a connection, and it replaces the exporter boundary.
"""

from __future__ import annotations

import ast
import fnmatch
import functools
import io
import os
import re
import tokenize
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple

import pytest

FUTUREAGI = Path(__file__).resolve().parents[2]

# A FROM clause over the spans table, over the rollup of trace counts, or over a
# template (``FROM {...}``) whose table only the reader knows, which is why a
# person names it. Whatever builds the hourly rollup source reads one as well.
READS_A_TABLE = re.compile(r"FROM\s+(\{[^}]*\}|spans\b|trace_count_rollup\b)")
HOURLY_ROLLUP_SOURCE = "hourly_aggregate_state_source("
SPAN_TABLES = (
    "spans",
    "trace_count_rollup",
    "span_metrics_hourly",
    "hourly_aggregate",
    "observation_span",
)

# Public ``ee`` code is always scanned; only the private cloud checkout is optional.
CLOUD_PREFIX = "ee.cloud."

# ---------------------------------------------------------------------------
# The categories
# ---------------------------------------------------------------------------

# BY_ID_ALONE, NOT_A_USER_READ and ROW_DRIVEN are added to the contract's five,
# pending the owner.

# Runs for project ids that a view or builder took from a project manager, which
# never returns the hidden project. Project ids read from a stored row (a monitor,
# an eval task, a config) or joined through ``project__`` use no manager, and
# those readers are ROW_DRIVEN. The reader's text, or its class's or module's,
# names a project. That is a weak check: the filing is a person's word.
PROJECT_SET = "takes its project set from a project manager"
# Opens the hidden project of a run's agent because the by-id reads hand it that
# project. A closed set of readers, each called only from the modules listed in
# ``HIDDEN_READERS``.
HIDDEN_ON_PURPOSE = "reads the hidden project on purpose"
# Deployment telemetry counts every span and trace, hidden projects included.
COUNTS_ON_PURPOSE = "counts it on purpose"
# Matched only through a ``FROM {...}`` template over a table that holds no
# span; its filing names that table.
NO_SPAN_TABLE = "reads no span table"
# Reads the trace, span or session ids it is given. A project, if it takes one,
# only narrows the read. Hidden data reaches it only if it is handed a hidden id,
# which only the by-id reads hand out.
BY_ID_ALONE = "takes a trace, span or session id; a project only narrows it"
# Operator tooling, schema and change-data-capture code, or code nothing calls.
NOT_A_USER_READ = "no user request reaches it"
# Runs for the project of a stored row (a scan setting, an eval task, an alert
# monitor, an eval config, an end user, a session, a project version) or of a
# ``project__`` join over such rows, and takes no project from a manager. It is
# safe because a simulated run never creates such a row for a hidden project. The
# filing names the model in ``row``, and the case at the end of this file pins
# that model in ``ROW_TYPES`` and counts its rows.
ROW_DRIVEN = "runs for the project of a row a simulated run never makes for it"
CATEGORIES = (
    PROJECT_SET,
    HIDDEN_ON_PURPOSE,
    COUNTS_ON_PURPOSE,
    NO_SPAN_TABLE,
    BY_ID_ALONE,
    NOT_A_USER_READ,
    ROW_DRIVEN,
)

# The children rows are the contract's table of readers that now see child
# spans. A production call has child spans under its top span, so a reader that
# adds up every span of a trace or a project shows new numbers. These readers are
# filed under one of these rows and under a category above, so the list of them
# is known.
CHILDREN_ERROR_FEED_LATENCY = "children counted: error-feed latency"
CHILDREN_MONITOR_RESPONSE_TIME = "children counted: monitor SPAN_RESPONSE_TIME"
CHILDREN_MONITOR_FUNCTION_ERRORS = (
    "children counted: monitor ERROR_RATES_FOR_FUNCTION_CALLING"
)
CHILDREN_PROJECT_CHART = "children counted: project chart, unfiltered"
CHILDREN_SPAN_EVAL_TASKS = "children counted: span-level eval tasks"
CHILDREN_VOICE_EVAL_CELLS = "children counted: voice list eval cells"
CHILDREN_ATTRIBUTE_PICKERS = "children counted: attribute pickers and key catalog"
CHILDREN_TRACE_FILTERS = (
    "children counted: trace filters on type, name, status and values; span list"
)
CHILDREN_ROWS = (
    CHILDREN_ERROR_FEED_LATENCY,
    CHILDREN_MONITOR_RESPONSE_TIME,
    CHILDREN_MONITOR_FUNCTION_ERRORS,
    CHILDREN_PROJECT_CHART,
    CHILDREN_SPAN_EVAL_TASKS,
    CHILDREN_VOICE_EVAL_CELLS,
    CHILDREN_ATTRIBUTE_PICKERS,
    CHILDREN_TRACE_FILTERS,
)
BUCKETS = CATEGORIES + CHILDREN_ROWS


class Filing(NamedTuple):
    buckets: tuple[str, ...]
    why: str
    table: str | None
    row: str | None = None


def filed(
    *buckets: str, why: str, table: str | None = None, row: str | None = None
) -> Filing:
    return Filing(buckets, why, table, row)


# ---------------------------------------------------------------------------
# Finding the readers
# ---------------------------------------------------------------------------

_NEVER_WALKED = {"node_modules", "__pycache__", "site-packages", "venv"}


def reads_a_table(text):
    return bool(READS_A_TABLE.search(text)) or HOURLY_ROLLUP_SOURCE in text


def is_test_file(relative):
    return (
        "tests" in relative.parts[:-1]
        or relative.name.startswith("test_")
        or relative.name == "conftest.py"
    )


@functools.cache
def non_test_python_files():
    """Every non-test ``.py`` under ``futureagi/``, relative to it."""

    found = []
    for directory, subdirectories, names in os.walk(FUTUREAGI):
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if name not in _NEVER_WALKED and not name.startswith(".")
        )
        for name in sorted(names):
            if name.endswith(".py"):
                relative = (Path(directory) / name).relative_to(FUTUREAGI)
                if not is_test_file(relative):
                    found.append(relative)
    return tuple(found)


@functools.cache
def sources():
    """``{relative path: source}`` of every non-test file."""

    return {
        relative: (FUTUREAGI / relative).read_text(encoding="utf-8")
        for relative in non_test_python_files()
    }


def module_name(relative):
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _blocks(node):
    for field in ("body", "orelse", "finalbody"):
        block = getattr(node, field, None)
        if isinstance(block, list):
            yield block
    for handler in getattr(node, "handlers", ()):
        yield handler.body
    for case in getattr(node, "cases", ()):
        yield case.body


def _own_text(node, lines):
    """The source of ``node`` without the functions and classes nested in it."""

    nested = set()
    for child in ast.walk(node):
        if child is not node and isinstance(
            child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            nested.update(range(child.lineno, child.end_lineno + 1))
    return "".join(
        lines[number - 1]
        for number in range(node.lineno, node.end_lineno + 1)
        if number not in nested
    )


def readers_in(source, module):
    """``{"module:qualname": text}`` for each function or template that reads."""

    lines = source.splitlines(keepends=True)
    found = {}

    def walk(body, prefix, inside_function):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = prefix + node.name
                text = _own_text(node, lines)
                if reads_a_table(text):
                    found[f"{module}:{qualname}"] = text
                walk(node.body, qualname + ".<locals>.", True)
            elif isinstance(node, ast.ClassDef):
                walk(node.body, prefix + node.name + ".", inside_function)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                text = "".join(lines[node.lineno - 1 : node.end_lineno])
                if not inside_function and reads_a_table(text):
                    target = (
                        node.targets[0] if isinstance(node, ast.Assign) else node.target
                    )
                    found[f"{module}:{prefix}{ast.unparse(target)}"] = text
            else:
                for block in _blocks(node):
                    walk(block, prefix, inside_function)

    walk(ast.parse(source).body, "", False)
    return found


def scan_readers():
    """Every reader the rule matches, and how many files were looked at."""

    readers = {}
    files = non_test_python_files()
    for relative, source in sources().items():
        # Only a file that mentions a table can hold a reader: skipping the rest
        # keeps the parse to the few files that matter.
        if reads_a_table(source):
            readers.update(readers_in(source, module_name(relative)))
    return readers, len(files)


def without_prose(source):
    """``source`` with comments and docstrings blanked, so line numbers hold."""

    lines = source.splitlines(keepends=True)
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            (row, start), (_, end) = token.start, token.end
            line = lines[row - 1]
            lines[row - 1] = line[:start] + " " * (end - start) + line[end:]
    for node in ast.walk(ast.parse(source)):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                line = lines[first.lineno - 1]
                indent = line[: len(line) - len(line.lstrip())]
                if first.col_offset != len(indent):
                    continue
                # A body that was only a docstring stays a valid body.
                lines[first.lineno - 1] = indent + "pass\n"
                for row in range(first.lineno + 1, first.end_lineno + 1):
                    lines[row - 1] = "\n"
    return "".join(lines)


def code_of(source):
    """What a file runs. A file that cannot be parsed is searched as written."""

    try:
        return without_prose(source)
    except (SyntaxError, tokenize.TokenError):
        return source


@functools.cache
def modules():
    return {module_name(relative): relative for relative in non_test_python_files()}


@functools.cache
def _code(relative):
    """What the file at ``relative`` runs, worked out once per file."""

    return code_of(sources()[relative])


def module_code(module):
    return _code(modules()[module])


@functools.cache
def module_tree(module):
    return ast.parse(module_code(module))


@functools.cache
def code_readers(module):
    return readers_in(module_code(module), module)


def functions_in(tree):
    """``(qualname, node)`` for every function defined anywhere in ``tree``."""

    def walk(body, prefix):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield prefix + node.name, node
                yield from walk(node.body, prefix + node.name + ".<locals>.")
            elif isinstance(node, ast.ClassDef):
                yield from walk(node.body, prefix + node.name + ".")
            else:
                for block in _blocks(node):
                    yield from walk(block, prefix)

    yield from walk(tree.body, "")


def files_naming(name, *, also=None):
    """Files that use ``name`` as a name, an attribute or an import.

    With ``also``, only the files that run code naming that as well. Prose does
    not count. Test files are not looked at.
    """

    found = set()
    for relative, text in sources().items():
        if name not in text:
            continue
        if also is not None and also not in _code(relative):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                (isinstance(node, ast.Name) and node.id == name)
                or (isinstance(node, ast.Attribute) and node.attr == name)
                or (isinstance(node, ast.alias) and node.name == name)
            ):
                found.add(relative.as_posix())
                break
    return found


# ---------------------------------------------------------------------------
# The inventory
# ---------------------------------------------------------------------------

SAMPLE_SOURCE = '''
"""Prose that says FROM spans is not a template."""
TEMPLATE = """SELECT 1 FROM spans WHERE 1"""
NOT_A_READER = "SELECT 1"


class Reader:
    ATTRIBUTE = "SELECT 1 FROM {table}"

    def reads(self):
        return f"SELECT 1 FROM {self.table}"

    def outer(self):
        def inner():
            return "SELECT 1 FROM spans"

        return inner

    def only_prose(self):
        """Mentions FROM spans without reading anything."""
        return 1


def rollup():
    return hourly_aggregate_state_source(1)
'''


@pytest.mark.unit
def test_the_scan_applies_the_rule_to_functions_and_templates():
    # The inventory is only as good as its scan: a scan that found nothing, or
    # that credited a nested function to its parent, would let readers go unfiled.
    found = readers_in(SAMPLE_SOURCE, "sample")

    assert set(found) == {
        "sample:TEMPLATE",
        "sample:Reader.ATTRIBUTE",
        "sample:Reader.reads",
        "sample:Reader.outer.<locals>.inner",
        "sample:Reader.only_prose",
        "sample:rollup",
    }
    assert set(readers_in(without_prose(SAMPLE_SOURCE), "sample")) == (
        set(found) - {"sample:Reader.only_prose"}
    )


# ---------------------------------------------------------------------------
# The filings
# ---------------------------------------------------------------------------

_ANNOTATION_CELLS = filed(
    NO_SPAN_TABLE,
    why="annotation cells of an already filtered page",
    table="model_hub_score",
)
_ATTRIBUTE_PICKER = filed(
    PROJECT_SET,
    CHILDREN_ATTRIBUTE_PICKERS,
    why=(
        "attribute key and value picker template, run for the project ids its view "
        "passes in"
    ),
)
_CANDIDATE_PROBE = filed(
    PROJECT_SET,
    why=(
        "candidate-only probe: returns a superset for the project ids it is given, and "
        "every path sends it through the classifier"
    ),
)
_CDC_LAG_CHECK = filed(
    NO_SPAN_TABLE,
    why="health check reads the lag of the change-data-capture tables",
    table="tracer_trace, trace_session and tracer_eval_logger",
)
_CONTINUOUS_CANDIDATES = filed(
    ROW_DRIVEN,
    why=(
        "continuous eval task candidates: an arrival superset for the project of the "
        "task row, replayed by the row resolver through the list builders"
    ),
    row="tracer.EvalTask",
)
_DASHBOARD_SIDE_TABLE_STATEMENT = filed(
    PROJECT_SET,
    why=(
        "dashboard eval or annotation statement over a side table, joined to the spans "
        "of the dashboard's project ids"
    ),
)
_DASHBOARD_SOURCE = filed(
    PROJECT_SET,
    why=(
        "dashboard traces source: reads the spans of the project ids the dashboard is "
        "given"
    ),
)
_DASHBOARD_V2_SOURCE = filed(
    PROJECT_SET,
    why="dashboard v2 sources, read for the project ids the dashboard is given",
)
_DEAD_ANNOTATION_BUILDER = filed(
    NO_SPAN_TABLE,
    why="dead code: only the dead get_annotation_graph_data builds it",
    table="model_hub_score",
)
_DEAD_SPAN_READER = filed(
    NOT_A_USER_READ,
    why=(
        "dead code: no production caller; the live previous and next reads are list "
        "readers"
    ),
)
_DICTIONARY_DDL = filed(
    NO_SPAN_TABLE,
    why="dictionary DDL over replicated Postgres tables",
    table="the replicated Postgres table each dictionary names",
)
_EMBEDDING_STORE = filed(
    NO_SPAN_TABLE,
    why="embedding store reads",
    table="vector store tables named by the caller",
)
_ERROR_EMBEDDINGS = filed(
    NO_SPAN_TABLE,
    why="error clustering reads its own embeddings",
    table="error_embeddings",
)
_EVAL_CELLS = filed(
    NO_SPAN_TABLE,
    why="eval cells of an already filtered page",
    table="tracer_eval_logger",
)
_EVAL_CENTROIDS = filed(
    NO_SPAN_TABLE,
    why="eval clustering reads its centroids",
    table="cluster_centroids",
)
_EVAL_CHART_ROWS = filed(
    NO_SPAN_TABLE,
    why=(
        "eval chart statement over eval rows; the span membership is the filter "
        "fragment"
    ),
    table="tracer_eval_logger",
)
_EVAL_FILTER_LEAF = filed(
    NO_SPAN_TABLE,
    why="eval filter leaf",
    table="tracer_eval_logger",
)
_EVAL_LOG_BACKFILL = filed(
    NO_SPAN_TABLE,
    why="operator backfill of eval log columns",
    table="tracer_eval_logger",
)
_EVAL_MONITOR = filed(
    NO_SPAN_TABLE,
    ROW_DRIVEN,
    why=(
        "eval monitor: reads eval rows whose spans come through "
        "_bounded_spans_subquery, for the project of the monitor row"
    ),
    table="tracer_eval_logger",
    row="tracer.UserAlertMonitor",
)
_EVAL_ROWS_OF_A_CONFIG = filed(
    NO_SPAN_TABLE,
    why="eval rows of the config or traces it is given",
    table="tracer_eval_logger",
)
_EXACT_CHART_STATEMENT = filed(
    PROJECT_SET,
    why="exact project chart statement for the project ids the chart view passes in",
)
_FILTER_LEAF = filed(
    PROJECT_SET,
    why=(
        "filter-leaf subquery of a list or chart reader, scoped by the builder's "
        "project ids and never by a filter"
    ),
)
_FILTER_VALUE_PICKER = filed(
    PROJECT_SET,
    why=(
        "SQL of the system filter-value pickers "
        "(read_span_system_filter_value_cursor_page and "
        "read_span_system_filter_values, called by the dashboard filter-value "
        "endpoints and the AI filter view), run for the project ids the view passes in"
    ),
)
_GROUPING_STORE = filed(
    NO_SPAN_TABLE,
    why="grouping reads and verifies its own feature rows",
    table="f6_grouping_features and f6_grouping_buckets",
)
_HOURLY_EVAL_STATEMENT = filed(
    NO_SPAN_TABLE,
    why="eval chart statement over the hourly eval aggregate",
    table="eval_metrics_hourly",
)
_LEGACY_PAGE_STATEMENT = filed(
    PROJECT_SET,
    why="legacy page statement for the project ids of the list view",
)
_MIGRATION_PARITY_CHECK = filed(
    NOT_A_USER_READ,
    why=(
        "operator parity check of the Postgres to ClickHouse migration; no project view"
    ),
)
_MONITOR_STATEMENT = filed(
    ROW_DRIVEN,
    CHILDREN_MONITOR_RESPONSE_TIME,
    CHILDREN_MONITOR_FUNCTION_ERRORS,
    why=(
        "monitor statement over _spans_base_where, for the project of the monitor "
        "row; a monitor metric adds up every span, children included"
    ),
    row="tracer.UserAlertMonitor",
)
_NO_LIVE_CALLER_ERROR_ANALYSIS = filed(
    NOT_A_USER_READ,
    why=("no live caller: its package exports the builder and nothing imports it"),
)
_NO_LIVE_CALLER_SESSION_ANALYTICS = filed(
    NOT_A_USER_READ,
    why=(
        "no live caller: its package exports the first-generation builder and "
        "nothing imports it; the live session reads use SessionAnalyticsQueryBuilderV2"
    ),
)
_NO_LIVE_CALLER_TRACE_GRAPH = filed(
    NOT_A_USER_READ,
    why=(
        "no live caller: reached only through _fetch_trace_system_metrics and "
        "_finite_trace_span_ids, and no code calls _fetch_trace_system_metric_graph "
        "or _finite_trace_span_ids"
    ),
)
_OBSERVED_CATALOG = filed(
    NO_SPAN_TABLE,
    PROJECT_SET,
    CHILDREN_ATTRIBUTE_PICKERS,
    why=(
        "the collector's observed key catalog (and the AI filter's schema keys), read "
        "for the project ids it is given"
    ),
    table="observed_attribute_keys",
)
_OUTBOX_BOOKKEEPING = filed(
    NO_SPAN_TABLE,
    why="outbox change-data-capture bookkeeping in Postgres",
    table="fi_cdc_outbox, fi_cdc_state and fi_cdc_deadletter",
)
_OUTBOX_CHECK = filed(
    NO_SPAN_TABLE,
    why="outbox change-data-capture checks the tables it replicates",
    table="the replicated model tables it is given",
)
_PROJECT_CHART_STATEMENT = filed(
    PROJECT_SET,
    CHILDREN_PROJECT_CHART,
    why=(
        "project chart statement: aggregate states or raw rows for the project ids the "
        "chart view passes in"
    ),
)
_PROJECT_WIDE_READ = filed(
    PROJECT_SET,
    why="project-wide reader without a filter list, for the project it is given",
)
_READS_THE_IDS_IT_IS_GIVEN = filed(
    BY_ID_ALONE,
    why=(
        "reads the trace, span or session ids it is given; a project, if it takes "
        "one, only narrows the read"
    ),
)
_RECORD_UPSERT = filed(
    NO_SPAN_TABLE,
    why="record upsert: reads the row it is about to update",
    table="events and the other model_hub tables named by the caller",
)
_ROOT_CAUSE_AGENT_READ = filed(
    BY_ID_ALONE,
    why="the root-cause agent reads the traces or the session it is given",
)
_ROOT_VERIFIER = filed(
    PROJECT_SET,
    why=(
        "root verifier of the exact chart lane: judges the live root for the chart's "
        "project ids"
    ),
)
_SCAN_CENTROIDS = filed(
    NO_SPAN_TABLE,
    why="scan clustering reads its centroids",
    table="cluster_centroids",
)
_SEED_PAGE = filed(
    PROJECT_SET,
    why=(
        "seed page of a list reader for the project ids it is given; the classifier "
        "stays the authority"
    ),
)
_SESSION_OR_USER = filed(
    PROJECT_SET,
    why=(
        "session or user reader, run for the project ids its list or chart view passes "
        "in"
    ),
)
_SESSION_USER_SURVIVOR_MAP = filed(
    NO_SPAN_TABLE,
    why="survivor map of renamed session and user ids",
    table="trace_session_id_remap and end_user_id_remap",
)
_SPAN_LIST_SOURCE = filed(
    PROJECT_SET,
    why="latest-state source of the span list, for the project ids of the list view",
)
_STATEMENT_BY_IDS = filed(
    PROJECT_SET,
    why=(
        "statement for the ids of a page the classifier already chose, within the list "
        "view's project ids"
    ),
)
_SURVIVOR_MAP = filed(
    NO_SPAN_TABLE,
    why="survivor map of renamed ids",
    table="end_user_id_remap and trace_session_id_remap",
)
_TELEMETRY_COUNTS = filed(
    COUNTS_ON_PURPOSE,
    why=(
        "deployment telemetry sent to us, which no user sees: it counts every span and "
        "trace, hidden projects included"
    ),
)
_TRACE_DETAIL_BY_ID = filed(
    HIDDEN_ON_PURPOSE,
    why=(
        "trace detail by trace id: opened on purpose in the hidden project of a run's "
        "agent by the by-id reads (see the callers map)"
    ),
)
_TRACE_INPUT_VECTORS = filed(
    NO_SPAN_TABLE,
    why="scan clustering reads the stored trace input vectors",
    table="trace_input_embeddings",
)
_TRACE_PROJECT_SCOPE = filed(
    NO_SPAN_TABLE,
    PROJECT_SET,
    why=(
        "project scope of traces read from the trace table, bounded by the project "
        "ids its callers (the dashboard builder and the eval usage page) pass in"
    ),
    table="traces",
)

# Every entry is decided: a reader is filed under the category whose claim holds,
# with a reason, and a reader nobody filed fails the case that reads this table.
_FILED_BY_MODULE: dict[str, dict[str, Filing]] = {
    "agentic_eval.core.database.ch_vector": {
        "ClickHouseVectorDB.fetch_all_vectors": _EMBEDDING_STORE,
        "ClickHouseVectorDB.fetch_vector_by_id": _EMBEDDING_STORE,
        "ClickHouseVectorDB.get_num_vectors": _EMBEDDING_STORE,
        "ClickHouseVectorDB.get_random_examples": _EMBEDDING_STORE,
        "ClickHouseVectorDB.vector_similarity_search": _EMBEDDING_STORE,
        "ClickHouseVectorDB.vector_similarity_search_with_threshold": _EMBEDDING_STORE,
    },
    "ee.agenthub.traceerroragent.error_cluster": {
        (
            "ErrorEmbeddingClusterer.cluster_project.<locals>._fetch_project_embeddings"
        ): filed(
            NO_SPAN_TABLE,
            why="error clustering reads the embeddings it stored",
            table="error_embeddings",
        ),
    },
    "ee.cloud.internal.scripts.backfill-simulate-to-ch": {
        "backfill_table": filed(
            NOT_A_USER_READ,
            why="one-time operator backfill of simulation rows from Postgres",
        ),
        "main": filed(
            NOT_A_USER_READ,
            why="one-time operator backfill of simulation rows from Postgres",
        ),
    },
    "ee.cloud.internal.scripts.sync_pg_to_clickhouse": {
        "verify": filed(
            NOT_A_USER_READ,
            why="one-time operator sync: compares row counts after the backfill",
        ),
    },
    "ee.cloud.tasks.retention": {
        "hard_delete_expired_data": filed(
            NOT_A_USER_READ,
            why="weekly retention job: deletes rows soft-deleted long ago; no view",
        ),
    },
    "ee.usage.migrations.0015_backfill_plan_and_wallet_credits": {
        "migrate_wallet_to_credits": filed(
            NO_SPAN_TABLE,
            why="data migration between billing tables",
            table="credit balance and organization subscription tables",
        ),
    },
    "model_hub.management.commands.backfill_score_tracer_project": {
        "_project_match_query": filed(
            NOT_A_USER_READ,
            why=(
                "one-shot operator backfill: finds which project owns a score's trace "
                "or span, writes the score's project column and lists nothing"
            ),
        ),
    },
    "model_hub.management.commands.gt_roundtrip_test": {
        "Command._live_row_count": filed(
            NO_SPAN_TABLE,
            why="operator round trip of the ground truth store",
            table="ground_truths",
        ),
    },
    "model_hub.management.commands.migrate_ch_vector_tables": {
        "Command._migrate_one_table": filed(
            NO_SPAN_TABLE,
            why="operator copy of the vector tables",
            table="vector tables named on the command line",
        ),
    },
    "model_hub.management.commands.migrate_legacy_default_tables_to_replicated": {
        "Command._migrate_one_table": filed(
            NO_SPAN_TABLE,
            why="operator copy of the clustering, embedding and log tables",
            table=(
                "cluster_centroids, trace_input_embeddings, error_embeddings, llm_logs "
                "and events"
            ),
        ),
    },
    "model_hub.management.commands.migrate_m2m_to_fk": {
        "Command.handle": filed(
            NO_SPAN_TABLE,
            why="operator migration between Postgres relation tables",
            table="the many-to-many table being migrated",
        ),
    },
    "model_hub.selectors.eval_usage": {
        "_latest_usage_slice": filed(
            NO_SPAN_TABLE,
            ROW_DRIVEN,
            why=(
                "the eval usage page reads the API call log through the project scope "
                "of each run's trace, for the projects of the eval configs of the "
                "template (a join through the config's project, which uses no "
                "manager)"
            ),
            table="usage_apicalllog",
            row="tracer.CustomEvalConfig",
        ),
    },
    "model_hub.utils.clickhouse": {
        "create_empty_conversation": _RECORD_UPSERT,
        "update_eval_record": _RECORD_UPSERT,
        "upsert_record": _RECORD_UPSERT,
    },
    "model_hub.views.separate_evals": {
        "_fetch_eval_metric_buckets": filed(
            NO_SPAN_TABLE,
            why=(
                "legacy template chart over the API call log: it counts every "
                "successful run of the template in the organization with no project "
                "scope and returns counts, never a name or an id. Its only caller "
                "is EvalMetricView, routed at model-hub/get-eval-metrics and shown "
                "by no web page, so only API callers see it. A hidden project's "
                "rows reach it only if an eval runs on a hidden trace, and the "
                "contract rules that out: the hidden project takes no eval task "
                "and the eval pickers cannot offer it"
            ),
            table="usage_apicalllog",
        ),
    },
    "simulate.services.preview_pagination": {
        "_read_revision_state": filed(
            NO_SPAN_TABLE,
            why="preview paging fences on the revision of a run's rows",
            table=(
                "simulate_run_test, simulate_test_execution and simulate_call_execution"
            ),
        ),
    },
    "simulate.services.run_results_v3_queries": {
        "_RunCallsTable.as_sql": filed(
            NO_SPAN_TABLE,
            why="run results read call execution rows",
            table="the simulate call execution table",
        ),
    },
    "simulate.services.run_results_v3_scan": {
        "_SCAN_SQL": filed(
            NO_SPAN_TABLE,
            why="run results scan reads call execution and scenario rows",
            table="CallExecution and Scenarios",
        ),
    },
    "simulate.services.run_traces_v3": {
        "run_traces_sql": filed(
            HIDDEN_ON_PURPOSE,
            why=(
                "one call's traces in the hidden project of a run's agent, for a "
                "caller who can read the run (see the callers map)"
            ),
        ),
    },
    "tfc.deployment_telemetry.ch_counts": {
        "count_spans": _TELEMETRY_COUNTS,
        "count_traces": _TELEMETRY_COUNTS,
        "ingesting_project_ids": _TELEMETRY_COUNTS,
    },
    "tracer.management.commands.backfill_eval_score": {
        "_AFFECTED_COUNT": filed(
            NO_SPAN_TABLE,
            why="operator backfill of an eval score column",
            table="usage_apicalllog",
        ),
    },
    "tracer.management.commands.recreate_clickhouse_mv": {
        "Command._run_backfill": filed(
            NO_SPAN_TABLE,
            why=(
                "operator backfill after recreating a view: reads the manifest's "
                "source table"
            ),
            table="tracer_eval_logger",
        ),
    },
    "tracer.queries.error_analysis": {
        "TraceErrorAnalysisDB._embedding_exists_for_detail": filed(
            NO_SPAN_TABLE,
            why="error analysis checks its own embeddings",
            table="error_embeddings",
        ),
    },
    "tracer.queries.error_clustering": {
        "ErrorClusteringDB.get_unclustered_embeddings": _ERROR_EMBEDDINGS,
        "ErrorClusteringDB.mark_embeddings_as_clustered": _ERROR_EMBEDDINGS,
    },
    "tracer.queries.eval_clustering": {
        "assign_to_cluster": _EVAL_CENTROIDS,
        "find_nearest_centroid": _EVAL_CENTROIDS,
    },
    "tracer.queries.scan_clustering": {
        "_absorb_centroid": _SCAN_CENTROIDS,
        "assign_to_cluster": _SCAN_CENTROIDS,
        "find_nearest_centroid": _SCAN_CENTROIDS,
        "find_success_trace_baseline": _TRACE_INPUT_VECTORS,
        "get_cluster_trace_embeddings": _TRACE_INPUT_VECTORS,
        "merge_duplicate_clusters": _SCAN_CENTROIDS,
    },
    "tracer.selectors.eval_tasks.continuous_candidates": {
        "_expand_changed_span_identity_batch": _CONTINUOUS_CANDIDATES,
        "_expand_end_user_id_batch": _CONTINUOUS_CANDIDATES,
        "_expand_relation_ref_batch": _CONTINUOUS_CANDIDATES,
        "_read_changed_remap_ids": filed(
            NO_SPAN_TABLE,
            why="changed id remaps",
            table="trace_session_id_remap and end_user_id_remap",
        ),
        "_read_changed_span_identities": _CONTINUOUS_CANDIDATES,
        "_read_relation_refs_paged": filed(
            NO_SPAN_TABLE,
            why="changed score and eval refs, expanded to spans by the sibling readers",
            table="model_hub_score and tracer_eval_logger",
        ),
        "_read_root_id_batch": _CONTINUOUS_CANDIDATES,
        "_read_workflow_changed_spans": _CONTINUOUS_CANDIDATES,
    },
    "tracer.selectors.eval_tasks.row_resolver": {
        "_build_sample_query": filed(
            ROW_DRIVEN,
            CHILDREN_SPAN_EVAL_TASKS,
            why=(
                "sampled historical eval task for the project of the task row; a "
                "span-level task selects every span, children included"
            ),
            row="tracer.EvalTask",
        ),
    },
    "tracer.services.annotation_label_source": {
        "_CH_PROJECT_SCOPE": filed(
            NOT_A_USER_READ,
            why=(
                "no live caller: only AnnotationLabelScoresCH uses it, the legacy "
                "compatibility reader kept for rollback tooling, and no code names "
                "that class"
            ),
        ),
    },
    "tracer.services.clickhouse.attribute_reads": {
        "_CANDIDATE_SQL": _ATTRIBUTE_PICKER,
        "_LATEST_BROWSE_SQL": _ATTRIBUTE_PICKER,
        "_LATEST_CARDINALITY_SQL": _ATTRIBUTE_PICKER,
        "_LATEST_JSON_TARGET_SQL": _ATTRIBUTE_PICKER,
        "_LATEST_TARGET_SQL": _ATTRIBUTE_PICKER,
        "_LATEST_TYPED_BROWSE_SQL": _ATTRIBUTE_PICKER,
        "_LATEST_TYPED_TARGET_SQL": _ATTRIBUTE_PICKER,
        "_LATEST_TYPED_VERSION_SQL": _ATTRIBUTE_PICKER,
        "_ORDERED_CANDIDATE_SQL": _ATTRIBUTE_PICKER,
        "_ORDERED_STRATIFIED_CANDIDATE_SQL": _ATTRIBUTE_PICKER,
        "_ORDERED_TYPED_VALUE_CANDIDATE_SQL": _ATTRIBUTE_PICKER,
        "_STRATIFIED_CANDIDATE_SQL": _ATTRIBUTE_PICKER,
        "_TYPED_VALUE_CANDIDATE_SQL": _ATTRIBUTE_PICKER,
        "_VALUE_CURSOR_SEARCHED_RAW_SINGLE_LANE_SQL": _ATTRIBUTE_PICKER,
        "_VALUE_CURSOR_SEARCHED_RAW_SUPERSET_SQL": _ATTRIBUTE_PICKER,
    },
    "tracer.services.clickhouse.client": {
        "ClickHouseClient.check_replication_lag": _CDC_LAG_CHECK,
        "ClickHouseClient.get_table_row_count": filed(
            NOT_A_USER_READ,
            why="dead code: no caller, and the table is the caller's argument",
        ),
    },
    "tracer.services.clickhouse.cluster_rca_spans": {
        "aggregate_span_field": _ROOT_CAUSE_AGENT_READ,
        "aggregate_trace_field": _ROOT_CAUSE_AGENT_READ,
        "distinct_sessions": _ROOT_CAUSE_AGENT_READ,
        "error_messages_in_traces": _ROOT_CAUSE_AGENT_READ,
        "list_spans_in_traces": _ROOT_CAUSE_AGENT_READ,
        "search_spans_in_traces": _ROOT_CAUSE_AGENT_READ,
        "search_trace_ids": _ROOT_CAUSE_AGENT_READ,
        "timeline_trace_counts": _ROOT_CAUSE_AGENT_READ,
        "trace_roots": _ROOT_CAUSE_AGENT_READ,
        "traces_in_session": _ROOT_CAUSE_AGENT_READ,
    },
    "tracer.services.clickhouse.consistency": {
        "ConsistencyChecker.check_row_counts": filed(
            NO_SPAN_TABLE,
            why="health check compares Postgres with the change-data-capture tables",
            table="tracer_trace, trace_session and tracer_eval_logger",
        ),
        "ConsistencyChecker.get_cdc_lag": _CDC_LAG_CHECK,
    },
    "tracer.services.clickhouse.eval_logger_table": {
        "latest_completed_eval_rows": filed(
            NO_SPAN_TABLE,
            why="newest completed eval row per key, over the eval rows it is given",
            table="tracer_eval_logger",
        ),
    },
    "tracer.services.clickhouse.exact_attribute_detail": {
        "EXACT_ATTRIBUTE_DETAIL_SQL": filed(
            PROJECT_SET,
            why=(
                "attribute catalog detail statement, run for the project ids its view "
                "passes in"
            ),
        ),
    },
    "tracer.services.clickhouse.exact_graph_reads": {
        "_eval_partition_trace_ids_sql": filed(
            NO_SPAN_TABLE,
            why="trace ids of the eval chart's partition",
            table="tracer_eval_logger",
        ),
        "_finite_survivor_map_ctes": _SESSION_OR_USER,
        "_latest_session_rows_sql": _SESSION_OR_USER,
        "_session_aggregate_source_sql": _SESSION_OR_USER,
        "_session_numeric_absence_probe_sql": _SESSION_OR_USER,
        "_user_aggregate_source_sql": filed(
            NO_SPAN_TABLE,
            why="user chart eval source",
            table="tracer_eval_logger",
        ),
        "session_graph_root_estimate_sql": _SESSION_OR_USER,
    },
    "tracer.services.clickhouse.filter_value_reads": {
        "_latest_span_value_cte": _FILTER_VALUE_PICKER,
        "_latest_voice_value_cte": _FILTER_VALUE_PICKER,
        "read_session_message_filter_values": filed(
            PROJECT_SET,
            why=(
                "session message filter values, run for the project ids the view "
                "passes in"
            ),
        ),
    },
    "tracer.services.clickhouse.graph_dispatch": {
        "_finite_eval_rows": filed(
            NO_SPAN_TABLE,
            why="dead code: reached only from _finite_eval_graph, which nothing calls",
            table="tracer_eval_logger",
        ),
        "_finite_trace_span_candidates": _NO_LIVE_CALLER_TRACE_GRAPH,
        "_finite_trace_span_ids": filed(
            NOT_A_USER_READ,
            why=(
                "dead code: no file names it; it only calls the candidate reader "
                "_finite_trace_span_candidates"
            ),
        ),
        "_select_raw_trace_seed_candidate": filed(
            PROJECT_SET,
            why=(
                "seed candidate of the raw chart lane: reads the request's filters "
                "only, for the chart's project ids"
            ),
        ),
        "_trace_system_metric_query": _NO_LIVE_CALLER_TRACE_GRAPH,
    },
    "tracer.services.clickhouse.graph_read_cost": {
        "_explain_raw_graph_scan": filed(
            PROJECT_SET,
            why=(
                "cost probe of a chart read: asks the index for a row estimate, "
                "returns no span data"
            ),
        ),
        "estimate_user_graph_scan_rows": filed(
            PROJECT_SET,
            why="cost probe of the user chart, for its project ids",
        ),
    },
    "tracer.services.clickhouse.oss_cdc_bootstrap": {
        "_definitions": filed(
            NO_SPAN_TABLE,
            why=(
                "install-time DDL text for the change-data-capture views: reads nothing"
            ),
            table="none: it rewrites DDL text",
        ),
    },
    "tracer.services.clickhouse.oss_native_bootstrap": {
        "_inspect": filed(
            NOT_A_USER_READ,
            why=(
                "install-time bootstrap probes whether a table is empty before it "
                "changes anything; no project view"
            ),
        ),
    },
    "tracer.services.clickhouse.oss_outbox_cdc": {
        "_ch_has_live_rows": _OUTBOX_CHECK,
        "_ch_live_among_sql": _OUTBOX_CHECK,
        "_ch_live_page_sql": _OUTBOX_CHECK,
        "_ch_max_version": _OUTBOX_CHECK,
        "_due_reconciles": _OUTBOX_BOOKKEEPING,
        "_outbox_head": _OUTBOX_BOOKKEEPING,
        "_pending_snapshots": _OUTBOX_BOOKKEEPING,
        "_probe": _OUTBOX_CHECK,
        "drain": _OUTBOX_BOOKKEEPING,
        "drain_batch": _OUTBOX_BOOKKEEPING,
        "load_floor": _OUTBOX_BOOKKEEPING,
        "reconcile": _OUTBOX_BOOKKEEPING,
        "reconcile_table": _OUTBOX_CHECK,
        "snapshot_page": _OUTBOX_BOOKKEEPING,
        "status": _OUTBOX_BOOKKEEPING,
    },
    "tracer.services.clickhouse.query_builders.agent_graph": {
        "AgentGraphQueryBuilder.build": filed(
            PROJECT_SET,
            why="Agent Graph statement for the project ids the chart view passes in",
        ),
    },
    "tracer.services.clickhouse.query_builders.annotation_graph": {
        "AnnotationGraphQueryBuilder._build_bool_query": _DEAD_ANNOTATION_BUILDER,
        "AnnotationGraphQueryBuilder._build_float_query": _DEAD_ANNOTATION_BUILDER,
        "AnnotationGraphQueryBuilder._build_str_list_query": _DEAD_ANNOTATION_BUILDER,
        "AnnotationGraphQueryBuilder._build_text_query": _DEAD_ANNOTATION_BUILDER,
        "AnnotationGraphQueryBuilder._filtered_spans_subquery": filed(
            NOT_A_USER_READ,
            why=(
                "no live caller: only get_annotation_graph_data builds the class and "
                "nothing calls it; the live annotation chart is "
                "read_exact_annotation_graph"
            ),
        ),
    },
    "tracer.services.clickhouse.query_builders.dashboard": {
        "DashboardQueryBuilder._annotation_presence_relation": _DASHBOARD_SOURCE,
        "DashboardQueryBuilder._build_annotation_companion_relation": filed(
            PROJECT_SET,
            why=(
                "annotation breakdown LEFT JOIN over the spans population of the "
                "dashboard's project ids"
            ),
        ),
        (
            "DashboardQueryBuilder._build_annotation_metric_query"
        ): _DASHBOARD_SIDE_TABLE_STATEMENT,
        "DashboardQueryBuilder._build_custom_attr_query": _DASHBOARD_SOURCE,
        (
            "DashboardQueryBuilder._build_eval_metric_query"
        ): _DASHBOARD_SIDE_TABLE_STATEMENT,
        "DashboardQueryBuilder._build_subquery_filters": _DASHBOARD_SOURCE,
        "DashboardQueryBuilder._build_system_metric_query": _DASHBOARD_SOURCE,
        "DashboardQueryBuilder._eval_presence_relation": filed(
            NO_SPAN_TABLE,
            PROJECT_SET,
            why=(
                "has-eval presence over eval rows and the trace table, for the "
                "dashboard's project ids"
            ),
            table="tracer_eval_logger and traces",
        ),
        "DashboardQueryBuilder._spans_source": _DASHBOARD_SOURCE,
        "_resolved_spans_source": _DASHBOARD_SOURCE,
        "_touched_survivor_map_subquery": _SURVIVOR_MAP,
    },
    "tracer.services.clickhouse.query_builders.error_analysis": {
        "ErrorAnalysisQueryBuilder._build_breakdown": _NO_LIVE_CALLER_ERROR_ANALYSIS,
        "ErrorAnalysisQueryBuilder._build_summary": _NO_LIVE_CALLER_ERROR_ANALYSIS,
        "ErrorAnalysisQueryBuilder._build_time_series": _NO_LIVE_CALLER_ERROR_ANALYSIS,
    },
    "tracer.services.clickhouse.query_builders.eval_metrics": {
        "EvalMetricsQueryBuilder._build_choices_query": _EVAL_CHART_ROWS,
        "EvalMetricsQueryBuilder._build_pass_fail_agg": _HOURLY_EVAL_STATEMENT,
        "EvalMetricsQueryBuilder._build_pass_fail_raw": _EVAL_CHART_ROWS,
        "EvalMetricsQueryBuilder._build_score_agg": _HOURLY_EVAL_STATEMENT,
        "EvalMetricsQueryBuilder._build_score_raw": _EVAL_CHART_ROWS,
        "EvalMetricsQueryBuilder._filter_fragment": filed(
            PROJECT_SET,
            why=(
                "superseded: the eval chart builds only EvalMetricsQueryBuilderV2 "
                "(exact_graph_reads.py), whose _filter_fragment replaces this one"
            ),
        ),
    },
    "tracer.services.clickhouse.query_builders.exact_graph_predicates": {
        "_compile_has_eval_filter": filed(
            NO_SPAN_TABLE,
            why="has-eval filter leaf of the exact chart compiler",
            table="tracer_eval_logger",
        ),
    },
    "tracer.services.clickhouse.query_builders.filters": {
        "ClickHouseFilterBuilder.__init__": _FILTER_LEAF,
        "ClickHouseFilterBuilder._build_enduser_string_subquery": _FILTER_LEAF,
        (
            "ClickHouseFilterBuilder._build_eval_condition.<locals>.eval_value_subquery"
        ): _EVAL_FILTER_LEAF,
        "ClickHouseFilterBuilder._build_has_eval_condition": _EVAL_FILTER_LEAF,
        "ClickHouseFilterBuilder._build_native_trace_tags_condition": filed(
            NO_SPAN_TABLE,
            why="trace tag filter leaf",
            table="tracer_trace",
        ),
        "ClickHouseFilterBuilder._build_system_metric_condition": _FILTER_LEAF,
        "ClickHouseFilterBuilder._candidate_trace_filter": _FILTER_LEAF,
        "ClickHouseFilterBuilder._enduser_dimension_id_subquery": filed(
            NO_SPAN_TABLE,
            why="end-user filter leaf",
            table="tracer_enduser",
        ),
        "ClickHouseFilterBuilder._scope_span_attr_inner": _FILTER_LEAF,
        "ClickHouseFilterBuilder._scoped_spans_subquery": _FILTER_LEAF,
        "ClickHouseFilterBuilder._score_side_candidate_filter": _FILTER_LEAF,
    },
    "tracer.services.clickhouse.query_builders.hourly_aggregate_states": {
        "hourly_aggregate_state_source": filed(
            PROJECT_SET,
            CHILDREN_PROJECT_CHART,
            why=(
                "hourly aggregate states of the chart's project ids: they count every "
                "physical row and carry no attribute"
            ),
        ),
    },
    "tracer.services.clickhouse.query_builders.latest_filter_predicates": {
        "latest_span_membership_source_sql": filed(
            PROJECT_SET,
            why=(
                "membership source the graph compiler's trace clause reads, for the "
                "chart's project ids"
            ),
        ),
    },
    "tracer.services.clickhouse.query_builders.monitor_metrics": {
        "MonitorMetricsQueryBuilder._bounded_spans_subquery": filed(
            ROW_DRIVEN,
            why=(
                "span membership of the eval monitors, for the project of the "
                "monitor row"
            ),
            row="tracer.UserAlertMonitor",
        ),
        "MonitorMetricsQueryBuilder._build_eval_metric_value_query": _EVAL_MONITOR,
        "MonitorMetricsQueryBuilder._build_eval_stats_query": _EVAL_MONITOR,
        "MonitorMetricsQueryBuilder._build_eval_time_series_query": _EVAL_MONITOR,
        "MonitorMetricsQueryBuilder._eval_rows_source": _EVAL_MONITOR,
        "MonitorMetricsQueryBuilder.build_historical_stats_query": _MONITOR_STATEMENT,
        "MonitorMetricsQueryBuilder.build_metric_value_query": _MONITOR_STATEMENT,
        "MonitorMetricsQueryBuilder.build_time_series_query": _MONITOR_STATEMENT,
    },
    "tracer.services.clickhouse.query_builders.session_analytics": {
        (
            "SessionAnalyticsQueryBuilder.build_first_last_message_query"
        ): _NO_LIVE_CALLER_SESSION_ANALYTICS,
        (
            "SessionAnalyticsQueryBuilder.build_session_metrics_query"
        ): _NO_LIVE_CALLER_SESSION_ANALYTICS,
        (
            "SessionAnalyticsQueryBuilder.build_session_navigation_query"
        ): _NO_LIVE_CALLER_SESSION_ANALYTICS,
        (
            "SessionAnalyticsQueryBuilder.build_user_stats_query"
        ): _NO_LIVE_CALLER_SESSION_ANALYTICS,
    },
    "tracer.services.clickhouse.query_builders.session_list": {
        "SessionListQueryBuilder._bounded_eval_membership_ctes": filed(
            NO_SPAN_TABLE,
            why="session list eval membership",
            table="tracer_eval_logger",
        ),
        (
            "SessionListQueryBuilder._build_session_user_membership_clause"
        ): _SESSION_OR_USER,
        "SessionListQueryBuilder._build_user_presence_clause": _SESSION_OR_USER,
        "SessionListQueryBuilder._candidate_session_ctes": _SESSION_OR_USER,
        "SessionListQueryBuilder._seed_witness_identity_gate": _SESSION_OR_USER,
        "SessionListQueryBuilder._session_from_where": _SESSION_OR_USER,
        "SessionListQueryBuilder.build_filter_anchor_probe": _SESSION_OR_USER,
        "SessionListQueryBuilder.build_filter_seed_page": _SESSION_OR_USER,
        "SessionListQueryBuilder.build_page_hydration_query": _SESSION_OR_USER,
    },
    "tracer.services.clickhouse.query_builders.session_time_series": {
        "SessionRollupTimeSeriesQueryBuilder._rollup_session_source": _SESSION_OR_USER,
        "SessionTimeSeriesQueryBuilder.build": _SESSION_OR_USER,
    },
    "tracer.services.clickhouse.query_builders.span_list": {
        "SpanListQueryBuilder._build_filter_match_query": filed(
            PROJECT_SET,
            why="classifier of the list reader, for the project ids it is given",
        ),
        "SpanListQueryBuilder.build": filed(
            PROJECT_SET,
            CHILDREN_TRACE_FILTERS,
            why="legacy page statement for the project ids of the list view",
        ),
        "SpanListQueryBuilder.build_annotation_query": _ANNOTATION_CELLS,
        "SpanListQueryBuilder.build_content_query": _STATEMENT_BY_IDS,
        "SpanListQueryBuilder.build_count_query": _LEGACY_PAGE_STATEMENT,
        "SpanListQueryBuilder.build_eval_query": _EVAL_CELLS,
        "SpanListQueryBuilder.build_filter_anchor_probe": _CANDIDATE_PROBE,
        "SpanListQueryBuilder.build_filter_seed_page": _SEED_PAGE,
        "SpanListQueryBuilder.build_id_query": _LEGACY_PAGE_STATEMENT,
    },
    "tracer.services.clickhouse.query_builders.time_series": {
        "TimeSeriesQueryBuilder._build_agg_query": _PROJECT_CHART_STATEMENT,
        "TimeSeriesQueryBuilder._build_exact_raw_query": _EXACT_CHART_STATEMENT,
        "TimeSeriesQueryBuilder._build_raw_query": _PROJECT_CHART_STATEMENT,
        "TimeSeriesQueryBuilder._exact_latest_scalar_source": _EXACT_CHART_STATEMENT,
        "TimeSeriesQueryBuilder._raw_scalar_source": _EXACT_CHART_STATEMENT,
        "TimeSeriesQueryBuilder.build_exact_span_partition": _EXACT_CHART_STATEMENT,
        (
            "TimeSeriesQueryBuilder.build_exact_trace_contribution_batch"
        ): _EXACT_CHART_STATEMENT,
    },
    "tracer.services.clickhouse.query_builders.trace_list": {
        "TraceListQueryBuilder._build_global_typed_map_witness_probe": _CANDIDATE_PROBE,
        "TraceListQueryBuilder.build": _LEGACY_PAGE_STATEMENT,
        "TraceListQueryBuilder.build_annotation_query": _ANNOTATION_CELLS,
        "TraceListQueryBuilder.build_content_query": _STATEMENT_BY_IDS,
        "TraceListQueryBuilder.build_count_query": _LEGACY_PAGE_STATEMENT,
        "TraceListQueryBuilder.build_eval_query": _EVAL_CELLS,
        (
            "TraceListQueryBuilder.build_exact_graph_latest_anchor_partition"
        ): _CANDIDATE_PROBE,
        "TraceListQueryBuilder.build_exact_graph_latest_root_partition": _ROOT_VERIFIER,
        "TraceListQueryBuilder.build_exact_graph_root_membership_query": _ROOT_VERIFIER,
        "TraceListQueryBuilder.build_filter_anchor_probe": _CANDIDATE_PROBE,
        "TraceListQueryBuilder.build_filter_candidate_witness_probe": _CANDIDATE_PROBE,
        "TraceListQueryBuilder.build_filter_exact_zero_probe": _CANDIDATE_PROBE,
        "TraceListQueryBuilder.build_filter_match_query": filed(
            PROJECT_SET,
            CHILDREN_TRACE_FILTERS,
            why="classifier of the list reader, for the project ids it is given",
        ),
        "TraceListQueryBuilder.build_filter_ordered_seed_page": _SEED_PAGE,
        "TraceListQueryBuilder.build_filter_page_hydration_query": _STATEMENT_BY_IDS,
        (
            "TraceListQueryBuilder.build_filter_root_time_discovery_query"
        ): _CANDIDATE_PROBE,
        "TraceListQueryBuilder.build_filter_seed_page": _SEED_PAGE,
        "TraceListQueryBuilder.build_id_query": _LEGACY_PAGE_STATEMENT,
        "TraceListQueryBuilder.build_span_attributes_query": _STATEMENT_BY_IDS,
        "TraceListQueryBuilder.build_span_count_query": _STATEMENT_BY_IDS,
        "TraceListQueryBuilder.build_user_id_query": _STATEMENT_BY_IDS,
    },
    "tracer.services.clickhouse.query_builders.user_list": {
        "UserListQueryBuilder._build_candidate_first": _SESSION_OR_USER,
        "UserListQueryBuilder._candidate_page_ctes": _SESSION_OR_USER,
        (
            "UserListQueryBuilder._finite_user_activity_ctes.<locals>.common_prefix"
        ): _SESSION_OR_USER,
        "UserListQueryBuilder.build_attribute_user_candidates_query": _SESSION_OR_USER,
        "UserListQueryBuilder.build_eval_query": filed(
            NO_SPAN_TABLE,
            why="user list eval cells",
            table="tracer_eval_logger",
        ),
        (
            "UserListQueryBuilder.build_matching_activity_existence_query"
        ): _SESSION_OR_USER,
        "UserListQueryBuilder.build_matching_activity_instant_query": _SESSION_OR_USER,
        "UserListQueryBuilder.build_matching_activity_presence_query": _SESSION_OR_USER,
        "UserListQueryBuilder.build_matching_activity_slice_query": _SESSION_OR_USER,
        "UserListQueryBuilder.build_physical_user_presence_query": _SESSION_OR_USER,
        "UserListQueryBuilder.build_relation_filter_user_query": _SESSION_OR_USER,
        "_touched_survivor_map_subquery": _SURVIVOR_MAP,
    },
    "tracer.services.clickhouse.query_builders.user_time_series": {
        "UserTimeSeriesQueryBuilder.build": _SESSION_OR_USER,
    },
    "tracer.services.clickhouse.query_builders.voice_call_list": {
        "VoiceCallListQueryBuilder._apply_bounded_voice_constraints": filed(
            PROJECT_SET,
            why=(
                "adds the voice-only constraints to the trace classifier of the list "
                "reader"
            ),
        ),
        "VoiceCallListQueryBuilder.build": _LEGACY_PAGE_STATEMENT,
        "VoiceCallListQueryBuilder.build_annotation_query": _ANNOTATION_CELLS,
        "VoiceCallListQueryBuilder.build_child_spans_query": _STATEMENT_BY_IDS,
        "VoiceCallListQueryBuilder.build_content_query": _STATEMENT_BY_IDS,
        "VoiceCallListQueryBuilder.build_count_query": _LEGACY_PAGE_STATEMENT,
        "VoiceCallListQueryBuilder.build_eval_query": filed(
            NO_SPAN_TABLE,
            CHILDREN_VOICE_EVAL_CELLS,
            why=(
                "voice list eval cells average every eval row of the trace, children "
                "included"
            ),
            table="tracer_eval_logger",
        ),
        "VoiceCallListQueryBuilder.build_filter_exact_zero_probe": filed(
            PROJECT_SET,
            why=(
                "compatibility probe that never ends a page; the classifier of the "
                "list reader does the work"
            ),
        ),
        "VoiceCallListQueryBuilder.build_id_query": _LEGACY_PAGE_STATEMENT,
    },
    "tracer.services.clickhouse.query_service": {
        "AnalyticsQueryService._eval_config_ids_query": filed(
            NO_SPAN_TABLE,
            why="eval config discovery",
            table="tracer_eval_logger",
        ),
        "AnalyticsQueryService.get_children_eval_metrics_ch": _EVAL_ROWS_OF_A_CONFIG,
        "AnalyticsQueryService.get_eval_config_ids_for_candidates_ch": filed(
            PROJECT_SET,
            why=(
                "eval-name picker and metrics catalog: keeps the eval configs that "
                "have an eval row among the candidates of the project ids it is given"
            ),
        ),
        "AnalyticsQueryService.get_eval_config_ids_with_data_ch": filed(
            PROJECT_SET,
            why=(
                "the fast path of the eval-name picker: it hands the candidate configs "
                "to the candidate read, and its spans trace-join branch is never "
                "taken, because every caller passes candidate_config_ids"
            ),
        ),
        "AnalyticsQueryService.get_eval_detail_ch": filed(
            BY_ID_ALONE,
            why="eval detail of the span or trace it is given",
        ),
        "AnalyticsQueryService.get_span_trace_map": filed(
            BY_ID_ALONE,
            why="span to trace map of the traces it is given",
        ),
        "AnalyticsQueryService.get_trace_eval_scores_ch": _EVAL_ROWS_OF_A_CONFIG,
    },
    "tracer.services.clickhouse.schema": {
        "COLUMN_DICT": _DICTIONARY_DDL,
        "DATASET_DICT": _DICTIONARY_DDL,
        "ENDUSER_DICT": _DICTIONARY_DDL,
        "PROMPT_DICT": _DICTIONARY_DDL,
        "PROMPT_LABEL_DICT": _DICTIONARY_DDL,
        "SIMULATE_AGENT_DICT": _DICTIONARY_DDL,
        "SIMULATE_RUN_TEST_DICT": _DICTIONARY_DDL,
        "SIMULATE_SCENARIO_DICT": _DICTIONARY_DDL,
        "SIMULATE_TEST_EXECUTION_DICT": _DICTIONARY_DDL,
        "SIMULATE_VERSION_DICT": _DICTIONARY_DDL,
        "SPAN_METRICS_HOURLY_MV": filed(
            NOT_A_USER_READ,
            why=(
                "legacy DDL of the hourly rollup view: a write-path definition, not a "
                "reader; the rollup's readers are filed on their own"
            ),
        ),
    },
    "tracer.services.clickhouse.span_attribute_lookups": {
        "aggregate_attribute_over_traces": _READS_THE_IDS_IT_IS_GIVEN,
        "list_attribute_keys_for_traces": filed(
            BY_ID_ALONE,
            CHILDREN_ATTRIBUTE_PICKERS,
            why=(
                "keys across the trace ids it is given; only the root-cause agent "
                "calls it, for the traces of one cluster. The project-wide listing "
                "(list_attribute_keys_for_project) takes its projects from the selector"
            ),
        ),
        "list_attributes_for_trace": _READS_THE_IDS_IT_IS_GIVEN,
        "scoped_trace_ids": _READS_THE_IDS_IT_IS_GIVEN,
        "span_id_by_provider_log_id": _READS_THE_IDS_IT_IS_GIVEN,
        "spans_by_eval_attribute_call_execution_ids": _READS_THE_IDS_IT_IS_GIVEN,
        "trace_ids_with_simulator_call_execution_id": _READS_THE_IDS_IT_IS_GIVEN,
    },
    "tracer.services.clickhouse.trace_project_scope": {
        "latest_live_project_traces_sql": _TRACE_PROJECT_SCOPE,
        "latest_live_trace_projects_sql": _TRACE_PROJECT_SCOPE,
    },
    "tracer.services.clickhouse.v2.backfill": {
        "_process_window": filed(
            NOT_A_USER_READ,
            why=(
                "operator Postgres to ClickHouse span backfill: writes spans and "
                "checks counts; no project view"
            ),
        ),
    },
    "tracer.services.clickhouse.v2.id_remap_sql": {
        "bounded_survivor_map_subquery": _SESSION_USER_SURVIVOR_MAP,
        "survivor_map_subquery": _SESSION_USER_SURVIVOR_MAP,
    },
    "tracer.services.clickhouse.v2.physical_span_detail_reads": {
        "build_physical_span_detail_query": filed(
            HIDDEN_ON_PURPOSE,
            why=(
                "one physical span by its full reference: opened on purpose in the "
                "hidden project of a run's agent by the by-id reads (see the callers "
                "map)"
            ),
        ),
    },
    "tracer.services.clickhouse.v2.property_catalog.reader": {
        "PropertyCatalogReader._category_counts": _OBSERVED_CATALOG,
        "PropertyCatalogReader._observed_keys": _OBSERVED_CATALOG,
    },
    "tracer.services.clickhouse.v2.property_catalog.value_reader": {
        "PropertyCatalogValueReader.read_page": filed(
            PROJECT_SET,
            NO_SPAN_TABLE,
            CHILDREN_ATTRIBUTE_PICKERS,
            why=(
                "the collector's observed values, read for the project ids the view "
                "passes in"
            ),
            table="observed_attribute_keys and observed_attribute_values",
        ),
    },
    "tracer.services.clickhouse.v2.query_builders.columns": {
        "json_path": filed(
            NO_SPAN_TABLE,
            why=(
                "text helper for the v2 path of a JSON attribute: reads nothing, spans "
                "appear only in its docstring"
            ),
            table="none: the match is the docstring",
        ),
    },
    "tracer.services.clickhouse.v2.query_builders.dashboard": {
        "DashboardQueryBuilderV2._build_custom_attr_query": _DASHBOARD_V2_SOURCE,
        "DashboardQueryBuilderV2._exact_filter_replay_source": _DASHBOARD_V2_SOURCE,
        "DashboardQueryBuilderV2._spans_source": _DASHBOARD_V2_SOURCE,
        "DashboardQueryBuilderV2.build_raw_metric_group_query": filed(
            NOT_A_USER_READ,
            why="dead code: no file names it",
        ),
    },
    "tracer.services.clickhouse.v2.query_builders.eval_metrics": {
        "EvalMetricsQueryBuilderV2._filter_fragment": filed(
            PROJECT_SET,
            why=(
                "eval chart membership and the scan over the eval rows of the chart's "
                "project ids"
            ),
        ),
    },
    "tracer.services.clickhouse.v2.query_builders.session_analytics": {
        "SessionAnalyticsQueryBuilderV2.build_session_metrics_query": filed(
            ROW_DRIVEN,
            why=(
                "session metrics of the background task that refreshes active "
                "sessions, for the project of each session row"
            ),
            row="tracer.TraceSession",
        ),
        (
            "SessionAnalyticsQueryBuilderV2.build_session_navigation_query"
        ): _SESSION_OR_USER,
        "SessionAnalyticsQueryBuilderV2.build_user_stats_query": filed(
            ROW_DRIVEN,
            why=(
                "user stats of the background task that refreshes end users, for the "
                "project of each end user row"
            ),
            row="tracer.EndUser",
        ),
    },
    "tracer.services.clickhouse.v2.query_builders.observe_usage": {
        "ObserveUsageFilterBuilderV2._build_system_metric_condition": filed(
            BY_ID_ALONE,
            why=(
                "Observe usage residual requires the authorized project's finite "
                "candidate trace IDs; project/trace tuple membership isolates "
                "same-ID traces across an authorized project set"
            ),
        ),
        "ObserveTraceListQueryBuilderV2.build_usage_sorted_query": filed(
            PROJECT_SET,
            why=(
                "exact usage sorting of the Observe list requires its one "
                "authorized project and finite root time window; the view's "
                "memory, read-byte and wall budgets remain fail-closed"
            ),
        ),
    },
    "tracer.services.clickhouse.v2.query_builders.session_detail": {
        "_LIVE_SESSION_SPANS": filed(BY_ID_ALONE, why="session detail by session id"),
    },
    "tracer.services.clickhouse.v2.query_builders.session_list": {
        (
            "SessionListQueryBuilderV2.build_candidate_slice_density_probe_query"
        ): _SESSION_OR_USER,
    },
    "tracer.services.clickhouse.v2.query_builders.span_list": {
        "SpanListQueryBuilderV2._latest_state_source_sql": _SPAN_LIST_SOURCE,
        "SpanListQueryBuilderV2._latest_window_source_sql": _SPAN_LIST_SOURCE,
        "SpanListQueryBuilderV2.build_content_query": _STATEMENT_BY_IDS,
        (
            "SpanListQueryBuilderV2.build_filter_population_time_discovery_query"
        ): _CANDIDATE_PROBE,
        (
            "SpanListQueryBuilderV2.build_filter_seed_density_probe_query"
        ): _CANDIDATE_PROBE,
    },
    "tracer.services.clickhouse.v2.query_builders.trace_list": {
        (
            "_TraceListQueryBuilderV2Core._filter_classifier_coordinate_predicate"
        ): _CANDIDATE_PROBE,
        (
            "_TraceListQueryBuilderV2Core._scalar_candidate_conjunction_gate"
        ): _CANDIDATE_PROBE,
        (
            "_TraceListQueryBuilderV2Core._scalar_candidate_root_population_predicate"
        ): _CANDIDATE_PROBE,
        "_TraceListQueryBuilderV2Core.build_count_query": filed(
            PROJECT_SET,
            why=(
                "pagination count of the trace list: reads trace_count_rollup, or "
                "counts live roots, for the project ids of the list view"
            ),
        ),
        (
            "_TraceListQueryBuilderV2Core.build_filter_seed_density_probe_query"
        ): _CANDIDATE_PROBE,
        "_TraceListQueryBuilderV2Core.build_span_attributes_query": _STATEMENT_BY_IDS,
        "_TraceListQueryBuilderV2Core.build_user_id_query": _STATEMENT_BY_IDS,
        "_TraceRootReplayV2._root_replay_query": _STATEMENT_BY_IDS,
        "_TraceRootReplayV2._trace_rollup_join_sql": _STATEMENT_BY_IDS,
    },
    "tracer.services.clickhouse.v2.query_builders.user_time_series": {
        "UserDetailTimeSeriesQueryBuilderV2.build": _SESSION_OR_USER,
        "_latest_spans_argmax_cte": _SESSION_OR_USER,
        "_touched_survivor_map_subquery": _SURVIVOR_MAP,
        "latest_physical_span_rows_sql": _SESSION_OR_USER,
    },
    "tracer.services.clickhouse.v2.span_reader": {
        "CHSpanReader._session_filter_remap": filed(
            NO_SPAN_TABLE,
            why=(
                "text helper for the session id remap join: it reads nothing, spans "
                "appear only in its docstring, and the readers that add the join "
                "are filed on their own"
            ),
            table="none: the match is the docstring",
        ),
        "CHSpanReader.aggregate_by_end_user": filed(
            ROW_DRIVEN,
            why=(
                "user rollup of the background task that refreshes end users, for "
                "the project of each end user row"
            ),
            row="tracer.EndUser",
        ),
        "CHSpanReader.aggregate_by_session_ids": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.aggregate_by_trace_ids": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.aggregate_window_with_filters": _DEAD_SPAN_READER,
        "CHSpanReader.child_tree_spans_by_trace_ids": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.count_by_project": _DEAD_SPAN_READER,
        "CHSpanReader.count_by_trace": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.count_with_filters": filed(
            PROJECT_SET,
            BY_ID_ALONE,
            why=(
                "count for the project it is given, or for the traces or the session "
                "it is given. Two callers pass a project that came from a project "
                "manager; the third, the session comparison, passes only a session "
                "id, after a trace session manager check of the organization when "
                "one is passed, and a hidden project has no session row to pass it"
            ),
        ),
        "CHSpanReader.distinct_end_users_by_trace_ids": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.distinct_session_ids_with_filters": filed(
            ROW_DRIVEN,
            why=(
                "session candidates of a session eval task, for the project of the "
                "task row"
            ),
            row="tracer.EvalTask",
        ),
        "CHSpanReader.export_fields_by_ids": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.first_span_by_type": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.get": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.has_root_spans_of_type": _DEAD_SPAN_READER,
        "CHSpanReader.ids_by_project": _PROJECT_WIDE_READ,
        "CHSpanReader.list_by_ids": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.list_by_parent": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.list_by_project": filed(
            ROW_DRIVEN,
            why=(
                "lists every span of one experiment project version for the custom "
                "eval config view, for the project of that version row"
            ),
            row="tracer.ProjectVersion",
        ),
        "CHSpanReader.list_by_session": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.list_by_trace": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.list_by_trace_ids": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.list_root_spans_by_trace_ids": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.newest_span_projects": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.newest_trace_projects": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.per_project_group_by_name": _PROJECT_WIDE_READ,
        "CHSpanReader.per_project_version_metric_aggregate": _DEAD_SPAN_READER,
        "CHSpanReader.per_trace_aggregate": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.per_trace_root_span_start_times": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.prev_next_span_by_start_time": _DEAD_SPAN_READER,
        "CHSpanReader.prev_next_trace_by_start_time": _DEAD_SPAN_READER,
        "CHSpanReader.recent_root_trace_ids_by_project": _PROJECT_WIDE_READ,
        "CHSpanReader.root_ids_by_project": _PROJECT_WIDE_READ,
        "CHSpanReader.root_ids_by_trace_ids": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.root_trace_candidates": filed(
            ROW_DRIVEN,
            why=(
                "feeds the trace scanner sweep, which visits the project of each "
                "scan setting row"
            ),
            row="tracer.TraceScanConfig",
        ),
        "CHSpanReader.roots_by_trace_ids": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.scope_by_ids": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.session_aggregate": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.session_trace_ids": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.time_bucket_aggregate": _DEAD_SPAN_READER,
        "CHSpanReader.time_bucket_aggregate_with_filters": _DEAD_SPAN_READER,
        "CHSpanReader.totals_by_trace_ids": filed(
            BY_ID_ALONE,
            CHILDREN_ERROR_FEED_LATENCY,
            why=(
                "error-feed latency sums every span of the given traces, children "
                "included"
            ),
        ),
        "CHSpanReader.trace_aggregate": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.trace_aggregate_with_stddev": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.trace_ids_for_span_ids": _READS_THE_IDS_IT_IS_GIVEN,
        "CHSpanReader.trace_session_ids_by_trace_ids": _READS_THE_IDS_IT_IS_GIVEN,
        "_dedup_sql": filed(
            BY_ID_ALONE,
            why=(
                "dedup wrapper of the span reads that name their trace or span ids; "
                "the caller supplies the where clause"
            ),
        ),
    },
    "tracer.services.clickhouse.v2.trace_detail_reads": {
        "TraceDetailReadBuilder.build_annotation_query": filed(
            NO_SPAN_TABLE,
            why="scores of a trace opened by id",
            table="model_hub_score",
        ),
        "TraceDetailReadBuilder.build_content_query": _TRACE_DETAIL_BY_ID,
        "TraceDetailReadBuilder.build_eval_query": filed(
            NO_SPAN_TABLE,
            why="eval rows of a trace opened by id",
            table="tracer_eval_logger",
        ),
        "TraceDetailReadBuilder.build_identity_query": _TRACE_DETAIL_BY_ID,
        "TraceDetailReadBuilder.build_span_anchor_query": _TRACE_DETAIL_BY_ID,
    },
    "tracer.services.clickhouse.v2.trace_session_dict_reader": {
        "session_exists": filed(
            NO_SPAN_TABLE,
            BY_ID_ALONE,
            why=(
                "session existence is read from the session table for the session "
                "id it is given; the project only narrows it"
            ),
            table="trace_sessions",
        ),
    },
    "tracer.services.clickhouse.v2.validate": {
        "_DEFAULT_QUERIES": _MIGRATION_PARITY_CHECK,
        "layer_a_counts": _MIGRATION_PARITY_CHECK,
        "layer_b_deep_equal": _MIGRATION_PARITY_CHECK,
    },
    "tracer.services.eval_tasks.backfill": {
        "_backfill_hashes": _EVAL_LOG_BACKFILL,
        "_backfill_status": _EVAL_LOG_BACKFILL,
        "backfill_blank_completed_status": _EVAL_LOG_BACKFILL,
    },
    "tracer.services.grouping.feature_store": {
        "GroupingFeatureStore.read_vectors": _GROUPING_STORE,
        "GroupingFeatureStore.write_and_verify": _GROUPING_STORE,
        "nearest_sql": _GROUPING_STORE,
    },
    "tracer.services.users_list_manager": {
        "_users_attr_enrichment_query": _SESSION_OR_USER,
    },
    "tracer.views.dashboard": {
        "_read_dashboard_rollup_fast_path": filed(
            PROJECT_SET,
            why="dashboard fast path over the rollup, for the dashboard's project ids",
        ),
    },
    "tracer.views.project": {
        "ProjectView.list_projects": filed(
            PROJECT_SET,
            why=(
                "the project list's 30-day volume and last-active, read from "
                "trace_count_rollup for the projects the list view passes in"
            ),
        ),
    },
    "tracer.views.trace": {
        "_has_voice_conversation_roots": filed(
            PROJECT_SET,
            why=(
                "reads spans only to choose the voice CSV shape, a yes or no bit with "
                "no call content, for the project being exported"
            ),
        ),
    },
    "tracer.views.trace_session": {
        "TraceSessionView._fetch_end_user_info": filed(
            PROJECT_SET,
            why="session list end-user enrichment",
        ),
        "TraceSessionView.eval_logs": filed(
            NO_SPAN_TABLE,
            why="session eval logs",
            table="tracer_eval_logger",
        ),
    },
}

# ``module:qualname`` -> how the reader is filed. Written out per module above so
# that a module's name is read once; the key is the full name the scan reports.
FILED: dict[str, Filing] = {
    f"{module}:{qualname}": filing
    for module, readers in _FILED_BY_MODULE.items()
    for qualname, filing in readers.items()
}

# ---------------------------------------------------------------------------
# What a filing claims, held to the code
# ---------------------------------------------------------------------------

# The files that may name the hidden project's manager or column. A reader filed
# under PROJECT_SET cannot live in one of them. A change that adds a user of either
# token adds its file here in the same commit; the case below is the prompt.
TOKENS = re.compile(r"\bwith_simulation\b|\bsimulation_agent(?:__\w+)?\b")
TOKEN_FILES = {
    "tracer/models/project.py": "declares the column and the manager",
    "tracer/migrations/0115_project_simulation_agent.py": "adds the column",
    "tracer/services/simulation_project.py": "creates the project, opens it for a run",
}

_DETAIL = "tracer.services.clickhouse.v2.trace_detail_reads"
_DETAIL_FILE = "tracer/services/clickhouse/v2/trace_detail_reads.py"
_SPAN_READS = "tracer/views/observation_span.py"
# Each reader that opens the hidden project on purpose: the name a module must
# also use to count as a caller (the three methods share their names with the
# list builders' methods), and the modules allowed to call it. A caller is found
# by module path, never by the bare name alone.
HIDDEN_READERS = {
    f"{_DETAIL}:TraceDetailReadBuilder.build_identity_query": (
        "TraceDetailReadBuilder",
        frozenset({_DETAIL_FILE, "tracer/views/shared_link.py"}),
    ),
    f"{_DETAIL}:TraceDetailReadBuilder.build_content_query": (
        "TraceDetailReadBuilder",
        frozenset({_DETAIL_FILE}),
    ),
    f"{_DETAIL}:TraceDetailReadBuilder.build_span_anchor_query": (
        "TraceDetailReadBuilder",
        frozenset({_DETAIL_FILE}),
    ),
    (
        "tracer.services.clickhouse.v2.physical_span_detail_reads:"
        "build_physical_span_detail_query"
    ): (
        None,
        frozenset({"tracer/services/clickhouse/v2/physical_span_detail_reads.py"}),
    ),
    "simulate.services.run_traces_v3:run_traces_sql": (
        None,
        frozenset({"simulate/services/run_traces_v3.py"}),
    ),
}
_RUN_SCOPE_CALLERS = frozenset(
    {
        "tracer/services/clickhouse/v2/query_builders/trace_detail.py",
        "tracer/views/observation_span.py",
        "tracer/views/trace.py",
    }
)
_RUN_TRACES_CALLERS = frozenset(
    {"simulate/services/run_traces_v3.py", "simulate/views/run_results_v3.py"}
)
# The entry points that hand those readers their project ids, pinned the same
# way so that a new route to a builder fails here too.
ENTRY_POINTS = {
    "read_trace_detail": (
        None,
        frozenset(
            {
                _DETAIL_FILE,
                "tracer/services/clickhouse/v2/query_builders/trace_detail.py",
                "tracer/views/trace.py",
            }
        ),
    ),
    "read_span_detail": (None, frozenset({_SPAN_READS})),
    "read_physical_span_detail": (None, frozenset({_SPAN_READS})),
    # The functions that turn a run into the hidden project's id. A module that
    # lists a run's traces adds itself here in the change that first calls them.
    "simulation_scope_for_run": (None, _RUN_SCOPE_CALLERS),
    "run_scope_project_ids": (None, _RUN_SCOPE_CALLERS),
    "list_run_traces": (None, _RUN_TRACES_CALLERS),
    "simulation_project_of": (None, frozenset({"simulate/services/run_traces_v3.py"})),
    # The launcher makes or reads the hidden project of the agent one run is bound
    # to, in that run's organisation, so it can send the project's name and find the
    # agent's key by the project's workspace. It reads no span.
    "ensure_simulation_project": (
        None,
        frozenset({"simulate/services/hosted_harness_gateway.py"}),
    ),
}

# A reader filed as taking an id takes an id of what it reads. The ids that only
# scope a read to a tenant do not count.
SCOPE_IDS = {
    "project_id",
    "project_ids",
    "org_id",
    "organization_id",
    "workspace_id",
    "project_version_id",
}
BY_ID_WITHOUT_AN_ID = {
    "tracer.services.clickhouse.v2.span_reader:_dedup_sql": (
        "wraps reads that name their ids in the where clause the caller passes"
    ),
}

TELEMETRY_MODULE_PREFIX = "tfc.deployment_telemetry."


def operator_tooling(module):
    """Whether the module is run by an operator, never by a request."""

    dotted = f".{module}."
    last = module.rsplit(".", 1)[-1]
    return (
        ".management.commands." in dotted
        or ".migrations." in dotted
        or module.startswith("tracer.services.clickhouse.schema")
        or fnmatch.fnmatchcase(last, "oss_*_cdc")
        or last == "oss_native_bootstrap"
        or module == "tracer.services.clickhouse.v2.backfill"
        or module == "tracer.services.clickhouse.v2.validate"
        or last.startswith("migrate_")
        # Enterprise operator scripts, run by hand inside the backend container.
        or module.startswith("ee.cloud.internal.scripts.")
        # The scheduled retention job: deletes rows soft-deleted months ago.
        or module == "ee.cloud.tasks.retention"
    )


# A reader filed "no live caller" is shown by the code: its module-level function
# or class is named by nothing that something live names, followed through the
# modules that name it. The only module-level naming that does not count as live
# is a package re-exporting a class, listed here. A decorated unit counts as live,
# because a decorator may register it. A string equal to the name counts as a use,
# so a builder routed by its class name (a registry entry loaded with ``getattr``)
# keeps it live. Blind spot: a name built at run time, or a dotted path in
# settings, is not seen.
EXPORT_ONLY = {
    "AnnotationGraphQueryBuilder": frozenset(
        {"tracer/services/clickhouse/query_builders/__init__.py"}
    ),
    "ErrorAnalysisQueryBuilder": frozenset(
        {"tracer/services/clickhouse/query_builders/__init__.py"}
    ),
    "SessionAnalyticsQueryBuilder": frozenset(
        {"tracer/services/clickhouse/query_builders/__init__.py"}
    ),
}


# Any reason that says a reader has no caller, wherever the words stand in it. A
# reader so described is filed as no user request reaching it. The chain below is
# followed only for a reason that starts "no live caller", which names a module
# level unit; a method of a live class that nothing calls is shown by the name
# search under "dead code" instead.
NO_CALLER_WORDS = re.compile(r"\bno (live |production )?callers?\b", re.IGNORECASE)


def _unit_name(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
    elif isinstance(node, ast.AnnAssign):
        target = node.target
    else:
        return "<module>"
    return target.id if isinstance(target, ast.Name) else "<module>"


def _names_used(node, *, strings=True):
    found = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            found.add(child.attr)
        elif isinstance(child, ast.alias):
            found.add(child.name)
        elif (
            strings and isinstance(child, ast.Constant) and isinstance(child.value, str)
        ):
            found.add(child.value)  # a builder routed by its class name as a string
    return found


@functools.cache
def _units(relative):
    """``(name, node, names it uses)`` for each top-level statement of a file."""

    tree = ast.parse(_code(relative))
    # The strings of an ``__all__`` list export names; they are not uses of them.
    return [
        (name, node, _names_used(node, strings=name != "__all__"))
        for name, node in ((_unit_name(node), node) for node in tree.body)
    ]


def live_referrer(relative, unit, seen=None):
    """What keeps ``unit`` of ``relative`` live, or None when nothing does."""

    seen = set() if seen is None else seen
    if (relative, unit) in seen:
        return None
    seen.add((relative, unit))
    node = next((n for name, n, _ in _units(relative) if name == unit), None)
    if node is None or getattr(node, "decorator_list", None):
        return f"{relative.as_posix()}:{unit} is decorated or is not a top-level unit"
    for other, text in sources().items():
        if unit not in text or unit not in _code(other):
            continue
        try:
            units = _units(other)
        except SyntaxError:
            return f"{other.as_posix()} cannot be parsed"
        for name, _node, used in units:
            if (other, name) == (relative, unit) or unit not in used:
                continue
            if name == "<module>":
                if other.as_posix() in EXPORT_ONLY.get(unit, ()):
                    continue
                return f"{other.as_posix()} names it at module level"
            found = live_referrer(other, name, seen)
            if found:
                return found
    return None


def _class_code(module, qualname):
    owner = qualname.split(".")[0]
    lines = module_code(module).splitlines(keepends=True)
    for node in module_tree(module).body:
        if isinstance(node, ast.ClassDef) and node.name == owner:
            return "".join(lines[node.lineno - 1 : node.end_lineno])
    return ""


def names_a_project(key):
    """The reader's own text, else its class's, else its module's, names a project."""

    module, qualname = key.split(":", 1)
    texts = (
        code_readers(module).get(key, ""),
        _class_code(module, qualname),
        module_code(module),
    )
    return any(re.search("project", text, re.IGNORECASE) for text in texts)


def caller_problems():
    """Readers of the hidden project that a module outside the map uses."""

    problems = []
    checks = [
        (key, key.split(":", 1)[1].rsplit(".", 1)[-1], also, allowed)
        for key, (also, allowed) in HIDDEN_READERS.items()
    ]
    checks += [
        (name, name, also, allowed) for name, (also, allowed) in ENTRY_POINTS.items()
    ]
    for label, name, also, allowed in checks:
        for path in sorted(files_naming(name, also=also) - allowed):
            problems.append(
                f"{label} is used by {path}, which the callers map does not list; "
                f"it may be used only from {sorted(allowed)}"
            )
    return problems


def _filing_problems(readers):
    """What the filings claim that the code no longer shows, and unfinished ones."""

    problems = []
    functions = {}

    def function_of(module, qualname):
        if module not in functions:
            functions[module] = dict(functions_in(module_tree(module)))
        return functions[module].get(qualname)

    for key, filing in sorted(FILED.items()):
        module, qualname = key.split(":", 1)
        categories = [bucket for bucket in filing.buckets if bucket in CATEGORIES]
        if filing.why.upper().startswith("UNDECIDED"):
            problems.append(f"{key} is filed as undecided: someone has to rule")
        if not categories:
            problems.append(f"{key} is filed under a children row and no category")
        if key not in readers or module not in modules():
            continue  # a stale filing, or ``ee`` in the open-source tree
        if PROJECT_SET in filing.buckets or ROW_DRIVEN in filing.buckets:
            if not names_a_project(key):
                problems.append(f"{key} takes its project set but names no project")
            if modules()[module].as_posix() in TOKEN_FILES:
                problems.append(
                    f"{key} takes its project set, but {module} names the hidden "
                    "project's manager or column"
                )
        if (ROW_DRIVEN in filing.buckets) != (filing.row is not None):
            problems.append(f"{key} names a row model only under {ROW_DRIVEN!r}")
        if filing.row is not None and not any(
            label.startswith(f"{filing.row}.") for label in ROW_TYPES
        ):
            problems.append(
                f"{key} runs for the project of a {filing.row} row, which ROW_TYPES "
                "does not pin"
            )
        if HIDDEN_ON_PURPOSE in filing.buckets and key not in HIDDEN_READERS:
            problems.append(
                f"{key} opens the hidden project and is not in the callers map"
            )
        if COUNTS_ON_PURPOSE in filing.buckets and not module.startswith(
            TELEMETRY_MODULE_PREFIX
        ):
            problems.append(
                f"{key} counts it on purpose but is not deployment telemetry"
            )
        no_live_caller = filing.why.startswith("no live caller")
        if NO_CALLER_WORDS.search(filing.why) and NOT_A_USER_READ not in filing.buckets:
            problems.append(f"{key} says it has no caller but is not filed so")
        if no_live_caller:
            kept_live = live_referrer(modules()[module], qualname.split(".")[0])
            if NOT_A_USER_READ not in filing.buckets:
                problems.append(f"{key} has no live caller and is not filed so")
            elif kept_live:
                problems.append(
                    f"{key} is filed as having no live caller, but {kept_live}"
                )
        if (
            NOT_A_USER_READ in filing.buckets
            and not no_live_caller
            and not (
                operator_tooling(module)
                or (
                    filing.why.startswith("dead code")
                    and not files_naming(qualname.rsplit(".", 1)[-1])
                )
            )
        ):
            problems.append(
                f"{key} is filed as reached by no request, but {module} is not "
                "operator tooling and the reader is not dead code that nothing names"
            )
        if BY_ID_ALONE in filing.buckets and key not in BY_ID_WITHOUT_AN_ID:
            node = function_of(module, qualname)
            if node is not None:  # a template has no parameters to look at
                arguments = node.args
                names = [
                    argument.arg
                    for argument in (
                        arguments.posonlyargs + arguments.args + arguments.kwonlyargs
                    )
                ]
                if not any(
                    name.endswith(("_id", "_ids")) and name not in SCOPE_IDS
                    for name in names
                ):
                    problems.append(
                        f"{key} is filed as taking an id and takes no id of what it "
                        f"reads: {names}"
                    )
    for key in HIDDEN_READERS:
        if HIDDEN_ON_PURPOSE not in getattr(FILED.get(key), "buckets", ()):
            problems.append(
                f"{key} is in the callers map but is not filed as opened on purpose"
            )
    for key in BY_ID_WITHOUT_AN_ID:
        if BY_ID_ALONE not in getattr(FILED.get(key), "buckets", ()):
            problems.append(f"{key} is excused from taking an id but takes one alone")
    return problems + caller_problems()


@pytest.mark.unit
def test_every_spans_reading_function_is_filed():
    readers, scanned = scan_readers()

    assert scanned > 1000 and (
        "tracer.services.clickhouse.v2.span_reader:CHSpanReader.get" in readers
    ), "the scan did not reach the code it is meant to cover"

    cloud_present = (FUTUREAGI / "ee" / "cloud").is_dir()
    unfiled = sorted(set(readers) - set(FILED))
    stale = sorted(
        key
        for key in set(FILED) - set(readers)
        if cloud_present or not key.startswith(CLOUD_PREFIX)
    )
    assert not unfiled, (
        "these functions or templates read a span table and nobody filed them; "
        "decide which category covers each, then add it to FILED with a reason:\n"
        + "\n".join(unfiled)
    )
    assert not stale, (
        "these filings no longer match a reader; rename or remove them:\n"
        + "\n".join(stale)
    )

    unlisted = {
        key: filing.buckets
        for key, filing in FILED.items()
        if not filing.buckets or not set(filing.buckets) <= set(BUCKETS)
    }
    assert not unlisted, f"filed under a category that does not exist: {unlisted}"
    assert not [key for key, filing in FILED.items() if not filing.why.strip()]
    used = {bucket for filing in FILED.values() for bucket in filing.buckets}
    assert used == set(BUCKETS), (
        f"categories nothing is filed under: {sorted(set(BUCKETS) - used)}"
    )
    claims = _filing_problems(readers)
    assert not claims, "\n".join(claims)


@pytest.mark.unit
@pytest.mark.parametrize("cloud_present", [False, True])
@pytest.mark.parametrize(
    "missing_reader",
    [
        "ee.cloud.tasks.retention:missing_reader",
        "ee.usage.tasks:missing_reader",
        "ee.cloudlike.tasks:missing_reader",
        "tracer.tasks:missing_reader",
    ],
)
def test_reader_inventory_only_exempts_absent_private_cloud(
    monkeypatch, tmp_path, cloud_present, missing_reader
):
    (tmp_path / "ee").mkdir()
    if cloud_present:
        (tmp_path / "ee" / "cloud").mkdir()

    # Isolate checkout detection from the AST scan and the filings' own claims.
    readers = dict.fromkeys(FILED)
    filings = dict(FILED)
    filings[missing_reader] = next(iter(FILED.values()))
    monkeypatch.setitem(globals(), "FUTUREAGI", tmp_path)
    monkeypatch.setitem(globals(), "FILED", filings)
    monkeypatch.setitem(globals(), "scan_readers", lambda: (readers, 1001))
    monkeypatch.setitem(globals(), "_filing_problems", lambda readers: [])

    if cloud_present or not missing_reader.startswith("ee.cloud."):
        with pytest.raises(AssertionError, match=re.escape(missing_reader)):
            test_every_spans_reading_function_is_filed()
    else:
        test_every_spans_reading_function_is_filed()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "files"),
    [
        ("list_run_traces", _RUN_TRACES_CALLERS),
        ("simulation_project_of", frozenset({"simulate/services/run_traces_v3.py"})),
    ],
)
def test_the_run_traces_read_is_pinned_to_the_files_that_serve_it(name, files):
    assert ENTRY_POINTS.get(name) == (None, files)
    # A pin on a name nothing uses any more would guard nothing.
    assert files_naming(name)


@pytest.mark.unit
def test_no_include_simulation_anywhere():
    # A request-level switch that brings simulated traces back would let any
    # caller undo the managers; none exists, and none is to be added by accident.
    # The name is searched as a word in code that runs, so a keyword, a query
    # parameter read or a typed argument all count.
    needle = re.compile(r"\binclude_simulation\b")
    offenders = [
        relative.as_posix()
        for relative, text in sources().items()
        if "include_simulation" in text and needle.search(_code(relative))
    ]

    assert not offenders, f"include_simulation appears in: {offenders}"


# The tables that hold a project's rows without being span tables. A reader of
# one takes a project set, the project of a stored row or an id, so a filing that
# names one carries that claim as well.
PROJECT_ROW_TABLES = ("traces", "trace_sessions", "observed_attribute_keys")

# Modules whose readers take the table from the caller's argument, a data
# migration, install-time DDL text or a replicated model list, so the code never
# spells it. A filing in one of them may describe its table in words. Every other
# module has to name its table in code it can reach.
TABLE_FROM_THE_CALLER = frozenset(
    {
        "agentic_eval.core.database.ch_vector",
        "ee.usage.migrations.0015_backfill_plan_and_wallet_credits",
        "model_hub.management.commands.migrate_ch_vector_tables",
        "model_hub.management.commands.migrate_m2m_to_fk",
        "model_hub.utils.clickhouse",
        "simulate.services.run_results_v3_queries",
        "tracer.services.clickhouse.oss_cdc_bootstrap",
        "tracer.services.clickhouse.oss_outbox_cdc",
        "tracer.services.clickhouse.schema",
        "tracer.services.clickhouse.v2.id_remap_sql",
    }
)
TEMPLATE_TABLE = re.compile(r"FROM\s+\{([^}]*)\}")


def _text_value(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _aims_at(target, name, attribute):
    if attribute:
        return (
            isinstance(target, ast.Attribute)
            and target.attr == name
            and isinstance(target.value, ast.Name)
            and target.value.id in {"self", "cls"}
        )
    return isinstance(target, ast.Name) and target.id == name


def _assigned_texts(nodes, name, *, attribute=False):
    """``(texts, whole)``: the strings assigned to a name, or to ``self.<name>``.

    ``whole`` is False when something else is assigned to it as well.
    """

    texts, whole = set(), True
    for node in nodes:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        elif isinstance(node, (ast.AugAssign, ast.For)):
            targets, value = [node.target], None
        else:
            continue
        for target in targets:
            if _aims_at(target, name, attribute):
                text = _text_value(value) if value is not None else None
                if text is None:
                    whole = False
                else:
                    texts.add(text)
            elif any(_aims_at(part, name, attribute) for part in ast.walk(target)):
                whole = False  # unpacked from something else
    return texts, whole


def _argument_default(function, name):
    arguments = function.args
    positional = arguments.posonlyargs + arguments.args
    defaults = [None] * (len(positional) - len(arguments.defaults)) + list(
        arguments.defaults
    )
    pairs = list(zip(positional, defaults, strict=True)) + list(
        zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True)
    )
    for argument, default in pairs:
        if argument.arg == name:
            return True, _text_value(default) if default is not None else None
    return False, None


def _class_node(tree, name):
    return next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name), None
    )


def _base_class(module, name):
    """``(module, ClassDef)`` of a base named ``name`` in ``module``, if it is ours."""

    tree = module_tree(module)
    klass = _class_node(tree, name)
    if klass is not None:
        return module, klass
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in modules():
            for alias in node.names:
                if (alias.asname or alias.name) == name:
                    found = _class_node(module_tree(node.module), alias.name)
                    if found is not None:
                        return node.module, found
    return None


def template_texts(module, qualname, expression):
    """``(texts, complete)``: what ``FROM {expression}`` can name, read from code.

    A name is looked up in the reader, then in its module, then in the module it
    was imported from; ``self.X`` and ``cls.X`` in the class and the classes of
    the same module it extends. Anything else (a call, a subscript, a name made
    by a call) is unresolved: ``complete`` is False and the texts are only what
    was found on the way.
    """

    try:
        node = ast.parse(expression.strip(), mode="eval").body
    except SyntaxError:
        return set(), False
    tree = module_tree(module)
    function = dict(functions_in(tree)).get(qualname)
    if isinstance(node, ast.Name):
        if function is not None:
            texts, whole = _assigned_texts(ast.walk(function), node.id)
            has_default, default = _argument_default(function, node.id)
            if has_default:
                if default is not None:
                    texts.add(default)
                return texts, False
            if texts or not whole:
                return texts, bool(texts) and whole
        texts, whole = _assigned_texts(tree.body, node.id)
        if texts:
            return texts, whole
        for imported in ast.walk(tree):
            if not isinstance(imported, ast.ImportFrom) or not imported.module:
                continue
            for alias in imported.names:
                bound = alias.asname or alias.name
                if bound == node.id and imported.module in modules():
                    other = module_tree(imported.module).body
                    texts, whole = _assigned_texts(other, alias.name)
                    return texts, bool(texts) and whole
        return set(), False
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in {"self", "cls"}
    ):
        owner = _class_node(tree, qualname.split(".")[0])
        texts, complete = set(), True
        pending = [(module, owner)] if owner is not None else []
        while pending:
            home, klass = pending.pop()
            found, whole = _assigned_texts(klass.body, node.attr)
            more, more_whole = _assigned_texts(
                ast.walk(klass), node.attr, attribute=True
            )
            texts |= found | more
            complete = complete and whole and more_whole
            for method in ast.walk(klass):
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    has_default, default = _argument_default(method, node.attr.lower())
                    if has_default:
                        complete = False
                        if default is not None:
                            texts.add(default)
            for base in klass.bases:
                if isinstance(base, ast.Name):
                    found = _base_class(home, base.id)
                    if found is not None:
                        pending.append(found)
        return texts, bool(texts) and complete
    return set(), False


@functools.cache
def _imported_modules(module):
    found = set()
    for node in ast.walk(module_tree(module)):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return sorted(name for name in found if name in modules())


def _table_parts(named):
    parts = re.split(r",| and ", named)
    return [part.strip().lower() for part in parts if part.strip()]


@pytest.mark.unit
def test_no_span_table_bucket_names_its_table():
    # The filing is a person's word; the code is checked against it. A comment or
    # a docstring that says "FROM spans" reads nothing. A template is read through
    # the code where it can be: ``self.TABLE``, a constant, an argument default.
    # A template made by a call stays unresolved, and then the table the filing
    # names has to appear in the code it can reach: the reader, its class, its
    # module or a module that module imports. That last check is weak: a filing
    # that names a table the module already mentions for another reason passes it.
    # Of the 94 span readers whose only match is a template, 14 could be filed
    # under such a table without the case noticing (for example a time series
    # reader filed under ``model_hub_score``); narrowing the search to the reader
    # and its class would break 75 filings that are right.
    problems = []
    for key, filing in sorted(FILED.items()):
        if NO_SPAN_TABLE not in filing.buckets:
            if filing.table is not None:
                problems.append(f"{key} names a table but is not filed as reading none")
            continue
        named = (filing.table or "").strip()
        if not named:
            problems.append(f"{key} is filed as reading no span table and names none")
            continue
        if any(span_table in named.lower() for span_table in SPAN_TABLES):
            problems.append(f"{key} names {named!r}, which is a span table")
        parts = _table_parts(named)
        if any(part in PROJECT_ROW_TABLES for part in parts) and not (
            {PROJECT_SET, BY_ID_ALONE, ROW_DRIVEN} & set(filing.buckets)
        ):
            problems.append(
                f"{key} reads a table that holds a project's rows and makes no "
                "project or id claim"
            )
        module, qualname = key.split(":", 1)
        if module not in modules():
            continue  # a stale filing; the first case reports it
        text = code_readers(module).get(key, "")
        for match in READS_A_TABLE.finditer(text):
            read = match.group(1)
            if (
                read.startswith(("spans", "trace_count_rollup"))
                or "span" in read.lower()
            ):
                problems.append(f"{key} says it reads no span table but reads {read!r}")
        if HOURLY_ROLLUP_SOURCE in text:
            problems.append(f"{key} says it reads no span table but builds the rollup")
        reachable = None
        for expression in sorted(set(TEMPLATE_TABLE.findall(text))):
            texts, complete = template_texts(module, qualname, expression)
            spans = sorted(
                value
                for value in texts
                if any(span_table in value.lower() for span_table in SPAN_TABLES)
            )
            if spans:
                problems.append(
                    f"{key} says it reads no span table but {{{expression}}} is {spans}"
                )
            elif complete:
                if not all(value.lower() in named.lower() for value in texts):
                    problems.append(
                        f"{key} names {named!r} but {{{expression}}} is {sorted(texts)}"
                    )
            elif module not in TABLE_FROM_THE_CALLER:
                if reachable is None:
                    neighbours = (module, *_imported_modules(module))
                    reachable = (
                        text
                        + _class_code(module, qualname)
                        + "".join(module_code(other) for other in neighbours)
                    ).lower()
                if not all(part in reachable for part in parts):
                    problems.append(
                        f"{key} names {named!r}, which its code does not show for "
                        f"{{{expression}}}"
                    )

    assert not problems, "\n".join(problems)


@pytest.mark.unit
def test_only_the_listed_modules_name_with_simulation():
    # Blind spot: ``simulation_agent_id`` on a project is not matched, because
    # another table carries the same attribute name.
    offenders = {}
    for relative, text in sources().items():
        if "with_simulation" not in text and "simulation_agent" not in text:
            continue
        found = sorted(set(TOKENS.findall(_code(relative))))
        if found and relative.as_posix() not in TOKEN_FILES:
            offenders[relative.as_posix()] = found

    assert not offenders, (
        "these modules name the hidden project's manager or column; add the module "
        "to TOKEN_FILES with a one-line reason in the change that first names it: "
        f"{offenders}"
    )


# ---------------------------------------------------------------------------
# Who may create a project
# ---------------------------------------------------------------------------

RESERVED_PREFIX = "fi-simulation-"
CREATING_METHODS = {"create", "get_or_create", "update_or_create", "bulk_create"}

CONSTANT_NAME = "constant name"
NON_RESERVED_PREFIX = "constant non-reserved prefix"
OPERATOR_TOOLING = "operator tooling"
REFUSES_RESERVED_NAMES = "refuses reserved names"
HIDDEN_PROJECT_OF_AN_AGENT = "hidden project of an agent"

# Files the dev branch deletes and this branch still has. An entry for one of them
# is skipped once the file is gone, whatever its claim, so the merge that brings
# the deletion does not fail the case below; the entry goes when the file does.
# Nothing else is skipped: a pinned creator in any other absent file fails.
REMOVED_ON_DEV = {
    "ai_tools/tools/tracing/create_project.py",
    "ai_tools/tools/tracing/update_project.py",
}

# Every direct call that builds a project in non-test code, by file and function,
# with the claim that makes it safe and why. A creator that fits no claim fails
# the case below. The view-level checks (create, rename, update) are tested with
# the views; this covers constructor calls only. The scan sees ``Project(...)``,
# ``Project.<manager>.create(...)`` and the same through a module attribute
# (``pm.Project``). It does not follow a model fetched at run time
# (``apps.get_model("tracer", "Project")``) or held in a variable.
CREATORS = {
    "accounts/user_onboard.py:create_demo_traces_and_spans": (
        NON_RESERVED_PREFIX,
        "the name starts with 'Demo SQL Agent (' and is followed by a trace type",
    ),
    "ai_tools/tools/tracing/create_project.py:CreateProjectTool.execute": (
        REFUSES_RESERVED_NAMES,
        "returns an error for a name that carries an agent id before it builds the "
        "project",
    ),
    "tfc/management/commands/seed_dummy_data.py:Command._seed_tracing": (
        OPERATOR_TOOLING,
        "a seeding command; the names come from a literal list of '[Seed] ' names",
    ),
    "tfc/management/commands/seed_dummy_data.py:Command._seed_voice_observability": (
        OPERATOR_TOOLING,
        "a seeding command; the names come from VAPI_PROJECT_NAMES, a literal list "
        "of '[Seed] ' names",
    ),
    "tracer/services/simulation_investigation.py:_project_for_execution": (
        NON_RESERVED_PREFIX,
        "the name starts with 'Simulation Debug ' and is followed by an execution id",
    ),
    "tracer/services/simulation_project.py:ensure_simulation_project": (
        HIDDEN_PROJECT_OF_AN_AGENT,
        "the one function that builds the hidden project, through its own manager",
    ),
    "tracer/utils/otel.py:get_or_create_project": (
        REFUSES_RESERVED_NAMES,
        "refuses a name that carries an agent id before it creates anything",
    ),
}


class Creator(NamedTuple):
    key: str
    relative: str
    manager: str | None
    call: ast.Call
    function: ast.AST | None


def creators_in(source, relative):
    """Each call that builds a project in ``source``, filed by file and function."""

    tree = ast.parse(source)
    names = {"Project"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(
                alias.asname
                for alias in node.names
                if alias.name == "Project" and alias.asname
            )
    found = []

    def is_project(node):
        """The model: a name bound to it, or ``<module>.Project``."""

        return (isinstance(node, ast.Name) and node.id in names) or (
            isinstance(node, ast.Attribute) and node.attr == "Project"
        )

    def creation(call):
        target = call.func
        if is_project(target):
            return True, None
        if (
            isinstance(target, ast.Attribute)
            and target.attr in CREATING_METHODS
            and isinstance(target.value, ast.Attribute)
            and is_project(target.value.value)
        ):
            return True, target.value.attr
        return False, None

    def visit(node, scope, function):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = child if not isinstance(child, ast.ClassDef) else function
                visit(child, scope + [child.name], inner)
                continue
            if isinstance(child, ast.Call):
                is_creation, manager = creation(child)
                if is_creation:
                    where = ".".join(scope) or "<module>"
                    key = f"{relative}:{where}"
                    found.append(Creator(key, relative, manager, child, function))
            visit(child, scope, function)

    visit(tree, [], None)
    return found


def _name_argument(call):
    for keyword in call.keywords:
        if keyword.arg == "name":
            return keyword.value
        if keyword.arg == "defaults" and isinstance(keyword.value, ast.Dict):
            pairs = zip(keyword.value.keys, keyword.value.values, strict=True)
            for key, value in pairs:
                if isinstance(key, ast.Constant) and key.value == "name":
                    return value
    return None


def _leading_text(node):
    """The text a name starts with, or None when it starts with something unknown."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        first = node.values[0] if node.values else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
        return ""
    return None


def _fixed_name(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and all(
        isinstance(part, ast.Constant) for part in node.values
    ):
        return "".join(part.value for part in node.values)
    return None


GUARD = "agent_id_in_simulation_project_name"


def _tests_the_guard(test):
    """Whether the guard call alone decides the ``if`` that ``test`` opens."""

    if isinstance(test, ast.Call):
        called = getattr(test.func, "id", None) or getattr(test.func, "attr", None)
        return called == GUARD
    if isinstance(test, ast.Compare):
        return (
            len(test.ops) == 1
            and isinstance(test.ops[0], ast.IsNot)
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value is None
            and _tests_the_guard(test.left)
        )
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return _tests_the_guard(test.operand)
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        return any(_tests_the_guard(value) for value in test.values)
    return False


def creator_claim_holds(claim, creator):
    name = _name_argument(creator.call)
    if claim == CONSTANT_NAME:
        fixed = _fixed_name(name) if name is not None else None
        return fixed is not None and not fixed.startswith(RESERVED_PREFIX)
    if claim == NON_RESERVED_PREFIX:
        leading = _leading_text(name) if name is not None else None
        # The start must differ from the reserved prefix before either ends.
        return bool(leading) and not (
            leading.startswith(RESERVED_PREFIX) or RESERVED_PREFIX.startswith(leading)
        )
    if claim == OPERATOR_TOOLING:
        return "/management/commands/" in f"/{creator.relative}"
    if claim == REFUSES_RESERVED_NAMES:
        return creator.function is not None and any(
            isinstance(node, ast.If)
            and node.lineno < creator.call.lineno
            and _tests_the_guard(node.test)
            and any(
                isinstance(statement, (ast.Raise, ast.Return))
                for statement in node.body
            )
            for node in ast.walk(creator.function)
        )
    if claim == HIDDEN_PROJECT_OF_AN_AGENT:
        return (
            creator.relative == "tracer/services/simulation_project.py"
            and creator.manager == "with_simulation"
        )
    return False


def creator_problems(found, pinned, present):
    """``found`` maps a key to its creators; ``present`` says which files exist."""

    problems = []
    for key, creators in sorted(found.items()):
        if key not in pinned:
            problems.append(
                f"{key} builds a project and nobody pinned it; pin it in CREATORS "
                "with the claim that keeps it from a reserved name"
            )
            continue
        claim, _why = pinned[key]
        for creator in creators:
            if not creator_claim_holds(claim, creator):
                problems.append(
                    f"{key} (line {creator.call.lineno}) is pinned as {claim!r} and "
                    "the code does not show it"
                )
    for key, (claim, _why) in sorted(pinned.items()):
        if key in found:
            continue
        relative = key.split(":", 1)[0]
        if relative in REMOVED_ON_DEV and relative not in present:
            continue
        problems.append(f"{key} is pinned as a creator and builds no project any more")
    return problems


@pytest.mark.unit
def test_an_entry_for_a_file_dev_deletes_is_skipped_only_while_it_is_absent():
    tool = "ai_tools/tools/tracing/create_project.py"
    pinned = {
        f"{tool}:CreateProjectTool.execute": (REFUSES_RESERVED_NAMES, "a guard"),
    }
    # The file is there and builds nothing: the entry is stale and must fail.
    assert creator_problems({}, pinned, {tool}) == [
        f"{tool}:CreateProjectTool.execute is pinned as a creator and builds no "
        "project any more"
    ]
    # The file is gone from the tree: the entry goes with it.
    assert creator_problems({}, pinned, set()) == []

    # Any other file is not excused by being absent.
    other = {"tracer/services/gone.py:make": (CONSTANT_NAME, "a literal")}
    assert creator_problems({}, other, set()) == [
        "tracer/services/gone.py:make is pinned as a creator and builds no project "
        "any more"
    ]


SAMPLE_CREATORS = """
from tracer.models.project import Project
from tracer.models.project import Project as TracerProject


def one():
    return Project(name=NAME)


class Seeder:
    def two(self, name):
        TracerProject.objects.create(name=name)
        Project.with_simulation.get_or_create(defaults={"name": f"{name}-x"})
        Project.objects.filter(name="not a creator")
        other.objects.create(name=name)
        models.Project.objects.update_or_create(name=name)
        models.Project(name=name)
"""


@pytest.mark.unit
def test_the_creator_scan_finds_constructor_and_manager_calls():
    found = creators_in(SAMPLE_CREATORS, "sample.py")

    assert [(creator.key, creator.manager) for creator in found] == [
        ("sample.py:one", None),
        ("sample.py:Seeder.two", "objects"),
        ("sample.py:Seeder.two", "with_simulation"),
        ("sample.py:Seeder.two", "objects"),
        ("sample.py:Seeder.two", None),
    ]
    by_name = {creator.call.lineno: _name_argument(creator.call) for creator in found}
    assert all(node is not None for node in by_name.values())
    # A name that starts with a variable fits no claim about its start.
    assert not creator_claim_holds(NON_RESERVED_PREFIX, found[1])
    assert not creator_claim_holds(CONSTANT_NAME, found[1])
    # A name that starts with text the reserved prefix could continue fits none.
    sample = ast.parse('Project(name=f"fi-{x}")').body[0].value
    assert not creator_claim_holds(
        NON_RESERVED_PREFIX, Creator("k", "a.py", None, sample, None)
    )


@pytest.mark.unit
def test_only_the_simulation_service_creates_a_reserved_name_project():
    found = {}
    for relative, text in sources().items():
        if "Project" not in text:
            continue
        try:
            creators = creators_in(text, relative.as_posix())
        except SyntaxError:
            continue
        for creator in creators:
            found.setdefault(creator.key, []).append(creator)

    present = {relative.as_posix() for relative in sources()}
    problems = creator_problems(found, CREATORS, present)
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# Nothing of the old rule is left
# ---------------------------------------------------------------------------

# Names the earlier hiding rule used. The managers replaced it, so none may come
# back in code that runs; tests are not searched, and neither is prose.
GONE_NAMES = (
    "hide_simulated",
    "row_exclusion_sql",
    "record_holding",
    "holds_simulated",
    "marked_trace_ids",
    "HOLDING_FLAG",
)
GONE = re.compile("|".join(re.escape(name) for name in GONE_NAMES))


@pytest.mark.unit
def test_nothing_of_the_predicate_survives():
    offenders = []
    for relative, text in sources().items():
        if not GONE.search(text):
            continue
        for name in sorted(set(GONE.findall(_code(relative)))):
            offenders.append(f"{relative.as_posix()}: {name}")

    assert not offenders, (
        "code that runs names the removed hiding rule; the managers replaced it:\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# No Postgres row names a hidden project after a simulated run
# ---------------------------------------------------------------------------

TRACE_STORE_ROW = "trace store row: none on this deployment"
USER_MADE = "needs a user-made object the pickers cannot offer"
JOB_THROUGH_MANAGERS = "job that visits projects through the managers"
OTHER_PROJECTS_ROW = "the agent's observability row, which belongs to the other project"
ROW_REASONS = (TRACE_STORE_ROW, USER_MADE, JOB_THROUGH_MANAGERS, OTHER_PROJECTS_ROW)

# Every reverse relation of a project, as ``app.Model.field``, and why a
# simulated run cannot leave a row behind it. A new model with a foreign key to a
# project fails the case below before any row is looked at: its author has to
# decide whether a simulated run can create one.
ROW_TYPES = {
    "integrations.IntegrationConnection.project": USER_MADE,
    "model_hub.AnnotationQueue.project": USER_MADE,
    "model_hub.AnnotationsLabels.project": USER_MADE,
    "prism.AgentccProject.tracer_project": USER_MADE,
    "tracer.CustomEvalConfig.project": USER_MADE,
    "tracer.EndUser.project": TRACE_STORE_ROW,
    "tracer.ErrorFeedAlertRule.project": USER_MADE,
    # Made from an error cluster, so only for a project the clustering job visits.
    "tracer.ErrorFeedIssueEvent.project": JOB_THROUGH_MANAGERS,
    "tracer.ErrorMemory.project": JOB_THROUGH_MANAGERS,
    "tracer.ErrorPattern.project": JOB_THROUGH_MANAGERS,
    "tracer.EvalTask.project": USER_MADE,
    "tracer.EvaluationRun.project": USER_MADE,
    "tracer.ImagineAnalysis.project": USER_MADE,
    "tracer.ObservabilityProvider.project": OTHER_PROJECTS_ROW,
    "tracer.ObservationSpan.project": TRACE_STORE_ROW,
    "tracer.ProjectCapability.project": JOB_THROUGH_MANAGERS,
    "tracer.ProjectVersion.project": USER_MADE,
    "tracer.ProjectVersionWinner.project": USER_MADE,
    "tracer.ReplaySession.project": USER_MADE,
    "tracer.SavedView.project": USER_MADE,
    "tracer.Trace.project": TRACE_STORE_ROW,
    "tracer.TraceErrorAnalysis.project": JOB_THROUGH_MANAGERS,
    "tracer.TraceErrorAnalysisTask.project": JOB_THROUGH_MANAGERS,
    "tracer.TraceErrorGroup.project": JOB_THROUGH_MANAGERS,
    "tracer.TraceGroupingScope.project": JOB_THROUGH_MANAGERS,
    "tracer.TraceInvestigationDelivery.project": JOB_THROUGH_MANAGERS,
    "tracer.TraceInvestigationJob.project": JOB_THROUGH_MANAGERS,
    "tracer.TraceInvestigationReport.project": JOB_THROUGH_MANAGERS,
    "tracer.TraceScanConfig.project": USER_MADE,
    "tracer.TraceScanResult.project": JOB_THROUGH_MANAGERS,
    "tracer.TraceSession.project": TRACE_STORE_ROW,
    "tracer.UserAlertMonitor.project": USER_MADE,
}


# The tables that name a project by a plain id instead of a foreign key, so
# ``Project._meta.related_objects`` does not list them. Each is a column whose
# name ends in ``project_id`` on a model that does not declare it as a relation;
# the case below scans every model for them, so a new one fails until someone
# decides, and it counts their rows next to the foreign keys.
#
# The scan reaches only that name rule. A soft project id under another name (a
# field called ``project`` or ``tracer_project``, an array field called
# ``project_ids``) or kept inside a JSON field is not found. Every model checked
# today has the two below and nothing else of the kind: ``applied_projects`` of a
# guardrail policy holds ids of the gateway's own projects, and ``project_key``
# and ``external_project_name`` are not ids.
SOFT_PROJECT_COLUMNS = {
    "model_hub.Score.tracer_project_id": (
        "the project of a trace or span a user scores; written by the score views"
    ),
    "tracer.TraceSessionOverlay.project_id": (
        "the project of a session a user bookmarks or renames; written by the "
        "session view"
    ),
}


def _soft_project_columns(apps):
    return {
        f"{model._meta.label}.{field.name}"
        for model in apps.get_models()
        for field in model._meta.get_fields()
        if not field.is_relation and field.name.endswith("project_id")
    }


class _Recorder:
    """What reached the exporter boundary: the project name of each span."""

    def __init__(self):
        self.project_names = []

    def clear(self):
        self.project_names.clear()


def _install_recorder(monkeypatch):
    from opentelemetry.sdk.trace.export import SpanExportResult

    recorder = _Recorder()

    class Exporter:
        def __init__(self, *args, **kwargs):
            pass

        def export(self, spans):
            for span in spans:
                recorder.project_names.append(
                    span.resource.attributes.get("project_name")
                )
            return SpanExportResult.SUCCESS

        def shutdown(self):
            return None

    def emit(spans, *, project_name, **kwargs):
        recorder.project_names.extend([project_name] * len(spans))
        return len(spans)

    monkeypatch.setattr(
        "simulate.services.sim_collector_emit.OTLPSpanExporter", Exporter
    )
    monkeypatch.setattr(
        "tracer.services.collector_ingest.emit_spans_to_collector", emit
    )
    return recorder


def _simulated_span():
    return {
        "trace_id": uuid.uuid4().hex,
        "span_id": uuid.uuid4().hex[:16],
        "parent_span_id": None,
        "name": "simulated call",
        "start_time": 1_000_000_000,
        "end_time": 2_000_000_000,
        "attributes": {"gen_ai.span.kind": "CONVERSATION"},
    }


# ``_agent_without_context`` and ``_run`` below copy the helpers of the same
# names in ``test_simulation_project.py``, so that file stays as it is. They move
# to one shared fixture module when both files are edited together.
def _agent_without_context(organization, workspace, name):
    from simulate.models.agent_definition import AgentDefinition
    from tfc.middleware.workspace_context import clear_workspace_context

    clear_workspace_context()
    agent = AgentDefinition.objects.create(
        agent_name=name,
        description=f"{name} description",
        agent_type="text",
        inbound=True,
        organization=organization,
        workspace=workspace,
    )
    clear_workspace_context()
    return agent


def _run(organization, workspace, agent):
    from simulate.models.run_test import RunTest
    from simulate.models.scenarios import Scenarios
    from simulate.models.test_execution import CallExecution, TestExecution
    from tfc.middleware.workspace_context import clear_workspace_context

    clear_workspace_context()
    run = RunTest.objects.create(
        name="Run",
        organization=organization,
        workspace=workspace,
        agent_definition=agent,
    )
    execution = TestExecution.objects.create(
        run_test=run,
        status=TestExecution.ExecutionStatus.COMPLETED,
    )
    scenario = Scenarios.objects.create(
        name="Scenario",
        source="synthetic",
        organization=organization,
        workspace=workspace,
    )
    call = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status=CallExecution.CallStatus.COMPLETED,
    )
    return run, execution, call


def _ensure_twice(world):
    from tracer.services.simulation_project import ensure_simulation_project

    first = ensure_simulation_project(world.agent)
    second = ensure_simulation_project(world.agent)
    assert first.pk == second.pk == world.hidden.pk


def _export_a_simulated_call(world):
    from simulate.services.sim_collector_emit import export_sim_spans

    # Nothing routes a simulated call to the hidden project on this branch, so the
    # case hands it the project name the way its caller will.
    export_sim_spans(
        [_simulated_span()],
        project_name=world.hidden.name,
        project_type=world.hidden.trace_type,
        api_key="inventory-case-key",
        secret_key="inventory-case-secret",
    )


def _export_a_polled_call_matched_by_number(world):
    from tracer.services.clickhouse.query_builders.voice_call_list import (
        VoiceCallListQueryBuilder,
    )
    from tracer.services.simulator_phones import SIMULATOR_PHONE_NUMBERS
    from tracer.utils.observability_provider import (
        _create_observation_span,
        _export_provider_call_to_collector,
    )

    raw_log = {"customer": {"number": SIMULATOR_PHONE_NUMBERS[0]}}
    assert VoiceCallListQueryBuilder.is_simulator_call(raw_log, "vapi")
    # An unsaved provider: a saved row for the hidden project would itself be a
    # row that names it.
    provider = SimpleNamespace(provider="vapi")
    log_id = "inventory-case-call"
    span = _create_observation_span(
        world.hidden,
        provider,
        {"span_attributes": {"raw_log": raw_log}},
        {"provider": "vapi", "provider_log_id": log_id},
        log_id,
    )
    span.project = world.hidden
    _export_provider_call_to_collector(span, "vapi", log_id)


# The platform's paths for a simulated run: a name, a callable that takes the
# case's world, and whether the path exports spans. Later changes append theirs
# (the launcher's export, ingestion, correction), and the case goes on guarding
# them.
SIMULATED_RUN_PATHS = [
    ("ensure the hidden project, twice", _ensure_twice, False),
    ("export a simulated call", _export_a_simulated_call, True),
    (
        "export a polled call matched by number",
        _export_a_polled_call_matched_by_number,
        True,
    ),
]


@pytest.mark.integration
@pytest.mark.django_db
def test_no_postgres_row_names_a_hidden_project_after_a_simulated_run(
    organization, workspace, monkeypatch
):
    from django.apps import apps

    from simulate.models.run_test import RunTest
    from simulate.models.test_execution import CallExecution, TestExecution
    from tracer.models.project import Project
    from tracer.services.simulation_project import ensure_simulation_project

    declared = {
        f"{relation.related_model._meta.label}.{relation.field.name}"
        for relation in Project._meta.related_objects
    }
    changed = sorted(declared ^ set(ROW_TYPES))
    assert declared == set(ROW_TYPES), (
        "the models that point at a project changed; decide whether a simulated "
        f"run can leave a row in each and pin it in ROW_TYPES: {changed}"
    )
    assert set(ROW_TYPES.values()) <= set(ROW_REASONS)
    soft = _soft_project_columns(apps)
    assert soft == set(SOFT_PROJECT_COLUMNS), (
        "the models that name a project by a plain id changed; decide whether a "
        "simulated run can leave a row in each and pin it in "
        f"SOFT_PROJECT_COLUMNS: {sorted(soft ^ set(SOFT_PROJECT_COLUMNS))}"
    )
    assert all(reason.strip() for reason in SOFT_PROJECT_COLUMNS.values())

    recorder = _install_recorder(monkeypatch)
    agent = _agent_without_context(organization, workspace, "Inventory agent")
    hidden = ensure_simulation_project(agent)
    run, execution, call = _run(organization, workspace, agent)
    world = SimpleNamespace(
        agent=agent, hidden=hidden, run=run, execution=execution, call=call
    )

    for name, path, exports in SIMULATED_RUN_PATHS:
        recorder.clear()
        path(world)
        if exports:
            assert hidden.name in recorder.project_names, (
                f"{name} sent nothing for the hidden project, so the row check "
                f"below would prove nothing for it: {recorder.project_names}"
            )
        else:
            assert not recorder.project_names, (
                f"{name} exported {recorder.project_names}"
            )

    # The run's own rows exist, so a row that is missing below is not for lack of one.
    assert RunTest.objects.filter(pk=run.pk).exists()
    assert TestExecution.objects.filter(pk=execution.pk).exists()
    assert CallExecution.objects.filter(pk=call.pk).exists()
    assert Project.with_simulation.filter(pk=hidden.pk).exists()

    named = {}
    for label in [*ROW_TYPES, *SOFT_PROJECT_COLUMNS]:
        model_label, field = label.rsplit(".", 1)
        model = apps.get_model(model_label)
        count = model._base_manager.filter(**{field: hidden.pk}).count()
        if count:
            named[label] = count
    assert not named, f"rows name the hidden project after a simulated run: {named}"
