// Row API copy text is offered only while dataset.rowApiPath shows AND the
// generated surface carries GET. DSX_DATASET_ROW_SOURCE_ENABLED is default
// OFF, so the route is a named exception, not a public contract. The exception
// permits formatting, never availability; retain the GET guard even in
// prototype builds. These helpers build text only, never send a request.
import {
  uncontractedApiPath,
  getContractedApiMethods,
} from "src/api/contracts/api-surface";
import {
  fiGetRequest,
  requestSnippets,
} from "src/sections/common/commands/requestSnippets";
import { isSurfaceShown } from "src/launch/launchSwitch";

/** The surface that gates the Row view's API items. */
export const ROW_API_SURFACE = "dataset.rowApiPath";

/** The gated row read's exact exception template. */
export const ROW_READ_TEMPLATE =
  "/model-hub/datasets/{dataset_id}/rows/{row_id}/";

/**
 * `{method, path}` of the row read, or null when either id is missing or the
 * generated contract does not declare its GET.
 */
export function rowReadRequest(datasetId, rowId) {
  if (!datasetId || !rowId) return null;
  if (!getContractedApiMethods(ROW_READ_TEMPLATE).includes("get")) return null;
  return {
    method: "GET",
    path: uncontractedApiPath(
      "/model-hub/datasets/{dataset_id}/rows/{row_id}/",
      {
        dataset_id: datasetId,
        row_id: rowId,
      },
    ),
  };
}

/**
 * CopyId's API props for a row: `{apiPath, apiCall}`, or `{}` while the
 * surface is hidden or the contract lacks the read.
 */
export function rowApiCopyProps(
  datasetId,
  rowId,
  shown = isSurfaceShown("dataset.rowApiPath"),
) {
  if (!shown) return {};
  const request = rowReadRequest(datasetId, rowId);
  if (!request) return {};
  return {
    apiPath: `${request.method} ${request.path}`,
    apiCall: requestSnippets(fiGetRequest(request.path)).curl,
  };
}
