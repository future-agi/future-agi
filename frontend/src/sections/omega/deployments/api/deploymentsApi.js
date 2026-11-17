import {
  omegaJson as sharedOmegaJson,
  openOmegaStream as sharedOpenOmegaStream,
  requireList,
} from "src/sections/omega/shared";

import { toBase64 } from "../lib/bundle";

/**
 * Client for EE's customer project deployments
 * (`omega-workspace-deployments/v1`; the create body is `omega-deploy/1`, the
 * same request `omega deploy --control-plane` sends), reached through the
 * Omega gateway at /omega/api. Every call rides the shared data layer (lane
 * platform): a fresh customer assertion per request, no host token, no
 * workspace headers. EE derives the tenant from the assertion.
 */
const ROOT = "/workspace/deployments";
const ID = /^dep-[0-9a-f]{16}$/;

const segment = (id) => {
  if (typeof id !== "string" || !ID.test(id)) {
    throw new TypeError(`Not a deployment id: ${JSON.stringify(id)}`);
  }
  return id;
};

export const deploymentRoutes = {
  list: () => ROOT,
  one: (id) => `${ROOT}/${segment(id)}`,
  health: (id) => `${ROOT}/${segment(id)}/health`,
  logs: (id) => `${ROOT}/${segment(id)}/logs`,
  token: (id) => `${ROOT}/${segment(id)}/access-token`,
  rollback: (id, revision) => {
    if (!Number.isSafeInteger(revision) || revision < 1)
      throw new TypeError("A revision is a positive integer.");
    return `${ROOT}/${segment(id)}/revisions/${revision}/rollback`;
  },
};

/**
 * The workspace vault (EE `omega-workspace-credentials/v1`). Admins write,
 * everyone reads names only. A value goes in once and never comes back: no
 * reply of these routes carries it.
 */
const VAULT = "/workspace/credentials";
const vaultId = (id) => {
  if (typeof id !== "string" || !/^[A-Za-z0-9_.:-]{1,200}$/.test(id))
    throw new TypeError(`Not a credential id: ${JSON.stringify(id)}`);
  return id;
};

export const credentialRoutes = {
  list: () => VAULT,
  one: (id) => `${VAULT}/${vaultId(id)}`,
  rotate: (id) => `${VAULT}/${vaultId(id)}/rotate`,
};

/** The `omega-deploy/1` create body for a bundle (lib/bundle.js) and settings. */
export function createBody({
  name,
  bundle,
  env = {},
  secretRefs = [],
  studio,
}) {
  return {
    protocol: "omega-deploy/1",
    kind: "project",
    name,
    bundle: {
      format: "tar+gzip",
      sha256: bundle.sha256,
      contentSha256: bundle.contentSha256,
      size: bundle.size,
      data: toBase64(bundle.archive),
    },
    env,
    secretRefs: [...secretRefs].sort(),
    serve: { studio: studio !== false },
  };
}

const keyed = (idempotencyKey) => {
  if (!idempotencyKey)
    throw new TypeError("This call needs an Idempotency-Key.");
  return { "Idempotency-Key": idempotencyKey };
};

/**
 * @param {{ omegaJson?: Function, openOmegaStream?: Function }} [deps]
 */
export function createDeploymentsApi({
  omegaJson = sharedOmegaJson,
  openOmegaStream = sharedOpenOmegaStream,
} = {}) {
  return {
    /** -> [summary] (newest first as EE lists them) */
    list: () =>
      omegaJson(deploymentRoutes.list()).then((body) =>
        requireList(body, "deployments"),
      ),
    /** -> detail (revisions newest first, history, health) */
    get: (id) => omegaJson(deploymentRoutes.one(id)),
    /** -> detail; a new deployment's detail carries `accessToken` once */
    create: (input, { idempotencyKey }) =>
      omegaJson(deploymentRoutes.list(), {
        method: "POST",
        json: createBody(input),
        headers: keyed(idempotencyKey),
      }),
    rollback: (
      id,
      revision,
      { expectedRevision, reason },
      { idempotencyKey },
    ) =>
      omegaJson(deploymentRoutes.rollback(id, revision), {
        method: "POST",
        json: reason ? { expectedRevision, reason } : { expectedRevision },
        headers: keyed(idempotencyKey),
      }),
    rotateToken: (id, { idempotencyKey }) =>
      omegaJson(deploymentRoutes.token(id), {
        method: "POST",
        headers: keyed(idempotencyKey),
      }),
    /** -> { id, name, containers, images, volume } */
    remove: (id) =>
      omegaJson(deploymentRoutes.one(id), { method: "DELETE" }).then(
        (b) => b.deleted,
      ),
    /** -> { health, liveRevision } */
    health: (id) => omegaJson(deploymentRoutes.health(id)),
    /**
     * Vault credentials a deployment can reference by name. Values never
     * leave EE; this lists names, kinds and status only.
     */
    credentials: () =>
      omegaJson(credentialRoutes.list()).then((body) =>
        requireList(body, "credentials"),
      ),
    /** -> the new credential's public record (no value). Admins only. */
    createCredential: ({ name, kind, secret }, { idempotencyKey }) =>
      omegaJson(credentialRoutes.list(), {
        method: "POST",
        json: { name, kind, secret },
        headers: keyed(idempotencyKey),
      }).then((b) => b.credential),
    /**
     * Replace a credential's value in one step: the old value stays in force
     * until EE commits the new one, and a refused rotation changes nothing.
     */
    rotateCredential: (id, { secret }, { idempotencyKey }) =>
      omegaJson(credentialRoutes.rotate(id), {
        method: "POST",
        json: { secret },
        headers: keyed(idempotencyKey),
      }).then((b) => b.credential),
    /** Revoke: the name stops resolving for every later start. */
    revokeCredential: (id, { idempotencyKey }) =>
      omegaJson(credentialRoutes.one(id), {
        method: "DELETE",
        headers: keyed(idempotencyKey),
      }).then((b) => b.deleted === true),
    /**
     * One log stream (SSE). `source` "build" (with `revision`, resumable by
     * `lastEventId` = seq) or "runtime" (followed, never stored).
     */
    logs: (id, { source, revision, tail, lastEventId, ...handlers }) =>
      openOmegaStream(deploymentRoutes.logs(id), {
        query: {
          source,
          ...(source === "build" && revision ? { revision } : {}),
          ...(source === "runtime" && tail !== undefined ? { tail } : {}),
        },
        lastEventId: lastEventId ?? null,
        // A build log resumes after its last seq; a runtime log is a fresh
        // tail on every connect, so reconnecting would repeat lines.
        resumable: source === "build",
        maxReconnects: 3,
        ...handlers,
      }),
  };
}
