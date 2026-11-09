// What each Deployments page can show as code, built from the tab's own route
// table and create body (deploymentsApi.js), so a snippet sends what the page
// sends. A write is listed only when the stack would accept it from this
// person: never to a member (EE answers 403 role_forbidden to anyone but a
// workspace owner or admin, manageRole.js; the builders fail closed, so a
// caller that does not say `canManage: true` gets reads only), nothing that
// changes a deployment while it is busy (EE answers 409 deployment_busy), a
// rollback only to a revision the Revisions tab offers, and no vault write
// (refused.js).

import { isBusy } from "../deployments/lib/format";
import {
  createBody,
  credentialRoutes,
  deploymentRoutes,
} from "../deployments/api/deploymentsApi";

import { offered } from "./refused";

const DEPLOYMENT = "/workspace/deployments/{deployment}";

/** The file a deploy snippet reads its body from (offered as a download). */
export const DEPLOY_BODY_FILE = "omega-deploy-request.json";

export const deploymentRequest = {
  list: () => ({
    id: "deployments.list",
    title: "List deployments",
    route: "GET /workspace/deployments",
    method: "GET",
    path: deploymentRoutes.list(),
  }),
  /** The `omega-deploy/1` body exactly as the wizard sends it (createBody). */
  create: (input) => ({
    id: "deployments.create",
    title: "Deploy this project",
    route: "POST /workspace/deployments",
    method: "POST",
    path: deploymentRoutes.list(),
    json: createBody(input),
    idempotent: "deploy",
    bodyFile: DEPLOY_BODY_FILE,
  }),
  get: (id) => ({
    id: "deployments.get",
    title: "Read this deployment",
    route: `GET ${DEPLOYMENT}`,
    method: "GET",
    path: deploymentRoutes.one(id),
  }),
  health: (id) => ({
    id: "deployments.health",
    title: "Check its health",
    route: `GET ${DEPLOYMENT}/health`,
    method: "GET",
    path: deploymentRoutes.health(id),
  }),
  buildLog: (id, revision) => ({
    id: "deployments.logs",
    title: `Build log of revision ${revision} (streamed)`,
    route: `GET ${DEPLOYMENT}/logs`,
    method: "GET",
    path: deploymentRoutes.logs(id),
    query: { source: "build", revision },
    stream: true,
  }),
  rollback: (id, revision, expectedRevision) => ({
    id: "deployments.rollback",
    title: `Roll back to revision ${revision}`,
    route: `POST ${DEPLOYMENT}/revisions/{revision}/rollback`,
    method: "POST",
    path: deploymentRoutes.rollback(id, revision),
    json: { expectedRevision },
    idempotent: "rollback",
  }),
  rotateToken: (id) => ({
    id: "deployments.rotateToken",
    title: "Mint a new access token (the old one stops working)",
    route: `POST ${DEPLOYMENT}/access-token`,
    method: "POST",
    path: deploymentRoutes.token(id),
    idempotent: "token",
    destructive: true,
  }),
  destroy: (id) => ({
    id: "deployments.delete",
    title: "Delete this deployment",
    route: `DELETE ${DEPLOYMENT}`,
    method: "DELETE",
    path: deploymentRoutes.one(id),
    destructive: true,
  }),
  credentials: () => ({
    id: "credentials.list",
    title: "List the workspace's secret names",
    route: "GET /workspace/credentials",
    method: "GET",
    path: credentialRoutes.list(),
  }),
};

/** Deployments list. */
export function deploymentListRequests() {
  return offered([deploymentRequest.list()]);
}

/** The newest revision the Revisions tab would offer to roll back to, or null. */
function rollbackTarget(deployment) {
  if (deployment.liveRevision === null || deployment.liveRevision === undefined)
    return null;
  return (
    (deployment.revisions || []).find(
      (revision) =>
        Boolean(revision.liveAt) &&
        revision.revision !== deployment.liveRevision,
    ) || null
  );
}

const TAB_FIRST = {
  overview: "deployments.get",
  revisions: "deployments.rollback",
  logs: "deployments.logs",
  settings: "deployments.rotateToken",
};

/**
 * One deployment: reads always; writes only for an owner or admin
 * (`canManage`), and only while it is not busy.
 */
export function deploymentDetailRequests(
  deployment,
  tab = "overview",
  { canManage = false } = {},
) {
  if (!deployment?.id) return [];
  const { id } = deployment;
  const requests = [deploymentRequest.get(id), deploymentRequest.health(id)];
  const latest =
    deployment.operation?.revision ?? deployment.revisions?.[0]?.revision;
  if (Number.isSafeInteger(latest) && latest > 0) {
    requests.push(deploymentRequest.buildLog(id, latest));
  }
  if (canManage && !isBusy(deployment.status)) {
    const target = rollbackTarget(deployment);
    if (target) {
      requests.push(
        deploymentRequest.rollback(
          id,
          target.revision,
          deployment.liveRevision,
        ),
      );
    }
    requests.push(
      deploymentRequest.rotateToken(id),
      deploymentRequest.destroy(id),
    );
  }
  const lead = TAB_FIRST[tab];
  const first = requests.findIndex((request) => request.id === lead);
  if (first > 0) requests.unshift(...requests.splice(first, 1));
  return offered(requests);
}

/**
 * The deploy request the wizard would send, or null until it would send one
 * (a bundle chosen and every setting accepted) or when EE would refuse it to
 * this person (not an owner or admin: `canManage`).
 */
export function deploymentCreateRequest(
  { bundle, settings, checks },
  { canManage = false } = {},
) {
  if (!canManage) return null;
  if (!bundle || !settings || !checks?.ok) return null;
  return deploymentRequest.create({
    name: settings.name,
    bundle,
    env: checks.env,
    secretRefs: settings.secretRefs,
    studio: settings.studio,
  });
}

/** Deployment secrets: names only. Adding, replacing and revoking stay in the form. */
export function deploymentSecretsRequests() {
  return offered([deploymentRequest.credentials()]);
}
