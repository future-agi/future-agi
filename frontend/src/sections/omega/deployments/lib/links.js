import { offerOmegaConnect } from "src/sections/omega/embed";

/**
 * Routes for the Deployments tab (registered by the shell lane as
 * paths.dashboard.deployments) and the Studio hand-off.
 */
const ROOT = "/dashboard/deployments";

export const DEPLOYMENT_TABS = ["overview", "revisions", "logs", "settings"];

export const deploymentsPath = () => ROOT;

/** The workspace vault's secrets, as Deployments uses them. */
export const secretsPath = () => `${ROOT}/secrets`;

/** The wizard; with an id it deploys the next revision of that deployment. */
export const newDeploymentPath = (id) =>
  id ? `${ROOT}/new?deployment=${encodeURIComponent(id)}` : `${ROOT}/new`;

export function deploymentPath(id, tab) {
  const base = `${ROOT}/${encodeURIComponent(id)}`;
  return tab && tab !== "overview"
    ? `${base}/${encodeURIComponent(tab)}`
    : base;
}

const STUDIO_PATH = "/dashboard/studio";

/**
 * Open the Studio tab on a deployment. Its server and access token go over in
 * memory (offerOmegaConnect: single use, 60 s at most, this origin only),
 * never in a URL, fragment included (OD-21), so the move is
 * a router push: a full page load would forget the offer.
 */
export function openInStudio(url, token, navigate) {
  if (!url || !token || typeof navigate !== "function") return false;
  if (!offerOmegaConnect(url, token)) return false;
  navigate(STUDIO_PATH);
  return true;
}
