// W96B / 708i: a published contract does not activate Results in launch.
// Keep this closed list until the composed-head Results walk is accepted.
// Remove a route here to release its explicit hold; a backing claim alone
// must not release it. The guard stays path-scoped, exactly as before.
export const LAUNCH_HOLD_ROUTES = Object.freeze([
  "/model-hub/datasets/{dataset_id}/runs/",
  "/model-hub/datasets/{dataset_id}/results/",
  "/model-hub/datasets/{dataset_id}/results/analysis/",
]);
