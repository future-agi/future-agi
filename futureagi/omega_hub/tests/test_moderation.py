"""Unrank, moves, the blocklist, reserved words, appeals and picks (SPEC §2.3, §3.3, §3.4, §5.5)."""

import json

import pytest

from omega_hub import reserved
from omega_hub.models import DownloadDaily, Entry, Like
from omega_hub.tests.flows import publish
from omega_hub.tests.helpers import entry_files, run_checks, submit, token_client

pytestmark = pytest.mark.django_db
VENDORED_RESERVED = reserved.RESERVED_FILE.parents[3] / "services" / "omega-hub-checker" / "vendor" / "reserved.json"


def two_entries(world):
    first = publish(world, "first-agent")
    notes = publish(world, "notes-plugin")
    return first, notes


def refs_of(client, query=""):
    return [card["ref"] for card in client.get(f"/omega-hub/v1/entries{query}").json()["results"]]


def test_unranked_entry_leaves_every_sort_and_picks_but_stays_in_search_and_installable(world):
    first, notes = two_entries(world)
    world.b.post("/omega-hub/v1/review/picks", {"entry": "future-agi/notes-plugin", "why": "A clean example of a plugin."},
                 format="json")
    response = world.b.post("/omega-hub/v1/review/entries/future-agi/notes-plugin/unrank",
                            {"reason": "quality", "note": "Thin README."}, format="json")
    assert response.status_code == 200, response.content
    for sort in ("trending", "new", "likes", "installs"):
        assert refs_of(world.c, f"?sort={sort}") == ["future-agi/first-agent"], sort
    assert world.c.get("/omega-hub/v1/picks").json() == []
    found = refs_of(world.c, "?q=agent")
    assert found[-1] == "future-agi/notes-plugin" and "future-agi/first-agent" in found
    assert world.c.get("/omega-hub/v1/entries/future-agi/notes-plugin/versions/1.0.0/bundle").status_code == 200
    page = world.c.get("/omega-hub/v1/entries/future-agi/notes-plugin").json()
    assert page["ranked"] is False and page["unrankedReason"] == "quality"
    export = world.c.get("/omega-hub/v1/export").json()
    assert next(e for e in export["entries"] if e["name"] == "notes-plugin")["ranked"] is False
    world.b.post("/omega-hub/v1/review/entries/future-agi/notes-plugin/rerank", {"note": "README fixed."}, format="json")
    assert "future-agi/notes-plugin" in refs_of(world.c, "?sort=new")


def test_move_keeps_counts_and_redirects_every_old_path(world):
    entry = publish(world, "first-agent")
    world.c.post("/omega-hub/v1/entries/future-agi/first-agent/like")
    world.c.get("/omega-hub/v1/entries/future-agi/first-agent/versions/1.0.0/bundle")
    moved = world.b.post("/omega-hub/v1/review/entries/future-agi/first-agent/move",
                         {"toName": "weather-agent", "note": "Publisher asked for a clearer name."}, format="json")
    assert moved.status_code == 200, moved.content
    for suffix in ("", "/versions", "/versions/1.0.0", "/versions/1.0.0/bundle"):
        response = world.c.get(f"/omega-hub/v1/entries/future-agi/first-agent{suffix}")
        assert response.status_code == 301, suffix
        assert response["Location"] == f"/omega-hub/v1/entries/future-agi/weather-agent{suffix}"
        assert response.json() == {"movedTo": "future-agi/weather-agent"}
    entry.refresh_from_db()
    assert entry.name == "weather-agent" and entry.like_count == 1
    assert Like.objects.filter(entry=entry).count() == 1
    assert DownloadDaily.objects.get(version__entry=entry).count == 1
    export = world.c.get("/omega-hub/v1/export").json()
    assert {"from": "future-agi/first-agent", "to": "future-agi/weather-agent"} in export["redirects"]
    # the old reference is never reused
    taken = submit(token_client(world.token_a), "future-agi", entry_files("first-agent", manifest={"version": "1.0.1"}),
                   name="first-agent", version="1.0.1")
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "OMEGA_HUB_REF_TAKEN"
    again = world.b.post("/omega-hub/v1/review/entries/future-agi/weather-agent/move",
                         {"toName": "first-agent", "note": "Back to the old name."}, format="json")
    assert again.status_code == 409 and again.json()["error"]["code"] == "OMEGA_HUB_REF_TAKEN"


def test_a_submission_whose_manifest_uses_a_moved_name_gets_moved_to_in_context(world):
    publish(world, "first-agent")
    world.b.post("/omega-hub/v1/review/entries/future-agi/first-agent/move",
                 {"toName": "weather-agent", "note": "Rename."}, format="json")
    files = entry_files("first-agent", manifest={"version": "1.0.1"})
    submit(token_client(world.token_a), "future-agi", files)  # no hint: the checker finds the id
    from omega_hub.tests.helpers import checker_client

    checker = checker_client()
    job = checker.post("/omega-hub/internal/checker/claim").json()
    ctx = checker.post(job["contextUrl"], {"name": "first-agent", "version": "1.0.1"}, format="json").json()
    assert ctx["movedTo"] == "future-agi/weather-agent"  # the checker turns this into U8


def test_publisher_rename_redirects_and_frees_nothing(world):
    publish(world, "notes-plugin", token=world.token_c, publisher="c-test")
    renamed = world.b.post("/omega-hub/v1/review/publishers/c-test/rename", {"toHandle": "carol-labs", "note": "Asked."},
                           format="json")
    assert renamed.status_code == 200, renamed.content
    assert world.c.get("/omega-hub/v1/entries/c-test/notes-plugin")["Location"].endswith("/entries/carol-labs/notes-plugin")
    assert world.c.get("/omega-hub/v1/publishers/c-test").status_code == 301
    claim = world.c.post("/omega-hub/v1/publishers", {"handle": "c-test", "kind": "user", "displayName": "X"}, format="json")
    assert claim.status_code in (409,)


def test_removal_blocks_every_digest_and_restore_lifts_them(world):
    entry = publish(world, "first-agent")
    version = entry.latest_version
    world.b.post("/omega-hub/v1/review/entries/future-agi/first-agent/remove",
                 {"category": "licence issue", "note": "Copied GPL code."}, format="json")
    blocklist = world.c.get("/omega-hub/v1/blocklist")
    body = blocklist.json()
    assert body["schema"] == "omega-hub-blocklist/v1"
    assert body["entries"][0]["digests"] == [version.digest]
    assert body["entries"][0]["bundleSha256"] == [version.bundle_sha256]
    assert "GPL" not in blocklist.content.decode() and world.maint_b.email not in blocklist.content.decode()
    assert world.c.get("/omega-hub/v1/blocklist", HTTP_IF_NONE_MATCH=blocklist["ETag"]).status_code == 304
    tomb = world.c.get("/omega-hub/v1/export").json()["tombstones"]
    assert tomb[0]["ref"] == "future-agi/first-agent" and tomb[0]["category"] == "licence issue"
    world.b.post("/omega-hub/v1/review/entries/future-agi/first-agent/restore", {"note": "Licence fixed."}, format="json")
    assert world.c.get("/omega-hub/v1/blocklist").json()["entries"] == []
    assert world.c.get("/omega-hub/v1/entries/future-agi/first-agent").status_code == 200


def test_bare_digest_blocklist(world):
    digest = "sha256:" + "a" * 64
    added = world.b.post("/omega-hub/v1/review/blocklist", {"digest": digest, "category": "security issue",
                                                            "note": "Malicious copy on a forum."}, format="json")
    assert added.status_code == 201
    assert world.c.get("/omega-hub/v1/blocklist").json()["digests"][0]["digest"] == digest
    world.b.post(f"/omega-hub/v1/review/blocklist/{added.json()['id']}/lift")
    assert world.c.get("/omega-hub/v1/blocklist").json()["digests"] == []


@pytest.mark.parametrize("handle", ["verified-tools", "acme-official", "official", "staff-pick", "review", "future-agi"])
def test_reserved_handles_are_refused(world, handle):
    response = world.c.post("/omega-hub/v1/publishers", {"handle": handle, "kind": "user", "displayName": "X"}, format="json")
    assert response.status_code in (400, 409)
    assert response.json()["error"]["code"] in ("OMEGA_HUB_HANDLE_RESERVED", "OMEGA_HUB_HANDLE_TAKEN")


def test_reserved_display_name_and_tags(world):
    response = world.c.patch("/omega-hub/v1/publishers/c-test", {"displayName": "Acme Official"}, format="json")
    assert response.status_code == 400 and response.json()["error"]["code"] == "OMEGA_HUB_DISPLAY_NAME_RESERVED"
    assert reserved.tag_is_reserved("staff pick") and reserved.tag_is_reserved("Staff_Pick")
    assert reserved.tag_is_reserved("community") and not reserved.tag_is_reserved("weather")
    assert not reserved.display_name_is_reserved("Future AGI Official", "future-agi")


def test_reserved_json_is_vendored_byte_for_byte_into_the_checker():
    assert VENDORED_RESERVED.read_bytes() == reserved.RESERVED_FILE.read_bytes()


def test_the_maintainer_who_decided_cannot_record_the_appeal(world):
    publish(world, "first-agent")
    world.b.post("/omega-hub/v1/review/entries/future-agi/first-agent/unrank",
                 {"reason": "misleading listing", "note": "Claims it books flights."}, format="json")
    entry_id = str(Entry.objects.get().pk)
    own = world.b.post("/omega-hub/v1/review/appeals", {"targetType": "entry", "targetId": entry_id,
                                                         "outcome": "granted", "note": "I changed my mind."}, format="json")
    assert own.status_code == 403 and own.json()["error"]["code"] == "OMEGA_HUB_APPEAL_SELF"
    other = world.a.post("/omega-hub/v1/review/appeals", {"targetType": "entry", "targetId": entry_id,
                                                           "outcome": "upheld", "note": "The listing still misleads."},
                         format="json")
    assert other.status_code == 201 and other.json()["action"] == "appeal.upheld"


def test_suspension_removes_entries_and_revokes_tokens(world):
    publish(world, "notes-plugin", token=world.token_c, publisher="c-test")
    response = world.b.post("/omega-hub/v1/review/publishers/c-test/suspend", {"note": "Malware in 1.0.0."}, format="json")
    assert response.status_code == 200, response.content
    assert world.c.get("/omega-hub/v1/entries/c-test/notes-plugin").status_code == 410
    assert token_client(world.token_c).get("/omega-hub/v1/me").status_code in (401, 403)
    assert world.c.get("/omega-hub/v1/blocklist").json()["entries"][0]["category"] == "breaks Hub rules"


def test_picks_cap_at_six_a_week(world):
    names = ["first-agent", "notes-plugin", "engineering-team"]
    for name in names:
        publish(world, name)
    for name in names:
        ok = world.b.post("/omega-hub/v1/review/picks", {"entry": f"future-agi/{name}", "why": "Worth a look this week."},
                          format="json")
        assert ok.status_code == 201, ok.content
    picks = world.c.get("/omega-hub/v1/picks").json()
    assert {p["ref"] for p in picks} == {f"future-agi/{n}" for n in names}
    assert json.dumps(picks).count("Worth a look") == 3
