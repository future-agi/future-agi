"""Decision rules (SPEC §5.4) and the rules file (§6.8)."""

import json

import pytest

from omega_hub import rules
from omega_hub.models import Submission, Version
from omega_hub.tests.helpers import approve_payload, entry_files, run_checks, submit, token_client

pytestmark = pytest.mark.django_db
DISCLOSURE = json.loads(entry_files("first-agent")["omega.hub.json"])["disclosure"]


def in_review(world, flags=(("scan-aaaa0001", "child-process"),), observed=None):
    files = entry_files("first-agent")
    created = submit(token_client(world.token_a), "future-agi", files, name="first-agent", version="1.0.0")
    _, report, _ = run_checks(files, flags=list(flags), observed=observed or {})
    return created.json()["id"], report


def decide(client, sub_id, payload):
    return client.post(f"/omega-hub/v1/review/submissions/{sub_id}/decision", payload, format="json")


def test_rules_endpoint_serves_rules_v1(world):
    response = world.c.get("/omega-hub/v1/rules")
    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "omega-hub-rules/v1" and body["version"] == 1
    assert len(body["checklist"]) == 10 and body["trending"]["weights"] == {"installs7d": 1, "likes7d": 3}
    again = world.c.get("/omega-hub/v1/rules", HTTP_IF_NONE_MATCH=response["ETag"])
    assert again.status_code == 304
    assert world.c.get("/omega-hub/v1/rules?version=9").status_code == 404


def test_approve_needs_the_checklist(world):
    sub_id, report = in_review(world)
    missing = decide(world.b, sub_id, {**approve_payload(report, disclosure=DISCLOSURE), "checklist": None})
    assert missing.status_code == 400 and missing.json()["error"]["code"] == "OMEGA_HUB_CHECKLIST_INCOMPLETE"
    partial = {key: True for key in rules.checklist_keys()}
    partial[rules.checklist_keys()[0]] = False
    unticked = decide(world.b, sub_id, approve_payload(report, disclosure=DISCLOSURE, checklist=partial))
    assert unticked.status_code == 400 and unticked.json()["error"]["code"] == "OMEGA_HUB_CHECKLIST_INCOMPLETE"
    assert Submission.objects.get(pk=sub_id).status == "in_review"


def test_checklist_with_other_keys_or_old_rules_version_is_409(world):
    sub_id, report = in_review(world)
    other = {**{key: True for key in rules.checklist_keys()[:-1]}, "made-up": True}
    response = decide(world.b, sub_id, approve_payload(report, disclosure=DISCLOSURE, checklist=other))
    assert response.status_code == 409 and response.json()["error"]["code"] == "OMEGA_HUB_RULES_CHANGED"
    old = decide(world.b, sub_id, approve_payload(report, disclosure=DISCLOSURE, rules_version=0))
    assert old.status_code == 409 and old.json()["error"]["code"] == "OMEGA_HUB_RULES_CHANGED"


def test_approve_needs_every_flag_acknowledged(world):
    sub_id, report = in_review(world, flags=(("scan-aaaa0001", "child-process"), ("scan-aaaa0002", "env-enumeration")))
    response = decide(world.b, sub_id, approve_payload(report, disclosure=DISCLOSURE, flags=["scan-aaaa0001"]))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OMEGA_HUB_FLAGS_UNACKNOWLEDGED"
    assert response.json()["flags"] == ["scan-aaaa0002"]


def test_approve_needs_a_confirmed_disclosure_without_gaps(world):
    sub_id, report = in_review(world, observed={"shell": ["main.ts:3"], "hosts": []})
    none = decide(world.b, sub_id, approve_payload(report, disclosure=None))
    assert none.status_code == 400 and none.json()["error"]["code"] == "OMEGA_HUB_DISCLOSURE_REQUIRED"
    short = decide(world.b, sub_id, approve_payload(report, disclosure={**DISCLOSURE, "summary": "short"}))
    assert short.status_code == 400 and short.json()["error"]["code"] == "OMEGA_HUB_DISCLOSURE_INVALID"
    gap = decide(world.b, sub_id, approve_payload(report, disclosure={**DISCLOSURE, "shell": "None."}))
    assert gap.status_code == 400 and gap.json()["error"]["code"] == "OMEGA_HUB_DISCLOSURE_GAP"
    ok = decide(world.b, sub_id, approve_payload(report, disclosure={**DISCLOSURE, "shell": "Spawns Node for its test."}))
    assert ok.status_code == 200, ok.content
    version = Version.objects.get()
    assert version.disclosure["shell"] == "Spawns Node for its test."
    assert version.disclosure_draft == DISCLOSURE
    assert version.rules_version == 1


def test_reject_and_request_changes_need_a_reason(world):
    sub_id, _ = in_review(world)
    for decision in ("reject", "request_changes"):
        response = decide(world.b, sub_id, {"decision": decision, "rulesVersion": 1, "reason": "too short"})
        assert response.status_code == 400 and response.json()["error"]["code"] == "OMEGA_HUB_REASON_LENGTH"
    response = decide(world.b, sub_id, {"decision": "reject", "rulesVersion": 1,
                                        "reason": "This is someone else's project submitted without them (C2)."})
    assert response.status_code == 200
    body = world.c  # the submitter reads it with their token
    status = token_client(world.token_a).get(f"/omega-hub/v1/submissions/{sub_id}").json()
    assert status["status"] == "rejected"
    assert status["decision"]["reason"].startswith("This is someone else's project")
    assert "reviewer" not in status and body is not None


def test_change_notes_cap_at_thirty_and_only_with_request_changes(world):
    sub_id, report = in_review(world)
    notes = [{"file": "main.mjs", "line": i + 1, "note": f"Explain line {i + 1}."} for i in range(31)]
    reason = "Please disclose what main.mjs spawns and why."
    too_many = decide(world.b, sub_id, {"decision": "request_changes", "rulesVersion": 1, "reason": reason, "notes": notes})
    assert too_many.status_code == 400 and too_many.json()["error"]["code"] == "OMEGA_HUB_NOTES_INVALID"
    with_approve = decide(world.b, sub_id, {**approve_payload(report, disclosure=DISCLOSURE), "notes": notes[:1]})
    assert with_approve.status_code == 400 and with_approve.json()["error"]["code"] == "OMEGA_HUB_NOTES_INVALID"
    ok = decide(world.b, sub_id, {"decision": "request_changes", "rulesVersion": 1, "reason": reason, "notes": notes[:30]})
    assert ok.status_code == 200, ok.content
    status = token_client(world.token_a).get(f"/omega-hub/v1/submissions/{sub_id}").json()
    assert status["status"] == "changes_requested" and len(status["decision"]["notes"]) == 30
    assert status["decision"]["notes"][0] == {"file": "main.mjs", "line": 1, "note": "Explain line 1."}


def test_soft_lock_and_take_over(world):
    sub_id, _ = in_review(world)
    assert world.a.post(f"/omega-hub/v1/review/submissions/{sub_id}/start").status_code == 200
    locked = world.b.post(f"/omega-hub/v1/review/submissions/{sub_id}/start")
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "OMEGA_HUB_REVIEW_LOCKED"
    taken = world.b.post(f"/omega-hub/v1/review/submissions/{sub_id}/take-over")
    assert taken.status_code == 200 and taken.json()["reviewer"] == world.maint_b.email


def test_recheck_queues_a_new_job(world):
    sub_id, _ = in_review(world)
    response = world.b.post(f"/omega-hub/v1/review/submissions/{sub_id}/recheck")
    assert response.status_code == 200 and response.json()["status"] == "received"
    assert Submission.objects.get(pk=sub_id).check_jobs.count() == 2
