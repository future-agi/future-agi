// What each Sandboxes page can show as code: request descriptors (curl.js)
// built from the tab's own route table and create-form builder, so the path,
// the body and the headers are the ones the page itself sends. A call is
// listed only where the page offers it for that sandbox's state (DetailActions,
// the Run, Files and Snapshots tabs), never one the stack refuses to a key
// (refused.js), and a creator-only call only to someone who may operate the
// sandbox (sandboxRole.js: EE refuses it to anyone but its creator).

import { sandboxRoutes } from "../sandboxes/api/sandboxesApi";
import {
  buildCreatePayload,
  initialForm,
  validateForm,
} from "../sandboxes/lib/createForm";

import { offered } from "./refused";

const SANDBOX = "/workspace/sandboxes/{sandbox}";

/** The example command the Run snippet sends: harmless, and its output shows where it ran. */
export const EXAMPLE_COMMAND = "uname -a && pwd";

export const sandboxRequest = {
  list: () => ({
    id: "sandboxes.list",
    title: "List sandboxes",
    route: "GET /workspace/sandboxes",
    method: "GET",
    path: sandboxRoutes.list(),
  }),
  /** The create body exactly as the drawer sends it (buildCreatePayload). */
  create: (form) => ({
    id: "sandboxes.create",
    title: "Create a sandbox",
    route: "POST /workspace/sandboxes",
    method: "POST",
    path: sandboxRoutes.list(),
    json: buildCreatePayload(form),
    idempotent: "sandbox",
  }),
  get: (id) => ({
    id: "sandboxes.get",
    title: "Read this sandbox",
    route: `GET ${SANDBOX}`,
    method: "GET",
    path: sandboxRoutes.one(id),
  }),
  exec: (id) => ({
    id: "sandboxes.exec",
    title: "Run a command (streamed)",
    route: `POST ${SANDBOX}/exec`,
    method: "POST",
    path: sandboxRoutes.exec(id),
    json: { command: EXAMPLE_COMMAND },
    stream: true,
  }),
  files: (id) => ({
    id: "sandboxes.files",
    title: "List files in /workspace",
    route: `GET ${SANDBOX}/files`,
    method: "GET",
    path: sandboxRoutes.files(id),
  }),
  snapshots: (id) => ({
    id: "sandboxes.snapshots",
    title: "List snapshots",
    route: `GET ${SANDBOX}/snapshots`,
    method: "GET",
    path: sandboxRoutes.snapshots(id),
  }),
  snapshot: (id) => ({
    id: "sandboxes.snapshot",
    title: "Take a snapshot",
    route: `POST ${SANDBOX}/snapshots`,
    method: "POST",
    path: sandboxRoutes.snapshots(id),
    idempotent: "sandbox",
  }),
  activity: (id) => ({
    id: "sandboxes.activity",
    title: "Read the activity",
    route: `GET ${SANDBOX}/activity`,
    method: "GET",
    path: sandboxRoutes.activity(id),
  }),
  hibernate: (id) => ({
    id: "sandboxes.hibernate",
    title: "Hibernate",
    route: `POST ${SANDBOX}/hibernate`,
    method: "POST",
    path: sandboxRoutes.lifecycle(id, "hibernate"),
  }),
  resume: (id) => ({
    id: "sandboxes.resume",
    title: "Resume",
    route: `POST ${SANDBOX}/resume`,
    method: "POST",
    path: sandboxRoutes.lifecycle(id, "resume"),
  }),
  destroy: (id) => ({
    id: "sandboxes.delete",
    title: "Delete this sandbox",
    route: `DELETE ${SANDBOX}`,
    method: "DELETE",
    path: sandboxRoutes.one(id),
    destructive: true,
  }),
  settings: () => ({
    id: "sandboxes.settings",
    title: "Read sandbox settings",
    route: "GET /workspace/sandboxes/settings",
    method: "GET",
    path: sandboxRoutes.settings(),
  }),
};

/**
 * The create request the drawer would send for `form`, or null while the
 * drawer would not send it: settings that refuse every create, or a field the
 * form itself refuses.
 */
export function sandboxCreateRequest(form, caps) {
  if (!form || !caps || caps.createRefusal) return null;
  if (Object.keys(validateForm(form, caps)).length > 0) return null;
  return sandboxRequest.create(form);
}

/** Whether a new sandbox may start now (the list page's New button). */
function canCreate(caps, active) {
  if (!caps || caps.createRefusal) return false;
  return !(
    Number.isFinite(caps.maxActiveSandboxes) &&
    active >= caps.maxActiveSandboxes
  );
}

/** Sandboxes list: list, and create with the drawer's defaults while New is enabled. */
export function sandboxListRequests(caps, { active = 0 } = {}) {
  const requests = [sandboxRequest.list()];
  if (canCreate(caps, active)) {
    requests.push(sandboxCreateRequest(initialForm(caps), caps));
  }
  return offered(requests);
}

/**
 * The calls EE keeps to the member who created the sandbox ("operate" in EE
 * 2be31e97 workspace/sandboxes/access/creator-only.ts); everyone else gets 403
 * role_forbidden. Reads and delete are open to an owner or admin as well.
 */
export const CREATOR_ONLY = Object.freeze([
  "sandboxes.exec",
  "sandboxes.snapshot",
  "sandboxes.hibernate",
  "sandboxes.resume",
]);
const CREATOR_ONLY_IDS = new Set(CREATOR_ONLY);

// Which calls each detail tab leads with.
const TAB_FIRST = {
  overview: "sandboxes.get",
  run: "sandboxes.exec",
  files: "sandboxes.files",
  snapshots: "sandboxes.snapshots",
  activity: "sandboxes.activity",
};

/**
 * One sandbox: reads always; writes only as the page offers them. An agent
 * run's sandbox is read-only, a deleted one keeps its record and activity, a
 * hibernated one resumes before it runs anything. The creator-only calls are
 * listed only with `mayOperate` (sandboxRole.js); a caller that does not say
 * so gets reads and delete, as EE answers an owner or admin who did not
 * create the sandbox.
 */
export function sandboxDetailRequests(
  sandbox,
  tab = "overview",
  { mayOperate = false } = {},
) {
  const requests = everyDetailRequest(sandbox).filter(
    (request) => mayOperate || !CREATOR_ONLY_IDS.has(request.id),
  );
  const lead = TAB_FIRST[tab];
  const first = requests.findIndex((request) => request.id === lead);
  if (first > 0) requests.unshift(...requests.splice(first, 1));
  return offered(requests);
}

/**
 * Whether a creator-only call this sandbox's page offers in its state was
 * left out for this person (the drawer then says why).
 */
export function creatorOnlyWithheld(sandbox, { mayOperate = false } = {}) {
  if (mayOperate) return false;
  return everyDetailRequest(sandbox).some((request) =>
    CREATOR_ONLY_IDS.has(request.id),
  );
}

/** Every call the page offers on this sandbox in its state, for its creator. */
function everyDetailRequest(sandbox) {
  if (!sandbox?.id) return [];
  const { id, state, readOnly } = sandbox;
  const requests = [sandboxRequest.get(id)];
  if (state === "destroyed") {
    requests.push(sandboxRequest.activity(id));
  } else if (readOnly) {
    if (state === "active") requests.push(sandboxRequest.files(id));
    requests.push(sandboxRequest.activity(id));
  } else if (state === "active") {
    requests.push(
      sandboxRequest.exec(id),
      sandboxRequest.files(id),
      sandboxRequest.snapshots(id),
      sandboxRequest.snapshot(id),
      sandboxRequest.activity(id),
      sandboxRequest.hibernate(id),
      sandboxRequest.destroy(id),
    );
  } else if (state === "hibernated") {
    requests.push(
      sandboxRequest.snapshots(id),
      sandboxRequest.activity(id),
      sandboxRequest.resume(id),
      sandboxRequest.destroy(id),
    );
  } else {
    requests.push(sandboxRequest.activity(id), sandboxRequest.destroy(id));
  }
  return requests;
}

/** Settings > Sandboxes: reading only. Saving is a person's decision (refused.js). */
export function sandboxSettingsRequests() {
  return offered([sandboxRequest.settings()]);
}
