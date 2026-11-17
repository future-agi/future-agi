"""The update's stop check for agentcc 0031's request-log indexes.

agentcc_check_request_log_indexes is read only. It passes when all five indexes
0031 adds are valid, and stops (exit 1) naming each one that is invalid (an
interrupted concurrent build) or missing. Each test runs on connections["default"]
and puts the five indexes back valid afterwards.
"""

import os
import re
from contextlib import contextmanager
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.db.migrations.loader import MigrationLoader

pytestmark = pytest.mark.django_db(transaction=True)

TABLE = "agentcc_request_log"
COMMAND = "agentcc_check_request_log_indexes"
WANTED = {
    "agentcc_rl_org_run_idx": "(organization_id, agent_run_id)",
    "agentcc_rl_org_agent_idx": "(organization_id, agent_id, started_at DESC)",
    "agentcc_rl_org_billing_idx": "(organization_id, billing_mode, started_at DESC)",
    "agentcc_rl_org_request_idx": "(organization_id, request_id)",
    "agentcc_rl_org_variant_idx": "(organization_id, policy_variant, started_at DESC)",
}
NAMES = list(WANTED)
NOT_READ_ONLY = re.compile(r"^\s*(CREATE|DROP|ALTER|UPDATE|INSERT|DELETE|SET)\b", re.I)
# The remedy the stop line ends with when 0031 is recorded as applied.
APPLIED_REMEDY = (
    " 0031 is recorded as applied, so rerunning the schema phase does not "
    "rebuild them. Stay quiesced; drop each index named above concurrently, "
    "if it exists, and build it again concurrently with 0031's definition; "
    "then rerun this check."
)


def _execute(sql, params=None):
    with connections["default"].cursor() as cursor:
        cursor.execute(sql, params)


def _valid():
    with connections["default"].cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname, i.indisvalid
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            JOIN pg_class t ON t.oid = i.indrelid
            WHERE t.relname = %s
            """,
            [TABLE],
        )
        return dict(cursor.fetchall())


def _restore():
    for name, columns in WANTED.items():
        if _valid().get(name) is False:
            _execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"')
        _execute(
            f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{name}" ON {TABLE} {columns}'
        )


@contextmanager
def _written():
    """Every statement the command sends that is not a read."""
    sent = []

    def record(execute, sql, params, many, context):
        if NOT_READ_ONLY.match(str(sql)):
            sent.append(str(sql))
        return execute(sql, params, many, context)

    with connections["default"].execute_wrapper(record):
        yield sent


def _check():
    out = StringIO()
    try:
        call_command(COMMAND, stdout=out)
    finally:
        _check.lines = out.getvalue().splitlines()
    return _check.lines


def test_it_reads_the_five_index_names_from_0031():
    from agentcc.management.commands.agentcc_check_request_log_indexes import (
        index_names,
    )

    assert index_names(MigrationLoader(connections["default"])) == NAMES


def test_all_five_valid_lets_the_update_go_on():
    with _written() as sent:
        lines = _check()
    assert sent == []
    assert lines == [f"request-log index {n}: valid" for n in NAMES] + [
        "request-log indexes: 5 of 5 valid"
    ]


def test_an_invalid_index_stops_the_update_by_name():
    try:
        # What an interrupted CREATE INDEX CONCURRENTLY leaves: the index, INVALID.
        _execute(
            "UPDATE pg_index SET indisvalid = false WHERE indexrelid = %s::regclass",
            [NAMES[1]],
        )
        with (
            _written() as sent,
            pytest.raises(
                CommandError,
                # The test database has 0031 recorded as applied.
                match="^"
                + re.escape(
                    f"stop: request-log indexes not valid: {NAMES[1]} invalid."
                    + APPLIED_REMEDY
                )
                + "$",
            ),
        ):
            _check()
        assert sent == []
        assert f"request-log index {NAMES[1]}: invalid" in _check.lines
        assert _check.lines[-1] == "request-log indexes: 4 of 5 valid"
    finally:
        _restore()


def test_a_missing_index_stops_the_update_by_name():
    try:
        _execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{NAMES[3]}"')
        _execute(
            "UPDATE pg_index SET indisvalid = false WHERE indexrelid = %s::regclass",
            [NAMES[0]],
        )
        with pytest.raises(
            CommandError,
            match="^"
            + re.escape(
                f"stop: request-log indexes not valid: {NAMES[0]} invalid, "
                f"{NAMES[3]} missing." + APPLIED_REMEDY
            )
            + "$",
        ):
            _check()
        assert f"request-log index {NAMES[3]}: missing" in _check.lines
        assert _check.lines[-1] == "request-log indexes: 3 of 5 valid"
    finally:
        _restore()


def test_the_remedy_for_an_interrupted_0031_is_a_rerun_of_the_schema_phase():
    # 0031 not recorded (an interrupted build): its rerun drops an index left
    # invalid and builds with IF NOT EXISTS (test_migration_resumable_indexes.py),
    # so the remedy is the schema phase again, not a drop by hand.
    from agentcc.management.commands.agentcc_check_request_log_indexes import remedy

    assert remedy(False) == (
        " 0031 is not recorded as applied, so rerunning the schema phase repairs "
        "them: it drops an index an interrupted build left invalid, keeps one "
        "already valid and builds the missing ones. Stay quiesced; rerun the "
        "schema phase, and then this check."
    )
    assert "already exists" not in remedy(False)
    assert remedy(True) == APPLIED_REMEDY


# Which connection the check reads through, by profile.
COMMAND_MODULE = "agentcc.management.commands.agentcc_check_request_log_indexes"
REFUSED_WITHOUT_DIRECT = (
    "On the hosted profile this check reads only through default_direct; run it "
    "in the bootstrap/operator pod with PG_DIRECT_HOST set"
)


class _Aliases:
    """connections as the command sees them; default_direct, when present, is this
    test's database. Records each alias the command asks for. Without direct the
    alias is removed, since the test settings may define it already."""

    def __init__(self, direct):
        self.asked = []
        self.databases = dict(connections.databases)
        self.databases.pop("default_direct", None)
        if direct:
            self.databases["default_direct"] = connections.databases["default"]

    def __getitem__(self, alias):
        self.asked.append(alias)
        return connections["default"]


@contextmanager
def _pod(profile, direct_alias, direct_host):
    aliases = _Aliases(direct_alias)
    with (
        patch.dict(os.environ, {"AGENTCC_DEPLOYMENT_PROFILE": profile}),
        patch(COMMAND_MODULE + ".connections", aliases),
    ):
        os.environ.pop("PG_DIRECT_HOST", None)
        if direct_host:
            os.environ["PG_DIRECT_HOST"] = "127.0.0.1"
        yield aliases


@pytest.mark.parametrize(
    "direct_alias,direct_host",
    [(False, False), (False, True), (True, False)],
    ids=["neither", "host-without-alias", "alias-without-host"],
)
def test_a_hosted_pod_without_the_direct_connection_is_refused_before_any_query(
    direct_alias, direct_host
):
    with (
        _pod("hosted", direct_alias, direct_host) as aliases,
        pytest.raises(
            CommandError, match="^" + re.escape(REFUSED_WITHOUT_DIRECT) + "$"
        ),
    ):
        _check()
    assert aliases.asked == []
    assert _check.lines == []


def test_a_hosted_pod_reads_through_default_direct():
    with _pod("hosted", True, True) as aliases:
        lines = _check()
    assert aliases.asked == ["default_direct"]
    assert lines[-1] == "request-log indexes: 5 of 5 valid"


@pytest.mark.parametrize("profile", ["", "self-hosted"])
def test_off_the_hosted_profile_it_reads_default_without_the_direct_connection(
    profile,
):
    with _pod(profile, False, False) as aliases:
        lines = _check()
    assert aliases.asked == ["default"]
    assert lines[-1] == "request-log indexes: 5 of 5 valid"
