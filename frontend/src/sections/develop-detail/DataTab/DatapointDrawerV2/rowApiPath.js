// Row API copy text needs both dataset.rowApiPath and a contracted GET.
// The default-on row read is part of the generated surface. Keep the GET
// guard for builds generated with the server switch explicitly off.
// These helpers build text only, never send a request.
import {
  apiPath,
  getContractedApiMethods,
} from "src/api/contracts/api-surface";
import {
  fiGetRequest,
  requestSnippets,
} from "src/sections/common/commands/requestSnippets";
import { isSurfaceShown } from "src/launch/launchSwitch";

/** The surface that gates the Row view's API items. */
export const ROW_API_SURFACE = "dataset.rowApiPath";

/** The contracted row read's exact path template. */
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
    path: apiPath("/model-hub/datasets/{dataset_id}/rows/{row_id}/", {
      dataset_id: datasetId,
      row_id: rowId,
    }),
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
