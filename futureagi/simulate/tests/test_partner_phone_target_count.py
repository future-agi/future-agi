"""Counting the stored phone agents configured with the partner phone target.

``manage.py count_partner_phone_target_agents`` reads the target from its own
environment, compares it with every stored agent version and hosted harness job
the way the hosted harness gateway does, and prints fixed words and integers
only: the matching rows of each table and how many of them are not deleted, the
organizations of the matching rows, and the rows it could not read. No part of
the target and nothing a row stores is ever printed, and it writes nothing.

Every number here is a synthetic placeholder in the fictional 555 range, made
fresh for each test.
"""

import io
import re
import secrets
import uuid
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.models.query import QuerySet
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import Organization
from model_hub.apps import guarded_management_command
from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HarnessAgentVersionRuntime,
    HostedHarnessJob,
)

pytestmark = pytest.mark.django_db

COMMAND = "count_partner_phone_target_agents"
SETTING = "ALK_CAB_GUEST_POC_TARGET_PHONE_NUMBER"
# Stored beside every agent below; none of it may reach the output.
CANARY = "canary-qzv-customer-json"
LOCKING = re.compile(r"\bFOR\s+(UPDATE|SHARE|NO\s+KEY\s+UPDATE|KEY\s+SHARE)\b")


class ScanInterrupted(Exception):
    """Raised from inside the scan by the failure test."""


def _number(*avoid):
    """A synthetic placeholder number, different from every one in ``avoid``."""
    while True:
        number = f"+1555{secrets.randbelow(10**7):07d}"
        if number not in avoid:
            return number


def _phone(number, connector="phone"):
    return {
        "connector": connector,
        "mode": "connect_only",
        "config": {"phone_number": number, "target_system_prompt": CANARY},
    }


def _organization(name):
    return Organization.objects.create(name=name)


def _agent_version(organization, agent_payload, *, deleted=False):
    """A version's stored agent settings; each version has its own agent."""
    definition = AgentDefinition.no_workspace_objects.create(
        agent_name="Phone agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        inbound=True,
        description=CANARY,
        organization=organization,
        workspace=None,
    )
    version = AgentVersion.no_workspace_objects.create(
        agent_definition=definition,
        organization=organization,
        workspace=None,
        description=CANARY,
        commit_message=CANARY,
        status=AgentVersion.StatusChoices.DRAFT,
    )
    return HarnessAgentVersionRuntime.no_workspace_objects.create(
        agent_version=version,
        agent_definition=definition,
        organization=organization,
        workspace=None,
        hash="sha256:" + secrets.token_hex(32),
        source_kind=HarnessAgentVersionRuntime.SourceKind.PHONE,
        trigger=HarnessAgentVersionRuntime.Trigger.CREATION,
        agent_payload=agent_payload,
        deleted=deleted,
    )


def _hosted_job(organization, agent=None, *, payload=None, deleted=False):
    if payload is None:
        payload = {"source": {}, "agent": agent, "metadata": {"note": CANARY}}
    return HostedHarnessJob.no_workspace_objects.create(
        organization=organization,
        workspace=None,
        run_id=uuid.uuid4(),
        idempotency_key=uuid.uuid4().hex,
        request_digest="digest",
        schema_version="1.6",
        seed=1,
        artifact_level="standard",
        max_artifact_bytes=1024,
        deadline_at=timezone.now() + timedelta(hours=1),
        scenario_count=1,
        payload=payload,
        deleted=deleted,
    )


def _run():
    out, err = io.StringIO(), io.StringIO()
    call_command(COMMAND, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def _lines(state="set", versions=(0, 0), jobs=(0, 0), organizations=0, skipped=0):
    return (
        f"poc_target={state}\n"
        f"agent_versions_matching={versions[0]} not_deleted={versions[1]}\n"
        f"hosted_jobs_matching={jobs[0]} not_deleted={jobs[1]}\n"
        f"organizations_matching={organizations}\n"
        f"rows_skipped={skipped}\n"
    )


def _assert_nothing_stored_is_shown(output, target):
    digits = target.lstrip("+")
    assert target not in output
    for start in range(len(digits) - 3):
        assert digits[start : start + 4] not in output
    for part in (CANARY, *CANARY.split("-")):
        assert part not in output
    lowered = output.lower()
    for word in ("phone", "connector", "config", "prompt", "metadata"):
        assert word not in lowered


@pytest.fixture
def target(monkeypatch):
    number = _number()
    monkeypatch.setenv(SETTING, number)
    return number


def test_a_version_and_a_hosted_job_with_the_target_count_two_organizations(target):
    _agent_version(_organization("First"), _phone(target))
    _hosted_job(_organization("Second"), _phone(target))

    out, err = _run()

    assert out == _lines(versions=(1, 1), jobs=(1, 1), organizations=2)
    assert err == ""


@pytest.mark.parametrize(
    "neighbour",
    [
        lambda target: _number(target),
        lambda target: target + "0",
        lambda target: target[:-1],
        lambda target: target.lstrip("+"),
    ],
    ids=["another-number", "one-digit-more", "one-digit-less", "without-plus"],
)
def test_a_neighbour_with_another_number_is_not_counted(target, neighbour):
    organization = _organization("Neighbour")
    _agent_version(organization, _phone(neighbour(target)))
    _hosted_job(organization, _phone(neighbour(target)))

    out, _ = _run()

    assert out == _lines()


@pytest.mark.parametrize("connector", ["vapi", "retell", "livekit", "auto"])
def test_another_connector_with_the_target_number_is_not_counted(target, connector):
    organization = _organization("Provider")
    _agent_version(organization, _phone(target, connector=connector))
    _hosted_job(organization, _phone(target, connector=connector))

    out, _ = _run()

    assert out == _lines()


def test_the_target_is_compared_stripped_and_the_connector_case_blind(monkeypatch):
    number = _number()
    monkeypatch.setenv(SETTING, f"  {number} ")
    organization = _organization("Padded")
    _agent_version(organization, _phone(f" {number}  ", connector=" PHONE "))
    _hosted_job(organization, _phone(f"{number} ", connector="Phone"))

    out, _ = _run()

    assert out == _lines(versions=(1, 1), jobs=(1, 1), organizations=1)


def test_a_deleted_row_counts_as_matching_but_not_as_not_deleted(target):
    organization = _organization("Deleted rows")
    _agent_version(organization, _phone(target))
    _agent_version(organization, _phone(target), deleted=True)
    _hosted_job(organization, _phone(target), deleted=True)

    out, _ = _run()

    assert out == _lines(versions=(2, 1), jobs=(1, 0), organizations=1)


MALFORMED = {
    "job-payload-is-a-list": lambda org, target: _hosted_job(
        org, payload=[CANARY, target]
    ),
    "job-agent-is-text": lambda org, target: _hosted_job(
        org, payload={"agent": f"{CANARY} {target}"}
    ),
    "job-config-is-a-list": lambda org, target: _hosted_job(
        org, {"connector": "phone", "config": [target, CANARY]}
    ),
    "version-settings-are-a-list": lambda org, target: _agent_version(
        org, [target, CANARY]
    ),
    "version-config-is-text": lambda org, target: _agent_version(
        org, {"connector": "phone", "config": f"{target} {CANARY}"}
    ),
}


@pytest.mark.parametrize(
    "store_malformed", list(MALFORMED.values()), ids=list(MALFORMED)
)
def test_a_malformed_row_is_skipped_and_nothing_stored_is_printed(
    target, store_malformed
):
    organization = _organization("Malformed")
    _agent_version(organization, _phone(target))
    store_malformed(organization, target)

    out, err = _run()

    assert out == _lines(versions=(1, 1), organizations=1, skipped=1)
    assert err == ""
    _assert_nothing_stored_is_shown(out + err, target)


@pytest.mark.parametrize("value", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_an_unset_target_prints_unset_and_zeros_and_reads_no_row(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(SETTING, raising=False)
    else:
        monkeypatch.setenv(SETTING, value)
    organization = _organization("Unset")
    _agent_version(organization, _phone(""))
    _hosted_job(organization, _phone(_number()))
    _hosted_job(organization, payload=[CANARY])

    with CaptureQueriesContext(connection) as queries:
        out, err = _run()

    assert out == _lines(state="unset")
    assert err == ""
    assert queries.captured_queries == []


def test_an_unexpected_error_prints_only_its_class_name_and_exits_non_zero(
    target, monkeypatch
):
    organization = _organization("Interrupted")
    _agent_version(organization, _phone(target))
    _hosted_job(organization, _phone(target))
    real_iterator = QuerySet.iterator

    def one_row_then_an_error(self, *args, **kwargs):
        for row in real_iterator(self, *args, **kwargs):
            yield row
            break
        raise ScanInterrupted(f"{target} {CANARY}")

    monkeypatch.setattr(QuerySet, "iterator", one_row_then_an_error)
    out, err = io.StringIO(), io.StringIO()

    with pytest.raises(SystemExit) as exited:
        call_command(COMMAND, stdout=out, stderr=err)

    assert exited.value.code not in (0, None)
    assert out.getvalue() == "count_failed error_class=ScanInterrupted\n"
    assert err.getvalue() == ""
    _assert_nothing_stored_is_shown(out.getvalue() + err.getvalue(), target)


def test_counting_reads_only_and_never_sends_the_target_to_the_database(target):
    organization = _organization("Read only")
    _agent_version(organization, _phone(target))
    _hosted_job(organization, _phone(target))
    _hosted_job(organization, payload=[CANARY])

    with CaptureQueriesContext(connection) as queries:
        out, _ = _run()

    assert out == _lines(versions=(1, 1), jobs=(1, 1), organizations=1, skipped=1)
    statements = [query["sql"] for query in queries.captured_queries]
    assert statements
    for sql in statements:
        assert sql.lstrip().split(None, 1)[0].upper() in {"SELECT", "DECLARE"}, sql
        assert not LOCKING.search(sql.upper()), sql
        assert target not in sql
        assert target.lstrip("+") not in sql


PREFIXES = [["manage.py"], ["python", "-m", "django"], ["django-admin"]]


@pytest.mark.parametrize(
    "prefix", PREFIXES, ids=["manage-py", "python-m-django", "django-admin"]
)
def test_the_startup_guard_admits_the_count_in_any_process(prefix):
    assert guarded_management_command([*prefix, COMMAND]) is None
