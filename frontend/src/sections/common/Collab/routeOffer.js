// Doors whose calls have no route in this build, decided in one place, and
// the one sentence each says. A route is missing when this build's absent
// list names its path (src/launch/absentPathGuard.js: the prototype-only
// endpoints no group has backed yet). When a group backs one (madeReal.js
// MADE_REAL_PATHS) or the backend publishes it, the door and its reads come
// back with no change here or in the door. Prototype builds and unit tests
// are not launch builds (launchSwitch.js), so they keep every door; a test
// reads a launch build with setLaunchOverrideForTest({ launch: true }).
import { absentTemplateOf } from "src/launch/absentPathGuard";
import { isLaunchBuild } from "src/launch/launchSwitch";

export const TAGS_NOT_OFFERED = "Tags are not part of this release.";
export const TRACE_MATCHING_NOT_OFFERED =
  "Matching traces to rows is not part of this release.";

/** The API path templates these doors call. */
export const DOOR_ROUTES = Object.freeze({
  tags: "/model-hub/tags/",
  experimentTags: "/model-hub/experiments/v2/{experiment_id}/tags/",
  traceMatching: "/model-hub/datasets/imports/",
  datasetAccess: "/model-hub/datasets/{dataset_id}/access/",
  datasetSources: "/model-hub/datasets/{dataset_id}/sources/",
});

/** Whether this build can call an API path template. */
export const routeOffered = (template) =>
  !isLaunchBuild() || absentTemplateOf(template) === null;

/** Tags: the tag list and an experiment's tags. */
export const tagsOffered = () =>
  routeOffered(DOOR_ROUTES.tags) && routeOffered(DOOR_ROUTES.experimentTags);

/** Matching traces to a dataset's rows (the traces card's match line). */
export const traceMatchingOffered = () =>
  routeOffered(DOOR_ROUTES.traceMatching);

/** The dataset's access read (background; no control of its own). */
export const datasetAccessOffered = () =>
  routeOffered(DOOR_ROUTES.datasetAccess);

/** The dataset's saved import sources read (background). */
export const datasetSourcesOffered = () =>
  routeOffered(DOOR_ROUTES.datasetSources);

/**
 * The Analysis workbench's run metrics, classification, best of N and
 * compare by key, by their endpoints.develop.w5 name.
 */
export const ANALYSIS_ROUTES = Object.freeze({
  derivedMetrics: "/model-hub/datasets/{dataset_id}/derived-metrics/",
  classification: "/model-hub/datasets/{dataset_id}/classification/",
  bestOf: "/model-hub/datasets/{dataset_id}/best-of/",
  resultsMatch: "/model-hub/results/match/",
});

/** Whether this build can call the analysis route named `name`. */
export const analysisRouteOffered = (name) =>
  Boolean(ANALYSIS_ROUTES[name]) && routeOffered(ANALYSIS_ROUTES[name]);
