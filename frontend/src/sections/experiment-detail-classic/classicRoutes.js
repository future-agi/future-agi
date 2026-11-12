// URL helpers of the classic experiment page (L3).

export function classicDataPath(experimentId) {
  return `/dashboard/develop/experiment/${experimentId}/data`;
}

// The search string the classic page needs, or null when the URL already has
// it (or the experiment has not loaded). Dev's list opens an experiment with
// ?datasetId=<dataset>&payload=<{id,name}>; the DSX create lands with neither.
// Other query keys are kept.
export function classicLandingSearch(search, experiment) {
  const params = new URLSearchParams(search ?? "");
  if (params.get("datasetId")) return null;
  const datasetId = experiment?.dataset_id;
  if (!datasetId) return null;
  params.set("datasetId", String(datasetId));
  if (!params.get("payload") && experiment?.id) {
    params.set(
      "payload",
      JSON.stringify({ id: experiment.id, name: experiment.name ?? "" }),
    );
  }
  return `?${params.toString()}`;
}
