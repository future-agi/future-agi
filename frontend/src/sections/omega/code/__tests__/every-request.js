// Every request a Sandboxes or Deployments page can show as code, across the
// states that change what a page offers. The catalog test checks each against
// the gateway's manifest; the gateway test runs each one with real curl.
import { CAPS_V2, sandboxView } from "../../sandboxes/__tests__/helpers";
import { detailView } from "../../deployments/__tests__/helpers";
import { initialForm } from "../../sandboxes/lib/createForm";
import {
  deploymentCreateRequest,
  deploymentDetailRequests,
  deploymentListRequests,
  deploymentSecretsRequests,
} from "../deploymentRequests";
import {
  sandboxCreateRequest,
  sandboxDetailRequests,
  sandboxListRequests,
  sandboxSettingsRequests,
} from "../sandboxRequests";

export const BUNDLE = {
  sha256: "a".repeat(64),
  contentSha256: "b".repeat(64),
  size: 3,
  archive: new Uint8Array([1, 2, 3]),
  manifest: { name: "greeter" },
};
export const SETTINGS = {
  name: "greeter",
  rows: [],
  secretRefs: ["b", "a"],
  studio: true,
};
/** What the wizard holds when Deploy is enabled (bundle, settings, their checks). */
export const DEPLOY_INPUT = {
  bundle: BUNDLE,
  settings: SETTINGS,
  checks: { ok: true, env: { GREETING: "it's a test" } },
};

/** A workspace owner or admin, whom EE lets deploy, roll back, mint a token and delete. */
export const MANAGER = Object.freeze({ canManage: true });

/** The member who created the sandbox: EE lets only them run, snapshot, hibernate and resume it. */
export const CREATOR = Object.freeze({ mayOperate: true });

/** Drawer forms that change the create body (ASCII only: the snippets run as commands). */
export function createForms() {
  const base = initialForm(CAPS_V2);
  return [
    base,
    { ...base, name: "it's mine" },
    { ...base, source: "image", image: "alpine:3.20" },
    { ...base, size: "custom", cpus: "1", memoryMb: "2048", pidsLimit: "512" },
    { ...base, network: "full", idleTtlSeconds: 3600 },
  ];
}

/** All of them, in page order (duplicates kept: each page builds its own). */
export function everyRequest() {
  const sandbox = (overrides) => sandboxView(overrides);
  const tabs = ["overview", "run", "files", "snapshots", "activity"];
  const out = [
    ...sandboxListRequests(CAPS_V2, { active: 0 }),
    ...createForms().map((form) => sandboxCreateRequest(form, CAPS_V2)),
    ...sandboxSettingsRequests(),
    ...deploymentListRequests(),
    ...deploymentSecretsRequests(),
    deploymentCreateRequest(DEPLOY_INPUT, MANAGER),
  ];
  for (const state of ["active", "hibernated", "destroyed", "starting"]) {
    for (const tab of tabs) {
      out.push(...sandboxDetailRequests(sandbox({ state }), tab, CREATOR));
      out.push(
        ...sandboxDetailRequests(
          sandbox({ state, readOnly: true }),
          tab,
          CREATOR,
        ),
      );
    }
  }
  for (const tab of ["overview", "revisions", "logs", "settings"]) {
    out.push(...deploymentDetailRequests(detailView(), tab, MANAGER));
    out.push(
      ...deploymentDetailRequests(
        detailView({ status: "building" }),
        tab,
        MANAGER,
      ),
    );
  }
  return out;
}

/**
 * Every request a Deployments page offers someone who is not a workspace
 * owner or admin (`who`: { canManage: false }, or nothing at all), in every
 * state; null where the page offers nothing (the wizard's deploy).
 */
export function everyDeploymentRequestFor(who) {
  const out = [
    ...deploymentListRequests(),
    ...deploymentSecretsRequests(),
    deploymentCreateRequest(DEPLOY_INPUT, who),
  ];
  for (const tab of ["overview", "revisions", "logs", "settings"]) {
    for (const status of ["live", "failed", "stopped", "building"]) {
      out.push(...deploymentDetailRequests(detailView({ status }), tab, who));
    }
  }
  return out;
}

/**
 * Every request a sandbox's page offers someone who may not operate it (an
 * owner or admin, who may be looking at another member's sandbox, or an
 * unknown role: `who` is { mayOperate: false } or nothing), in every state
 * and tab.
 */
export function everySandboxDetailRequestFor(who) {
  const out = [];
  for (const state of ["active", "hibernated", "destroyed", "starting"]) {
    for (const tab of ["overview", "run", "files", "snapshots", "activity"]) {
      for (const readOnly of [false, true]) {
        out.push(
          ...sandboxDetailRequests(sandboxView({ state, readOnly }), tab, who),
        );
      }
    }
  }
  return out;
}
