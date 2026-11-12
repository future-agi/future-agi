/* eslint-env node */
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { CAPS, CAPS_V2, sandboxView } from "../../sandboxes/__tests__/helpers";
import {
  BUNDLE,
  CREATOR,
  MANAGER,
  SETTINGS,
  everyDeploymentRequestFor,
  everyRequest,
  everySandboxDetailRequestFor,
} from "./every-request";
import {
  ID,
  detailView,
  revisionView,
} from "../../deployments/__tests__/helpers";
import { initialForm } from "../../sandboxes/lib/createForm";
import {
  DEPLOY_BODY_FILE,
  deploymentCreateRequest,
  deploymentDetailRequests,
  deploymentSecretsRequests,
} from "../deploymentRequests";
import { canManageDeploymentsFor } from "../manageRole";
import { HUMAN_ONLY_ROUTES, KEY_REFUSED_ROUTES, isOffered } from "../refused";
import {
  CREATOR_ONLY,
  EXAMPLE_COMMAND,
  creatorOnlyWithheld,
  sandboxCreateRequest,
  sandboxDetailRequests,
  sandboxListRequests,
  sandboxSettingsRequests,
} from "../sandboxRequests";
import { mayOperateSandboxesFor } from "../sandboxRole";
import { stackForms } from "./stack-forms";

const GATEWAY = path.resolve(
  __dirname,
  "../../../../../../services/omega-gateway",
);
const MANIFEST = JSON.parse(
  fs.readFileSync(
    path.join(GATEWAY, "manifest/console-route-manifest.v1.json"),
    "utf8",
  ),
);
// The gateway's own list of human-session-only verbs, read from its source.
const SESSION_ONLY_ROUTES = (() => {
  const source = fs.readFileSync(
    path.join(GATEWAY, "src/session-only.mjs"),
    "utf8",
  );
  const list = source.match(
    /SESSION_ONLY_ROUTES = Object\.freeze\(\[([\s\S]*?)\]\)/,
  );
  return [...list[1].matchAll(/"([^"]+)"/g)].map((match) => match[1]);
})();
const MANIFEST_ROUTES = new Set(
  MANIFEST.routes.map((route) => `${route.method} ${route.path}`),
);

/** The manifest template as a pattern over a concrete path (one segment per {param}). */
const templatePattern = (template) =>
  new RegExp(`^${template.replace(/\{[^}]+\}/g, "[^/?]+")}(?:\\?.*)?$`);

const ids = (requests) => requests.map((request) => request.id);

describe("the code panels' request catalog", () => {
  it("names only routes the gateway admits, each path matching its template", () => {
    const all = everyRequest();
    expect(all.length).toBeGreaterThan(40);
    for (const request of all) {
      expect(MANIFEST_ROUTES.has(request.route), request.route).toBe(true);
      expect(request.route.startsWith(`${request.method} `), request.id).toBe(
        true,
      );
      const template = request.route.slice(request.method.length + 1);
      expect(request.path, request.id).toMatch(templatePattern(template));
    }
  });

  it("never offers a call the stack refuses to a key, or a vault write", () => {
    expect([...KEY_REFUSED_ROUTES].sort()).toEqual(
      [...SESSION_ONLY_ROUTES].sort(),
    );
    for (const route of [...KEY_REFUSED_ROUTES, ...HUMAN_ONLY_ROUTES]) {
      expect(MANIFEST_ROUTES.has(route), route).toBe(true);
      expect(isOffered(route)).toBe(false);
    }
    for (const request of everyRequest()) {
      expect(isOffered(request.route), request.route).toBe(true);
    }
  });

  it("lists and creates on the Sandboxes list only while New would create", () => {
    expect(ids(sandboxListRequests(CAPS_V2, { active: 0 }))).toEqual([
      "sandboxes.list",
      "sandboxes.create",
    ]);
    expect(ids(sandboxListRequests(CAPS_V2, { active: 10 }))).toEqual([
      "sandboxes.list",
    ]);
    expect(
      ids(
        sandboxListRequests({
          ...CAPS_V2,
          createRefusal: { code: "no_templates" },
        }),
      ),
    ).toEqual(["sandboxes.list"]);
  });

  it("builds the create body with the drawer's own builder, and none for a form the drawer would not send", () => {
    const form = { ...initialForm(CAPS_V2), name: "it's mine" };
    const request = sandboxCreateRequest(form, CAPS_V2);
    expect(request.json).toEqual({
      name: "it's mine",
      template: "base",
      size: "small",
      network: "none",
      idleTtlSeconds: 1800,
    });
    expect(request.idempotent).toBe("sandbox");
    expect(sandboxCreateRequest({ ...form, size: "xl" }, CAPS_V2)).toBeNull();
    expect(
      sandboxCreateRequest({ ...form, source: "environment" }, CAPS_V2),
    ).toBeNull();
    expect(
      sandboxCreateRequest(form, { ...CAPS_V2, createRefusal: {} }),
    ).toBeNull();
  });

  it("offers its creator on a sandbox only what its page offers in that state, leading with the open tab", () => {
    const active = sandboxView();
    expect(ids(sandboxDetailRequests(active, "run", CREATOR))).toEqual([
      "sandboxes.exec",
      "sandboxes.get",
      "sandboxes.files",
      "sandboxes.snapshots",
      "sandboxes.snapshot",
      "sandboxes.activity",
      "sandboxes.hibernate",
      "sandboxes.delete",
    ]);
    expect(sandboxDetailRequests(active, "run", CREATOR)[0].json).toEqual({
      command: EXAMPLE_COMMAND,
    });
    expect(
      ids(
        sandboxDetailRequests(
          sandboxView({ state: "hibernated" }),
          "overview",
          CREATOR,
        ),
      ),
    ).toEqual([
      "sandboxes.get",
      "sandboxes.snapshots",
      "sandboxes.activity",
      "sandboxes.resume",
      "sandboxes.delete",
    ]);
    expect(
      ids(
        sandboxDetailRequests(
          sandboxView({ state: "destroyed" }),
          "overview",
          CREATOR,
        ),
      ),
    ).toEqual(["sandboxes.get", "sandboxes.activity"]);
    // An agent run's sandbox is read-only: reads only.
    expect(
      ids(
        sandboxDetailRequests(
          sandboxView({ readOnly: true }),
          "files",
          CREATOR,
        ),
      ),
    ).toEqual(["sandboxes.files", "sandboxes.get", "sandboxes.activity"]);
    expect(ids(sandboxSettingsRequests())).toEqual(["sandboxes.settings"]);
  });

  // POLISH-FA (CODE-PANELS verdict, low): EE keeps exec, snapshot, hibernate
  // and resume to the sandbox's creator (creator-only.ts at 2be31e97) and
  // answers anyone else 403 role_forbidden; owners and admins may still read
  // and delete any sandbox. A Future AGI member is only ever shown their own
  // sandboxes; an owner or admin may be shown anyone's, and EE's view names no
  // creator, so the panel fails closed for them.
  it("offers no creator-only call to someone who may not be the sandbox's creator, in any state or tab", () => {
    expect([...CREATOR_ONLY].sort()).toEqual([
      "sandboxes.exec",
      "sandboxes.hibernate",
      "sandboxes.resume",
      "sandboxes.snapshot",
    ]);
    for (const role of ["Member", "workspace_admin", "workspace_member"]) {
      expect(mayOperateSandboxesFor(role), role).toBe(true);
    }
    for (const role of [
      "Owner",
      "Admin",
      "Viewer",
      "workspace_viewer",
      "Workspace Admin",
      "owner",
      "",
      null,
      undefined,
    ]) {
      expect(mayOperateSandboxesFor(role), String(role)).toBe(false);
    }
    for (const who of [{ mayOperate: false }, {}, undefined]) {
      const shown = everySandboxDetailRequestFor(who);
      expect(shown.length).toBeGreaterThan(40);
      for (const request of shown) {
        expect(
          CREATOR_ONLY,
          `${request.id} ${JSON.stringify(who)}`,
        ).not.toContain(request.id);
      }
      // What EE answers an owner or admin on another member's sandbox: reads and delete.
      expect(ids(sandboxDetailRequests(sandboxView(), "run", who))).toEqual([
        "sandboxes.get",
        "sandboxes.files",
        "sandboxes.snapshots",
        "sandboxes.activity",
        "sandboxes.delete",
      ]);
      expect(
        ids(
          sandboxDetailRequests(
            sandboxView({ state: "hibernated" }),
            "overview",
            who,
          ),
        ),
      ).toEqual([
        "sandboxes.get",
        "sandboxes.snapshots",
        "sandboxes.activity",
        "sandboxes.delete",
      ]);
    }
    // The creator is offered all four where the state allows them.
    const creator = everySandboxDetailRequestFor(CREATOR).map((r) => r.id);
    for (const id of CREATOR_ONLY) expect(creator).toContain(id);
  });

  it("says a creator-only call was left out exactly when the page would offer one in that state", () => {
    const at = (overrides) => sandboxView(overrides);
    expect(creatorOnlyWithheld(at({ state: "active" }))).toBe(true);
    expect(creatorOnlyWithheld(at({ state: "hibernated" }), {})).toBe(true);
    expect(creatorOnlyWithheld(at({ state: "active" }), CREATOR)).toBe(false);
    // Nothing to leave out: a deleted, a starting or an agent run's sandbox.
    expect(creatorOnlyWithheld(at({ state: "destroyed" }))).toBe(false);
    expect(creatorOnlyWithheld(at({ state: "starting" }))).toBe(false);
    expect(creatorOnlyWithheld(at({ readOnly: true }))).toBe(false);
    expect(creatorOnlyWithheld(null)).toBe(false);
  });

  it("offers no deployment write while it is busy, and a rollback only to a revision that served", () => {
    const live = deploymentDetailRequests(detailView(), "revisions", MANAGER);
    expect(ids(live)).toEqual([
      "deployments.rollback",
      "deployments.get",
      "deployments.health",
      "deployments.logs",
      "deployments.rotateToken",
      "deployments.delete",
    ]);
    expect(live[0].path).toBe(
      `/workspace/deployments/${ID}/revisions/1/rollback`,
    );
    expect(live[0].json).toEqual({ expectedRevision: 2 });
    expect(live.find((r) => r.id === "deployments.logs").query).toEqual({
      source: "build",
      revision: 2,
    });

    expect(
      ids(
        deploymentDetailRequests(
          detailView({ status: "building" }),
          "overview",
          MANAGER,
        ),
      ),
    ).toEqual(["deployments.get", "deployments.health", "deployments.logs"]);
    const never = detailView({
      revisions: [
        revisionView(2, { status: "live" }),
        revisionView(1, { liveAt: null }),
      ],
    });
    expect(
      ids(deploymentDetailRequests(never, "overview", MANAGER)),
    ).not.toContain("deployments.rollback");
  });

  it("offers a member, or anyone whose role is not known yet, no deployment write: EE refuses them all", () => {
    // EE 2be31e97: deploy, rollback, access-token and delete call
    // context.manage(...), MANAGING_ROLES = {owner, admin}, else 403
    // role_forbidden. The exchange makes only an organization Owner or Admin
    // one of those (accounts/omega_session.py delegation_role).
    expect(canManageDeploymentsFor("Owner")).toBe(true);
    expect(canManageDeploymentsFor("Admin")).toBe(true);
    for (const role of [
      "Member",
      "Viewer",
      "workspace_admin",
      "workspace_member",
      "Workspace Admin",
      "",
      null,
      undefined,
    ]) {
      expect(canManageDeploymentsFor(role), String(role)).toBe(false);
    }
    for (const who of [{ canManage: false }, {}, undefined]) {
      const offeredToThem = everyDeploymentRequestFor(who);
      expect(offeredToThem).toContain(null); // no deploy request at all
      const shown = offeredToThem.filter(Boolean);
      expect(shown.length).toBeGreaterThan(10);
      for (const request of shown) {
        expect(request.method, `${request.id} ${JSON.stringify(who)}`).toBe(
          "GET",
        );
      }
      expect(
        ids(deploymentDetailRequests(detailView(), "revisions", who)),
      ).toEqual(["deployments.get", "deployments.health", "deployments.logs"]);
    }
    // The same deployment, as a manager: every write the page offers.
    expect(
      ids(deploymentDetailRequests(detailView(), "overview", MANAGER)),
    ).toEqual(
      expect.arrayContaining([
        "deployments.rollback",
        "deployments.rotateToken",
        "deployments.delete",
      ]),
    );
  });

  it("sends the wizard's own create body from a file, and nothing until the wizard would deploy", () => {
    const request = deploymentCreateRequest(
      {
        bundle: BUNDLE,
        settings: SETTINGS,
        checks: { ok: true, env: { A: "1" } },
      },
      MANAGER,
    );
    expect(request.bodyFile).toBe(DEPLOY_BODY_FILE);
    expect(request.json).toEqual({
      protocol: "omega-deploy/1",
      kind: "project",
      name: "greeter",
      bundle: {
        format: "tar+gzip",
        sha256: BUNDLE.sha256,
        contentSha256: BUNDLE.contentSha256,
        size: 3,
        data: "AQID",
      },
      env: { A: "1" },
      secretRefs: ["a", "b"],
      serve: { studio: true },
    });
    expect(
      deploymentCreateRequest(
        { bundle: null, settings: SETTINGS, checks: { ok: true } },
        MANAGER,
      ),
    ).toBeNull();
    expect(
      deploymentCreateRequest(
        { bundle: BUNDLE, settings: SETTINGS, checks: { ok: false } },
        MANAGER,
      ),
    ).toBeNull();
    expect(ids(deploymentSecretsRequests())).toEqual(["credentials.list"]);
  });

  it("gives the live run only forms that stack's drawer would send", () => {
    const noNetwork = {
      ...CAPS_V2,
      networkModes: ["none"],
      defaultNetwork: "none",
    };
    for (const caps of [CAPS, CAPS_V2, noNetwork]) {
      const forms = stackForms(caps);
      expect(forms.length).toBeGreaterThan(1);
      expect(forms.some((form) => form.name === "it's a snippet")).toBe(true);
      for (const form of forms) {
        const request = sandboxCreateRequest(form, caps);
        expect(request, JSON.stringify(form)).not.toBeNull();
        if (!caps.networkModes.includes("full"))
          expect(request.json.network).toBe("none");
      }
    }
    expect(stackForms(noNetwork).some((form) => form.network === "full")).toBe(
      false,
    );
    expect(stackForms(CAPS_V2).some((form) => form.network === "full")).toBe(
      CAPS_V2.networkModes.includes("full"),
    );
  });
});
