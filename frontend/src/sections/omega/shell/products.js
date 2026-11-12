// Which product a route belongs to. The route is the only source of truth:
// /dashboard/console/* is Omega Console, /dashboard/gateway/* is the AI
// Gateway, everything else is Future AGI. No choice is ever written to
// storage.

import { paths } from "src/routes/paths";

export const PRODUCT = {
  FUTURE_AGI: "future-agi",
  OMEGA_CONSOLE: "omega-console",
  AI_GATEWAY: "ai-gateway",
};

export const PRODUCTS = [
  {
    id: PRODUCT.FUTURE_AGI,
    label: "Future AGI",
    description: "Build, evaluate and observe",
  },
  {
    id: PRODUCT.AI_GATEWAY,
    label: "AI Gateway",
    description: "Route and govern models",
  },
  {
    id: PRODUCT.OMEGA_CONSOLE,
    label: "Omega Console",
    description: "Hosted agents for your team",
  },
];

// A build without the Omega routes still loads this module: each Omega root
// falls back to its fixed URL.
const CONSOLE_ROOT = paths.dashboard.console?.root ?? "/dashboard/console";
const GATEWAY_ROOT = paths.dashboard.gateway.root;

const FALLBACK_HOST_PATH = paths.dashboard.develop;

function startsWithSegment(pathname, root) {
  return pathname === root || pathname.startsWith(`${root}/`);
}

export function isConsolePath(pathname = "") {
  return startsWithSegment(pathname, CONSOLE_ROOT);
}

export function isGatewayPath(pathname = "") {
  return startsWithSegment(pathname, GATEWAY_ROOT);
}

// Routes added by the shell. They are the only routes where the dashboard
// layout drops its 1200 px minimum width, so Future AGI pages keep their
// current behaviour. Settings -> Sandboxes is one of them: the Sandboxes tab
// added it, and its owners reach it from the Sandboxes page on a phone too.
const AGENT_INFRASTRUCTURE_ROOTS = [
  paths.dashboard.studio ?? "/dashboard/studio",
  paths.dashboard.deployments?.root ?? "/dashboard/deployments",
  paths.dashboard.sandboxes?.root ?? "/dashboard/sandboxes",
  "/dashboard/settings/sandboxes",
  paths.dashboard.hub?.root ?? "/dashboard/hub",
];

export function isAgentInfrastructurePath(pathname = "") {
  return AGENT_INFRASTRUCTURE_ROOTS.some((root) =>
    startsWithSegment(pathname, root),
  );
}

export function isOmegaShellPath(pathname = "") {
  return isConsolePath(pathname) || isAgentInfrastructurePath(pathname);
}

export function productForPath(pathname = "") {
  if (isConsolePath(pathname)) return PRODUCT.OMEGA_CONSOLE;
  if (isGatewayPath(pathname)) return PRODUCT.AI_GATEWAY;
  return PRODUCT.FUTURE_AGI;
}

// The last Future AGI page, kept in memory only, so "Future AGI" in the
// switcher returns the user to where they were before opening the Console or
// the AI Gateway.
let lastHostPath = null;

export function rememberHostPath(pathname) {
  if (!pathname || isConsolePath(pathname) || isGatewayPath(pathname)) return;
  if (!startsWithSegment(pathname, "/dashboard")) return;
  lastHostPath = pathname;
}

export function hostReturnPath() {
  return lastHostPath || FALLBACK_HOST_PATH;
}

export function resetHostPathForTests() {
  lastHostPath = null;
}

export function pathForProduct(productId) {
  if (productId === PRODUCT.OMEGA_CONSOLE) return CONSOLE_ROOT;
  if (productId === PRODUCT.AI_GATEWAY) return GATEWAY_ROOT;
  return hostReturnPath();
}
