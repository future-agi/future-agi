"""Seeding (SPEC §14.1): seed_dev, the Official import, the checker, and the dev-approve shortcut."""

import io
import json
import re
import shutil
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from omega_hub import official_seed
from omega_hub.models import AuditEvent, Entry, Submission
from omega_hub.services import disclosure
from omega_hub.services.bundles import read_canonical
from omega_hub.tests.helpers import FIXTURES, build_report, checker_client, post_result

pytestmark = pytest.mark.django_db
COMMIT = "881b05ba5" + "1" * 31


def omega_tree(tmp_path, *, legacy: bool):
    """A tiny exported Omega tree: hub/<id> for the three fixtures (legacy: without version/disclosure)."""
    root = tmp_path / "omega"
    for name in ("first-agent", "notes-plugin", "engineering-team"):
        shutil.copytree(FIXTURES / name, root / "hub" / name)
        if legacy:
            path = root / "hub" / name / "omega.hub.json"
            manifest = json.loads(path.read_text())
            manifest.pop("version"), manifest.pop("disclosure")
            path.write_text(json.dumps(manifest, indent=2))
    return root


def seed(tmp_path):
    out = io.StringIO()
    call_command("omega_hub_seed_dev", "--password", "dev-password-123", stdout=out)
    return json.loads(out.getvalue())


def check_everything():
    checker = checker_client()
    while True:
        claimed = checker.post("/omega-hub/internal/checker/claim")
        if claimed.status_code == 204:
            return
        job = claimed.json()
        files = read_canonical(checker.get(job["bundleUrl"]).content)
        report, bundle = build_report(job, files["entry"], flags=[("scan-0000abcd", "child-process")])
        assert post_result(checker, job, report, bundle).status_code == 200


def test_seed_dev_is_idempotent_and_official(db, hub_settings, tmp_path):
    first = seed(tmp_path)
    second = seed(tmp_path)
    assert first["maintainerOrgId"] == second["maintainerOrgId"] and first["tokenA"].startswith("ohp_")
    hub_settings.OMEGA_HUB_MAINTAINER_ORG_ID = first["maintainerOrgId"]
    from django.contrib.auth import get_user_model

    from omega_hub.permissions import is_maintainer

    users = get_user_model().objects
    assert is_maintainer(users.get(email=first["maintainerA"])) and is_maintainer(users.get(email=first["maintainerB"]))
    assert AuditEvent.objects.filter(action="maintainer.granted", data__via="seed").count() == 2


def test_seed_dev_opens_the_dark_hub_for_the_local_stack(db, hub_settings, tmp_path):
    """The Hub ships dark (READINESS 3.13); the local dev seed opens it to everyone, once."""
    from omega_rollout import service
    from omega_rollout.models import ProductRollout, RolloutAuditEvent

    ProductRollout.objects.filter(product="hub").delete()
    RolloutAuditEvent.objects.all().delete()
    seed(tmp_path)
    seed(tmp_path)
    assert service.admission("hub", None, None).admitted
    assert RolloutAuditEvent.objects.filter(product="hub").count() == 2


def test_import_check_and_dev_approve_the_official_entries(db, hub_settings, tmp_path, monkeypatch):
    summary = seed(tmp_path)
    hub_settings.OMEGA_HUB_MAINTAINER_ORG_ID = summary["maintainerOrgId"]
    root = omega_tree(tmp_path, legacy=False)
    call_command("omega_hub_import_official", "--omega-dir", str(root), "--commit", COMMIT,
                 "--submitted-by", summary["maintainerA"], stdout=io.StringIO())
    assert Submission.objects.filter(status="received", channel="github", github_commit=COMMIT).count() == 3
    check_everything()
    assert Submission.objects.filter(status="in_review").count() == 3
    with pytest.raises(CommandError):
        call_command("omega_hub_dev_approve", "--as", summary["maintainerB"], "--all-in-review", stdout=io.StringIO())
    monkeypatch.setenv("OMEGA_HUB_ALLOW_DEV_APPROVE", "1")
    as_submitter = io.StringIO()
    call_command("omega_hub_dev_approve", "--as", summary["maintainerA"], "--all-in-review", stdout=as_submitter)
    assert {row["code"] for row in json.loads(as_submitter.getvalue())["skipped"]} == {"OMEGA_HUB_SELF_APPROVAL"}
    out = io.StringIO()
    call_command("omega_hub_dev_approve", "--as", summary["maintainerB"], "--all-in-review", stdout=out)
    assert len(json.loads(out.getvalue())["approved"]) == 3
    entries = Entry.objects.filter(publisher__handle="future-agi")
    assert entries.count() == 3
    for entry in entries:
        assert entry.latest_version.source_commit == COMMIT
        assert entry.latest_version.disclosure == entry.latest_version.disclosure_draft
    approvals = AuditEvent.objects.filter(action="review.approved")
    assert approvals.count() == 3 and all(event.data.get("via") == "dev-seed" for event in approvals)
    from rest_framework.test import APIClient

    export = APIClient().get("/omega-hub/v1/export").json()
    assert len(export["entries"]) == 3
    assert all(e["tier"] == "official" and e["ranked"] and len(e["latest"]["disclosure"]) == 6 for e in export["entries"])


def claimed_manifest(hub_settings, summary) -> dict:
    raw = checker_client()
    hub_settings.OMEGA_HUB_MAINTAINER_ORG_ID = summary["maintainerOrgId"]
    job = raw.post("/omega-hub/internal/checker/claim").json()
    return json.loads(read_canonical(raw.get(job["bundleUrl"]).content)["entry"]["omega.hub.json"])


def import_official(root, summary, *extra):
    call_command("omega_hub_import_official", "--omega-dir", str(root), "--commit", COMMIT,
                 "--submitted-by", summary["maintainerA"], *extra, stdout=io.StringIO())


def test_fill_missing_uses_the_seed_disclosure_written_from_the_code(db, hub_settings, tmp_path):
    summary = seed(tmp_path)
    root = omega_tree(tmp_path, legacy=True)
    import_official(root, summary, "--only", "first-agent", "--fill-missing")
    manifest = claimed_manifest(hub_settings, summary)
    assert manifest["version"] == "1.0.0"
    assert manifest["disclosure"] == official_seed.load()["entries"]["first-agent"]["disclosure"]
    assert "not written yet" not in json.dumps(manifest["disclosure"]).lower()


def test_fill_missing_keeps_a_manifest_disclosure_of_its_own(db, hub_settings, tmp_path):
    summary = seed(tmp_path)
    root = omega_tree(tmp_path, legacy=False)
    own = json.loads((root / "hub" / "first-agent" / "omega.hub.json").read_text())["disclosure"]
    import_official(root, summary, "--only", "first-agent", "--fill-missing")
    assert claimed_manifest(hub_settings, summary)["disclosure"] == own


def test_fill_missing_refuses_changed_code_and_imports_nothing(db, hub_settings, tmp_path):
    summary = seed(tmp_path)
    root = omega_tree(tmp_path, legacy=True)
    main = root / "hub" / "notes-plugin" / "plugin" / "notes-server.mjs"
    main.write_text(main.read_text() + "\n// a later change the seed text never saw\n")
    with pytest.raises(CommandError, match="notes-plugin: the code changed since its seed disclosure was written"):
        import_official(root, summary, "--fill-missing")
    assert not Submission.objects.exists()


def test_fill_missing_refuses_an_entry_with_no_seed(db, hub_settings, tmp_path):
    summary = seed(tmp_path)
    root = omega_tree(tmp_path, legacy=True)
    shutil.copytree(root / "hub" / "first-agent", root / "hub" / "brand-new")
    path = root / "hub" / "brand-new" / "omega.hub.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "id": "brand-new"}))
    with pytest.raises(CommandError, match="brand-new: no seed disclosure"):
        import_official(root, summary, "--fill-missing")
    assert not Submission.objects.exists()


def test_the_seed_report_names_matching_stale_and_missing_entries(tmp_path):
    root = omega_tree(tmp_path, legacy=True)
    (root / "hub" / "engineering-team" / "main.ts").write_text("// changed\n")
    shutil.copytree(root / "hub" / "first-agent", root / "hub" / "brand-new")
    path = root / "hub" / "brand-new" / "omega.hub.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "id": "brand-new"}))
    report = official_seed.report(root)
    assert {entry_id: row["seed"] for entry_id, row in report.items()} == {
        "brand-new": "missing", "engineering-team": "stale", "first-agent": "match", "notes-plugin": "match"}
    assert report["first-agent"]["codeSha256"] == official_seed.load()["entries"]["first-agent"]["codeSha256"]


def test_a_readme_change_does_not_make_a_seed_stale(tmp_path):
    root = omega_tree(tmp_path, legacy=True)
    entry = root / "hub" / "first-agent"
    manifest = json.loads((entry / "omega.hub.json").read_text())
    before = official_seed.fingerprint(entry, manifest, root)
    (entry / "README.md").write_text("# Rewritten\n")
    (entry / "omega.hub.json").write_text(json.dumps({**manifest, "version": "1.0.0", "disclosure": {}}))
    assert official_seed.fingerprint(entry, manifest, root) == before
    (entry / "main.mjs").write_text("// changed\n")
    assert official_seed.fingerprint(entry, manifest, root) != before


SEED_IDS = {
    "bank-voice-line", "cafe-chat-page", "chatbot-with-memory", "ci-release-gate", "coding-agent", "data-analyst",
    "document-qa", "engineering-team", "first-agent", "incident-team", "local-web-gui", "mcp-notes-agent",
    "notes-plugin", "omega-coding-skills", "payment-saga", "research-society", "self-improving-agent",
    "serve-tools-over-mcp", "shop-browser-assistant", "slack-and-whatsapp", "society-tree", "sql-analyst",
    "summarize-feedback", "support-org", "support-skill-pack", "verified-task-tree", "web-chat-app",
}
PROSE_RULES = Path(__file__).resolve().parents[3] / "services" / "omega-hub-checker" / "vendor" / "omega-hub" / "prose-rules.json"
BANNED_CLAIMS = re.compile(r"\b(used\s+by|customers|production[\s-]ready|enterprise[\s-]grade|the\s+only|guarantee[sd]?)\b", re.I)


def test_every_official_entry_has_a_real_seed_disclosure():
    seed_file = official_seed.load()
    assert set(seed_file["entries"]) == SEED_IDS
    assert re.fullmatch(r"[0-9a-f]{40}", seed_file["writtenFrom"]["commit"])
    rules = json.loads(PROSE_RULES.read_text())
    words = [w.lower() for w in rules["bannedWords"] + rules["aiTells"]]
    for entry_id, row in seed_file["entries"].items():
        assert re.fullmatch(r"[0-9a-f]{64}", row["codeSha256"]), entry_id
        confirmed = disclosure.require_shape(row["disclosure"])  # U2 / §6.7 lengths and fields
        for field, text in confirmed.items():
            where = f"{entry_id}.{field}"
            assert not text.lower().startswith("not written yet"), where
            assert not re.search(r"[`<>\[\]\r\n!]", text), where
            assert not BANNED_CLAIMS.search(text), where
            assert not [w for w in words if re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", text, re.I)], where
        # Five distinct parts: none of them is the same sentence pasted into every field.
        assert len({confirmed[part] for part in disclosure.PARTS}) >= 3, entry_id


OLD_PLACEHOLDER = {"summary": "Not written yet: imported before disclosures existed.",
                   **dict.fromkeys(disclosure.PARTS, "Not written yet: this entry was imported before disclosures existed.")}


def test_dev_approve_never_confirms_a_placeholder_draft(db, hub_settings, tmp_path, monkeypatch):
    summary = seed(tmp_path)
    hub_settings.OMEGA_HUB_MAINTAINER_ORG_ID = summary["maintainerOrgId"]
    root = omega_tree(tmp_path, legacy=False)
    path = root / "hub" / "first-agent" / "omega.hub.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "disclosure": OLD_PLACEHOLDER}))
    import_official(root, summary, "--only", "first-agent")
    check_everything()
    monkeypatch.setenv("OMEGA_HUB_ALLOW_DEV_APPROVE", "1")
    out = io.StringIO()
    call_command("omega_hub_dev_approve", "--as", summary["maintainerB"], "--all-in-review", stdout=out)
    result = json.loads(out.getvalue())
    assert result["approved"] == []
    assert [row["code"] for row in result["skipped"]] == ["OMEGA_HUB_DISCLOSURE_PLACEHOLDER"]
    assert not Entry.objects.filter(name="first-agent").exists()


@pytest.mark.parametrize("env_type", ["staging", "production", "prod", ""])
def test_dev_paths_refuse_outside_a_local_environment_even_with_debug(db, hub_settings, tmp_path, monkeypatch, env_type):
    summary = seed(tmp_path)
    hub_settings.OMEGA_HUB_MAINTAINER_ORG_ID = summary["maintainerOrgId"]
    monkeypatch.setenv("OMEGA_HUB_ALLOW_DEV_APPROVE", "1")
    hub_settings.ENV_TYPE = env_type
    hub_settings.DEBUG = True
    with pytest.raises(CommandError, match="runs only locally"):
        call_command("omega_hub_seed_dev", "--password", "dev-password-123", stdout=io.StringIO())
    with pytest.raises(CommandError, match="needs a local environment"):
        call_command("omega_hub_dev_approve", "--as", summary["maintainerB"], "--all-in-review", stdout=io.StringIO())
    from omega_hub.system_checks import dev_approve_outside_local

    assert [warning.id for warning in dev_approve_outside_local()] == ["omega_hub.W001"]


def test_dev_paths_refuse_a_local_environment_without_debug_or_test_settings(db, hub_settings, tmp_path, monkeypatch):
    summary = seed(tmp_path)
    monkeypatch.setenv("OMEGA_HUB_ALLOW_DEV_APPROVE", "1")
    hub_settings.ENV_TYPE = "local"
    hub_settings.DEBUG = False
    hub_settings.TESTING = False
    with pytest.raises(CommandError, match="runs only locally"):
        call_command("omega_hub_seed_dev", stdout=io.StringIO())
    with pytest.raises(CommandError, match="needs a local environment"):
        call_command("omega_hub_dev_approve", "--as", summary["maintainerB"], "--all-in-review", stdout=io.StringIO())


def test_dev_approve_refuses_any_maintainer_org_but_the_dev_seed_one(world, monkeypatch):
    """A local shell pointed at a real database (a port-forward) names the real maintainer org: refused."""
    monkeypatch.setenv("OMEGA_HUB_ALLOW_DEV_APPROVE", "1")
    with pytest.raises(CommandError, match="only for the maintainer organisation omega_hub_seed_dev made"):
        call_command("omega_hub_dev_approve", "--as", world.maint_b.email, "--all-in-review", stdout=io.StringIO())
    world.maint_org.name = "Future AGI (Omega Hub maintainers, dev)"
    world.maint_org.save(update_fields=["name"])
    out = io.StringIO()
    call_command("omega_hub_dev_approve", "--as", world.maint_b.email, "--all-in-review", stdout=out)
    assert json.loads(out.getvalue()) == {"approved": [], "skipped": []}


def test_the_dev_approve_warning_is_quiet_locally_and_when_unset(hub_settings, monkeypatch):
    from omega_hub.system_checks import dev_approve_outside_local

    monkeypatch.setenv("OMEGA_HUB_ALLOW_DEV_APPROVE", "1")
    assert dev_approve_outside_local() == []  # the test settings: ENV_TYPE local, TESTING
    monkeypatch.delenv("OMEGA_HUB_ALLOW_DEV_APPROVE")
    hub_settings.ENV_TYPE = "production"
    assert dev_approve_outside_local() == []
