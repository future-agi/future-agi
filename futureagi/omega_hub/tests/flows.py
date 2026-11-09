"""Multi-step flows shared by several test modules."""

import json

from omega_hub.models import Entry
from omega_hub.tests.helpers import approve_payload, entry_files, run_checks, submit, token_client


def disclosure_of(name: str) -> dict:
    return json.loads(entry_files(name)["omega.hub.json"])["disclosure"]


def publish(world, name="first-agent", *, token=None, publisher="future-agi", approver=None, manifest=None,
            files=None, **report_options) -> Entry:
    """Submit with a token, run the stand-in checker, approve as maintainer B. Returns the entry."""
    files = files or entry_files(name, manifest=manifest)
    meta = json.loads(files["omega.hub.json"])
    created = submit(token_client(token or world.token_a), publisher, files, name=meta["id"], version=meta["version"])
    assert created.status_code == 201, created.content
    _, report, result = run_checks(files, **report_options)
    assert result["submission"] == "in_review", result
    decided = (approver or world.b).post(f"/omega-hub/v1/review/submissions/{created.json()['id']}/decision",
                                         approve_payload(report, disclosure=meta["disclosure"]), format="json")
    assert decided.status_code == 200, decided.content
    return Entry.objects.get(publisher__handle=publisher, name=meta["id"])
