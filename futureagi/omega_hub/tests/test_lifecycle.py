"""The happy path and the structural "approved only" rule (SPEC §3.1, §3.2)."""

import json

import pytest

from omega_hub.models import AuditEvent, Entry, Submission, Version
from omega_hub.tests.helpers import approve_payload, entry_files, run_checks, submit, token_client

pytestmark = pytest.mark.django_db

PUBLIC_PATHS = (
    "/omega-hub/v1/entries/{p}/{n}",
    "/omega-hub/v1/entries/{p}/{n}/versions",
    "/omega-hub/v1/entries/{p}/{n}/versions/1.0.0",
    "/omega-hub/v1/entries/{p}/{n}/versions/1.0.0/bundle",
    "/omega-hub/v1/entries/{p}/{n}/versions/1.0.0/cast",
    "/omega-hub/v1/entries/{p}/{n}/versions/1.0.0/files/README.md",
    "/omega-hub/v1/entries/{p}/{n}/remixes",
)


def first_agent_disclosure():
    return json.loads(entry_files("first-agent")["omega.hub.json"])["disclosure"]


def publish_first_agent(world):
    files = entry_files("first-agent")
    created = submit(token_client(world.token_a), "future-agi", files, channel="cli", name="first-agent", version="1.0.0")
    assert created.status_code == 201, created.content
    _, report, _ = run_checks(files, flags=[("scan-11111111", "child-process")], observed={"shell": ["main.test.mjs:3"]})
    sub_id = created.json()["id"]
    decided = world.b.post(f"/omega-hub/v1/review/submissions/{sub_id}/decision",
                           approve_payload(report, disclosure={**first_agent_disclosure(), "shell": "Runs its own test with Node."}),
                           format="json")
    assert decided.status_code == 200, decided.content
    return sub_id


def assert_not_public(client, ref="future-agi/first-agent"):
    p, n = ref.split("/")
    for path in PUBLIC_PATHS:
        response = client.get(path.format(p=p, n=n))
        assert response.status_code == 404, (path, response.status_code)
    listing = client.get("/omega-hub/v1/entries").json()
    assert all(card["ref"] != ref for card in listing["results"])
    export = client.get("/omega-hub/v1/export").json()
    assert all(entry["ref"] != ref for entry in export["entries"])


def test_every_pre_approval_state_is_invisible(world):
    files = entry_files("first-agent")
    created = submit(token_client(world.token_a), "future-agi", files, name="first-agent", version="1.0.0")
    sub = Submission.objects.get(pk=created.json()["id"])
    assert sub.status == "received"
    assert_not_public(world.c)
    from omega_hub.tests.helpers import build_report, checker_client, post_result

    checker = checker_client()
    job = checker.post("/omega-hub/internal/checker/claim").json()
    sub.refresh_from_db()
    assert sub.status == "checking"
    assert_not_public(world.c)
    report, bundle = build_report(job, files, flags=[("scan-22222222", "child-process")])
    assert post_result(checker, job, report, bundle).status_code == 200
    sub.refresh_from_db()
    assert sub.status == "in_review"
    assert_not_public(world.c)
    assert not Version.objects.exists() and not Entry.objects.exists()
    # changes_requested, rejected and withdrawn are terminal and still invisible
    world.b.post(f"/omega-hub/v1/review/submissions/{sub.pk}/decision",
                 {"decision": "request_changes", "rulesVersion": 1, "reason": "Please disclose the child process use."},
                 format="json")
    sub.refresh_from_db()
    assert sub.status == "changes_requested"
    assert_not_public(world.c)


def test_checks_failed_is_invisible_and_cannot_be_approved(world):
    files = entry_files("first-agent")
    created = submit(token_client(world.token_a), "future-agi", files, name="first-agent", version="1.0.0")
    _, report, result = run_checks(files, blocks=[("scan-deadbeef", "eval")])
    assert result["submission"] == "checks_failed"
    decided = world.b.post(f"/omega-hub/v1/review/submissions/{created.json()['id']}/decision",
                           approve_payload(report, disclosure=first_agent_disclosure()), format="json")
    assert decided.status_code == 409
    assert decided.json()["error"]["code"] == "OMEGA_HUB_BAD_STATE"
    assert_not_public(world.c)


def test_approval_publishes_and_public_reads_show_only_the_confirmed_disclosure(world):
    sub_id = publish_first_agent(world)
    sub = Submission.objects.get(pk=sub_id)
    assert sub.status == "approved"
    version = Version.objects.get()
    assert version.disclosure["shell"] == "Runs its own test with Node."
    assert version.disclosure_draft["shell"] == "None."
    detail = world.c.get("/omega-hub/v1/entries/future-agi/first-agent").json()
    assert detail["latestVersion"] == "1.0.0"
    assert detail["publisher"]["tier"] == "official"
    assert detail["latest"]["disclosure"]["shell"] == "Runs its own test with Node."
    assert "disclosure" not in detail["latest"]["manifest"]
    assert detail["latest"]["sourceCommit"] is None
    bundle = world.c.get("/omega-hub/v1/entries/future-agi/first-agent/versions/1.0.0/bundle")
    assert bundle.status_code == 200
    assert bundle["X-Omega-Hub-Bundle-Sha256"] == version.bundle_sha256
    assert bundle["X-Omega-Hub-Digest"] == version.digest
    readme = world.c.get("/omega-hub/v1/entries/future-agi/first-agent/versions/1.0.0/files/README.md")
    assert readme.status_code == 200 and readme["Content-Type"].startswith("text/plain")
    assert readme["Content-Security-Policy"] == "default-src 'none'"
    actions = list(AuditEvent.objects.filter(target_id=str(sub_id)).values_list("action", flat=True))
    assert actions.count("review.approved") == 1 and actions.count("check.passed") == 1


def test_a_new_version_is_reviewed_again(world):
    publish_first_agent(world)
    files = entry_files("first-agent", manifest={"version": "1.0.1"})
    created = submit(token_client(world.token_a), "future-agi", files, name="first-agent", version="1.0.1")
    assert created.status_code == 201, created.content
    job, report, _ = run_checks(files)
    assert Submission.objects.get(pk=created.json()["id"]).status == "in_review"
    assert Submission.objects.get(pk=created.json()["id"]).previous_version.version == "1.0.0"
    detail = world.c.get("/omega-hub/v1/entries/future-agi/first-agent").json()
    assert detail["latestVersion"] == "1.0.0"
    assert world.c.get("/omega-hub/v1/entries/future-agi/first-agent/versions/1.0.1").status_code == 404
    world.b.post(f"/omega-hub/v1/review/submissions/{created.json()['id']}/decision",
                 approve_payload(report, disclosure=first_agent_disclosure()), format="json")
    assert world.c.get("/omega-hub/v1/entries/future-agi/first-agent").json()["latestVersion"] == "1.0.1"


def test_republishing_an_approved_version_is_refused(world):
    publish_first_agent(world)
    files = entry_files("first-agent")
    again = submit(token_client(world.token_a), "future-agi", files, name="first-agent", version="1.0.0")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "OMEGA_HUB_VERSION_EXISTS"


def test_yanked_version_is_not_latest_and_downloads_only_by_exact_version(world):
    publish_first_agent(world)
    yank = token_client(world.token_a).post("/omega-hub/v1/entries/future-agi/first-agent/versions/1.0.0/yank",
                                            {"reason": "broken on Node 22"}, format="json")
    assert yank.status_code == 200, yank.content
    entry = Entry.objects.get()
    assert entry.latest_version is None
    listing = world.c.get("/omega-hub/v1/entries").json()
    assert listing["count"] == 0
    assert all(e["ref"] != "future-agi/first-agent" for e in world.c.get("/omega-hub/v1/export").json()["entries"])
    exact = world.c.get("/omega-hub/v1/entries/future-agi/first-agent/versions/1.0.0/bundle")
    assert exact.status_code == 200
    assert exact["X-Omega-Hub-Yanked"] == "broken on Node 22"
    # Every version yanked: the entry page still has its header, from the newest yanked version.
    detail = world.c.get("/omega-hub/v1/entries/future-agi/first-agent")
    assert detail.status_code == 200
    body = detail.json()
    title = json.loads(entry_files("first-agent")["omega.hub.json"])["title"]
    assert body["title"] == title and body["tagline"] and body["cover"]
    assert body["latestVersion"] is None and body["latest"] is None


def test_removed_entry_answers_410_with_a_tombstone(world):
    publish_first_agent(world)
    removed = world.b.post("/omega-hub/v1/review/entries/future-agi/first-agent/remove",
                           {"category": "security issue", "note": "Exfiltrates the env."}, format="json")
    assert removed.status_code == 200, removed.content
    for path in PUBLIC_PATHS[:6]:
        response = world.c.get(path.format(p="future-agi", n="first-agent"))
        assert response.status_code == 410, path
    body = world.c.get("/omega-hub/v1/entries/future-agi/first-agent").json()
    assert body["tombstone"]["category"] == "security issue"
    assert "Exfiltrates" not in json.dumps(body)
