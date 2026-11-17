// Controlled rollout of the hosted Omega products (READINESS 3.13, OD-01).
//
// The server decides; this module only reads its answer. Every product ships
// dark: a product shows when VITE_OMEGA_CONSOLE (the deployment-wide master
// switch) is on AND GET /api/capabilities/ admits the org and workspace.
// Anything else (still loading, an older backend without the entry, an
// unreadable answer) keeps the product hidden: fail closed, never a flash.

export const OMEGA_PRODUCT = Object.freeze({
  CONSOLE: "console",
  SANDBOXES: "sandboxes",
  DEPLOYMENTS: "deployments",
  STUDIO_TAB: "studio_tab",
  HUB: "hub",
});

export const OMEGA_PRODUCTS = Object.freeze(Object.values(OMEGA_PRODUCT));

export const PRODUCT_LABEL = Object.freeze({
  [OMEGA_PRODUCT.CONSOLE]: "Omega Console",
  [OMEGA_PRODUCT.SANDBOXES]: "Sandboxes",
  [OMEGA_PRODUCT.DEPLOYMENTS]: "Deployments",
  [OMEGA_PRODUCT.STUDIO_TAB]: "Studio",
  [OMEGA_PRODUCT.HUB]: "Hub",
});

export const PRODUCT_STATUS = Object.freeze({
  LOADING: "loading",
  ADMITTED: "admitted",
  PAUSED: "paused",
  HIDDEN: "hidden",
  UNAVAILABLE: "unavailable",
});

// How often an open page may re-read the rollout answer when a gate mounts or
// the window regains focus. The server re-decides on every request anyway.
export const ROLLOUT_STALE_MS = 30_000;

export const capabilityIdFor = (product) => `omega_${product}`;

/** One product's status from the /api/capabilities/ answer. */
export function productStatus(capabilities, product, { isLoading, isError }) {
  if (isLoading) return PRODUCT_STATUS.LOADING;
  if (isError) return PRODUCT_STATUS.UNAVAILABLE;
  const feature = capabilities?.features?.[capabilityIdFor(product)];
  if (!feature) return PRODUCT_STATUS.HIDDEN;
  if (feature.allowed === true) return PRODUCT_STATUS.ADMITTED;
  if (feature.reason_code === "PRODUCT_PAUSED") return PRODUCT_STATUS.PAUSED;
  if (feature.reason_code === "RESOLVER_UNAVAILABLE") {
    return PRODUCT_STATUS.UNAVAILABLE;
  }
  return PRODUCT_STATUS.HIDDEN;
}

// Parts of the hosted products that show only when they work today
// (omega_rollout/readiness.py): a released CLI command, the Hub checker, a
// deployment that opens in Studio, and the parity program's hide switches
// (switches.js). The server reports each as `omega_part_<part>`; anything but
// `allowed: true` hides the part, or keeps its honest wording.
export const OMEGA_PART = Object.freeze({
  HUB_CLI: "hub_cli",
  HUB_PUBLISH: "hub_publish",
  DEPLOY_CLI: "deploy_cli",
  DEPLOYMENT_STUDIO: "deployment_studio",
  DEPLOY_NO_DOWNTIME: "deploy_no_downtime",
  DEPLOY_LANGGRAPH_JS: "deploy_langgraph_js",
  SDK_PYTHON: "sdk_python",
  SDK_TYPESCRIPT: "sdk_typescript",
});

export const partCapabilityId = (part) => `omega_part_${part}`;

/** Whether one part works today. Loading, an error or no answer: false (fail closed). */
export function isPartReady(capabilities, part, { isLoading, isError }) {
  if (isLoading || isError) return false;
  return capabilities?.features?.[partCapabilityId(part)]?.allowed === true;
}

/** Shown in the nav and the switcher: admitted, or admitted and paused. */
export const isListed = (status) =>
  status === PRODUCT_STATUS.ADMITTED || status === PRODUCT_STATUS.PAUSED;
