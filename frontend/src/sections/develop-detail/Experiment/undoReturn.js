// v2.1 (action:create-toast-undo): where Undo on the create toast lands
// once the experiment is deleted: the dataset's Experiments tab, keeping
// the prototype stage and other non-experiment query params. Pure.

const DROP = new Set(["new_experiment", "experiment", "run", "row", "rows"]);

/**
 * @param {string} datasetId
 * @param {{search?: string}} [location]  window.location (or a stand-in)
 * @returns {string} e.g. `/dashboard/develop/<id>?tab=experiments&proto_stage=r4`
 */
export function undoReturnPath(datasetId, location = {}) {
  const params = new URLSearchParams(location?.search ?? "");
  DROP.forEach((k) => params.delete(k));
  params.set("tab", "experiments");
  return `/dashboard/develop/${datasetId}?${params.toString()}`;
}
