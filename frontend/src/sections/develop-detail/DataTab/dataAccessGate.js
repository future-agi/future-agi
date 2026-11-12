import { isSurfaceShown } from "src/launch/launchSwitch";

/**
 * v2.1 g8 (access:gates): the Data tab's controls follow the dataset's own
 * access level (useDatasetAccess), not only the workspace role. A member
 * whose access is View sees the viewer grid and a read-only toolbar; Run
 * prompt / Evaluate also need Run. While the access view loads `can` is open
 * (the server's guard still refuses), so nothing flickers off. Pure.
 */
export const gridViewOnly = ({ roleViewer = false, can } = {}) =>
  Boolean(roleViewer) || can?.edit === false;

export function toolbarAccessGate({ can, reason } = {}) {
  const editOff = can?.edit === false;
  const runOff = can?.run === false;
  const say = (perm, fallback) =>
    (typeof reason === "function" && reason(perm)) || fallback;
  return {
    editOff,
    runOff,
    editReason: editOff
      ? say("edit", "Your access to this dataset is View")
      : "",
    runReason: runOff
      ? say("run", "Your access to this dataset doesn't include Run")
      : "",
  };
}

/**
 * v2.1 g8 (create:from-production): what `?add=production` does on the
 * dataset page — open the production sheet on this dataset, or refuse when
 * the dataset is read-only for this person. Waits while access loads (the
 * open default would let a viewer through). Pure.
 * @returns {null|{kind: "wait"}|{kind: "refuse"}|{kind: "open", request: object}}
 */
export function addParamAction({
  addParam,
  datasetId,
  can,
  roleViewer = false,
  loading = false,
} = {}) {
  if (addParam !== "production" || !datasetId) return null;
  // L6 launch switch: production failures in is absent on dev; the link
  // does nothing in a launch build (the route guard also drops the key).
  if (!isSurfaceShown("dataset.fromProduction")) return null;
  if (loading) return { kind: "wait" };
  if (gridViewOnly({ roleViewer, can })) return { kind: "refuse" };
  return {
    kind: "open",
    request: {
      source: { kind: "sessions" },
      target: { dataset_id: datasetId },
    },
  };
}
