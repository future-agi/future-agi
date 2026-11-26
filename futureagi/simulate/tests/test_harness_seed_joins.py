"""Cases held as unreadable while what they need cannot be read just now, and released once it is."""

import pytest

from simulate.services.harness_readiness import selection
from simulate.services.harness_readiness.report import resolve_pairing
from simulate.services.harness_scenario_lifecycle import runnable
from simulate.tests.test_harness_affected_scenarios import (
    LOOKUP_KEYS,
    PROVED,
    REFUND_KEYS,
    _suite,
    _ten,
)
from simulate.tests.test_harness_readiness import _environment
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_scenario_lifecycle import _proofs, _views

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("readiness_on")]
ORDERS = "knowledge/orders.csv"
REFUNDS = "knowledge/refunds.json"


def test_a_case_citing_seed_records_is_never_held_on_a_join_guessed_from_field_names(
    user, workspace, storage, monkeypatch
):
    """No contract declares how one seed file's rows refer to another's, so `order_id` naming a row `orders.csv` lacks
    holds nothing: the files are shown as they are."""
    import json

    from simulate.models import HarnessSuiteMember
    from simulate.services.harness_actor_rules import KNOWLEDGE_ROLES

    environment = _environment(user, workspace, storage, "seed-unjoined")
    _suite(environment, _ten())
    citing = REFUND_KEYS[0]
    member = HarnessSuiteMember.no_workspace_objects.get(
        suite_version=environment.suite_version, scenario_key=citing
    )
    HarnessSuiteMember.no_workspace_objects.filter(id=member.id).update(
        content={
            **member.content,
            "sources": [{"kind": "file", "ref": f"file:{REFUNDS}", "quote": "R-2"}],
        }
    )
    _proofs(environment, dict.fromkeys(REFUND_KEYS + LOOKUP_KEYS, PROVED))
    files = {
        KNOWLEDGE_ROLES: json.dumps(
            {ORDERS: "seed_records", REFUNDS: "seed_records"}
        ).encode(),
        ORDERS: b"id,total\nO-1,40\n",
        REFUNDS: json.dumps([{"id": "R-2", "order_id": "O-999"}]).encode(),
    }

    def read(version, paths):
        return {path: files[path] for path in paths if path in files}, True

    for module in ("harness_seed_joins", "harness_scenario_sources"):
        monkeypatch.setattr(
            f"simulate.services.{module}.read_files", read, raising=False
        )

    view = _views(environment)[citing]

    assert view["reason"] != "broken_join"


def _validated(environment, *, recorded):
    """A finished validation of the environment's pair, whose proofs were or were not read before its sandbox went."""
    import uuid

    from simulate.models import HostedHarnessJob

    job = HostedHarnessJob.no_workspace_objects.get(id=environment.id)
    payload = dict(job.payload or {})
    payload["metadata"] = {
        **(payload.get("metadata") or {}),
        "certify_only": True,
        "scenario_proofs_recorded": recorded,
    }
    job.pk = job.id = uuid.uuid4()
    job.environment_id = environment.id
    job.idempotency_key = f"check-{job.id}"
    job.run_id = uuid.uuid4()
    job.state = HostedHarnessJob.State.COMPLETED
    job.payload = payload
    job.save()
    return job


def test_a_validation_whose_proofs_could_not_be_read_holds_its_cases_until_one_is_read(
    user, workspace, storage
):
    """Unknown, never "not validated" or a stale Active: the cases are held as unreadable, then read Active again once
    a validation's proofs are read."""
    environment = _environment(user, workspace, storage, "proofs-unread")
    _suite(environment, _ten())
    _proofs(environment, dict.fromkeys(REFUND_KEYS + LOOKUP_KEYS, PROVED))
    key = LOOKUP_KEYS[0]
    assert _views(environment)[key]["state"] == "active"

    _validated(environment, recorded=False)
    held = _views(environment)[key]
    plan = selection.select_for_run(resolve_pairing(environment), keys=[key], trials=1)

    assert (held["state"], held["reason"]) == ("draft", "proofs_unread")
    assert not runnable(held)
    assert plan.view["held_back"]["by"] == {"unreadable": 1}

    _validated(environment, recorded=True)

    assert _views(environment)[key]["state"] == "active"


def test_a_cited_file_that_cannot_be_compared_just_now_holds_the_case_and_recovers(
    user, workspace, storage, monkeypatch
):
    """A version that recorded no file digests compares a cited file's text with its parent's: an archive that cannot
    be read just now leaves the case unknown, never changed or Active."""
    from simulate.models import HarnessEnvironmentVersion, HarnessSuiteMember
    from simulate.services.harness_versioning import archives

    environment = _environment(user, workspace, storage, "files-unread")
    _suite(environment, _ten())
    key = LOOKUP_KEYS[0]
    member = HarnessSuiteMember.no_workspace_objects.get(
        suite_version=environment.suite_version, scenario_key=key
    )
    HarnessSuiteMember.no_workspace_objects.filter(id=member.id).update(
        content={
            **member.content,
            "sources": [
                {"kind": "file", "ref": f"file:{ORDERS}", "quote": "O-1"},
            ],
        }
    )
    # Versions saved before file digests were recorded: the cited file's text is compared from both archives.
    version = environment.environment_version
    documents = {
        name: one
        for name, one in (version.documents or {}).items()
        if name != "file_digests"
    }
    parent = HarnessEnvironmentVersion._base_manager.get(id=version.id)
    parent.pk = parent.id = None
    parent.version_number = None
    parent.hash = "sha256:parent"
    parent.documents = documents
    parent.save()
    HarnessEnvironmentVersion._base_manager.filter(id=version.id).update(
        parent_id=parent.id, documents=documents
    )
    _proofs(environment, dict.fromkeys(REFUND_KEYS + LOOKUP_KEYS, PROVED))
    reads = {"fail": True}
    real = archives.stream_members

    def stream(*args):
        if reads["fail"]:
            raise OSError("storage down")
        return real(*args)

    monkeypatch.setattr(archives, "stream_members", stream)
    environment.refresh_from_db()

    held = _views(environment)[key]
    reads["fail"] = False
    again = _views(environment)[key]

    assert (held["state"], held["reason"]) == ("draft", "storage_unreadable")
    assert held["source_changed"] == []
    assert again["reason"] != "storage_unreadable"
