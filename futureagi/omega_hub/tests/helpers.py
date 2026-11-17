"""Builders for the Hub tests: people and organisations, real JWT sessions, fixture bundles, and a
stand-in checker that posts reports to the internal endpoints (SPEC §14.1 B1)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from omega_hub.services.bundles import catalog_digest, pack_canonical, read_canonical
from tfc.constants.levels import Level

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "entries"
CHECKER_TOKEN = "test-checker-token-0123456789"
STATIC_STEPS = ("unpack", "manifest", "licence", "secrets", "dependencies")


class Person:
    def __init__(self, user, organization, workspace):
        self.user, self.organization, self.workspace = user, organization, workspace

    @property
    def email(self):
        return self.user.email


def make_person(name: str, *, org_level: int = Level.OWNER, workspace_level: int = Level.WORKSPACE_ADMIN,
                organization: Organization | None = None, email_verified: bool = True) -> Person:
    """A user with an organisation membership and a workspace. `email_verified` records the Hub's
    email confirmation (HUB-25) the way opening the emailed link does; pass False for an account
    that never confirmed."""
    organization = organization or Organization.objects.create(name=f"{name} org {uuid.uuid4().hex[:6]}")
    user = User.objects.create_user(
        email=f"{name}-{uuid.uuid4().hex[:8]}@futureagi.com", password="testpassword123", name=name.title(),
        organization=organization, organization_role=Level.LEVEL_TO_ORG_STRING.get(org_level, "Member"),
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user, organization=organization,
        defaults={"role": Level.LEVEL_TO_ORG_STRING.get(org_level, "Member"), "level": org_level, "is_active": True},
    )
    workspace = Workspace.no_workspace_objects.filter(organization=organization, is_default=True).first()
    if workspace is None:
        workspace = Workspace.objects.create(name=f"{name} ws", organization=organization, is_default=True,
                                             is_active=True, created_by=user)
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user, workspace=workspace,
        defaults={"role": {8: "Workspace Admin", 3: "Workspace Member", 1: "Workspace Viewer"}[workspace_level],
                  "level": workspace_level, "is_active": True, "organization_membership": membership},
    )
    if email_verified:
        from omega_hub.models import EmailVerification

        EmailVerification.objects.create(user=user, email=user.email)
    return Person(user, organization, workspace)


def jwt_for(user) -> str:
    token = AuthToken.objects.create(user=user, auth_type=AuthTokenType.ACCESS.value, last_used_at=timezone.now(),
                                     is_active=True)
    return generate_encrypted_message({"user_id": str(user.id), "id": str(token.id)})


def session_client(person: Person) -> APIClient:
    """A client that authenticates like the app's browser session: a Bearer JWT through the real
    APIKeyAuthentication, with the person's organisation and workspace headers."""
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {jwt_for(person.user)}",
                       HTTP_X_ORGANIZATION_ID=str(person.organization.id), HTTP_X_WORKSPACE_ID=str(person.workspace.id))
    return client


def token_client(plaintext: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_X_OMEGA_HUB_TOKEN=plaintext)
    return client


def checker_client() -> APIClient:
    client = APIClient()
    client.credentials(HTTP_X_OMEGA_HUB_CHECKER_TOKEN=CHECKER_TOKEN, HTTP_X_OMEGA_HUB_CHECKER_ID="test-checker")
    return client


def metrics_client() -> APIClient:
    client = APIClient()
    client.credentials(HTTP_X_OMEGA_HUB_METRICS_TOKEN="metrics-test-token")
    return client


def entry_files(name: str, *, manifest: dict | None = None, extra: dict | None = None, drop=()) -> dict[str, bytes]:
    root = FIXTURES / name
    files = {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    data = json.loads(files["omega.hub.json"])
    for key, value in (manifest or {}).items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    files["omega.hub.json"] = (json.dumps(data, indent=2) + "\n").encode()
    for rel in drop:
        files.pop(rel, None)
    files.update(extra or {})
    return files


def submit(client: APIClient, publisher: str, files: dict[str, bytes], **fields):
    from django.core.files.uploadedfile import SimpleUploadedFile

    upload = SimpleUploadedFile("entry.tar.gz", pack_canonical(files), content_type="application/gzip")
    return client.post("/omega-hub/v1/submissions", {"publisher": publisher, "bundle": upload, **fields}, format="multipart")


def build_report(job: dict, files: dict[str, bytes], *, flags=(), blocks=(), observed=None, capabilities=None,
                 outcome=None, diff=None) -> tuple[dict, bytes]:
    bundle = pack_canonical(files)
    manifest = json.loads(files["omega.hub.json"])
    digest = catalog_digest(read_canonical(bundle), manifest)
    steps = [{"id": step, "status": "pass", "ms": 1, "findings": []} for step in STATIC_STEPS]
    findings = [{"id": fid, "step": "static-scan", "severity": "flag", "rule": rule, "file": "main.ts", "line": 3,
                 "message": f"{rule} used"} for fid, rule in flags]
    findings += [{"id": fid, "step": "static-scan", "severity": "block", "rule": rule, "file": "main.ts", "line": 1,
                  "message": f"{rule} used"} for fid, rule in blocks]
    status = "fail" if blocks else ("flag" if flags else "pass")
    steps.append({"id": "static-scan", "status": status, "ms": 5, "findings": findings})
    steps.append({"id": "sandbox-run", "status": "pass", "ms": 812, "findings": [], "network": "none"})
    steps.append({"id": "capabilities", "status": "pass", "ms": 1, "findings": [],
                  "result": capabilities or {"cli": True, "openInStudio": False, "deploy": False, "consoleTemplate": False},
                  "observed": observed or {}})
    steps.append({"id": "diff", "status": "pass", "ms": 1, "findings": [], "previous": None, "diff": diff})
    steps.append({"id": "similarity", "status": "pass", "ms": 1, "findings": []})
    sha = __import__("hashlib").sha256(bundle).hexdigest()
    report = {
        "schema": "omega-hub-check-report/v1", "job": job["job"], "submission": job["submission"],
        "checker": {"version": "0.1.0", "omegaCommit": "881b05ba5" + "0" * 31, "runnerImage": "sha256:test",
                    "vendoredHub": "881b05ba5"},
        "manifest": {"id": manifest["id"], "version": manifest.get("version"), "kind": manifest["kind"]},
        "digest": digest, "bundleSha256": sha, "bundleBytes": len(bundle), "steps": steps,
        "outcome": outcome or ("checks_failed" if blocks else "in_review"),
    }
    return report, bundle


def post_result(checker: APIClient, job: dict, report: dict, bundle: bytes | None, log: bytes = b"run log\n"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    parts = {"report": SimpleUploadedFile("report.json", json.dumps(report).encode(), content_type="application/json"),
             "log": SimpleUploadedFile("log.txt", log)}
    if bundle is not None:
        parts["bundle"] = SimpleUploadedFile("bundle.tar.gz", bundle, content_type="application/gzip")
    return checker.post(f"/omega-hub/internal/checker/jobs/{job['job']}/result", parts, format="multipart")


def run_checks(files: dict[str, bytes], **report_options):
    """Claim the next job, ask for context like the real checker, and post a report for `files`."""
    checker = checker_client()
    claimed = checker.post("/omega-hub/internal/checker/claim")
    assert claimed.status_code == 200, claimed.content
    job = claimed.json()
    manifest = json.loads(files["omega.hub.json"])
    ctx = checker.post(job["contextUrl"],
                       {"name": manifest["id"], "version": manifest.get("version"), "remixOf": manifest.get("remix_of")},
                       format="json")
    assert ctx.status_code == 200, ctx.content
    report, bundle = build_report(job, files, **report_options)
    response = post_result(checker, job, report, bundle)
    assert response.status_code == 200, response.content
    return job, report, response.json()


def approve_payload(report: dict | None = None, *, disclosure: dict | None = None, rules_version: int | None = None,
                    checklist: dict | None = None, flags=None) -> dict:
    from omega_hub import rules
    from omega_hub.services.publish import flag_ids

    return {
        "decision": "approve",
        "rulesVersion": rules_version if rules_version is not None else rules.current_version(),
        "checklist": checklist if checklist is not None else dict.fromkeys(rules.checklist_keys(), True),
        "acknowledgedFlags": flags if flags is not None else flag_ids(report),
        "disclosure": disclosure,
    }


def clear_caches():
    cache.clear()
