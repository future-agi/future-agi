import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

import { observeLinkForTarget } from "./observeLinks";

/**
 * Host-side Observe operations are intentionally kept separate from the
 * customer gateway client. The gateway assertion is for EE customer routes;
 * ensuring an Observe project uses the signed-in Future AGI session and the
 * workspace selected in the shell.
 *
 * One operation exists on the backend today:
 * `tracer_omega_deployments_observe_create`
 * (`POST /tracer/omega/deployments/<id>/observe/` with `{name}`; FA
 * `tracer/views/omega_deployments.py`). It answers the `{status, result}`
 * envelope with `projectId`, `projectName`, `alerts` and `alertsDeferred`.
 * EE calls the same operation on every deploy and shows the linked project
 * on the deployment (`observe.projectId`), so the Deployments page links
 * without calling this; it is here for callers that hold only an id and name.
 */
export const OBSERVE_DEPLOYMENTS_ROOT = "/tracer/omega/deployments";
export const observeRouteTemplates = Object.freeze({
  ensure: `${OBSERVE_DEPLOYMENTS_ROOT}/{deployment_id}/observe/`,
});

export const observeRoutes = Object.freeze({
  ensure: (deploymentId) =>
    apiPath("/tracer/omega/deployments/{deployment_id}/observe/", {
      deployment_id: deploymentId,
    }),
});

const DEPLOYMENT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$/;
const DEPLOYMENT_NAME_RE = /^[a-z][a-z0-9-]{0,39}$/;

function assertDeploymentId(value) {
  if (typeof value !== "string" || !DEPLOYMENT_ID_RE.test(value)) {
    throw new TypeError(`Invalid deployment id: ${JSON.stringify(value)}`);
  }
  return value;
}

function assertDeploymentName(value) {
  const name = typeof value === "string" ? value.trim() : value;
  if (typeof name !== "string" || !DEPLOYMENT_NAME_RE.test(name)) {
    throw new TypeError("An Observe link needs the deployment name");
  }
  return name;
}

/** The ensure answer's `result`, or a TypeError when it is not a project. */
function ensuredTarget(response) {
  const body = response?.data ?? response;
  const result = body?.result;
  if (
    body?.status !== true ||
    !result ||
    typeof result.projectId !== "string" ||
    typeof result.projectName !== "string"
  ) {
    throw new TypeError("The Observe ensure answer is not a project");
  }
  return {
    projectId: result.projectId,
    projectName: result.projectName,
    alerts: Array.isArray(result.alerts) ? result.alerts : [],
    alertsDeferred: result.alertsDeferred ?? null,
  };
}

function requestMethods(request) {
  if (!request || typeof request.post !== "function") {
    throw new TypeError(
      "observeClient needs an axios-compatible request client",
    );
  }
  return request;
}

/** @param {{ request?: {post: Function} }} [deps] */
export function createObserveClient({ request = axios } = {}) {
  const http = requestMethods(request);

  const ensure = async ({ deploymentId, deploymentName }) => {
    const id = assertDeploymentId(deploymentId);
    const name = assertDeploymentName(deploymentName);
    const response = await http.post(observeRoutes.ensure(id), { name });
    return ensuredTarget(response);
  };

  const linkForDeployment = async ({
    deploymentId,
    deploymentName,
    revision,
    returnTo,
    origin,
    location,
    filters,
    includeErrors,
    runId,
    traceId,
    primaryTraceDateFilter,
    dateFilter,
    window,
  }) => {
    const target = await ensure({ deploymentId, deploymentName });
    const href = observeLinkForTarget(target, {
      deploymentId,
      deploymentName,
      revision,
      returnTo,
      origin,
      location,
      filters,
      includeErrors,
      runId,
      traceId,
      primaryTraceDateFilter,
      dateFilter,
      window,
    });
    return { target, href };
  };

  return {
    ensure,
    ensureObserveTarget: ensure,
    linkForDeployment,
  };
}

const defaultClient = createObserveClient();

export const ensureObserveTarget = defaultClient.ensure;
export const linkDeploymentToObserve = defaultClient.linkForDeployment;
