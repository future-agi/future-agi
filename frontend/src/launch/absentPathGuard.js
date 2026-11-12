// L6 launch switch: no request to a path the real backend does not have.
// The DSX prototype endpoints (the DSX_PROTOTYPE entries of
// api/contracts/api-contract-exceptions.js; none exists on origin/dev) are
// refused in the browser in a launch build, before any network call, with a
// 404-shaped error the callers already handle as "not there". The r4/connect
// machines poll, the runs list, versions, splits and the W7 evaluator routes
// therefore never leave the page, even from a door a gate missed. A lane
// that backs one of them lists its template in madeReal.js MADE_REAL_PATHS.
// main.jsx installs it once, in non-prototype builds only. Every refusal is
// counted on window.__dsxLaunchAbsent for the launch walk to read.
import { API_CONTRACT_EXCEPTIONS } from "src/api/contracts/api-contract-exceptions";
import { madeRealPathTemplates } from "./madeReal";
import { SHOW_UNRELEASED_PARTS, isLaunchBuild } from "./launchSwitch";
import { LAUNCH_HOLD_ROUTES } from "./launchHoldRoutes";

const DSX_REASON = "dsx prototype (PRODUCT-SPEC §6.2)";

// A public schema is not a launch claim. Retain these formerly uncontracted
// templates in the guard inventory until their slot claims each path; jury
// remains guarded even though its response now has a backend contract.
export const CONTRACTED_PROTOTYPE_PATHS = Object.freeze([
  "/model-hub/eval-templates/create-dag/",
  "/model-hub/eval-templates/{template_id}/dag/",
  "/model-hub/eval-templates/dag/execute-adhoc/",
  "/model-hub/eval-templates/{template_id}/dag/paths/",
  "/model-hub/eval-templates/{template_id}/composite/jury/",
  // F06 run control: the backend publishes resume; the XP claim backs it.
  "/model-hub/experiments/v2/{experiment_id}/resume/",
  // 708J published these and dropped their exceptions (71c17835b4: versions,
  // version detail, baseline; f2c4e6b0c9: saved views). No slot claims them
  // and their owners (dataset.versions, exp.results.matrix,
  // dataset.savedViews) ship hidden, so a launch build still refuses them.
  "/model-hub/datasets/{dataset_id}/versions/",
  "/model-hub/datasets/{dataset_id}/versions/{version_id}/",
  "/model-hub/datasets/{dataset_id}/baseline/",
  // The backend now documents row activity and add results to a dataset;
  // their owners (dataset.rowExtras.activity, exp.results.addToDataset) ship
  // hidden, so a launch build still refuses them (as 708J above).
  "/model-hub/datasets/{dataset_id}/rows/{row_id}/activity/",
  "/model-hub/datasets/{dataset_id}/rows/from-results/",
  "/model-hub/r3/saved-views/",
  "/model-hub/r3/saved-views/{view_id}/",
  // The backend serves and documents the workspace's decision-model listing
  // (agentcc/urls.py). Its owner (models.decision) ships hidden and no slot
  // claims it, so a launch build with the switch off still refuses it. As
  // shipped the guard is not installed and the listing is requested.
  "/agentcc/decisions/models/",
]);

const templateRe = (template) =>
  new RegExp(
    `^${template
      .replace(/\/$/, "")
      .split(/\{[^}]+\}/)
      .map((p) => p.replace(/[.+?^${}()|[\]\\]/g, "\\$&"))
      .join("[^/]+")}/?$`,
  );

/** The DSX prototype path templates this build refuses. Pure. */
export function absentTemplates(
  exceptions = API_CONTRACT_EXCEPTIONS,
  backed = madeRealPathTemplates(),
) {
  const uncontracted = Object.entries(exceptions)
    .filter(([, meta]) => meta?.reason === DSX_REASON)
    .map(([template]) => template);
  const absent = [...uncontracted, ...CONTRACTED_PROTOTYPE_PATHS].filter(
    (t) => !backed.has(t),
  );
  return [...new Set([...absent, ...LAUNCH_HOLD_ROUTES])];
}

const ABSENT = absentTemplates().map((t) => ({
  template: t,
  re: templateRe(t),
}));

/** The path part of a request URL (`/model-hub/r4/…`), without the host. */
export function apiPathOf(url, baseURL = "") {
  const raw = String(url ?? "");
  let path = raw;
  try {
    const u = /^https?:\/\//i.test(raw)
      ? new URL(raw)
      : new URL(raw, /^https?:\/\//i.test(baseURL) ? baseURL : "http://x");
    path = u.pathname;
  } catch {
    path = raw.split("?")[0];
  }
  return path.endsWith("/") ? path : `${path}/`;
}

/** The absent template a request path hits, or null. */
export function absentTemplateOf(url, baseURL = "", list = ABSENT) {
  const path = apiPathOf(url, baseURL);
  return list.find((t) => t.re.test(path))?.template ?? null;
}

/** The error a refused request rejects with (axios-shaped, no response). */
export function absentError(template, config) {
  const err = new Error(`Not available yet: ${template}`);
  err.name = "LaunchAbsentPath";
  err.code = "ERR_LAUNCH_ABSENT";
  err.config = config;
  err.launchAbsent = template;
  err.response = {
    status: 404,
    statusText: "Not Found",
    data: { detail: "Not available yet", launch_absent: template },
    headers: {},
    config,
  };
  return err;
}

function record(template, config) {
  try {
    const w = window;
    w.__dsxLaunchAbsent = w.__dsxLaunchAbsent ?? [];
    w.__dsxLaunchAbsent.push({
      template,
      method: String(config?.method ?? "get").toUpperCase(),
      at: Date.now(),
    });
  } catch {
    /* no window */
  }
}

/** The request interceptor: refuse absent paths, pass everything else. */
export function absentPathInterceptor(config) {
  const template = absentTemplateOf(config?.url, config?.baseURL ?? "");
  if (!template) return config;
  record(template, config);
  return Promise.reject(absentError(template, config));
}

let installed = false;

/** Install on an axios instance (once). A no-op outside a launch build. */
export function installAbsentPathGuard(instance) {
  if (
    installed ||
    !instance?.interceptors ||
    !isLaunchBuild() ||
    SHOW_UNRELEASED_PARTS
  )
    return false;
  // Request interceptors run last-registered first: this one runs before
  // the contract guard and the network.
  instance.interceptors.request.use(absentPathInterceptor);
  installed = true;
  return true;
}
