// the interface contract · the R3 ids request of a results view (the interface contract §5 A2): the GET
// the results grid sends, plus the candidate expression pill and
// `ids_only=true` (the expression count and custom columns read it). L7c g1
// (the capability contract a): it is no longer printed as a Copy as API call. Pure.
import { fiGetRequest } from "src/sections/common/commands/requestSnippets";
import { runResultsParams } from "src/api/develop/run-results";

export const resultsPath = (datasetId) =>
  `/model-hub/datasets/${datasetId}/results/`;

/**
 * R3 params of a results view: the grid's own builder
 * (api/develop/run-results.runResultsParams), then `ids_only=true`, as
 * fetchResultIds sends them. view: { runIds, baselineId, pills, show, search, sort, agg }
 */
export const resultsIdsParams = (view) => ({
  ...runResultsParams(view),
  ids_only: "true",
});

/** The request (method, url, headers) for a results view. */
export const resultsIdsRequest = (datasetId, view) =>
  fiGetRequest(resultsPath(datasetId), resultsIdsParams(view));

/** The same request against another origin (walks run it against MSW). */
export function sameRequestAt(request, origin) {
  const u = new URL(request.url);
  return `${origin.replace(/\/$/, "")}${u.pathname}${u.search}`;
}
