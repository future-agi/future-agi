/* eslint-env node */
// Runs Python inside the Hub dev stack's Django (the dev-up.sh settings and
// environment) through `manage.py shell -c`, the one way the platform's
// startup gate lets local tooling run code. Used only to create user C.
import { spawnSync } from "node:child_process";
import path from "node:path";

import { PASSWORD, REPO, STATE } from "./registry.mjs";

export function djangoShell(code, env = {}) {
  const script = [
    `. "${path.join(REPO, "scripts/omega-hub/lane-env.sh")}"`,
    `[ -f "${STATE}/env.sh" ] && . "${STATE}/env.sh"`,
    `[ -f "${STATE}/checker-token" ] && export OMEGA_HUB_CHECKER_TOKEN="$(cat "${STATE}/checker-token")"`,
    '[ "${S3_ACCESS_KEY:-}" = minioadmin ] && export S3_SECRET_KEY=minioadmin',
    `cd "${path.join(REPO, "futureagi")}"`,
    '.venv/bin/python manage.py shell --settings="${OMEGA_HUB_SETTINGS:-tfc.settings.settings}" -c "$HUB_E2E_CODE"',
  ].join("\n");
  const run = spawnSync("sh", ["-c", script], {
    env: { ...process.env, ...env, HUB_E2E_CODE: code },
    encoding: "utf8",
    timeout: 120_000,
  });
  if (run.status !== 0)
    throw new Error(
      `django shell failed (${run.status}): ${run.stderr.slice(-1500)}`,
    );
  return run.stdout;
}

// A plain Future AGI user in an organisation of their own: Owner there, with
// a default workspace, and nothing to do with the maintainer organisation.
// With HUB_E2E_EMAIL_VERIFIED=1 the user has already confirmed their email
// with the Hub (likes, reports and new publishers need it, HUB-25), as if
// they had opened the emailed link; the link flow itself is walked by
// .omega/program/HUB-LAUNCH-SAFETY/scripts/hub-walk.mjs (verify-like).
const CREATE_USER = `
import json, os
from django.contrib.auth import get_user_model
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level

email, name, org_name, password = os.environ["HUB_E2E_EMAIL"], os.environ["HUB_E2E_NAME"], os.environ["HUB_E2E_ORG"], os.environ["HUB_E2E_PASSWORD"]
org = Organization.objects.filter(name=org_name).first() or Organization.objects.create(name=org_name)
User = get_user_model()
user = User.objects.filter(email=email).first()
if user is None:
    user = User.objects.create_user(email=email, password=password, name=name, organization=org, organization_role="Owner")
level = getattr(Level, "OWNER", Level.ADMIN)
membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
    user=user, organization=org, defaults={"role": "Owner", "level": level, "is_active": True})
workspace = Workspace.no_workspace_objects.filter(organization=org, is_default=True).first()
if workspace is None:
    workspace = Workspace.objects.create(name="Default", organization=org, is_default=True, is_active=True, created_by=user)
WorkspaceMembership.no_workspace_objects.get_or_create(
    user=user, workspace=workspace, defaults={"role": "Workspace Admin", "level": Level.WORKSPACE_ADMIN,
                                              "is_active": True, "organization_membership": membership})
if os.environ.get("HUB_E2E_EMAIL_VERIFIED") == "1":
    from omega_hub.models import EmailVerification
    EmailVerification.objects.update_or_create(user=user, defaults={"email": user.email})
print("HUB_E2E_USER " + json.dumps({"email": email, "org": str(org.id)}))
`;

export function ensureUser({ email, name, org, emailVerified = true }) {
  const out = djangoShell(CREATE_USER, {
    HUB_E2E_EMAIL: email,
    HUB_E2E_NAME: name,
    HUB_E2E_ORG: org,
    HUB_E2E_PASSWORD: PASSWORD,
    HUB_E2E_EMAIL_VERIFIED: emailVerified ? "1" : "0",
  });
  const line = out.split("\n").find((l) => l.startsWith("HUB_E2E_USER "));
  if (!line)
    throw new Error(`user ${email} was not created: ${out.slice(-500)}`);
  return JSON.parse(line.slice("HUB_E2E_USER ".length));
}
