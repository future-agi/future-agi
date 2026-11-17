"""The four MCP annotations and the posture: derived where HTTP decides (a
GET reads, a DELETE destroys), stated by a reviewer where only a person
knows (whether a write destroys data, whether the tool reaches an open
world), and checked as CN1's L1 checks them."""

from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import support
from conftest import GENERATED, edit_overlay, output, provider_dir, run_refused, tool
from restgen.annotate import annotations_for
from restgen.model import HTTP_METHODS

HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


def test_annotations_all_four_explicit_on_every_tool(generated):
    for app in ("gmail", "google-calendar", "microsoft-outlook", "microsoft-teams", "slack", "adyen"):
        for t in output(generated, app)["tools"]:
            assert tuple(t["annotations"]) == HINTS, (app, t["name"])
            assert all(isinstance(v, bool) for v in t["annotations"].values())


def test_annotations_get_is_read_only_and_idempotent(generated):
    t = tool(output(generated, "gmail"), "search_threads")
    assert t["annotations"] == {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
                                "openWorldHint": False}  # fmt: skip


def test_annotations_get_cannot_claim_to_write(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: tool(d, "list_labels")["annotations"].update(readOnlyHint=False))
    err = run_refused(catalog)
    assert "(list_labels): annotations.readOnlyHint: a GET is read-only" in err


def test_annotations_write_must_state_destructive(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: tool(d, "send_message")["annotations"].pop("destructiveHint"))
    err = run_refused(catalog)
    assert "(send_message): annotations.destructiveHint: a POST tool writes; the reviewer states whether it destroys data" in err


def test_annotations_open_world_is_stated_by_the_reviewer(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: tool(d, "get_profile").pop("annotations"))
    err = run_refused(catalog)
    assert "(get_profile): annotations.openWorldHint: the reviewer states whether the tool reaches an open world" in err


def test_annotations_delete_is_destructive(app_catalog, generated):
    t = tool(output(generated, "google-calendar"), "delete_event")
    assert t["annotations"]["destructiveHint"] is True and t["annotations"]["idempotentHint"] is True
    catalog = app_catalog("google-calendar")
    edit_overlay(catalog, "google-calendar", lambda d: tool(d, "delete_event")["annotations"].update(destructiveHint=False))
    err = run_refused(catalog)
    assert "(delete_event): annotations.destructiveHint: a DELETE is destructive" in err


def test_annotations_delete_marked_read_only_is_refused(app_catalog):
    # The CN4 review's repro: a DELETE the reviewer marks read-only, posture
    # auto. Taken at its word, the tool would delete events with no approval.
    catalog = app_catalog("google-calendar")
    target = provider_dir(catalog, "google-calendar") / GENERATED
    before = target.read_bytes()

    def mark_read_only(d):
        t = tool(d, "delete_event")
        t["annotations"] = {"readOnlyHint": True, "openWorldHint": True}
        t["posture"] = "auto"

    edit_overlay(catalog, "google-calendar", mark_read_only)
    err = run_refused(catalog)
    assert "(delete_event): annotations.readOnlyHint: a DELETE writes, so it is never read-only" in err
    assert "(delete_event): posture: the tool is destructive, so its posture is deny (it is auto)" in err
    assert target.read_bytes() == before  # nothing written for a provider with a problem


def test_annotations_delete_marked_read_only_exits_non_zero_as_a_process(app_catalog):
    catalog = app_catalog("google-calendar")
    edit_overlay(catalog, "google-calendar",
                 lambda d: tool(d, "delete_event")["annotations"].update(readOnlyHint=True, destructiveHint=False))  # fmt: skip
    script = support.HERE.parent / "generate.py"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, str(script), "--catalog", str(catalog)],
                          capture_output=True, text=True, env=env, timeout=300)  # fmt: skip
    assert done.returncode == 2, done.stdout + done.stderr
    assert "(delete_event): annotations.readOnlyHint: a DELETE writes, so it is never read-only" in done.stderr


def test_annotations_patch_cannot_be_read_only(app_catalog):
    catalog = app_catalog("google-calendar")
    edit_overlay(catalog, "google-calendar",
                 lambda d: tool(d, "update_event")["annotations"].update(readOnlyHint=True))  # fmt: skip
    err = run_refused(catalog)
    assert "(update_event): annotations.readOnlyHint: a PATCH writes, so it is never read-only" in err


def test_annotations_put_cannot_be_read_only(app_catalog):
    # No example tool is a PUT, so update_event is pointed at Calendar's
    # events.update (PUT) for this test.
    catalog = app_catalog("google-calendar")

    def put_read_only(d):
        t = tool(d, "update_event")
        t["operation"] = {"id": "calendar.events.update"}
        t["annotations"].update(readOnlyHint=True)

    edit_overlay(catalog, "google-calendar", put_read_only)
    err = run_refused(catalog)
    assert "(update_event): annotations.readOnlyHint: a PUT writes, so it is never read-only" in err


def test_annotations_only_get_and_post_can_be_read_only():
    # Every method the generator accepts, straight through annotations_for.
    for method in HTTP_METHODS:
        problems: list[str] = []
        t = SimpleNamespace(annotations={"readOnlyHint": True, "openWorldHint": False})
        ann = annotations_for(method, t, problems.append)
        if method in ("GET", "POST"):
            assert problems == [] and ann["readOnlyHint"] is True, method
        else:
            assert f"annotations.readOnlyHint: a {method} writes, so it is never read-only" in problems, method
            assert ann["readOnlyHint"] is False, method
    problems = []
    ann = annotations_for("DELETE", SimpleNamespace(annotations={"readOnlyHint": True, "openWorldHint": False}), problems.append)
    assert ann["destructiveHint"] is True


def test_annotations_destructive_tool_is_denied(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: tool(d, "trash_message").update(posture="ask"))
    err = run_refused(catalog)
    assert "(trash_message): posture: the tool is destructive, so its posture is deny (it is ask)" in err


def test_annotations_write_is_never_auto(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: tool(d, "send_message").update(posture="auto"))
    err = run_refused(catalog)
    assert "(send_message): posture: the tool writes, so its posture is ask or deny, not auto" in err


def test_annotations_default_posture_applies_to_writes(app_catalog):
    catalog = app_catalog("gmail")
    record = catalog / "apps/gmail/providers/futureagi-rest.yaml"
    record.write_text(record.read_text().replace("defaultPosture: ask", "defaultPosture: auto"))
    edit_overlay(catalog, "gmail", lambda d: tool(d, "send_message").pop("posture"))
    err = run_refused(catalog)
    assert "(send_message): posture: the tool writes, so its posture is ask or deny, not auto" in err


def test_annotations_post_that_reads_may_be_read_only(generated):
    t = tool(output(generated, "microsoft-outlook"), "get_schedule")
    assert t["operation"]["method"] == "POST"
    assert t["annotations"]["readOnlyHint"] is True and t["annotations"]["idempotentHint"] is True
    assert t["posture"] == "auto"


def test_annotations_read_only_tool_cannot_destroy(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: tool(d, "get_profile")["annotations"].update(destructiveHint=True))
    err = run_refused(catalog)
    assert "(get_profile): annotations.destructiveHint: a read-only tool destroys nothing" in err


def test_annotations_reviewer_idempotence_wins_over_the_method(generated):
    t = tool(output(generated, "gmail"), "modify_thread")
    assert t["operation"]["method"] == "POST" and t["annotations"]["idempotentHint"] is True
