import {
  OmegaError,
  omegaFetch as sharedOmegaFetch,
  omegaJson as sharedOmegaJson,
  openOmegaStream as sharedOpenOmegaStream,
} from "src/sections/omega/shared";

/**
 * Client for EE's customer-scoped sandboxes (`omega-workspace-sandboxes/v1`),
 * reached through the Omega gateway at /omega/api. Every call rides the shared
 * data layer: a fresh customer assertion per request, no host
 * token, no workspace headers. The tenant is derived by EE from the assertion,
 * never from anything this client sends.
 */
const ROOT = "/workspace/sandboxes";

const segment = (value) => {
  if (typeof value !== "string" || value.length === 0) {
    throw new TypeError("Sandbox ids must be non-empty strings.");
  }
  return encodeURIComponent(value);
};

/** `a/b c.txt` -> `a/b%20c.txt`: each path segment encoded, separators kept. */
export function encodeFilePath(relPath) {
  const parts = String(relPath || "")
    .split("/")
    .filter((part) => part.length > 0);
  if (parts.length === 0) throw new TypeError("A file path is required.");
  if (parts.some((part) => part === "." || part === "..")) {
    throw new TypeError("File paths may not contain . or .. segments.");
  }
  return parts.map(encodeURIComponent).join("/");
}

export const sandboxRoutes = {
  list: () => ROOT,
  settings: () => `${ROOT}/settings`,
  one: (id) => `${ROOT}/${segment(id)}`,
  lifecycle: (id, verb) => `${ROOT}/${segment(id)}/${verb}`,
  exec: (id) => `${ROOT}/${segment(id)}/exec`,
  cancel: (id, execId) =>
    `${ROOT}/${segment(id)}/exec/${segment(execId)}/cancel`,
  files: (id) => `${ROOT}/${segment(id)}/files`,
  file: (id, relPath) =>
    `${ROOT}/${segment(id)}/files/${encodeFilePath(relPath)}`,
  download: (id, relPath) =>
    `${ROOT}/${segment(id)}/download/${encodeFilePath(relPath)}`,
  upload: (id, relPath) =>
    `${ROOT}/${segment(id)}/upload/${encodeFilePath(relPath)}`,
  folder: (id, relPath) =>
    `${ROOT}/${segment(id)}/folders/${encodeFilePath(relPath)}`,
  move: (id) => `${ROOT}/${segment(id)}/move`,
  snapshots: (id) => `${ROOT}/${segment(id)}/snapshots`,
  restore: (id, snapshotId) =>
    `${ROOT}/${segment(id)}/snapshots/${segment(snapshotId)}/restore`,
  activity: (id) => `${ROOT}/${segment(id)}/activity`,
};

/** The 404 codes that mean "no environments here" (see `environments`). */
const NO_ENVIRONMENTS = new Set(["not_found", "product_not_admitted"]);

/** A fresh key per upload, so a re-sent PUT (after a stale assertion) is safe to retry. */
const uploadKey = () =>
  globalThis.crypto?.randomUUID?.() ??
  `upload-${Date.now()}-${Math.random().toString(16).slice(2)}`;

/**
 * @param {{ omegaJson?: Function, omegaFetch?: Function, openOmegaStream?: Function }} [deps]
 */
export function createSandboxesApi({
  omegaJson = sharedOmegaJson,
  omegaFetch = sharedOmegaFetch,
  openOmegaStream = sharedOpenOmegaStream,
} = {}) {
  // Creates, restores and snapshots carry an Idempotency-Key when the caller
  // gives one, so a retry after a lost reply replays EE's first answer
  // instead of starting a second container.
  const post = (path, json, idempotencyKey) =>
    omegaJson(path, {
      method: "POST",
      ...(json ? { json } : {}),
      ...(idempotencyKey
        ? { headers: { "Idempotency-Key": idempotencyKey } }
        : {}),
    });

  return {
    /** -> { sandboxes, caps, provider } */
    list: () => omegaJson(sandboxRoutes.list()),
    get: (id) => omegaJson(sandboxRoutes.one(id)).then((body) => body.sandbox),
    create: (input, { idempotencyKey } = {}) =>
      post(sandboxRoutes.list(), input, idempotencyKey).then(
        (body) => body.sandbox,
      ),
    update: (id, input) =>
      omegaJson(sandboxRoutes.one(id), { method: "PATCH", json: input }).then(
        (body) => body.sandbox,
      ),
    destroy: (id) =>
      omegaJson(sandboxRoutes.one(id), { method: "DELETE" }).then(
        (body) => body.sandbox,
      ),
    hibernate: (id) =>
      post(sandboxRoutes.lifecycle(id, "hibernate")).then((b) => b.sandbox),
    resume: (id) =>
      post(sandboxRoutes.lifecycle(id, "resume")).then((b) => b.sandbox),
    /** -> { path, entries: [{ name, size, mode, directory? }] } */
    listFiles: (id, path = "") =>
      omegaJson(sandboxRoutes.files(id), { query: path ? { path } : {} }),
    /** -> { path, encoding, content, bytes, truncated, modifiedAt } */
    readFile: (id, relPath) =>
      omegaJson(sandboxRoutes.file(id, relPath)).then((body) => body.file),
    writeFile: (id, relPath, content) =>
      omegaJson(sandboxRoutes.file(id, relPath), {
        method: "PUT",
        json: { content, encoding: "utf8" },
      }).then((body) => body.file),
    /**
     * -> Blob: one file's bytes, streamed from inside the sandbox (EE types
     * it by extension only and always as an attachment).
     */
    downloadFile: async (id, relPath, { signal } = {}) => {
      const result = await omegaFetch(sandboxRoutes.download(id, relPath), {
        raw: true,
        headers: { Accept: "*/*" },
        signal,
      });
      if (!result.ok) throw new OmegaError(result);
      return result.response.blob();
    },
    /** -> { path, bytes, modifiedAt }: the file's bytes as the request body. */
    uploadFile: (id, relPath, file) =>
      omegaJson(sandboxRoutes.upload(id, relPath), {
        method: "PUT",
        body: file,
        headers: {
          "Content-Type": "application/octet-stream",
          "Idempotency-Key": uploadKey(),
        },
      }).then((body) => body.file),
    /** A file or link; a folder only when empty unless `recursive`. */
    deletePath: (id, relPath, { recursive = false } = {}) =>
      omegaJson(sandboxRoutes.file(id, relPath), {
        method: "DELETE",
        query: recursive ? { recursive: "true" } : {},
      }).then((body) => body.deleted),
    /** Rename within the workspace; `to` must not exist. */
    movePath: (id, from, to) =>
      omegaJson(sandboxRoutes.move(id), {
        method: "POST",
        json: { from, to },
      }).then((body) => body.moved),
    makeFolder: (id, relPath) =>
      omegaJson(sandboxRoutes.folder(id, relPath), { method: "PUT" }).then(
        (body) => body.folder,
      ),
    snapshots: (id) =>
      omegaJson(sandboxRoutes.snapshots(id)).then((body) => body.snapshots),
    snapshot: (id, { idempotencyKey } = {}) =>
      post(sandboxRoutes.snapshots(id), undefined, idempotencyKey).then(
        (body) => body.snapshot,
      ),
    restore: (id, snapshotId, input, { idempotencyKey } = {}) =>
      post(sandboxRoutes.restore(id, snapshotId), input, idempotencyKey).then(
        (body) => body.sandbox,
      ),
    activity: (id) =>
      omegaJson(sandboxRoutes.activity(id)).then((body) => body.activity),
    /**
     * -> { settings, operator, canEdit, caps, updatedAt?, updatedBy? }: this
     * workspace's sandbox settings and the operator's layer they narrow.
     */
    settings: () => omegaJson(sandboxRoutes.settings()),
    /** Owners and admins only; EE answers 403 role_forbidden otherwise. */
    saveSettings: (input) =>
      omegaJson(sandboxRoutes.settings(), { method: "PUT", json: input }),
    /**
     * Container templates come from the workspace's environment profiles.
     * This workspace has none to start from, which is not an error to show,
     * when the gateway or EE answers 404 with either code:
     * - not_found: environments are hidden from the hosted Console until they
     *   can be made there (the route manifest's `hidden` list);
     * - product_not_admitted: the route belongs to Console, and this
     *   workspace is admitted to Sandboxes only.
     * Any other failure is still thrown.
     */
    environments: () =>
      omegaJson("/workspace/environments").then(
        (body) => body?.environments ?? [],
        (error) => {
          if (error?.status === 404 && NO_ENVIRONMENTS.has(error?.code))
            return [];
          throw error;
        },
      ),
    cancelExec: (id, execId) => post(sandboxRoutes.cancel(id, execId)),
    /**
     * Run one command as an SSE stream. Never resumable: re-sending the POST
     * would run the command a second time.
     */
    exec: (id, input, { onEvent, onState, signal } = {}) =>
      openOmegaStream(sandboxRoutes.exec(id), {
        method: "POST",
        json: input,
        resumable: false,
        onEvent,
        onState,
        signal,
      }),
  };
}
